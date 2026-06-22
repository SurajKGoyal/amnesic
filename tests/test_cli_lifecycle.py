"""
Tests for the v0.2.2 lifecycle CLI: export / import / remove / clear,
plus init writing .env.example.

Everything is isolated to a tmp dir via AMNESIC_HOME (+ patched cli module
constants, which are captured at import time). No real database is touched —
the knowledge store is a separate SQLite file from the connection target.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

import amnesic.cli as cli_mod
import amnesic.store as store_mod
from amnesic.cli import _remove_connection_block, cli
from amnesic.config import invalidate_config_cache


_TWO_CONN_TOML = """\
# amnesic connections — keep this header comment
[connections.demo]
driver = "sqlite"
database = "/tmp/demo.db"

[connections.other]
driver = "postgres"
server = "localhost"
port = 5432
database = "otherdb"
user = "bob"
password = "${OTHER_PASSWORD}"
"""


def _reset_caches() -> None:
    for s in list(store_mod._store_cache.values()):
        try:
            s.close()
        except Exception:
            pass
    store_mod._store_cache.clear()
    invalidate_config_cache()


@pytest.fixture()
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("AMNESIC_HOME", str(tmp_path))
    monkeypatch.setenv("AMNESIC_NO_UPDATE_CHECK", "1")
    # cli.py captured these as module constants at import time.
    monkeypatch.setattr(cli_mod, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cli_mod, "_CONFIG_FILE", tmp_path / "connections.toml")
    monkeypatch.setattr(cli_mod, "_ENV_FILE", tmp_path / ".env")
    _reset_caches()
    yield tmp_path
    _reset_caches()


def _seed_knowledge(conn: str = "demo"):
    """Populate a store with a table, two columns (one deprecated), a relationship."""
    store = store_mod.get_store(conn)
    store.save_table_knowledge("dbo.orders", description="customer orders", aliases=["sales"])
    store.save_column_knowledge(
        "dbo.orders", "status",
        description="order state",
        enum_values={"1": "active", "3": "cancelled"},
    )
    store.save_column_knowledge("dbo.orders", "legacy_flag", description="old flag")
    store.set_column_deprecated("dbo.orders", "legacy_flag", True, reason="dropped in v5")
    store.save_relationship("dbo.orders", "customer_id", "dbo.customers", "id")
    return store


# ---------------------------------------------------------------------------
# _remove_connection_block — pure string surgery
# ---------------------------------------------------------------------------

class TestRemoveConnectionBlock:
    def test_removes_target_preserves_sibling_bytes(self):
        new, found = _remove_connection_block(_TWO_CONN_TOML, "demo")
        assert found is True
        assert "[connections.demo]" not in new
        # The 'other' block must survive byte-for-byte.
        other_block = (
            '[connections.other]\n'
            'driver = "postgres"\n'
            'server = "localhost"\n'
            'port = 5432\n'
            'database = "otherdb"\n'
            'user = "bob"\n'
            'password = "${OTHER_PASSWORD}"\n'
        )
        assert other_block in new
        # Header comment is preserved.
        assert "keep this header comment" in new

    def test_remove_last_block(self):
        new, found = _remove_connection_block(_TWO_CONN_TOML, "other")
        assert found is True
        assert "[connections.other]" not in new
        assert "[connections.demo]" in new

    def test_not_found_returns_original(self):
        new, found = _remove_connection_block(_TWO_CONN_TOML, "ghost")
        assert found is False
        assert new == _TWO_CONN_TOML

    def test_nested_connection_names(self):
        toml = (
            "[connections.orders.prod]\n"
            'driver = "mssql"\n'
            'database = "Prod"\n'
            "\n"
            "[connections.orders.staging]\n"
            'driver = "mssql"\n'
            'database = "Staging"\n'
        )
        new, found = _remove_connection_block(toml, "orders.prod")
        assert found is True
        assert "[connections.orders.prod]" not in new
        assert "[connections.orders.staging]" in new
        assert 'database = "Staging"' in new


# ---------------------------------------------------------------------------
# export / import round-trip
# ---------------------------------------------------------------------------

class TestExportImport:
    def test_roundtrip_via_cli(self, cli_env: Path):
        (cli_env / "connections.toml").write_text(_TWO_CONN_TOML)
        _seed_knowledge("demo")
        out = cli_env / "dump.json"

        runner = CliRunner()
        r = runner.invoke(cli, ["export", "demo", "-o", str(out)])
        assert r.exit_code == 0, r.output
        assert out.exists()
        payload = json.loads(out.read_text())
        assert payload["format_version"] == 1
        assert payload["source"] == "demo"
        assert len(payload["knowledge"]["tables"]) == 1
        assert len(payload["knowledge"]["columns"]) == 2
        assert len(payload["knowledge"]["relationships"]) == 1

        # Wipe, then import the dump back.
        r = runner.invoke(cli, ["clear", "demo", "--yes"])
        assert r.exit_code == 0, r.output
        assert store_mod.get_store("demo").export_knowledge()["tables"] == []

        r = runner.invoke(cli, ["import", "demo", str(out)])
        assert r.exit_code == 0, r.output

        store = store_mod.get_store("demo")
        tk = store.get_table_knowledge("dbo.orders")
        assert tk is not None and tk["description"] == "customer orders"
        ck = store.get_column_knowledge("dbo.orders", "status")
        assert ck["enum_values"] == {"1": "active", "3": "cancelled"}
        # Deprecation flag survives the round-trip.
        dep = store.get_column_knowledge("dbo.orders", "legacy_flag")
        assert dep["deprecated"] is True
        assert dep["deprecated_reason"] == "dropped in v5"
        rels = store.get_all_relationships()
        assert len(rels) == 1 and rels[0]["to_table"] == "dbo.customers"

    def test_export_to_stdout_is_valid_json(self, cli_env: Path):
        (cli_env / "connections.toml").write_text(_TWO_CONN_TOML)
        _seed_knowledge("demo")
        r = CliRunner().invoke(cli, ["export", "demo"])
        assert r.exit_code == 0, r.output
        payload = json.loads(r.output)  # whole stdout parses as JSON
        assert payload["format_version"] == 1

    def test_import_rejects_unknown_format_version(self, cli_env: Path):
        (cli_env / "connections.toml").write_text(_TWO_CONN_TOML)
        bad = cli_env / "bad.json"
        bad.write_text(json.dumps({"format_version": 999, "knowledge": {}}))
        r = CliRunner().invoke(cli, ["import", "demo", str(bad)])
        assert r.exit_code == 1
        assert "format_version" in r.output

    def test_export_unknown_connection_exits(self, cli_env: Path):
        (cli_env / "connections.toml").write_text(_TWO_CONN_TOML)
        r = CliRunner().invoke(cli, ["export", "nope"])
        assert r.exit_code == 1
        assert "not found" in r.output


# ---------------------------------------------------------------------------
# remove / clear
# ---------------------------------------------------------------------------

class TestRemoveClear:
    def test_remove_keeps_knowledge_by_default(self, cli_env: Path):
        (cli_env / "connections.toml").write_text(_TWO_CONN_TOML)
        store = _seed_knowledge("demo")
        kpath = Path(store_mod._amnesic_knowledge_path("demo"))
        assert kpath.exists()

        r = CliRunner().invoke(cli, ["remove", "demo", "--yes"])
        assert r.exit_code == 0, r.output

        cfg = (cli_env / "connections.toml").read_text()
        assert "[connections.demo]" not in cfg
        assert "[connections.other]" in cfg
        # Knowledge file untouched.
        assert kpath.exists()

    def test_remove_with_delete_knowledge(self, cli_env: Path):
        (cli_env / "connections.toml").write_text(_TWO_CONN_TOML)
        _seed_knowledge("demo")
        kpath = Path(store_mod._amnesic_knowledge_path("demo"))
        assert kpath.exists()

        r = CliRunner().invoke(cli, ["remove", "demo", "--yes", "--delete-knowledge"])
        assert r.exit_code == 0, r.output
        assert not kpath.exists()

    def test_clear_keeps_config_entry(self, cli_env: Path):
        (cli_env / "connections.toml").write_text(_TWO_CONN_TOML)
        _seed_knowledge("demo")

        r = CliRunner().invoke(cli, ["clear", "demo", "--yes"])
        assert r.exit_code == 0, r.output

        cfg = (cli_env / "connections.toml").read_text()
        assert "[connections.demo]" in cfg  # config entry stays
        assert store_mod.get_store("demo").export_knowledge()["tables"] == []


# ---------------------------------------------------------------------------
# init writes .env.example
# ---------------------------------------------------------------------------

class TestInitEnvExample:
    def test_template_init_writes_env_example(self, cli_env: Path):
        r = CliRunner().invoke(cli, ["init", "--template"])
        assert r.exit_code == 0, r.output
        example = cli_env / ".env.example"
        assert example.exists()
        body = example.read_text()
        assert "_PASSWORD=" in body  # at least one KEY=VALUE example
        assert (cli_env / "connections.toml").exists()
