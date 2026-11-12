"""
db_query — execute a read-only SQL query against a named connection.
"""

from sqlalchemy import text

from amnesic.config import ConnectionConfig, load_config, resolve_connection
from amnesic.drivers import get_engine, safe_connect
from amnesic.readonly import assert_readonly


def run_readonly(
    conn_cfg: ConnectionConfig,
    sql: str,
    params: dict | None = None,
    max_rows: int | None = None,
) -> tuple[list[dict], bool]:
    """
    Execute a read-only SQL statement inside a rolled-back transaction.

    Shared by db_query and db_discover_enums so both benefits from the same
    two-layer write protection without duplicating connection/transaction handling.

    Args:
        conn_cfg: Resolved ConnectionConfig (caller must have resolved it).
        sql:      SQL string. Checked with assert_readonly here, so the helper is
                  safe by construction rather than by caller discipline.
        params:   Optional bind parameters for text() — None means no params.
        max_rows: If set, fetch at most this many rows and set truncated=True when
                  more exist (fetch+1 technique). None fetches all rows.

    Returns:
        (rows, truncated) — rows is a list of dicts; truncated is False when
        max_rows is None.
    """
    assert_readonly(sql)
    engine = get_engine(conn_cfg)

    with safe_connect(engine, conn_cfg) as conn_db:
        trans = conn_db.begin()
        try:
            stmt = text(sql)
            result = conn_db.execute(stmt, params) if params else conn_db.execute(stmt)

            if max_rows is not None:
                # Fetch one extra row to distinguish "exactly max_rows results"
                # from "more results exist". Without this, a result of exactly
                # max_rows rows would be incorrectly flagged as truncated.
                fetched = result.fetchmany(max_rows + 1)
                truncated = len(fetched) > max_rows
                rows = [dict(r._mapping) for r in fetched[:max_rows]]
            else:
                rows = [dict(r._mapping) for r in result.fetchall()]
                truncated = False
        finally:
            trans.rollback()

    return rows, truncated


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
    # run_readonly checks this too; checking first keeps db_query's old contract
    # of rejecting a write before any config is loaded or connection opened.
    assert_readonly(sql)

    connections = load_config()
    conn_cfg = resolve_connection(connection, connections)
    rows, truncated = run_readonly(conn_cfg, sql, max_rows=max_rows)

    return {
        "rows": rows,
        "row_count": len(rows),
        "connection": conn_cfg.name,
        "database": conn_cfg.database,
        "truncated": truncated,
    }
