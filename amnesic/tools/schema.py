"""
db_get_schema, db_list_tables — schema introspection with knowledge merge.

Supports MSSQL, PostgreSQL, MySQL, SQLite.
FQN normalization is driver-specific.
Schema results are cached in the KnowledgeStore and enriched with annotations.
"""

from sqlalchemy import text

from amnesic.config import ConnectionConfig, load_config, resolve_connection
from amnesic.drivers import get_engine
from amnesic.readonly import validate_identifier
from amnesic.store import get_store


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

    with engine.connect() as conn_db:
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
        {table, connection, columns (with annotations), table_description, cached}
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

    return {
        "table": fqn,
        "connection": conn_cfg.name,
        "columns": merged["columns"],
        "table_description": merged["table_description"],
        "table_deprecated": merged["table_deprecated"],
        "table_deprecated_reason": merged["table_deprecated_reason"],
        "cached": cached,
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
