"""
SQLite-backed knowledge store — one file per connection.

Replaces both db_schemas.json and db_knowledge.json from coding-agent.
Stores schema cache, table/column annotations, and the FK relationship graph.

File location: ~/.config/amnesic/knowledge_{conn_name_safe}.db
  where conn_name_safe = conn_name.replace(".", "_")
  e.g. orders.prod → knowledge_orders_prod.db
"""

import json
import sqlite3
import threading
from collections import deque
from pathlib import Path
from typing import Any

from amnesic._paths import config_dir as _amnesic_config_dir, knowledge_path as _amnesic_knowledge_path, secure_file as _secure_file

_CONFIG_DIR = _amnesic_config_dir()

_CREATE_SCHEMA_CACHE = """
CREATE TABLE IF NOT EXISTS schema_cache (
    table_fqn   TEXT NOT NULL,
    column_name TEXT NOT NULL,
    data_type   TEXT,
    is_nullable TEXT,
    max_length  INTEGER,
    cached_at   TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (table_fqn, column_name)
);
"""

_CREATE_TABLE_KNOWLEDGE = """
CREATE TABLE IF NOT EXISTS table_knowledge (
    table_fqn         TEXT PRIMARY KEY,
    description       TEXT DEFAULT '',
    aliases           TEXT DEFAULT '[]',
    deprecated_at     TEXT,
    deprecated_reason TEXT DEFAULT ''
);
"""

_CREATE_COLUMN_KNOWLEDGE = """
CREATE TABLE IF NOT EXISTS column_knowledge (
    table_fqn         TEXT NOT NULL,
    column_name       TEXT NOT NULL,
    description       TEXT DEFAULT '',
    enum_values       TEXT DEFAULT '{}',
    foreign_key       TEXT DEFAULT '',
    example_values    TEXT DEFAULT '[]',
    deprecated_at     TEXT,
    deprecated_reason TEXT DEFAULT '',
    PRIMARY KEY (table_fqn, column_name)
);
"""

# v0.2 migration: knowledge tables created before v0.2 lack the deprecation
# columns. CREATE TABLE IF NOT EXISTS won't alter an existing table, so add the
# columns idempotently via ALTER. Table names are hardcoded literals (not user
# input) — safe to interpolate.
_DEPRECATION_COLUMNS = (
    ("deprecated_at", "TEXT"),
    ("deprecated_reason", "TEXT DEFAULT ''"),
)

_CREATE_TABLE_RELATIONSHIPS = """
CREATE TABLE IF NOT EXISTS table_relationships (
    from_table        TEXT NOT NULL,
    from_column       TEXT NOT NULL,
    to_table          TEXT NOT NULL,
    to_column         TEXT NOT NULL,
    relationship_type TEXT DEFAULT 'fk',
    cardinality       TEXT DEFAULT 'many_to_one',
    source            TEXT DEFAULT 'manual',
    notes             TEXT DEFAULT '',
    PRIMARY KEY (from_table, from_column, to_table, to_column)
);
"""

_CREATE_KNOWLEDGE_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
    target_type UNINDEXED,
    table_fqn   UNINDEXED,
    column_name UNINDEXED,
    name_text,
    description,
    extras,
    tokenize = "porter unicode61"
);
"""

_CREATE_FTS_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS trg_tk_after_insert AFTER INSERT ON table_knowledge BEGIN
    INSERT INTO knowledge_fts(target_type, table_fqn, column_name, name_text, description, extras)
    VALUES ('table', NEW.table_fqn, '', NEW.table_fqn, NEW.description, NEW.aliases);
END;

CREATE TRIGGER IF NOT EXISTS trg_tk_after_update AFTER UPDATE ON table_knowledge BEGIN
    DELETE FROM knowledge_fts WHERE target_type='table' AND table_fqn=OLD.table_fqn AND column_name='';
    INSERT INTO knowledge_fts(target_type, table_fqn, column_name, name_text, description, extras)
    VALUES ('table', NEW.table_fqn, '', NEW.table_fqn, NEW.description, NEW.aliases);
END;

CREATE TRIGGER IF NOT EXISTS trg_tk_after_delete AFTER DELETE ON table_knowledge BEGIN
    DELETE FROM knowledge_fts WHERE target_type='table' AND table_fqn=OLD.table_fqn AND column_name='';
END;

CREATE TRIGGER IF NOT EXISTS trg_ck_after_insert AFTER INSERT ON column_knowledge BEGIN
    INSERT INTO knowledge_fts(target_type, table_fqn, column_name, name_text, description, extras)
    VALUES ('column', NEW.table_fqn, NEW.column_name, NEW.column_name, NEW.description, NEW.enum_values);
END;

CREATE TRIGGER IF NOT EXISTS trg_ck_after_update AFTER UPDATE ON column_knowledge BEGIN
    DELETE FROM knowledge_fts WHERE target_type='column' AND table_fqn=OLD.table_fqn AND column_name=OLD.column_name;
    INSERT INTO knowledge_fts(target_type, table_fqn, column_name, name_text, description, extras)
    VALUES ('column', NEW.table_fqn, NEW.column_name, NEW.column_name, NEW.description, NEW.enum_values);
END;

CREATE TRIGGER IF NOT EXISTS trg_ck_after_delete AFTER DELETE ON column_knowledge BEGIN
    DELETE FROM knowledge_fts WHERE target_type='column' AND table_fqn=OLD.table_fqn AND column_name=OLD.column_name;
END;
"""


