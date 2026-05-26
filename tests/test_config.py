"""
Tests for amnesic.config — TOML loading, ${VAR} expansion, hierarchy parsing.

No real DB required — uses tmp files and controlled environment.
"""

import os
import textwrap
from pathlib import Path

import pytest

from amnesic.config import (
    ConfigError,
    ConnectionConfig,
    get_default_connection_name,
    load_config,
    resolve_connection,
)


def write_toml(tmp_path: Path, content: str) -> Path:
    """Helper: write a TOML file and return its path."""
    p = tmp_path / "connections.toml"
    p.write_text(textwrap.dedent(content))
    return p


# ---------------------------------------------------------------------------
# Flat connections [connections.name]
# ---------------------------------------------------------------------------

class TestFlatConnections:
    def test_single_flat_connection(self, tmp_path):
        p = write_toml(tmp_path, """
            [connections.mydb]
            driver = "postgres"
            server = "localhost"
            port = 5432
            database = "mydb"
            user = "admin"
            password = "secret"
        """)
        connections = load_config(p)
        assert "mydb" in connections
        conn = connections["mydb"]
        assert conn.driver == "postgres"
        assert conn.server == "localhost"
        assert conn.port == 5432
        assert conn.database == "mydb"
        assert conn.user == "admin"
        assert conn.password == "secret"
        assert conn.name == "mydb"

    def test_multiple_flat_connections(self, tmp_path):
        p = write_toml(tmp_path, """
            [connections.pg]
            driver = "postgres"
            server = "pg.example.com"
            port = 5432
            database = "warehouse"
            user = "u"
            password = "p"

            [connections.local]
            driver = "sqlite"
            database = "/tmp/local.db"
        """)
        connections = load_config(p)
        assert set(connections.keys()) == {"pg", "local"}
        assert connections["pg"].driver == "postgres"
        assert connections["local"].driver == "sqlite"

    def test_sqlite_has_no_server(self, tmp_path):
        p = write_toml(tmp_path, """
            [connections.local]
            driver = "sqlite"
            database = "/tmp/local.db"
        """)
        connections = load_config(p)
        conn = connections["local"]
        assert conn.server == ""
        assert conn.port == 0
        assert conn.database == "/tmp/local.db"


# ---------------------------------------------------------------------------
# Nested connections [connections.product.env]
# ---------------------------------------------------------------------------

class TestNestedConnections:
    def test_two_level_nested(self, tmp_path):
        p = write_toml(tmp_path, """
            [connections.orders.prod]
            driver = "mssql"
            server = "localhost"
            port = 11433
            database = "OrdersDB"
            user = "sa"
            password = "pw"

            [connections.orders.staging]
            driver = "mssql"
            server = "localhost"
            port = 11434
            database = "OrdersDB_Staging"
            user = "sa"
            password = "pw"
        """)
        connections = load_config(p)
        assert "orders.prod" in connections
        assert "orders.staging" in connections
        assert connections["orders.prod"].database == "OrdersDB"
        assert connections["orders.staging"].database == "OrdersDB_Staging"
        assert connections["orders.prod"].name == "orders.prod"

    def test_canonical_dot_notation(self, tmp_path):
        p = write_toml(tmp_path, """
            [connections.slot_service.dev]
            driver = "mysql"
            server = "localhost"
            port = 3306
            database = "slot_service"
            user = "root"
            password = "root"
        """)
        connections = load_config(p)
        assert "slot_service.dev" in connections
        conn = connections["slot_service.dev"]
        assert conn.name == "slot_service.dev"


# ---------------------------------------------------------------------------
# Mixed flat + nested
# ---------------------------------------------------------------------------

class TestMixedConnections:
    def test_mixed_flat_and_nested(self, tmp_path):
        p = write_toml(tmp_path, """
            [connections.analytics]
            driver = "postgres"
            server = "analytics.company.com"
            port = 5432
            database = "warehouse"
            user = "reader"
            password = "pw"

            [connections.orders.prod]
            driver = "mssql"
            server = "localhost"
            port = 11433
            database = "OrdersDB"
            user = "sa"
            password = "pw"
        """)
        connections = load_config(p)
        assert set(connections.keys()) == {"analytics", "orders.prod"}


# ---------------------------------------------------------------------------
# ${VAR} expansion
# ---------------------------------------------------------------------------

