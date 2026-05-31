"""
db_discover_relationships, db_get_relationships — FK graph tools.

db_discover_relationships runs driver-specific FK discovery SQL and populates
the relationship graph in the KnowledgeStore.

db_get_relationships does a BFS over the stored graph to find neighbors and
join paths up to a given depth.
"""

from sqlalchemy import text

from amnesic.config import ConnectionConfig, load_config, resolve_connection
from amnesic.drivers import get_engine
from amnesic.readonly import validate_identifier
from amnesic.store import get_store
from amnesic.tools.schema import normalize_fqn


# ---------------------------------------------------------------------------
# Per-driver FK discovery SQL
# ---------------------------------------------------------------------------

_MSSQL_FK_SQL = text("""
SELECT
    tp.name AS from_table,
    cp.name AS from_column,
    tr.name AS to_table,
    cr.name AS to_column
FROM sys.foreign_keys fk
JOIN sys.tables tp ON fk.parent_object_id = tp.object_id
JOIN sys.tables tr ON fk.referenced_object_id = tr.object_id
JOIN sys.foreign_key_columns fkc ON fk.object_id = fkc.constraint_object_id
JOIN sys.columns cp ON fkc.parent_column_id = cp.column_id AND cp.object_id = tp.object_id
JOIN sys.columns cr ON fkc.referenced_column_id = cr.column_id AND cr.object_id = tr.object_id
""")

_POSTGRES_FK_SQL = text("""
SELECT
    kcu.table_name  AS from_table,
    kcu.column_name AS from_column,
    ccu.table_name  AS to_table,
    ccu.column_name AS to_column
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
     ON tc.constraint_name = kcu.constraint_name
     AND tc.table_schema   = kcu.table_schema
JOIN information_schema.constraint_column_usage ccu
     ON tc.constraint_name = ccu.constraint_name
     AND tc.table_schema   = ccu.table_schema
WHERE tc.constraint_type = 'FOREIGN KEY'
  AND tc.table_schema    = :schema
""")

_MYSQL_FK_SQL = text("""
SELECT
    TABLE_NAME           AS from_table,
    COLUMN_NAME          AS from_column,
    REFERENCED_TABLE_NAME AS to_table,
    REFERENCED_COLUMN_NAME AS to_column
FROM information_schema.KEY_COLUMN_USAGE
WHERE TABLE_SCHEMA = :database
  AND REFERENCED_TABLE_NAME IS NOT NULL
""")


def _discover_from_db(conn_cfg: ConnectionConfig) -> list[dict]:
    """Run driver-specific FK discovery and return raw relationship rows."""
    engine = get_engine(conn_cfg)
    driver = conn_cfg.driver.lower()

    with engine.connect() as conn_db:
        if driver == "mssql":
            result = conn_db.execute(_MSSQL_FK_SQL)
            rows = result.fetchall()
            return [
                {"from_table": r[0], "from_column": r[1], "to_table": r[2], "to_column": r[3]}
                for r in rows
            ]

        if driver in ("postgres", "postgresql"):
            schema = conn_cfg.default_schema or "public"
            result = conn_db.execute(_POSTGRES_FK_SQL, {"schema": schema})
            rows = result.fetchall()
            return [
                {"from_table": r[0], "from_column": r[1], "to_table": r[2], "to_column": r[3]}
                for r in rows
            ]

        if driver == "mysql":
            result = conn_db.execute(_MYSQL_FK_SQL, {"database": conn_cfg.database})
            rows = result.fetchall()
            return [
                {"from_table": r[0], "from_column": r[1], "to_table": r[2], "to_column": r[3]}
                for r in rows
            ]

        if driver == "sqlite":
            return _discover_sqlite(conn_db)

        raise ValueError(f"Unsupported driver for FK discovery: {conn_cfg.driver}")


