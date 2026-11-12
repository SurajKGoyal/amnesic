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


def test_mcp_dependency_stays_below_2() -> None:
    """mcp 2.0 removed mcp.server.fastmcp. Without the cap a fresh install
    resolves mcp 2.x and the server dies on launch, even with a green suite."""
    import tomllib
    from pathlib import Path

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    deps = tomllib.loads(pyproject.read_text())["project"]["dependencies"]
    mcp_dep = next(d for d in deps if d.replace(" ", "").startswith("mcp"))
    assert "<2" in mcp_dep.replace(" ", ""), f"mcp cap missing: {mcp_dep!r}"


class TestFirstRun:
    """A brand-new user starts the server before running `amnesic init`."""

    def test_list_connections_without_config_returns_empty_with_hint(self, tmp_path, monkeypatch):
        from amnesic.config import invalidate_config_cache
        from amnesic.tools.connections import db_list_connections

        monkeypatch.setenv("AMNESIC_CONFIG", str(tmp_path / "missing.toml"))
        monkeypatch.setenv("AMNESIC_HOME", str(tmp_path))
        invalidate_config_cache()
        result = db_list_connections()
        assert result["connections"] == []
        assert "amnesic init" in result["hint"]

    def test_broken_config_still_raises(self, tmp_path, monkeypatch):
        import pytest
        from amnesic.config import ConfigError, invalidate_config_cache
        from amnesic.tools.connections import db_list_connections

        bad = tmp_path / "connections.toml"
        bad.write_text('[connections.x]\ndriver = "sqlite"\nschema_cache_ttl_days = true\n')
        monkeypatch.setenv("AMNESIC_CONFIG", str(bad))
        monkeypatch.setenv("AMNESIC_HOME", str(tmp_path))
        invalidate_config_cache()
        with pytest.raises(ConfigError, match="schema_cache_ttl_days"):
            db_list_connections()
