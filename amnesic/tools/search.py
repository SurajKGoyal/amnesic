"""
db_search — BM25-ranked search over the knowledge layer.

Use this BEFORE db_list_tables when looking for a specific concept.
db_list_tables is for enumeration; db_search is for "find the right one".
"""

from amnesic.config import load_config, resolve_connection
from amnesic.store import get_store


def db_search(
    query: str,
    connection: str | None = None,
    target: str = "all",
    limit: int = 10,
) -> dict:
    """Run a BM25 search over the connection's knowledge store."""
    connections = load_config()
    conn_cfg = resolve_connection(connection, connections)
    store = get_store(conn_cfg.name)
    results = store.search(query=query, target=target, limit=limit)
    return {
        "query": query,
        "connection": conn_cfg.name,
        "target": target,
        "result_count": len(results),
        "results": results,
    }
