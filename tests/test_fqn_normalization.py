"""
Regression tests for the FQN normalization bug class.

Verifies that normalize_fqn is consistent — a bare table name and a
fully-qualified name with driver defaults both produce the same canonical key.
No real MSSQL connection required: we test the normalization function directly.
"""

import pytest

from amnesic.config import ConnectionConfig
from amnesic.tools.schema import normalize_fqn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_conn(driver: str, database: str = "mydb", default_schema: str = "") -> ConnectionConfig:
    return ConnectionConfig(
        name=f"{driver}_test",
        driver=driver,
        server="localhost",
        port=1433,
        database=database,
        user="u",
        password="p",
        default_schema=default_schema,
    )


# ---------------------------------------------------------------------------
# MSSQL
# ---------------------------------------------------------------------------

class TestMSSQLNormalization:
    def test_bare_name_gets_default_schema(self):
        conn = make_conn("mssql", database="drive")
        fqn, db, schema, table_name = normalize_fqn("orders", conn)
        assert fqn == "drive.dbo.orders"
        assert db == "drive"
        assert schema == "dbo"
        assert table_name == "orders"

    def test_schema_qualified_name(self):
        conn = make_conn("mssql", database="drive")
        fqn, db, schema, table_name = normalize_fqn("dbo.orders", conn)
        assert fqn == "drive.dbo.orders"

    def test_fully_qualified_name(self):
        conn = make_conn("mssql", database="drive")
        fqn, db, schema, table_name = normalize_fqn("drive.dbo.orders", conn)
        assert fqn == "drive.dbo.orders"

    def test_bare_name_and_fqn_produce_same_key(self):
        """Core regression: 'orders' and 'drive.dbo.orders' must hash to the same key."""
        conn = make_conn("mssql", database="drive")
        bare_fqn, *_ = normalize_fqn("orders", conn)
        full_fqn, *_ = normalize_fqn("drive.dbo.orders", conn)
        assert bare_fqn == full_fqn

    def test_case_insensitive_output(self):
        conn = make_conn("mssql", database="Drive")
        fqn, *_ = normalize_fqn("ORDERS", conn)
        assert fqn == "drive.dbo.orders"

    def test_custom_default_schema(self):
        conn = make_conn("mssql", database="mydb", default_schema="ops")
        fqn, db, schema, table_name = normalize_fqn("orders", conn)
        assert fqn == "mydb.ops.orders"
        assert schema == "ops"


# ---------------------------------------------------------------------------
# PostgreSQL
# ---------------------------------------------------------------------------

class TestPostgresNormalization:
    def test_bare_name_gets_public_schema(self):
        conn = make_conn("postgres", database="appdb")
        fqn, db, schema, table_name = normalize_fqn("users", conn)
        assert fqn == "public.users"
        assert schema == "public"

    def test_schema_qualified_name(self):
        conn = make_conn("postgres", database="appdb")
        fqn, db, schema, table_name = normalize_fqn("reporting.metrics", conn)
        assert fqn == "reporting.metrics"

    def test_bare_and_qualified_produce_same_key(self):
        conn = make_conn("postgres", database="appdb")
        bare_fqn, *_ = normalize_fqn("users", conn)
        qualified_fqn, *_ = normalize_fqn("public.users", conn)
        assert bare_fqn == qualified_fqn


# ---------------------------------------------------------------------------
# MySQL
# ---------------------------------------------------------------------------

class TestMySQLNormalization:
    def test_bare_name_gets_database(self):
        conn = make_conn("mysql", database="shop")
        fqn, db, schema, table_name = normalize_fqn("products", conn)
        assert fqn == "shop.products"

    def test_db_qualified_name(self):
        conn = make_conn("mysql", database="shop")
        fqn, *_ = normalize_fqn("shop.products", conn)
        assert fqn == "shop.products"

    def test_bare_and_qualified_produce_same_key(self):
        conn = make_conn("mysql", database="shop")
        bare_fqn, *_ = normalize_fqn("products", conn)
        qualified_fqn, *_ = normalize_fqn("shop.products", conn)
        assert bare_fqn == qualified_fqn


# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------

class TestSQLiteNormalization:
    def test_bare_name_passthrough(self):
        conn = make_conn("sqlite", database="")
        fqn, db, schema, table_name = normalize_fqn("logs", conn)
        assert fqn == "logs"
        assert db == ""
        assert schema == ""
        assert table_name == "logs"

    def test_case_insensitive(self):
        conn = make_conn("sqlite", database="")
        fqn, *_ = normalize_fqn("LOGS", conn)
        assert fqn == "logs"


# ---------------------------------------------------------------------------
# Store round-trip: annotate with bare name, retrieve with bare name
# ---------------------------------------------------------------------------

class TestStoreRoundTrip:
    """
    Ensure that db_annotate + db_get_schema share the same FQN key.
    We simulate this by calling normalize_fqn on both sides and asserting equality —
    the same logic that db_annotate and db_get_schema now both use.
    """

    def test_mssql_annotate_key_matches_schema_key(self):
        conn = make_conn("mssql", database="drive")
        annotate_key, *_ = normalize_fqn("orders", conn)
        schema_key, *_ = normalize_fqn("drive.dbo.orders", conn)
        assert annotate_key == schema_key, (
            f"Annotation stored under '{annotate_key}' but schema lookup uses '{schema_key}'"
        )

    def test_postgres_annotate_key_matches_schema_key(self):
        conn = make_conn("postgres", database="appdb")
        annotate_key, *_ = normalize_fqn("users", conn)
        schema_key, *_ = normalize_fqn("public.users", conn)
        assert annotate_key == schema_key