class TestEnvVarExpansion:
    def test_var_expansion(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TEST_DB_USER", "alice")
        monkeypatch.setenv("TEST_DB_PASS", "s3cr3t")
        p = write_toml(tmp_path, """
            [connections.mydb]
            driver = "postgres"
            server = "localhost"
            port = 5432
            database = "mydb"
            user = "${TEST_DB_USER}"
            password = "${TEST_DB_PASS}"
        """)
        connections = load_config(p)
        assert connections["mydb"].user == "alice"
        assert connections["mydb"].password == "s3cr3t"

    def test_missing_var_raises_config_error(self, tmp_path, monkeypatch):
        monkeypatch.delenv("NONEXISTENT_VAR_AMNESIC", raising=False)
        p = write_toml(tmp_path, """
            [connections.mydb]
            driver = "postgres"
            server = "localhost"
            port = 5432
            database = "mydb"
            user = "${NONEXISTENT_VAR_AMNESIC}"
            password = "pw"
        """)
        with pytest.raises(ConfigError, match="NONEXISTENT_VAR_AMNESIC"):
            load_config(p)

    def test_partial_var_expansion(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DB_HOST", "db.example.com")
        p = write_toml(tmp_path, """
            [connections.mydb]
            driver = "postgres"
            server = "${DB_HOST}"
            port = 5432
            database = "mydb"
            user = "static_user"
            password = "static_pass"
        """)
        connections = load_config(p)
        assert connections["mydb"].server == "db.example.com"


# ---------------------------------------------------------------------------
# AMNESIC_CONFIG env var override
# ---------------------------------------------------------------------------

class TestConfigEnvOverride:
    def test_amnesic_config_override(self, tmp_path, monkeypatch):
        p = write_toml(tmp_path, """
            [connections.fromenv]
            driver = "sqlite"
            database = "/tmp/test.db"
        """)
        monkeypatch.setenv("AMNESIC_CONFIG", str(p))
        connections = load_config()  # no path arg — uses AMNESIC_CONFIG
        assert "fromenv" in connections

    def test_missing_config_file_raises(self, tmp_path):
        missing = tmp_path / "nonexistent.toml"
        with pytest.raises(ConfigError, match="not found"):
            load_config(missing)


# ---------------------------------------------------------------------------
# Default schema per driver
# ---------------------------------------------------------------------------

class TestDefaultSchema:
    @pytest.mark.parametrize("driver,expected_schema", [
        ("mssql", "dbo"),
        ("postgres", "public"),
        ("postgresql", "public"),
        ("sqlite", ""),
    ])
    def test_default_schema(self, driver, expected_schema, tmp_path):
        p = write_toml(tmp_path, f"""
            [connections.conn]
            driver = "{driver}"
            server = "localhost"
            port = 5432
            database = "mydb"
            user = "u"
            password = "p"
        """)
        connections = load_config(p)
        assert connections["conn"].default_schema == expected_schema

    def test_explicit_default_schema_override(self, tmp_path):
        p = write_toml(tmp_path, """
            [connections.conn]
            driver = "mssql"
            server = "localhost"
            port = 1433
            database = "mydb"
            user = "u"
            password = "p"
            default_schema = "custom_schema"
        """)
        connections = load_config(p)
        assert connections["conn"].default_schema == "custom_schema"


# ---------------------------------------------------------------------------
# get_default_connection_name / resolve_connection
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_default_connection_name_returns_first(self, tmp_path):
        p = write_toml(tmp_path, """
            [connections.first]
            driver = "sqlite"
            database = "/tmp/a.db"

            [connections.second]
            driver = "sqlite"
            database = "/tmp/b.db"
        """)
        connections = load_config(p)
        assert get_default_connection_name(connections) == "first"

    def test_get_default_empty_raises(self):
        with pytest.raises(ConfigError):
            get_default_connection_name({})

    def test_resolve_connection_by_name(self, tmp_path):
        p = write_toml(tmp_path, """
            [connections.mydb]
            driver = "sqlite"
            database = "/tmp/x.db"
        """)
        connections = load_config(p)
        conn = resolve_connection("mydb", connections)
        assert conn.name == "mydb"

    def test_resolve_connection_none_returns_first(self, tmp_path):
        p = write_toml(tmp_path, """
            [connections.first]
            driver = "sqlite"
            database = "/tmp/a.db"
        """)
        connections = load_config(p)
        conn = resolve_connection(None, connections)
        assert conn.name == "first"

    def test_resolve_unknown_connection_raises(self, tmp_path):
        p = write_toml(tmp_path, """
            [connections.mydb]
            driver = "sqlite"
            database = "/tmp/x.db"
        """)
        connections = load_config(p)
        with pytest.raises(ConfigError, match="badname"):
            resolve_connection("badname", connections)
