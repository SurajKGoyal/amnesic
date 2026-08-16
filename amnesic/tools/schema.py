"""
db_get_schema, db_list_tables — schema introspection with knowledge merge.

Supports MSSQL, PostgreSQL, MySQL, SQLite.
FQN normalization is driver-specific.
Schema results are cached in the KnowledgeStore and enriched with annotations.
Cached schemas report their age and a `stale` flag so the agent can decide
whether to force_refresh — the cache is never silently refreshed for you.
"""

import os
from datetime import datetime, timezone

from sqlalchemy import text

from amnesic.config import ConnectionConfig, load_config, resolve_connection
from amnesic.drivers import get_engine, safe_connect
from amnesic.readonly import validate_identifier
from amnesic.store import get_store

_DEFAULT_SCHEMA_TTL_DAYS = 30


def _resolve_schema_ttl_days(conn_cfg: ConnectionConfig) -> int:
    """Staleness threshold in days: per-connection override, then env, then 30.

    0 at either level disables staleness reporting entirely.
    """
    if conn_cfg.schema_cache_ttl_days is not None:
        return conn_cfg.schema_cache_ttl_days
    env_raw = os.environ.get("AMNESIC_SCHEMA_TTL_DAYS")
    if env_raw is not None and env_raw.strip() != "":
        try:
            return int(env_raw)
        except ValueError:
            # Unreadable global setting: fall through to the default rather
            # than refusing to serve schemas over a typo.
            pass
    return _DEFAULT_SCHEMA_TTL_DAYS


def _parse_cache_stamp(stamp: str) -> datetime | None:
    """Parse a cached_at stamp into an aware UTC datetime.

    Accepts both the current ISO-8601 form (2026-02-14T09:31:02Z) and the
    legacy SQLite datetime('now') form (2026-02-14 09:31:02). Returns None
    for anything unparseable — the caller reports unknown age instead of
    guessing.
    """
    raw = stamp.strip()
    if raw.endswith("Z"):
        raw = raw[:-1]
    raw = raw.replace(" ", "T", 1) if " " in raw and "T" not in raw else raw
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _cache_info(
    cached: bool,
    stamp: str | None,
    ttl_days: int,
) -> dict:
    """Build the cached_at / cache_age_days / stale / hint block for db_get_schema."""
    info: dict = {"cached": cached}
    if stamp is None:
        # Not cached, or rows predate the cached_at column: report unknown
        # age rather than pretending the cache is fresh.
        info["cached_at"] = None
        info["cache_age_days"] = None
        info["stale"] = None
        return info
    parsed = _parse_cache_stamp(stamp)
    if parsed is None:
        info["cached_at"] = stamp
        info["cache_age_days"] = None
        info["stale"] = None
        return info
    age_days = (datetime.now(timezone.utc) - parsed).days
    info["cached_at"] = stamp
    info["cache_age_days"] = age_days
    if ttl_days <= 0:
        # Staleness reporting disabled — age is still surfaced, the flag is not.
        info["stale"] = None
    else:
        info["stale"] = age_days >= ttl_days
        if info["stale"]:
            info["hint"] = (
                f"Schema cached {age_days} days ago. "
                "Pass force_refresh=true to re-fetch, or run db_detect_drift."
            )
    return info


# ---------------------------------------------------------------------------
# FQN normalization per driver
# ---------------------------------------------------------------------------

def normalize_fqn(table: str, conn: ConnectionConfig) -> tuple[str, str, str, str]:
    """
    Parse a table reference into (fqn, db, schema, table_name) tuples.

    MSSQL:      db.schema.table  (defaults: conn.database, dbo)
    PostgreSQL: schema.table     (default: public)
    MySQL:      database.table   (no schema concept)
    SQLite:     table            (just the name)

    Returns:
        (fqn, db, schema, table_name)
        where fqn is the canonical lowercase key used in the store.
    """
    driver = conn.driver.lower()
    parts = [p.strip() for p in table.split(".")]

    if driver == "sqlite":
        table_name = parts[-1]
        return table_name.lower(), "", "", table_name

    if driver in ("postgres", "postgresql"):
        if len(parts) == 2:
            schema, table_name = parts
        elif len(parts) == 1:
            schema, table_name = conn.default_schema or "public", parts[0]
        else:
            schema, table_name = parts[-2], parts[-1]
        fqn = f"{schema}.{table_name}".lower()
        return fqn, "", schema, table_name

    if driver == "mysql":
        if len(parts) == 2:
            db, table_name = parts
        else:
            db, table_name = conn.database, parts[0]
        fqn = f"{db}.{table_name}".lower()
        return fqn, db, "", table_name

    if driver == "mssql":
        if len(parts) == 3:
            db, schema, table_name = parts
        elif len(parts) == 2:
            db, schema, table_name = conn.database, parts[0], parts[1]
        else:
            db, schema, table_name = conn.database, conn.default_schema or "dbo", parts[0]
        fqn = f"{db}.{schema}.{table_name}".lower()
        return fqn, db, schema, table_name

    # Fallback: treat as table name only
    table_name = parts[-1]
    return table_name.lower(), "", "", table_name


