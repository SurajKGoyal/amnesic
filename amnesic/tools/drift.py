"""
db_detect_drift — compare saved annotations against the live schema.

Schemas evolve; annotations don't follow automatically. This tool surfaces the
gap (read-only — it changes nothing):
  - orphaned annotations: a table/column you annotated that no longer exists
  - undocumented tables: live tables with no annotation yet

The computation core (`_compute_drift`) is pure so it can be unit-tested
without a live database; the tool wrapper supplies the live-schema lookups.
"""

from typing import Callable

from amnesic.config import load_config, resolve_connection
from amnesic.store import KnowledgeStore, get_store
from amnesic.tools.schema import (
    _fetch_schema_from_db,
    fetch_all_table_fqns_from_db,
)

# Cap the undocumented-tables list so a large database doesn't flood the
# response; the true total is always reported in the summary.
_UNDOCUMENTED_LIST_CAP = 100


def _compute_drift(
    store: KnowledgeStore,
    live_tables: set[str],
    fetch_live_columns: Callable[[str], set[str]],
) -> dict:
    """Pure drift computation. No DB/config access — fully unit-testable.

    Args:
        store:              the KnowledgeStore to audit.
        live_tables:        canonical FQNs of every table currently in the DB.
        fetch_live_columns: fqn -> set of lowercased live column names for a
                            table known to exist.
    """
    tk_tables = set(store.get_all_table_knowledge_fqns())
    ck_tables = set(store.get_all_column_knowledge_tables())
    annotated_tables = tk_tables | ck_tables

    # Orphaned tables: annotated, but gone from the live schema.
    orphaned_tables = sorted(annotated_tables - live_tables)

    # Orphaned columns: column annotated on a table that DOES still exist, but
    # the column itself is gone. (If the whole table is gone it's already an
    # orphaned table — don't double-report.)
    orphaned_columns: list[dict] = []
    for table_fqn in sorted(ck_tables):
        if table_fqn not in live_tables:
            continue
        live_cols = fetch_live_columns(table_fqn)
        for ck in store.get_all_column_knowledge(table_fqn):
            if ck["column_name"].lower() not in live_cols:
                orphaned_columns.append(
                    {"table": table_fqn, "column": ck["column_name"]}
                )

    # Undocumented: live table with no annotation at all.
    undocumented_all = sorted(live_tables - annotated_tables)
    undocumented_truncated = len(undocumented_all) > _UNDOCUMENTED_LIST_CAP

    return {
        "orphaned_tables": orphaned_tables,
        "orphaned_columns": orphaned_columns,
        "undocumented_tables": undocumented_all[:_UNDOCUMENTED_LIST_CAP],
        "undocumented_truncated": undocumented_truncated,
        "summary": {
            "orphaned_tables": len(orphaned_tables),
            "orphaned_columns": len(orphaned_columns),
            "undocumented_tables": len(undocumented_all),
            "live_tables": len(live_tables),
        },
    }


def db_detect_drift(connection: str | None = None) -> dict:
    """
    Audit saved annotations against the live database schema (read-only).

    Surfaces two kinds of drift:
      - orphaned annotations — a table or column you annotated that no longer
        exists in the database (dropped or renamed). Remove these with db_forget,
        or db_deprecate if the change is pending.
      - undocumented tables — live tables with no annotation yet (coverage gaps).

    Changes nothing in the database or the knowledge store — purely a report.

    Args:
        connection: Connection name. Defaults to first defined.

    Returns:
        {connection, orphaned_tables, orphaned_columns, undocumented_tables,
         undocumented_truncated, summary}
    """
    connections = load_config()
    conn_cfg = resolve_connection(connection, connections)
    store = get_store(conn_cfg.name)

    live_tables = fetch_all_table_fqns_from_db(conn_cfg)

    def fetch_live_columns(table_fqn: str) -> set[str]:
        return {
            c["column_name"].lower()
            for c in _fetch_schema_from_db(table_fqn, conn_cfg)
        }

    return {
        "connection": conn_cfg.name,
        **_compute_drift(store, live_tables, fetch_live_columns),
    }
