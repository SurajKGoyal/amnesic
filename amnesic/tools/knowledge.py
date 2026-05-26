"""
db_annotate, db_sync_knowledge — knowledge layer management.

db_annotate persists semantic annotations for tables and columns.
db_sync_knowledge copies annotations from one connection's store to another,
skipping tables/columns that don't exist in the target schema cache.
"""

from amnesic.config import load_config, resolve_connection
from amnesic.store import get_store


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

    Call after discovering what a status code means, what a table is for, or
    how columns relate. These annotations are merged into every future
    db_get_schema() call so the AI never has to rediscover them.

    To annotate a table: provide table_description and/or table_aliases.
    To annotate a column: provide column plus any of column_description,
      enum_values, foreign_key, or example_values.
    Both can be done in a single call.

    Args:
        table:              Fully qualified table name (e.g. "dbo.Orders").
        connection:         Connection name. Defaults to first defined.
        table_description:  Human-readable description of what the table is for.
        table_aliases:      List of alternative names this table is known by.
        column:             Column name to annotate (required for column-level fields).
        column_description: What this column represents.
        enum_values:        Dict mapping stored values to human labels
                            e.g. {"1": "active", "2": "inactive"}.
        foreign_key:        FK reference as "other_table.column_name".
        example_values:     Representative sample values for this column.

    Returns:
        {table, connection, updated: {table_knowledge?, column_knowledge?}}
    """
    connections = load_config()
    conn_cfg = resolve_connection(connection, connections)
    store = get_store(conn_cfg.name)

    # Normalise FQN for the store key
    fqn = table.lower().strip()

    updated: dict = {}

    # Table-level annotations
    if table_description is not None or table_aliases is not None:
        store.save_table_knowledge(
            fqn,
            description=table_description,
            aliases=table_aliases,
        )
        updated["table_knowledge"] = store.get_table_knowledge(fqn)

    # Column-level annotations
    if column is not None and any(
        v is not None
        for v in (column_description, enum_values, foreign_key, example_values)
    ):
        store.save_column_knowledge(
            fqn,
            column,
            description=column_description,
            enum_values=enum_values,
            foreign_key=foreign_key,
            example_values=example_values,
        )
        updated["column_knowledge"] = store.get_column_knowledge(fqn, column)

    return {
        "table": fqn,
        "connection": conn_cfg.name,
        "updated": updated,
    }


def db_sync_knowledge(
    from_connection: str,
    to_connection: str,
    tables: list[str] | None = None,
) -> dict:
    """
    Copy annotations from one connection's store to another (e.g. staging → prod).

    Only syncs tables that exist in the target schema cache — skips missing ones.
    Only syncs columns that exist in the target schema cache for that table.
    Syncs relationships only where both endpoints exist in the target schema cache.

    Typical use: after promoting a staging DB to prod, sync all the semantic
    knowledge you've built up so the prod AI context is immediately enriched.

    Args:
        from_connection: Source connection name (e.g. "orders.staging").
        to_connection:   Target connection name (e.g. "orders.prod").
        tables:          Optional list of table FQNs to sync. Defaults to all tables
                         in the source store.

    Returns:
        {synced: [...], skipped: [{table, reason}], warnings: [{table, column, reason}]}
    """
    connections = load_config()
    from_cfg = resolve_connection(from_connection, connections)
    to_cfg = resolve_connection(to_connection, connections)

    from_store = get_store(from_cfg.name)
    to_store = get_store(to_cfg.name)

    # Determine source tables to sync
    source_tables = from_store.list_tables()
    if tables is not None:
        tables_lower = {t.lower().strip() for t in tables}
        source_tables = [t for t in source_tables if t["table_fqn"] in tables_lower]

    target_schema_tables = to_store.get_all_table_fqns()

    synced: list[str] = []
    skipped: list[dict] = []
    warnings: list[dict] = []

    for table_entry in source_tables:
        fqn = table_entry["table_fqn"]

        # Step 1: check if table exists in target schema cache
        if fqn not in target_schema_tables:
            skipped.append({
                "table": fqn,
                "reason": (
                    f"table not in target schema cache — run "
                    f"db_get_schema('{fqn}', connection='{to_connection}') first"
                ),
            })
            continue

        # Step 2: sync table-level knowledge
        src_table_knowledge = from_store.get_table_knowledge(fqn)
        if src_table_knowledge:
            to_store.save_table_knowledge(
                fqn,
                description=src_table_knowledge.get("description") or None,
                aliases=src_table_knowledge.get("aliases") or None,
            )

        # Step 3: sync column knowledge (warn on missing columns)
        target_columns = to_store.get_all_column_names(fqn)
        src_columns = from_store.get_all_column_knowledge(fqn)

        for col_entry in src_columns:
            col_name = col_entry["column_name"]
            if col_name not in target_columns:
                warnings.append({
                    "table": fqn,
                    "column": col_name,
                    "reason": (
                        f"column '{col_name}' not found in target schema cache for table '{fqn}'"
                    ),
                })
                continue

            to_store.save_column_knowledge(
                fqn,
                col_name,
                description=col_entry.get("description") or None,
                enum_values=col_entry.get("enum_values") or None,
                foreign_key=col_entry.get("foreign_key") or None,
                example_values=col_entry.get("example_values") or None,
            )

        synced.append(fqn)

    # Step 4: sync relationships where both endpoints exist in target
    src_relationships = from_store.get_all_relationships()
    for rel in src_relationships:
        from_t = rel["from_table"]
        to_t = rel["to_table"]
        if from_t in target_schema_tables and to_t in target_schema_tables:
            to_store.save_relationship(
                from_t,
                rel["from_column"],
                to_t,
                rel["to_column"],
                relationship_type=rel.get("relationship_type", "fk"),
                cardinality=rel.get("cardinality", "many_to_one"),
                source=rel.get("source", "manual"),
                notes=rel.get("notes", ""),
            )

    return {
        "from_connection": from_connection,
        "to_connection": to_connection,
        "synced": synced,
        "skipped": skipped,
        "warnings": warnings,
    }
