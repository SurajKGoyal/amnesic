"""
amnesic MCP server — FastMCP entry point.

All 8 tools are registered here with full docstrings.
First line of every docstring is self-contained — non-Claude clients show only that line.

Start with:
  python -m amnesic.server
  uvx amnesic
"""

from mcp.server.fastmcp import FastMCP

from amnesic.tools.connections import db_list_connections as _db_list_connections
from amnesic.tools.knowledge import db_annotate as _db_annotate
from amnesic.tools.knowledge import db_sync_knowledge as _db_sync_knowledge
from amnesic.tools.query import db_query as _db_query
from amnesic.tools.relationships import db_discover_relationships as _db_discover_relationships
from amnesic.tools.relationships import db_get_relationships as _db_get_relationships
from amnesic.tools.schema import db_get_schema as _db_get_schema
from amnesic.tools.schema import db_list_tables as _db_list_tables

mcp = FastMCP(
    "amnesic",
    instructions="""
amnesic gives you persistent, annotated access to SQL databases. The name is ironic —
this server is anything but amnesic. It remembers your schema, your annotations,
and your relationships across every session.

Recommended workflow for any DB question:
1. db_list_connections() — see what databases are available
2. db_list_tables(connection) — see all tables with descriptions
3. db_get_schema(table, connection) — get columns + saved annotations BEFORE writing SQL
4. db_get_relationships(table, connection) — understand JOINs before writing complex queries
5. db_query(sql, connection) — execute read-only SELECT
6. db_annotate(...) — persist new understanding (enum meanings, FKs, table purpose)

Always call db_get_schema before querying an unfamiliar table.
Always call db_annotate after discovering what an enum value or status code means.
Once per database, run db_discover_relationships to populate the FK graph from the DB itself.
""",
)


@mcp.tool()
def db_query(sql: str, connection: str | None = None, max_rows: int = 500) -> dict:
    """
    Execute a read-only SELECT query and return rows as a list of dicts.

    All queries run inside an immediately-rolled-back transaction — write
    statements are blocked both statically and at the transaction level.
    Call db_get_schema first if you are unfamiliar with the table structure.

    Args:
        sql:        SELECT query to execute. No INSERT/UPDATE/DELETE allowed.
        connection: Connection name (e.g. "orders.prod"). Defaults to first defined.
        max_rows:   Maximum rows to return (default 500). Set lower for large tables.

    Returns:
        {rows, row_count, connection, database, truncated}
    """
    return _db_query(sql=sql, connection=connection, max_rows=max_rows)


@mcp.tool()
def db_get_schema(
    table: str,
    connection: str | None = None,
    force_refresh: bool = False,
) -> dict:
    """
    Get column schema for a table, merged with any saved semantic annotations.

    Checks the local cache first; fetches from the database on cache miss or
    when force_refresh=True. Saves the result to cache for future calls.
    Merges column descriptions, enum value mappings, and FK references from
    previous db_annotate() calls into the response.

    Args:
        table:         Table name, optionally qualified (e.g. "dbo.Orders", "Orders").
        connection:    Connection name. Defaults to first defined.
        force_refresh: Bypass cache and fetch fresh schema from the database.

    Returns:
        {table, connection, columns (with annotations merged in), table_description, cached}
    """
    return _db_get_schema(table=table, connection=connection, force_refresh=force_refresh)


@mcp.tool()
def db_list_tables(connection: str | None = None) -> dict:
    """
    List all known tables for a connection, with descriptions and column counts.

    Tables appear once they have been fetched via db_get_schema or annotated via
    db_annotate. Descriptions come from the knowledge store — richer than raw
    INFORMATION_SCHEMA.

    Args:
        connection: Connection name. Defaults to first defined.

    Returns:
        {connection, database, tables: [{table_fqn, description, aliases, column_count}]}
    """
    return _db_list_tables(connection=connection)


