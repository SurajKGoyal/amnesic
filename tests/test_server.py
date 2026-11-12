"""
The MCP server must import and register every tool.

No other test imports amnesic.server, which is how an incompatible mcp release
(2.0 removed mcp.server.fastmcp) shipped unnoticed: the whole suite passed while
`amnesic` crashed on launch for every fresh install.
"""

import asyncio

EXPECTED_TOOLS = {
    "db_query", "db_get_schema", "db_list_tables", "db_search", "db_annotate",
    "db_deprecate", "db_sync_knowledge", "db_list_connections", "db_forget",
    "db_detect_drift", "db_discover_relationships", "db_get_relationships",
    "db_discover_enums",
}


def test_server_imports_and_registers_every_tool() -> None:
    from amnesic.server import mcp

    tools = asyncio.run(mcp.list_tools())
    assert {t.name for t in tools} == EXPECTED_TOOLS


def test_every_tool_has_a_self_contained_first_docstring_line() -> None:
    """Non-Claude clients show only the first line of a tool description."""
    from amnesic.server import mcp

    for tool in asyncio.run(mcp.list_tools()):
        first = (tool.description or "").strip().splitlines()[0]
        assert len(first) > 20, f"{tool.name}: {first!r}"