def _discover_sqlite(conn_db) -> list[dict]:  # type: ignore[no-untyped-def]
    """SQLite FK discovery: loop over tables, call PRAGMA foreign_key_list for each."""
    # Get all tables from sqlite_master
    result = conn_db.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    )
    table_names = [row[0] for row in result.fetchall()]

    relationships = []
    for table_name in table_names:
        # validate before interpolation
        try:
            validate_identifier(table_name, "table")
        except ValueError:
            continue  # skip tables with unusual names (temp tables, etc.)

        result = conn_db.execute(text(f"PRAGMA foreign_key_list({table_name})"))
        rows = result.fetchall()
        for row in rows:
            # PRAGMA foreign_key_list returns: id, seq, table, from, to, ...
            to_table = row[2]
            from_column = row[3]
            to_column = row[4]
            relationships.append({
                "from_table": table_name,
                "from_column": from_column,
                "to_table": to_table,
                "to_column": to_column,
            })

    return relationships


# ---------------------------------------------------------------------------
# Public tool functions
# ---------------------------------------------------------------------------

def db_discover_relationships(connection: str | None = None) -> dict:
    """
    Discover all foreign key relationships in the database and save them to the graph.

    Runs driver-specific FK introspection (sys.foreign_keys for MSSQL,
    INFORMATION_SCHEMA for PostgreSQL/MySQL, PRAGMA foreign_key_list for SQLite).
    Results are saved to the KnowledgeStore for use by db_get_relationships.

    Run once per database to populate the graph; re-run after schema changes.

    Args:
        connection: Connection name from connections.toml. Defaults to first.

    Returns:
        {connection, discovered: count, relationships: [{from_table, from_column, to_table, to_column}]}
    """
    connections = load_config()
    conn_cfg = resolve_connection(connection, connections)
    store = get_store(conn_cfg.name)

    raw_relationships = _discover_from_db(conn_cfg)

    # Normalize from_table and to_table to canonical FQNs before storing.
    # SQL queries return bare names (e.g. sys.tables.name = "currentjobs");
    # we need the same canonical key that db_get_schema uses.
    normalized = []
    for r in raw_relationships:
        from_fqn, *_ = normalize_fqn(r["from_table"], conn_cfg)
        to_fqn, *_ = normalize_fqn(r["to_table"], conn_cfg)
        normalized.append({**r, "from_table": from_fqn, "to_table": to_fqn})

    # Enrich with source tag
    relationships = [
        {**r, "source": "discovered", "relationship_type": "fk", "cardinality": "many_to_one"}
        for r in normalized
    ]

    store.discover_relationships_bulk(relationships)

    return {
        "connection": conn_cfg.name,
        "discovered": len(relationships),
        "relationships": raw_relationships,
    }


def db_get_relationships(
    table: str,
    connection: str | None = None,
    depth: int = 1,
) -> dict:
    """
    Get the FK relationship graph for a table, up to the given traversal depth.

    Depth 1 returns direct neighbors (tables one JOIN away).
    Depth 2 returns neighbors and their neighbors (two JOINs away).
    Returns both a flat neighbor list and formatted join path strings.

    Run db_discover_relationships first to populate the graph from the database.

    Args:
        table:      Table name, optionally schema-qualified to match your DB —
                    e.g. "users", "public.users" (Postgres), "dbo.Orders"
                    (MSSQL), "mydb.orders" (MySQL).
        connection: Connection name from connections.toml. Defaults to first.
        depth:      BFS depth (default 1, max recommended 3).

    Returns:
        {table, connection, neighbors: [...], paths: ["TableA -> TableB -> TableC", ...]}
    """
    connections = load_config()
    conn_cfg = resolve_connection(connection, connections)
    store = get_store(conn_cfg.name)

    fqn, *_ = normalize_fqn(table, conn_cfg)
    result = store.get_relationships(fqn, depth=depth)

    return {
        "table": fqn,
        "connection": conn_cfg.name,
        "neighbors": result["neighbors"],
        "paths": result["paths"],
    }