# ---------------------------------------------------------------------------
# Per-driver schema fetch SQL
# ---------------------------------------------------------------------------

def _fetch_schema_from_db(
    table: str, conn_cfg: ConnectionConfig
) -> list[dict]:
    """Fetch raw column metadata from the database (no cache)."""
    engine = get_engine(conn_cfg)
    driver = conn_cfg.driver.lower()
    fqn, db, schema, table_name = normalize_fqn(table, conn_cfg)

    # Validate identifiers before any interpolation
    validate_identifier(table_name, "table")
    if db:
        validate_identifier(db, "database")
    if schema:
        validate_identifier(schema, "schema")

    with safe_connect(engine, conn_cfg) as conn_db:
        if driver == "sqlite":
            # PRAGMA doesn't support parameterized table names — identifier validated above
            result = conn_db.execute(text(f"PRAGMA table_info({table_name})"))
            rows = result.fetchall()
            columns = []
            for row in rows:
                columns.append({
                    "column_name": row[1],   # name
                    "data_type": row[2],     # type
                    "is_nullable": "YES" if not row[3] else "NO",  # notnull (inverted)
                    "max_length": None,
                })
            return columns

        if driver == "mssql":
            sql = text(
                f"SELECT COLUMN_NAME AS column_name, DATA_TYPE AS data_type, "
                f"IS_NULLABLE AS is_nullable, CHARACTER_MAXIMUM_LENGTH AS max_length "
                f"FROM [{db}].INFORMATION_SCHEMA.COLUMNS "
                f"WHERE TABLE_SCHEMA = :schema AND TABLE_NAME = :table "
                f"ORDER BY ORDINAL_POSITION"
            )
            result = conn_db.execute(sql, {"schema": schema, "table": table_name})

        elif driver in ("postgres", "postgresql"):
            sql = text(
                "SELECT COLUMN_NAME AS column_name, DATA_TYPE AS data_type, "
                "IS_NULLABLE AS is_nullable, CHARACTER_MAXIMUM_LENGTH AS max_length "
                "FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = :schema AND TABLE_NAME = :table "
                "ORDER BY ORDINAL_POSITION"
            )
            result = conn_db.execute(sql, {"schema": schema, "table": table_name})

        elif driver == "mysql":
            sql = text(
                "SELECT COLUMN_NAME AS column_name, DATA_TYPE AS data_type, "
                "IS_NULLABLE AS is_nullable, CHARACTER_MAXIMUM_LENGTH AS max_length "
                "FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = :schema AND TABLE_NAME = :table "
                "ORDER BY ORDINAL_POSITION"
            )
            result = conn_db.execute(sql, {"schema": db, "table": table_name})

        else:
            raise ValueError(f"Unsupported driver for schema fetch: {conn_cfg.driver}")

        rows = result.fetchall()
        return [
            {
                "column_name": row[0],
                "data_type": row[1],
                "is_nullable": row[2],
                "max_length": row[3],
            }
            for row in rows
        ]


def fetch_all_table_fqns_from_db(conn_cfg: ConnectionConfig) -> set[str]:
    """Return the canonical FQNs of every base table in the live database.

    FQNs match normalize_fqn's output exactly (lowercased, driver-specific
    shape) so they can be compared directly against the knowledge store's keys.
    Used by db_detect_drift. Read-only.
    """
    engine = get_engine(conn_cfg)
    driver = conn_cfg.driver.lower()
    db = conn_cfg.database

    with safe_connect(engine, conn_cfg) as conn_db:
        if driver == "sqlite":
            result = conn_db.execute(text(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ))
            return {row[0].lower() for row in result.fetchall()}

        if driver == "mssql":
            validate_identifier(db, "database")
            result = conn_db.execute(text(
                f"SELECT TABLE_SCHEMA, TABLE_NAME FROM [{db}].INFORMATION_SCHEMA.TABLES "
                f"WHERE TABLE_TYPE = 'BASE TABLE'"
            ))
            return {f"{db}.{r[0]}.{r[1]}".lower() for r in result.fetchall()}

        if driver in ("postgres", "postgresql"):
            result = conn_db.execute(text(
                "SELECT table_schema, table_name FROM information_schema.tables "
                "WHERE table_type = 'BASE TABLE' "
                "AND table_schema NOT IN ('pg_catalog', 'information_schema')"
            ))
            return {f"{r[0]}.{r[1]}".lower() for r in result.fetchall()}

        if driver == "mysql":
            result = conn_db.execute(
                text(
                    "SELECT TABLE_NAME FROM information_schema.tables "
                    "WHERE TABLE_SCHEMA = :db AND TABLE_TYPE = 'BASE TABLE'"
                ),
                {"db": db},
            )
            return {f"{db}.{r[0]}".lower() for r in result.fetchall()}

        raise ValueError(f"Unsupported driver for table listing: {conn_cfg.driver}")


