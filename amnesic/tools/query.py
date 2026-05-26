"""
db_query — execute a read-only SQL query against a named connection.
"""

from sqlalchemy import text

from amnesic.config import load_config, resolve_connection
from amnesic.drivers import get_engine
from amnesic.readonly import assert_readonly


def db_query(
    sql: str,
    connection: str | None = None,
    max_rows: int = 500,
) -> dict:
    """
    Execute a read-only SELECT query and return results as a list of dicts.

    Two layers of enforcement:
      1. Static analysis via assert_readonly() — rejects any write/DDL statement
         before a connection is opened.
      2. The query always runs inside an immediately-rolled-back transaction —
         even if a write somehow slips through static analysis, no data is mutated.

    Args:
        sql:        The SQL SELECT query to execute.
        connection: Connection name from connections.toml (e.g. "orders.prod").
                    Defaults to the first defined connection.
        max_rows:   Maximum number of rows to return (default 500).

    Returns:
        {rows, row_count, connection, database, truncated}
    """
    assert_readonly(sql)

    connections = load_config()
    conn_cfg = resolve_connection(connection, connections)
    engine = get_engine(conn_cfg)

    with engine.connect() as conn_db:
        trans = conn_db.begin()
        try:
            result = conn_db.execute(text(sql))
            rows = [dict(r._mapping) for r in result.fetchmany(max_rows)]
        finally:
            trans.rollback()

    return {
        "rows": rows,
        "row_count": len(rows),
        "connection": conn_cfg.name,
        "database": conn_cfg.database,
        "truncated": len(rows) == max_rows,
    }