class KnowledgeStore:
    """
    Per-connection SQLite knowledge store.

    Thread-safe: all reads and writes are guarded by a threading.Lock.
    Uses WAL journal mode for concurrent read performance.
    """

    def __init__(self, conn_name: str) -> None:
        _amnesic_config_dir().mkdir(parents=True, exist_ok=True)
        db_path = _amnesic_knowledge_path(conn_name)

        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        _secure_file(db_path)  # chmod 600 — knowledge stores may contain credentials/annotations

        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            # busy_timeout: when a second process holds the write lock (e.g. two
            # MCP clients pointed at the same knowledge file), wait up to 5s for
            # it to clear instead of failing immediately with SQLITE_BUSY. WAL
            # handles concurrent reads; this makes concurrent cross-process
            # writes queue rather than error.
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute(_CREATE_SCHEMA_CACHE)
            self._conn.execute(_CREATE_TABLE_KNOWLEDGE)
            self._conn.execute(_CREATE_COLUMN_KNOWLEDGE)
            self._conn.execute(_CREATE_TABLE_RELATIONSHIPS)
            self._conn.execute(_CREATE_KNOWLEDGE_FTS)
            # executescript handles multi-statement DDL (trigger definitions)
            self._conn.executescript(_CREATE_FTS_TRIGGERS)
            # v0.2 migration: add deprecation columns to pre-v0.2 knowledge
            # tables (idempotent — skipped when the columns already exist).
            self._migrate_deprecation_columns()
            # Same pattern for schema_cache.cached_at: stores created before
            # staleness reporting lack the column entirely, and ALTER cannot
            # add a non-constant DEFAULT, so old rows keep NULL (reported as
            # unknown age rather than pretending to be fresh).
            self._migrate_schema_cache_cached_at()
            # One-time migration: lowercase existing column_name values so future
            # lookups (which always lowercase) match annotations saved before
            # the v0.1.11 case-insensitivity fix.
            #
            # We can't just `UPDATE ... SET column_name = LOWER(column_name)`
            # because two mixed-case rows can lowercase to the same key
            # (e.g. `JobStatus` and `JOBSTATUS` both become `jobstatus`) and
            # the second UPDATE would hit the (table_fqn, column_name) PK.
            #
            # Robust approach: only run when migration is needed (idempotent
            # guard), then dedupe in Python and rewrite the table atomically.
            needs_migration = self._conn.execute(
                "SELECT 1 FROM column_knowledge "
                "WHERE column_name != LOWER(column_name) LIMIT 1"
            ).fetchone()
            if needs_migration:
                rows = self._conn.execute(
                    "SELECT table_fqn, column_name, description, enum_values, "
                    "foreign_key, example_values FROM column_knowledge"
                ).fetchall()
                # Dedupe by (table_fqn, lower(column_name)). Last write wins —
                # we iterate in rowid order, so newer annotations overwrite older.
                deduped: dict[tuple[str, str], tuple] = {}
                for r in rows:
                    key = (r["table_fqn"], r["column_name"].lower())
                    deduped[key] = (
                        key[0], key[1],
                        r["description"], r["enum_values"],
                        r["foreign_key"], r["example_values"],
                    )
                self._conn.execute("DELETE FROM column_knowledge")
                self._conn.executemany(
                    "INSERT INTO column_knowledge "
                    "(table_fqn, column_name, description, enum_values, "
                    "foreign_key, example_values) VALUES (?, ?, ?, ?, ?, ?)",
                    list(deduped.values()),
                )
            self._conn.commit()

            # Backfill FTS from existing knowledge rows (idempotent via WHERE NOT EXISTS)
            self._conn.execute("""
                INSERT INTO knowledge_fts(target_type, table_fqn, column_name, name_text, description, extras)
                SELECT 'table', table_fqn, '', table_fqn, description, aliases
                FROM table_knowledge
                WHERE NOT EXISTS (
                    SELECT 1 FROM knowledge_fts
                    WHERE target_type='table'
                      AND knowledge_fts.table_fqn=table_knowledge.table_fqn
                      AND column_name=''
                )
            """)
            self._conn.execute("""
                INSERT INTO knowledge_fts(target_type, table_fqn, column_name, name_text, description, extras)
                SELECT 'column', table_fqn, column_name, column_name, description, enum_values
                FROM column_knowledge
                WHERE NOT EXISTS (
                    SELECT 1 FROM knowledge_fts
                    WHERE target_type='column'
                      AND knowledge_fts.table_fqn=column_knowledge.table_fqn
                      AND knowledge_fts.column_name=column_knowledge.column_name
                )
            """)
            self._conn.commit()

    def _migrate_deprecation_columns(self) -> None:
        """Idempotently add v0.2 deprecation columns to knowledge tables.

        Assumes the caller holds self._lock. Reads PRAGMA table_info and only
        ALTERs when a column is missing, so it's a no-op on v0.2+ stores and a
        one-time upgrade on pre-v0.2 stores. No data is touched.
        """
        for table in ("table_knowledge", "column_knowledge"):
            existing = {
                row["name"]
                for row in self._conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            for col_name, col_decl in _DEPRECATION_COLUMNS:
                if col_name not in existing:
                    self._conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {col_name} {col_decl}"
                    )

    def _migrate_schema_cache_cached_at(self) -> None:
        """Idempotently add cached_at to a pre-staleness schema_cache table.

        Assumes the caller holds self._lock. Stores created before schema
        staleness reporting have no cached_at column at all; ALTER TABLE cannot
        add a DEFAULT calling a function, so migrated rows stay NULL and are
        reported as cache_age_days: null rather than a bogus age.
        """
        existing = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(schema_cache)").fetchall()
        }
        if "cached_at" not in existing:
            self._conn.execute("ALTER TABLE schema_cache ADD COLUMN cached_at TEXT")

    # ------------------------------------------------------------------
    # Schema cache
    # ------------------------------------------------------------------

    def get_cached_schema(self, table_fqn: str) -> list[dict[str, Any]] | None:
        """Return cached columns for a table, or None if not cached."""
        key = table_fqn.lower().strip()
        with self._lock:
            cur = self._conn.execute(
                "SELECT column_name, data_type, is_nullable, max_length "
                "FROM schema_cache WHERE table_fqn = ? ORDER BY rowid",
                (key,),
            )
            rows = cur.fetchall()
        if not rows:
            return None
        return [dict(r) for r in rows]

    def get_schema_cache_timestamp(self, table_fqn: str) -> str | None:
        """Return the newest cached_at stamp for a table's cached schema.

        None when the table is not cached or when the rows predate the
        cached_at column (migrated stores) — callers report unknown age
        rather than guessing.
        """
        key = table_fqn.lower().strip()
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(cached_at) AS stamp FROM schema_cache WHERE table_fqn = ?",
                (key,),
            ).fetchone()
        return row["stamp"] if row and row["stamp"] else None

    def save_cached_schema(self, table_fqn: str, columns: list[dict[str, Any]]) -> None:
        """Replace all cached columns for a table."""
        key = table_fqn.lower().strip()
        params = [
            (
                key,
                col.get("column_name", col.get("name", "")),
                col.get("data_type", col.get("type", "")),
                col.get("is_nullable", ""),
                col.get("max_length") if col.get("max_length") is not None else None,
            )
            for col in columns
        ]
        with self._lock:
            self._conn.execute(
                "DELETE FROM schema_cache WHERE table_fqn = ?", (key,)
            )
            if params:
                # Stamp explicitly (not via the column DEFAULT) so the format
                # is pinned here regardless of how the table was created:
                # UTC ISO-8601 with a trailing Z.
                self._conn.executemany(
                    "INSERT INTO schema_cache (table_fqn, column_name, data_type, is_nullable, max_length, cached_at) "
                    "VALUES (?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))",
                    params,
                )
            self._conn.commit()

    # ------------------------------------------------------------------
    # Table knowledge
    # ------------------------------------------------------------------

    def get_table_knowledge(self, table_fqn: str) -> dict[str, Any] | None:
        """Return table-level annotations, or None if none saved."""
        key = table_fqn.lower().strip()
        with self._lock:
            cur = self._conn.execute(
                "SELECT description, aliases, deprecated_at, deprecated_reason "
                "FROM table_knowledge WHERE table_fqn = ?",
                (key,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return {
            "description": row["description"] or "",
            "aliases": json.loads(row["aliases"] or "[]"),
            "deprecated": row["deprecated_at"] is not None,
            "deprecated_at": row["deprecated_at"],
            "deprecated_reason": row["deprecated_reason"] or "",
        }

    def set_table_deprecated(
        self, table_fqn: str, deprecated: bool = True, reason: str = ""
    ) -> bool:
        """Mark or unmark a table annotation as deprecated (soft, reversible).

        Upserts a table_knowledge row if none exists (you can deprecate a table
        that was cached but never annotated). Returns True if a row now carries
        the requested state.
        """
        key = table_fqn.lower().strip()
        with self._lock:
            existing = self._conn.execute(
                "SELECT 1 FROM table_knowledge WHERE table_fqn = ?", (key,)
            ).fetchone()
            if deprecated:
                if existing:
                    self._conn.execute(
                        "UPDATE table_knowledge SET deprecated_at = datetime('now'), "
                        "deprecated_reason = ? WHERE table_fqn = ?",
                        (reason, key),
                    )
                else:
                    self._conn.execute(
                        "INSERT INTO table_knowledge "
                        "(table_fqn, description, aliases, deprecated_at, deprecated_reason) "
                        "VALUES (?, '', '[]', datetime('now'), ?)",
                        (key, reason),
                    )
            else:
                # Undo — only meaningful if a row exists.
                if existing:
                    self._conn.execute(
                        "UPDATE table_knowledge SET deprecated_at = NULL, "
                        "deprecated_reason = '' WHERE table_fqn = ?",
                        (key,),
                    )
            self._conn.commit()
        return bool(existing) or deprecated

    def save_table_knowledge(
        self,
        table_fqn: str,
        description: str | None = None,
        aliases: list[str] | None = None,
    ) -> None:
        """Upsert table-level annotations. Only updates non-None fields."""
        key = table_fqn.lower().strip()
        with self._lock:
            cur = self._conn.execute(
                "SELECT description, aliases FROM table_knowledge WHERE table_fqn = ?",
                (key,),
            )
            existing = cur.fetchone()
            if existing:
                new_description = description if description is not None else existing["description"]
                new_aliases = json.dumps(aliases) if aliases is not None else existing["aliases"]
                self._conn.execute(
                    "UPDATE table_knowledge SET description = ?, aliases = ? WHERE table_fqn = ?",
                    (new_description, new_aliases, key),
                )
            else:
                new_description = description if description is not None else ""
                new_aliases = json.dumps(aliases) if aliases is not None else "[]"
                self._conn.execute(
                    "INSERT INTO table_knowledge (table_fqn, description, aliases) VALUES (?, ?, ?)",
                    (key, new_description, new_aliases),
                )
            self._conn.commit()

    # ------------------------------------------------------------------
    # Column knowledge
    # ------------------------------------------------------------------

    def get_column_knowledge(
        self, table_fqn: str, column_name: str
    ) -> dict[str, Any] | None:
        """Return column-level annotations, or None if none saved."""
        key = table_fqn.lower().strip()
        col_key = column_name.lower().strip()
        with self._lock:
            cur = self._conn.execute(
                "SELECT description, enum_values, foreign_key, example_values, "
                "deprecated_at, deprecated_reason "
                "FROM column_knowledge WHERE table_fqn = ? AND column_name = ?",
                (key, col_key),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return {
            "description": row["description"] or "",
            "enum_values": json.loads(row["enum_values"] or "{}"),
            "foreign_key": row["foreign_key"] or "",
            "example_values": json.loads(row["example_values"] or "[]"),
            "deprecated": row["deprecated_at"] is not None,
            "deprecated_at": row["deprecated_at"],
            "deprecated_reason": row["deprecated_reason"] or "",
        }

    def set_column_deprecated(
        self, table_fqn: str, column_name: str,
        deprecated: bool = True, reason: str = "",
    ) -> bool:
        """Mark or unmark a column annotation as deprecated (soft, reversible).

        Upserts a column_knowledge row if none exists. Returns True if a row now
        carries the requested state.
        """
        key = table_fqn.lower().strip()
        col_key = column_name.lower().strip()
        with self._lock:
            existing = self._conn.execute(
                "SELECT 1 FROM column_knowledge WHERE table_fqn = ? AND column_name = ?",
                (key, col_key),
            ).fetchone()
            if deprecated:
                if existing:
                    self._conn.execute(
                        "UPDATE column_knowledge SET deprecated_at = datetime('now'), "
                        "deprecated_reason = ? WHERE table_fqn = ? AND column_name = ?",
                        (reason, key, col_key),
                    )
                else:
                    self._conn.execute(
                        "INSERT INTO column_knowledge "
                        "(table_fqn, column_name, description, enum_values, foreign_key, "
                        "example_values, deprecated_at, deprecated_reason) "
                        "VALUES (?, ?, '', '{}', '', '[]', datetime('now'), ?)",
                        (key, col_key, reason),
                    )
            else:
                if existing:
                    self._conn.execute(
                        "UPDATE column_knowledge SET deprecated_at = NULL, "
                        "deprecated_reason = '' WHERE table_fqn = ? AND column_name = ?",
                        (key, col_key),
                    )
            self._conn.commit()
        return bool(existing) or deprecated

    def save_column_knowledge(
        self,
        table_fqn: str,
        column_name: str,
        description: str | None = None,
        enum_values: dict | None = None,
        foreign_key: str | None = None,
        example_values: list | None = None,
    ) -> None:
        """Upsert column-level annotations. Only updates non-None fields.

        column_name is lowercased before storage so lookups are case-insensitive
        regardless of the case used by INFORMATION_SCHEMA or the caller.
        """
        key = table_fqn.lower().strip()
        col_key = column_name.lower().strip()
        with self._lock:
            cur = self._conn.execute(
                "SELECT description, enum_values, foreign_key, example_values "
                "FROM column_knowledge WHERE table_fqn = ? AND column_name = ?",
                (key, col_key),
            )
            existing = cur.fetchone()
            if existing:
                new_desc = description if description is not None else existing["description"]
                new_enum = json.dumps(enum_values) if enum_values is not None else existing["enum_values"]
                new_fk = foreign_key if foreign_key is not None else existing["foreign_key"]
                new_ex = json.dumps(example_values) if example_values is not None else existing["example_values"]
                self._conn.execute(
                    "UPDATE column_knowledge SET description = ?, enum_values = ?, foreign_key = ?, example_values = ? "
                    "WHERE table_fqn = ? AND column_name = ?",
                    (new_desc, new_enum, new_fk, new_ex, key, col_key),
                )
            else:
                self._conn.execute(
                    "INSERT INTO column_knowledge (table_fqn, column_name, description, enum_values, foreign_key, example_values) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        key,
                        col_key,
                        description if description is not None else "",
                        json.dumps(enum_values) if enum_values is not None else "{}",
                        foreign_key if foreign_key is not None else "",
                        json.dumps(example_values) if example_values is not None else "[]",
                    ),
                )
            self._conn.commit()

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------

    def get_relationships(
        self, table_fqn: str, depth: int = 1
    ) -> dict[str, Any]:
        """
        BFS over table_relationships up to the given depth.

        Loads the full edge set in a single query, releases the lock, then
        traverses in pure Python — concurrent reads are not blocked by the BFS.

        Returns:
            {neighbors: [{from_table, from_column, to_table, to_column, ...}],
             paths: ["TableA -> TableB -> TableC", ...]}
        """
        key = table_fqn.lower().strip()

        # Load all edges in one query, then release the lock before BFS.
        with self._lock:
            cur = self._conn.execute(
                "SELECT from_table, from_column, to_table, to_column, "
                "relationship_type, cardinality, source, notes "
                "FROM table_relationships"
            )
            all_edges = [dict(r) for r in cur.fetchall()]

        # Build adjacency lists in pure Python — no lock held.
        forward: dict[str, list[dict]] = {}
        reverse: dict[str, list[dict]] = {}
        for edge in all_edges:
            forward.setdefault(edge["from_table"], []).append(edge)
            reverse.setdefault(edge["to_table"], []).append(edge)

        # BFS
        neighbors: list[dict] = []
        paths: list[str] = []
        visited: set[str] = {key}
        queue: deque[tuple[str, int, list[str]]] = deque([(key, 0, [key])])

        while queue:
            current, current_depth, current_path = queue.popleft()
            if current_depth >= depth:
                continue

            for edge in forward.get(current, []):
                neighbor = edge["to_table"]
                neighbors.append(edge)
                new_path = current_path + [neighbor]
                if len(new_path) > 1:
                    paths.append(" -> ".join(new_path))
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, current_depth + 1, new_path))

            for edge in reverse.get(current, []):
                neighbor = edge["from_table"]
                neighbors.append(edge)
                new_path = current_path + [neighbor]
                if len(new_path) > 1:
                    paths.append(" -> ".join(new_path))
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, current_depth + 1, new_path))

        unique_paths = list(dict.fromkeys(paths))
        return {"neighbors": neighbors, "paths": unique_paths}

    def save_relationship(
        self,
        from_table: str,
        from_column: str,
        to_table: str,
        to_column: str,
        relationship_type: str = "fk",
        cardinality: str = "many_to_one",
        source: str = "manual",
        notes: str = "",
    ) -> None:
        """Upsert a single table relationship."""
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO table_relationships "
                "(from_table, from_column, to_table, to_column, relationship_type, cardinality, source, notes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (from_table.lower(), from_column, to_table.lower(), to_column,
                 relationship_type, cardinality, source, notes),
            )
            self._conn.commit()

    def discover_relationships_bulk(self, rows: list[dict[str, Any]]) -> None:
        """Bulk-insert discovered FK relationships, replacing existing ones."""
        params = [
            (
                row.get("from_table", "").lower(),
                row.get("from_column", ""),
                row.get("to_table", "").lower(),
                row.get("to_column", ""),
                row.get("relationship_type", "fk"),
                row.get("cardinality", "many_to_one"),
                row.get("source", "discovered"),
                row.get("notes", ""),
            )
            for row in rows
        ]
        with self._lock:
            if params:
                try:
                    self._conn.executemany(
                        "INSERT OR REPLACE INTO table_relationships "
                        "(from_table, from_column, to_table, to_column, relationship_type, cardinality, source, notes) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        params,
                    )
                    self._conn.commit()
                except Exception:
                    self._conn.rollback()
                    raise

    # ------------------------------------------------------------------
    # List tables
    # ------------------------------------------------------------------

    def list_tables(self) -> list[dict[str, Any]]:
        """
        Return all known tables, merging schema_cache and table_knowledge.

        Each entry: {table_fqn, description, aliases, column_count}
        """
        with self._lock:
            # Schema counts
            cur = self._conn.execute(
                "SELECT table_fqn, COUNT(*) as column_count FROM schema_cache GROUP BY table_fqn"
            )
            schema_rows = {r["table_fqn"]: r["column_count"] for r in cur.fetchall()}

            # Knowledge entries
            cur = self._conn.execute(
                "SELECT table_fqn, description, aliases FROM table_knowledge"
            )
            knowledge_rows = {
                r["table_fqn"]: {
                    "description": r["description"] or "",
                    "aliases": json.loads(r["aliases"] or "[]"),
                }
                for r in cur.fetchall()
            }

        merged: dict[str, dict] = {}

        for fqn, count in schema_rows.items():
            merged[fqn] = {
                "table_fqn": fqn,
                "description": "",
                "aliases": [],
                "column_count": count,
            }

        for fqn, knowledge in knowledge_rows.items():
            if fqn in merged:
                merged[fqn]["description"] = knowledge["description"]
                merged[fqn]["aliases"] = knowledge["aliases"]
            else:
                merged[fqn] = {
                    "table_fqn": fqn,
                    "description": knowledge["description"],
                    "aliases": knowledge["aliases"],
                    "column_count": 0,
                }

        return sorted(merged.values(), key=lambda x: x["table_fqn"])

    def get_all_table_fqns(self) -> set[str]:
        """Return all table FQNs in the schema cache."""
        with self._lock:
            cur = self._conn.execute("SELECT DISTINCT table_fqn FROM schema_cache")
            return {r["table_fqn"] for r in cur.fetchall()}

    def get_all_column_names(self, table_fqn: str) -> set[str]:
        """Return all column names in the schema cache for a given table."""
        key = table_fqn.lower().strip()
        with self._lock:
            cur = self._conn.execute(
                "SELECT column_name FROM schema_cache WHERE table_fqn = ?", (key,)
            )
            return {r["column_name"] for r in cur.fetchall()}

    def get_all_column_knowledge_tables(self) -> list[str]:
        """Return distinct table FQNs that have column knowledge entries."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT DISTINCT table_fqn FROM column_knowledge"
            )
            return [r["table_fqn"] for r in cur.fetchall()]

    def get_all_table_knowledge_fqns(self) -> list[str]:
        """Return all table FQNs that have a table_knowledge row (annotated)."""
        with self._lock:
            cur = self._conn.execute("SELECT table_fqn FROM table_knowledge")
            return [r["table_fqn"] for r in cur.fetchall()]

    # ------------------------------------------------------------------
    # Hard delete (db_forget)
    # ------------------------------------------------------------------

    def forget_table(self, table_fqn: str, cascade: bool = False) -> dict[str, Any]:
        """Permanently delete a table annotation. Cascade is opt-in.

        Without cascade, removes ONLY the table_knowledge row. With cascade,
        also removes every column_knowledge row for the table and every
        table_relationships edge touching it. FTS stays consistent via the
        delete triggers. Returns counts of what was removed.
        """
        key = table_fqn.lower().strip()
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM table_knowledge WHERE table_fqn = ?", (key,)
            )
            removed_table = cur.rowcount > 0
            removed_columns = 0
            removed_relationships = 0
            if cascade:
                cur = self._conn.execute(
                    "DELETE FROM column_knowledge WHERE table_fqn = ?", (key,)
                )
                removed_columns = cur.rowcount
                cur = self._conn.execute(
                    "DELETE FROM table_relationships "
                    "WHERE from_table = ? OR to_table = ?",
                    (key, key),
                )
                removed_relationships = cur.rowcount
            self._conn.commit()
        return {
            "removed_table": removed_table,
            "removed_columns": removed_columns,
            "removed_relationships": removed_relationships,
        }

    def forget_column(self, table_fqn: str, column_name: str) -> bool:
        """Permanently delete a single column annotation. Returns True if a row
        was removed. FTS stays consistent via the delete trigger."""
        key = table_fqn.lower().strip()
        col_key = column_name.lower().strip()
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM column_knowledge WHERE table_fqn = ? AND column_name = ?",
                (key, col_key),
            )
            removed = cur.rowcount > 0
            self._conn.commit()
        return removed

    def get_all_column_knowledge(self, table_fqn: str) -> list[dict[str, Any]]:
        """Return all column knowledge rows for a table."""
        key = table_fqn.lower().strip()
        with self._lock:
            cur = self._conn.execute(
                "SELECT column_name, description, enum_values, foreign_key, example_values, "
                "deprecated_at, deprecated_reason "
                "FROM column_knowledge WHERE table_fqn = ?",
                (key,),
            )
            rows = cur.fetchall()
        result = []
        for row in rows:
            result.append({
                "column_name": row["column_name"],
                "description": row["description"] or "",
                "enum_values": json.loads(row["enum_values"] or "{}"),
                "foreign_key": row["foreign_key"] or "",
                "example_values": json.loads(row["example_values"] or "[]"),
                "deprecated": row["deprecated_at"] is not None,
                "deprecated_at": row["deprecated_at"],
                "deprecated_reason": row["deprecated_reason"] or "",
            })
        return result

    def get_all_relationships(self) -> list[dict[str, Any]]:
        """Return all relationship rows."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT from_table, from_column, to_table, to_column, "
                "relationship_type, cardinality, source, notes "
                "FROM table_relationships"
            )
            return [dict(r) for r in cur.fetchall()]

    # ------------------------------------------------------------------
    # Full-text search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        target: str = "all",
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        BM25-ranked full-text search over annotations.

        Returns ranked list of {target_type, table_fqn, column_name, score, description, snippet}.
        Score is BM25 (negated so higher is better).

        Args:
            query:  Search text. Supports FTS5 syntax: phrases ("foo bar"),
                    prefix matching (pay*), boolean operators (foo AND bar).
            target: "tables", "columns", or "all" (default).
            limit:  Maximum results to return.
        """
        if target not in ("all", "tables", "columns"):
            raise ValueError(f"target must be 'all', 'tables', or 'columns', got {target!r}")

        where_target = ""
        params: list[Any] = [query]
        if target == "tables":
            where_target = " AND target_type='table'"
        elif target == "columns":
            where_target = " AND target_type='column'"

        sql = f"""
            SELECT
                target_type,
                table_fqn,
                column_name,
                description,
                snippet(knowledge_fts, 4, '<mark>', '</mark>', '…', 12) AS snippet,
                -bm25(knowledge_fts) AS score
            FROM knowledge_fts
            WHERE knowledge_fts MATCH ?{where_target}
            ORDER BY score DESC
            LIMIT ?
        """
        params.append(limit)

        with self._lock:
            try:
                cur = self._conn.execute(sql, params)
                rows = cur.fetchall()
            except sqlite3.OperationalError:
                # Any malformed/unsupported FTS query — return empty rather than crash.
                # Includes: unbalanced quotes, '*' alone, 'AND'/'OR' without operands,
                # and FTS5-version-specific edge cases like "unknown special query".
                return []

            # Load deprecation status once (two small queries, not N+1) so search
            # results can warn the AI off stale tables/columns — same signal as
            # db_get_schema surfaces.
            dep_tables = {
                row["table_fqn"]: (row["deprecated_reason"] or "")
                for row in self._conn.execute(
                    "SELECT table_fqn, deprecated_reason FROM table_knowledge "
                    "WHERE deprecated_at IS NOT NULL"
                ).fetchall()
            }
            dep_cols = {
                (row["table_fqn"], row["column_name"]): (row["deprecated_reason"] or "")
                for row in self._conn.execute(
                    "SELECT table_fqn, column_name, deprecated_reason FROM column_knowledge "
                    "WHERE deprecated_at IS NOT NULL"
                ).fetchall()
            }

        results = []
        for r in rows:
            target_type = r["target_type"]
            table_fqn = r["table_fqn"]
            column_name = r["column_name"] if r["column_name"] else None
            if target_type == "table":
                reason = dep_tables.get(table_fqn)
            else:
                reason = dep_cols.get((table_fqn, r["column_name"]))
            results.append({
                "target_type": target_type,
                "table_fqn": table_fqn,
                "column_name": column_name,
                "description": r["description"] or "",
                "snippet": r["snippet"] or "",
                "score": float(r["score"]),
                "deprecated": reason is not None,
                "deprecated_reason": reason or "",
            })
        return results

    # ------------------------------------------------------------------
    # Export / import / clear  (portable knowledge — team handoff)
    # ------------------------------------------------------------------

    def export_knowledge(self) -> dict[str, Any]:
        """Dump all annotations + relationships for a full-fidelity handoff.

        Excludes schema_cache (re-derivable via db_get_schema). Preserves
        deprecation flags so a round-trip is lossless. enum_values /
        example_values / aliases are kept as raw JSON strings — import writes
        them back verbatim, so no parse/re-serialize drift.
        """
        with self._lock:
            tables = [
                {
                    "table_fqn": r["table_fqn"],
                    "description": r["description"] or "",
                    "aliases": r["aliases"] or "[]",
                    "deprecated_at": r["deprecated_at"],
                    "deprecated_reason": r["deprecated_reason"] or "",
                }
                for r in self._conn.execute(
                    "SELECT table_fqn, description, aliases, deprecated_at, "
                    "deprecated_reason FROM table_knowledge ORDER BY table_fqn"
                ).fetchall()
            ]
            columns = [
                {
                    "table_fqn": r["table_fqn"],
                    "column_name": r["column_name"],
                    "description": r["description"] or "",
                    "enum_values": r["enum_values"] or "{}",
                    "foreign_key": r["foreign_key"] or "",
                    "example_values": r["example_values"] or "[]",
                    "deprecated_at": r["deprecated_at"],
                    "deprecated_reason": r["deprecated_reason"] or "",
                }
                for r in self._conn.execute(
                    "SELECT table_fqn, column_name, description, enum_values, "
                    "foreign_key, example_values, deprecated_at, deprecated_reason "
                    "FROM column_knowledge ORDER BY table_fqn, column_name"
                ).fetchall()
            ]
            relationships = [
                dict(r)
                for r in self._conn.execute(
                    "SELECT from_table, from_column, to_table, to_column, "
                    "relationship_type, cardinality, source, notes "
                    "FROM table_relationships "
                    "ORDER BY from_table, from_column, to_table, to_column"
                ).fetchall()
            ]
        return {"tables": tables, "columns": columns, "relationships": relationships}

    def import_knowledge(self, data: dict[str, Any]) -> dict[str, int]:
        """Upsert annotations + relationships from an export() payload.

        Unconditional INSERT OR REPLACE — a handoff loads everything. The
        DELETE+INSERT semantics of REPLACE fire the FTS delete+insert triggers,
        so the search index stays consistent. Returns counts of rows written.
        """
        tables = data.get("tables", []) or []
        columns = data.get("columns", []) or []
        relationships = data.get("relationships", []) or []

        table_params = [
            (
                t["table_fqn"].lower().strip(),
                t.get("description", "") or "",
                t.get("aliases", "[]") or "[]",
                t.get("deprecated_at"),
                t.get("deprecated_reason", "") or "",
            )
            for t in tables
        ]
        column_params = [
            (
                c["table_fqn"].lower().strip(),
                c["column_name"].lower().strip(),
                c.get("description", "") or "",
                c.get("enum_values", "{}") or "{}",
                c.get("foreign_key", "") or "",
                c.get("example_values", "[]") or "[]",
                c.get("deprecated_at"),
                c.get("deprecated_reason", "") or "",
            )
            for c in columns
        ]
        rel_params = [
            (
                r["from_table"].lower().strip(),
                r.get("from_column", ""),
                r["to_table"].lower().strip(),
                r.get("to_column", ""),
                r.get("relationship_type", "fk"),
                r.get("cardinality", "many_to_one"),
                r.get("source", "manual"),
                r.get("notes", ""),
            )
            for r in relationships
        ]

        with self._lock:
            try:
                if table_params:
                    self._conn.executemany(
                        "INSERT OR REPLACE INTO table_knowledge "
                        "(table_fqn, description, aliases, deprecated_at, deprecated_reason) "
                        "VALUES (?, ?, ?, ?, ?)",
                        table_params,
                    )
                if column_params:
                    self._conn.executemany(
                        "INSERT OR REPLACE INTO column_knowledge "
                        "(table_fqn, column_name, description, enum_values, foreign_key, "
                        "example_values, deprecated_at, deprecated_reason) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        column_params,
                    )
                if rel_params:
                    self._conn.executemany(
                        "INSERT OR REPLACE INTO table_relationships "
                        "(from_table, from_column, to_table, to_column, relationship_type, "
                        "cardinality, source, notes) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        rel_params,
                    )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

        return {
            "tables": len(table_params),
            "columns": len(column_params),
            "relationships": len(rel_params),
        }

    def clear_knowledge(self) -> dict[str, int]:
        """Wipe every knowledge row for this connection (schema cache included).

        Annotations, relationships, and the cached schema are all removed; the
        SQLite file itself is kept so the store keeps working. FTS is emptied
        via the table_knowledge / column_knowledge delete triggers. Returns
        counts of what was removed.
        """
        with self._lock:
            tk = self._conn.execute("SELECT COUNT(*) FROM table_knowledge").fetchone()[0]
            ck = self._conn.execute("SELECT COUNT(*) FROM column_knowledge").fetchone()[0]
            rel = self._conn.execute("SELECT COUNT(*) FROM table_relationships").fetchone()[0]
            sc = self._conn.execute("SELECT COUNT(*) FROM schema_cache").fetchone()[0]
            try:
                # Order matters only for clarity — triggers keep FTS in lockstep.
                self._conn.execute("DELETE FROM column_knowledge")
                self._conn.execute("DELETE FROM table_knowledge")
                self._conn.execute("DELETE FROM table_relationships")
                self._conn.execute("DELETE FROM schema_cache")
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return {
            "tables": tk,
            "columns": ck,
            "relationships": rel,
            "schema_cache": sc,
        }

    def close(self) -> None:
        """Close the SQLite connection."""
        with self._lock:
            self._conn.close()


# Module-level store cache keyed by connection name
_store_cache: dict[str, KnowledgeStore] = {}
_store_cache_lock = threading.Lock()


def get_store(conn_name: str) -> KnowledgeStore:
    """Return a cached KnowledgeStore for the given connection name."""
    with _store_cache_lock:
        if conn_name not in _store_cache:
            _store_cache[conn_name] = KnowledgeStore(conn_name)
        return _store_cache[conn_name]


def close_store(conn_name: str) -> None:
    """Close and evict a cached store so its SQLite file can be deleted/replaced.

    No-op if the connection was never opened. Used by `amnesic remove
    --delete-knowledge` before unlinking the knowledge file.
    """
    with _store_cache_lock:
        store = _store_cache.pop(conn_name, None)
    if store is not None:
        store.close()