@mcp.tool()
def db_annotate(
    table: str,
    connection: str | None = None,
    table_description: str | None = None,
    table_aliases: list[str] | None = None,
    column: str | None = None,
    column_description: str | None = None,
    enum_values: dict | None = None,
    foreign_key: str | None = None,
    example_values: list | None = None,
) -> dict:
    """
    Persist semantic annotations for a table or column — survives across sessions.

    This is the core of amnesic's persistent memory. Every annotation saved here
    is automatically merged into future db_get_schema() responses, so the AI
    never has to rediscover what a status code means or what a table is for.

    Call this after discovering: what an enum value means, what a column represents,
    how a table relates to another, or what a table is used for.

    Args:
        table:              Table name (e.g. "dbo.Orders").
        connection:         Connection name. Defaults to first defined.
        table_description:  Human-readable description of the table's purpose.
        table_aliases:      Alternative names the table is known by.
        column:             Column to annotate (required for column-level args below).
        column_description: What this column represents in the business domain.
        enum_values:        Dict mapping stored values to labels {"1": "active", "2": "inactive"}.
        foreign_key:        FK reference as "other_table.column_name".
        example_values:     Representative sample values from this column.

    Returns:
        {table, connection, updated: {table_knowledge?, column_knowledge?}}
    """
    return _db_annotate(
        table=table,
        connection=connection,
        table_description=table_description,
        table_aliases=table_aliases,
        column=column,
        column_description=column_description,
        enum_values=enum_values,
        foreign_key=foreign_key,
        example_values=example_values,
    )


@mcp.tool()
def db_sync_knowledge(
    from_connection: str,
    to_connection: str,
    tables: list[str] | None = None,
) -> dict:
    """
    Copy annotations from one connection's knowledge store to another.

    Typical use: after confirming that staging and prod share the same schema,
    sync all the semantic knowledge you've built up in staging to prod.
    Only syncs tables and columns that exist in the target schema cache —
    tables missing from target are reported in 'skipped', columns in 'warnings'.

    Args:
        from_connection: Source connection (e.g. "orders.staging").
        to_connection:   Target connection (e.g. "orders.prod").
        tables:          Optional list of specific table FQNs to sync. Defaults to all.

    Returns:
        {synced: [...], skipped: [{table, reason}], warnings: [{table, column, reason}]}
    """
    return _db_sync_knowledge(
        from_connection=from_connection,
        to_connection=to_connection,
        tables=tables,
    )


@mcp.tool()
def db_list_connections() -> dict:
    """
    List all configured database connections without exposing passwords or usernames.

    Use this first to see what databases are available before calling other tools.
    Returns connection names, drivers, databases, and server addresses.

    Returns:
        {connections: [{name, driver, database, server}]}
    """
    return _db_list_connections()


@mcp.tool()
def db_discover_relationships(connection: str | None = None) -> dict:
    """
    Discover all foreign key relationships in the database and save them to the graph.

    Runs driver-specific FK introspection queries against the live database and
    persists results to the local KnowledgeStore. Run once per database; re-run
    after schema changes. After discovery, use db_get_relationships to navigate
    the graph when planning complex JOIN queries.

    Args:
        connection: Connection name. Defaults to first defined.

    Returns:
        {connection, discovered: count, relationships: [{from_table, from_column, to_table, to_column}]}
    """
    return _db_discover_relationships(connection=connection)


@mcp.tool()
def db_get_relationships(
    table: str,
    connection: str | None = None,
    depth: int = 1,
) -> dict:
    """
    Get the foreign key relationship graph for a table up to the given traversal depth.

    Depth 1 returns direct neighbors (tables one JOIN away). Depth 2 returns
    neighbors-of-neighbors. Returns both a flat neighbor list and formatted join
    path strings to help plan multi-table queries. Requires db_discover_relationships
    to have been run first.

    Args:
        table:      Table name (e.g. "Orders").
        connection: Connection name. Defaults to first defined.
        depth:      BFS traversal depth (default 1, recommended max 3).

    Returns:
        {table, connection, neighbors: [...], paths: ["TableA -> TableB -> TableC", ...]}
    """
    return _db_get_relationships(table=table, connection=connection, depth=depth)


def main() -> None:
    """Entry point for python -m amnesic.server."""
    mcp.run()


if __name__ == "__main__":
    main()