def _merge_knowledge_into_schema(
    fqn: str,
    columns: list[dict],
    conn_name: str,
) -> dict:
    """Merge table + column knowledge annotations into schema results.

    Fetches all column knowledge for the table in a single SQLite query
    (previously N+1 — one query per column).
    """
    store = get_store(conn_name)
    table_knowledge = store.get_table_knowledge(fqn)
    table_description = table_knowledge.get("description", "") if table_knowledge else ""
    table_deprecated = bool(table_knowledge and table_knowledge.get("deprecated"))
    table_deprecated_reason = (
        table_knowledge.get("deprecated_reason", "") if table_knowledge else ""
    )

    # Single query for all column annotations — keyed by lowercase column_name
    # to match the v0.1.11 convention (column_name is stored lowercase).
    all_col_knowledge: dict[str, dict] = {
        ck["column_name"]: ck
        for ck in store.get_all_column_knowledge(fqn)
    }

    enriched = []
    for col in columns:
        col_name = col.get("column_name", "").lower().strip()
        col_knowledge = all_col_knowledge.get(col_name)
        enriched_col = dict(col)
        if col_knowledge:
            if col_knowledge.get("description"):
                enriched_col["description"] = col_knowledge["description"]
            if col_knowledge.get("enum_values"):
                enriched_col["enum_values"] = col_knowledge["enum_values"]
            if col_knowledge.get("foreign_key"):
                enriched_col["foreign_key"] = col_knowledge["foreign_key"]
            if col_knowledge.get("example_values"):
                enriched_col["example_values"] = col_knowledge["example_values"]
            # Surface deprecation so the AI is warned off a stale column.
            if col_knowledge.get("deprecated"):
                enriched_col["deprecated"] = True
                enriched_col["deprecated_reason"] = col_knowledge.get("deprecated_reason", "")
        enriched.append(enriched_col)

    return {
        "table_description": table_description,
        "table_deprecated": table_deprecated,
        "table_deprecated_reason": table_deprecated_reason,
        "columns": enriched,
    }


# ---------------------------------------------------------------------------
# Public tool functions
# ---------------------------------------------------------------------------

def db_get_schema(
    table: str,
    connection: str | None = None,
    force_refresh: bool = False,
) -> dict:
    """
    Get column schema for a table, enriched with saved annotations.

    Checks the schema cache first (SQLite store). On cache miss or force_refresh,
    fetches from the database and saves the result. Merges any saved table/column
    annotations (descriptions, enum values, foreign keys) into the response.

    Args:
        table:         Table name, optionally schema-qualified to match your DB —
                       e.g. "users", "public.users" (Postgres), "dbo.Orders"
                       (MSSQL), "mydb.orders" (MySQL). Format is driver-dependent.
        connection:    Connection name from connections.toml. Defaults to first.
        force_refresh: Skip the cache and fetch fresh from the database.

    Returns:
        {table, connection, columns (with annotations), table_description,
         cached, cached_at, cache_age_days, stale[, hint]}
        cached_at/cache_age_days/stale are null when the age is unknown
        (fresh fetch failures aside, mainly rows cached before the
        cached_at column existed). stale is true past the TTL threshold
        (per-connection schema_cache_ttl_days, then AMNESIC_SCHEMA_TTL_DAYS,
        default 30 days; 0 disables the flag).
    """
    connections = load_config()
    conn_cfg = resolve_connection(connection, connections)
    fqn, _, _, _ = normalize_fqn(table, conn_cfg)
    store = get_store(conn_cfg.name)

    cached_columns = None if force_refresh else store.get_cached_schema(fqn)
    cached = cached_columns is not None

    if not cached:
        columns = _fetch_schema_from_db(table, conn_cfg)
        store.save_cached_schema(fqn, columns)
    else:
        columns = cached_columns

    merged = _merge_knowledge_into_schema(fqn, columns, conn_cfg.name)

    cache = _cache_info(
        cached,
        store.get_schema_cache_timestamp(fqn),
        _resolve_schema_ttl_days(conn_cfg),
    )

    return {
        "table": fqn,
        "connection": conn_cfg.name,
        "columns": merged["columns"],
        "table_description": merged["table_description"],
        "table_deprecated": merged["table_deprecated"],
        "table_deprecated_reason": merged["table_deprecated_reason"],
        **cache,
    }


def db_list_tables(connection: str | None = None) -> dict:
    """
    List all known tables for a connection, with descriptions and column counts.

    Returns tables from the schema cache and table_knowledge store combined.
    A table appears if it has ever been fetched (db_get_schema) or annotated.

    Args:
        connection: Connection name from connections.toml. Defaults to first.

    Returns:
        {connection, database, tables: [{table_fqn, description, aliases, column_count}]}
    """
    connections = load_config()
    conn_cfg = resolve_connection(connection, connections)
    store = get_store(conn_cfg.name)
    tables = store.list_tables()

    return {
        "connection": conn_cfg.name,
        "database": conn_cfg.database,
        "tables": tables,
    }
