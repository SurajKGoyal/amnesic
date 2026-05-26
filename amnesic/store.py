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

from amnesic._paths import config_dir as _amnesic_config_dir, knowledge_path as _amnesic_knowledge_path

_CONFIG_DIR = _amnesic_config_dir()

_CREATE_SCHEMA_CACHE = """
CREATE TABLE IF NOT EXISTS schema_cache (
    table_fqn   TEXT NOT NULL,
    column_name TEXT NOT NULL,
    data_type   TEXT,
    is_nullable TEXT,
    max_length  INTEGER,
    cached_at   TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (table_fqn, column_name)
);
"""

_CREATE_TABLE_KNOWLEDGE = """
CREATE TABLE IF NOT EXISTS table_knowledge (
    table_fqn   TEXT PRIMARY KEY,
    description TEXT DEFAULT '',
    aliases     TEXT DEFAULT '[]'
);
"""

_CREATE_COLUMN_KNOWLEDGE = """
CREATE TABLE IF NOT EXISTS column_knowledge (
    table_fqn      TEXT NOT NULL,
    column_name    TEXT NOT NULL,
    description    TEXT DEFAULT '',
    enum_values    TEXT DEFAULT '{}',
    foreign_key    TEXT DEFAULT '',
    example_values TEXT DEFAULT '[]',
    PRIMARY KEY (table_fqn, column_name)
);
"""

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

        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute(_CREATE_SCHEMA_CACHE)
            self._conn.execute(_CREATE_TABLE_KNOWLEDGE)
            self._conn.execute(_CREATE_COLUMN_KNOWLEDGE)
            self._conn.execute(_CREATE_TABLE_RELATIONSHIPS)
            self._conn.commit()

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

    def save_cached_schema(self, table_fqn: str, columns: list[dict[str, Any]]) -> None:
        """Replace all cached columns for a table."""
        key = table_fqn.lower().strip()
        with self._lock:
            self._conn.execute(
                "DELETE FROM schema_cache WHERE table_fqn = ?", (key,)
            )
            for col in columns:
                self._conn.execute(
                    "INSERT INTO schema_cache (table_fqn, column_name, data_type, is_nullable, max_length) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        key,
                        col.get("column_name", col.get("name", "")),
                        col.get("data_type", col.get("type", "")),
                        col.get("is_nullable", ""),
                        col.get("max_length") if col.get("max_length") is not None else None,
                    ),
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
                "SELECT description, aliases FROM table_knowledge WHERE table_fqn = ?",
                (key,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return {
            "description": row["description"] or "",
            "aliases": json.loads(row["aliases"] or "[]"),
        }

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
        with self._lock:
            cur = self._conn.execute(
                "SELECT description, enum_values, foreign_key, example_values "
                "FROM column_knowledge WHERE table_fqn = ? AND column_name = ?",
                (key, column_name),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return {
            "description": row["description"] or "",
            "enum_values": json.loads(row["enum_values"] or "{}"),
            "foreign_key": row["foreign_key"] or "",
            "example_values": json.loads(row["example_values"] or "[]"),
        }

    def save_column_knowledge(
        self,
        table_fqn: str,
        column_name: str,
        description: str | None = None,
        enum_values: dict | None = None,
        foreign_key: str | None = None,
        example_values: list | None = None,
    ) -> None:
        """Upsert column-level annotations. Only updates non-None fields."""
        key = table_fqn.lower().strip()
        with self._lock:
            cur = self._conn.execute(
                "SELECT description, enum_values, foreign_key, example_values "
                "FROM column_knowledge WHERE table_fqn = ? AND column_name = ?",
                (key, column_name),
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
                    (new_desc, new_enum, new_fk, new_ex, key, column_name),
                )
            else:
                self._conn.execute(
                    "INSERT INTO column_knowledge (table_fqn, column_name, description, enum_values, foreign_key, example_values) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        key,
                        column_name,
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

        Returns:
            {neighbors: [{from_table, from_column, to_table, to_column, ...}],
             paths: ["TableA -> TableB -> TableC", ...]}
        """
        key = table_fqn.lower().strip()
        visited: set[str] = {key}
        neighbors: list[dict] = []
        paths: list[str] = []

        # BFS queue: (current_table, current_depth, path_so_far)
        queue: deque[tuple[str, int, list[str]]] = deque()
        queue.append((key, 0, [key]))

        with self._lock:
            while queue:
                current, current_depth, current_path = queue.popleft()
                if current_depth >= depth:
                    continue

                # Forward edges (from_table = current)
                cur = self._conn.execute(
                    "SELECT from_table, from_column, to_table, to_column, "
                    "relationship_type, cardinality, source, notes "
                    "FROM table_relationships WHERE from_table = ?",
                    (current,),
                )
                for row in cur.fetchall():
                    neighbor = row["to_table"]
                    neighbors.append(dict(row))
                    new_path = current_path + [neighbor]
                    if len(new_path) > 1:
                        paths.append(" -> ".join(new_path))
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append((neighbor, current_depth + 1, new_path))

                # Reverse edges (to_table = current)
                cur = self._conn.execute(
                    "SELECT from_table, from_column, to_table, to_column, "
                    "relationship_type, cardinality, source, notes "
                    "FROM table_relationships WHERE to_table = ?",
                    (current,),
                )
                for row in cur.fetchall():
                    neighbor = row["from_table"]
                    neighbors.append(dict(row))
                    new_path = current_path + [neighbor]
                    if len(new_path) > 1:
                        paths.append(" -> ".join(new_path))
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append((neighbor, current_depth + 1, new_path))

        # Deduplicate paths
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
        with self._lock:
            for row in rows:
                self._conn.execute(
                    "INSERT OR REPLACE INTO table_relationships "
                    "(from_table, from_column, to_table, to_column, relationship_type, cardinality, source, notes) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        row.get("from_table", "").lower(),
                        row.get("from_column", ""),
                        row.get("to_table", "").lower(),
                        row.get("to_column", ""),
                        row.get("relationship_type", "fk"),
                        row.get("cardinality", "many_to_one"),
                        row.get("source", "discovered"),
                        row.get("notes", ""),
                    ),
                )
            self._conn.commit()

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

    def get_all_column_knowledge(self, table_fqn: str) -> list[dict[str, Any]]:
        """Return all column knowledge rows for a table."""
        key = table_fqn.lower().strip()
        with self._lock:
            cur = self._conn.execute(
                "SELECT column_name, description, enum_values, foreign_key, example_values "
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
