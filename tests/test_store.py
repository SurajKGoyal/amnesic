"""
Tests for amnesic.store — SQLite knowledge store CRUD.

Uses a tmp directory so tests don't touch ~/.config/amnesic.
No real DB connection required.
"""

import json
import os
import sqlite3
import stat
from pathlib import Path

import pytest

from amnesic._paths import knowledge_path
from amnesic.store import KnowledgeStore


@pytest.fixture()
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> KnowledgeStore:
    """Return a KnowledgeStore backed by a tmp directory.

    Uses AMNESIC_HOME to redirect config dir resolution — see amnesic/_paths.py.
    This is the cross-platform-safe way to isolate tests; patching the
    module-level _CONFIG_DIR constant no longer works because
    KnowledgeStore.__init__ now calls config_dir() directly.
    """
    monkeypatch.setenv("AMNESIC_HOME", str(tmp_path))
    return KnowledgeStore("test_conn")


# ---------------------------------------------------------------------------
# Concurrency pragmas
# ---------------------------------------------------------------------------

class TestConcurrencyPragmas:
    def test_wal_mode_enabled(self, store):
        mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"

    def test_busy_timeout_set(self, store):
        # Cross-process writers should queue (up to 5s) instead of failing
        # immediately with SQLITE_BUSY.
        timeout = store._conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert timeout == 5000


# ---------------------------------------------------------------------------
# Schema cache
# ---------------------------------------------------------------------------

class TestSchemaCache:
    def test_cache_miss_returns_none(self, store):
        assert store.get_cached_schema("dbo.orders") is None

    def test_save_and_retrieve_schema(self, store):
        columns = [
            {"column_name": "id", "data_type": "int", "is_nullable": "NO", "max_length": None},
            {"column_name": "name", "data_type": "varchar", "is_nullable": "YES", "max_length": 255},
        ]
        store.save_cached_schema("dbo.orders", columns)
        result = store.get_cached_schema("dbo.orders")
        assert result is not None
        assert len(result) == 2
        assert result[0]["column_name"] == "id"
        assert result[1]["column_name"] == "name"

    def test_save_replaces_existing(self, store):
        old_columns = [{"column_name": "id", "data_type": "int", "is_nullable": "NO", "max_length": None}]
        new_columns = [
            {"column_name": "id", "data_type": "int", "is_nullable": "NO", "max_length": None},
            {"column_name": "email", "data_type": "varchar", "is_nullable": "YES", "max_length": 100},
        ]
        store.save_cached_schema("dbo.users", old_columns)
        store.save_cached_schema("dbo.users", new_columns)
        result = store.get_cached_schema("dbo.users")
        assert len(result) == 2

    def test_fqn_is_case_insensitive(self, store):
        columns = [{"column_name": "id", "data_type": "int", "is_nullable": "NO", "max_length": None}]
        store.save_cached_schema("DBO.Orders", columns)
        result = store.get_cached_schema("dbo.orders")
        assert result is not None

    def test_get_all_table_fqns(self, store):
        cols = [{"column_name": "id", "data_type": "int", "is_nullable": "NO", "max_length": None}]
        store.save_cached_schema("table_a", cols)
        store.save_cached_schema("table_b", cols)
        fqns = store.get_all_table_fqns()
        assert "table_a" in fqns
        assert "table_b" in fqns

    def test_get_all_column_names(self, store):
        columns = [
            {"column_name": "id", "data_type": "int", "is_nullable": "NO", "max_length": None},
            {"column_name": "status", "data_type": "int", "is_nullable": "NO", "max_length": None},
        ]
        store.save_cached_schema("orders", columns)
        names = store.get_all_column_names("orders")
        assert names == {"id", "status"}


# ---------------------------------------------------------------------------
# Table knowledge
# ---------------------------------------------------------------------------

class TestTableKnowledge:
    def test_get_missing_returns_none(self, store):
        assert store.get_table_knowledge("dbo.orders") is None

    def test_save_and_retrieve_table_knowledge(self, store):
        store.save_table_knowledge(
            "dbo.orders",
            description="Customer orders table",
            aliases=["Orders", "CustomerOrders"],
        )
        result = store.get_table_knowledge("dbo.orders")
        assert result is not None
        assert result["description"] == "Customer orders table"
        assert "Orders" in result["aliases"]

    def test_upsert_description_only(self, store):
        store.save_table_knowledge("dbo.orders", description="First description")
        store.save_table_knowledge("dbo.orders", description="Updated description")
        result = store.get_table_knowledge("dbo.orders")
        assert result["description"] == "Updated description"

    def test_upsert_aliases_preserves_description(self, store):
        store.save_table_knowledge("dbo.orders", description="My desc")
        store.save_table_knowledge("dbo.orders", aliases=["alias1"])
        result = store.get_table_knowledge("dbo.orders")
        assert result["description"] == "My desc"
        assert "alias1" in result["aliases"]

    def test_none_fields_not_overwritten(self, store):
        store.save_table_knowledge("dbo.orders", description="existing")
        store.save_table_knowledge("dbo.orders", description=None)  # should not overwrite
        result = store.get_table_knowledge("dbo.orders")
        assert result["description"] == "existing"


# ---------------------------------------------------------------------------
# Column knowledge
# ---------------------------------------------------------------------------

class TestColumnKnowledge:
    def test_get_missing_returns_none(self, store):
        assert store.get_column_knowledge("dbo.orders", "status") is None

    def test_save_and_retrieve_column_knowledge(self, store):
        store.save_column_knowledge(
            "dbo.orders",
            "status",
            description="Order status code",
            enum_values={"1": "pending", "2": "shipped", "3": "delivered"},
            foreign_key="",
            example_values=["1", "2"],
        )
        result = store.get_column_knowledge("dbo.orders", "status")
        assert result is not None
        assert result["description"] == "Order status code"
        assert result["enum_values"]["1"] == "pending"
        assert "1" in result["example_values"]

    def test_upsert_partial_fields(self, store):
        store.save_column_knowledge("dbo.orders", "user_id", foreign_key="users.id")
        store.save_column_knowledge("dbo.orders", "user_id", description="FK to users")
        result = store.get_column_knowledge("dbo.orders", "user_id")
        assert result["foreign_key"] == "users.id"
        assert result["description"] == "FK to users"

    def test_none_fields_not_overwritten(self, store):
        store.save_column_knowledge("dbo.orders", "status", description="existing desc")
        store.save_column_knowledge("dbo.orders", "status", description=None)
        result = store.get_column_knowledge("dbo.orders", "status")
        assert result["description"] == "existing desc"

    def test_enum_values_as_dict(self, store):
        store.save_column_knowledge(
            "orders", "type",
            enum_values={"A": "standard", "B": "express"},
        )
        result = store.get_column_knowledge("orders", "type")
        assert isinstance(result["enum_values"], dict)
        assert result["enum_values"]["A"] == "standard"


# ---------------------------------------------------------------------------
# Relationships
# ---------------------------------------------------------------------------

class TestRelationships:
    def test_save_and_retrieve_relationship(self, store):
        store.save_relationship("orders", "user_id", "users", "id")
        result = store.get_relationships("orders", depth=1)
        assert len(result["neighbors"]) > 0
        neighbor_tables = {n["to_table"] for n in result["neighbors"]}
        assert "users" in neighbor_tables

    def test_reverse_edge_included(self, store):
        # Saving orders → users means querying from users should see orders too
        store.save_relationship("orders", "user_id", "users", "id")
        result = store.get_relationships("users", depth=1)
        neighbor_tables = {n["from_table"] for n in result["neighbors"]}
        assert "orders" in neighbor_tables

    def test_paths_returned(self, store):
        store.save_relationship("orders", "user_id", "users", "id")
        result = store.get_relationships("orders", depth=1)
        assert any("users" in path for path in result["paths"])

    def test_depth_2_traversal(self, store):
        store.save_relationship("order_items", "order_id", "orders", "id")
        store.save_relationship("orders", "user_id", "users", "id")
        result = store.get_relationships("order_items", depth=2)
        neighbor_tables = {n.get("to_table", n.get("from_table")) for n in result["neighbors"]}
        # At depth 2 from order_items: orders (depth 1), users (depth 2)
        assert "orders" in neighbor_tables

    def test_discover_relationships_bulk(self, store):
        rows = [
            {"from_table": "a", "from_column": "b_id", "to_table": "b", "to_column": "id",
             "source": "discovered"},
            {"from_table": "b", "from_column": "c_id", "to_table": "c", "to_column": "id",
             "source": "discovered"},
        ]
        store.discover_relationships_bulk(rows)
        result = store.get_relationships("a", depth=1)
        assert len(result["neighbors"]) > 0

    def test_get_all_relationships(self, store):
        store.save_relationship("x", "y_id", "y", "id")
        all_rels = store.get_all_relationships()
        assert any(r["from_table"] == "x" for r in all_rels)


# ---------------------------------------------------------------------------
# list_tables — merging schema_cache + table_knowledge
# ---------------------------------------------------------------------------

class TestListTables:
    def test_empty_store_returns_empty_list(self, store):
        assert store.list_tables() == []

    def test_tables_from_schema_cache(self, store):
        cols = [{"column_name": "id", "data_type": "int", "is_nullable": "NO", "max_length": None}]
        store.save_cached_schema("orders", cols)
        tables = store.list_tables()
        assert len(tables) == 1
        assert tables[0]["table_fqn"] == "orders"
        assert tables[0]["column_count"] == 1

    def test_tables_from_knowledge_only(self, store):
        store.save_table_knowledge("users", description="User accounts")
        tables = store.list_tables()
        assert len(tables) == 1
        assert tables[0]["table_fqn"] == "users"
        assert tables[0]["description"] == "User accounts"
        assert tables[0]["column_count"] == 0

    def test_merges_schema_and_knowledge(self, store):
        cols = [
            {"column_name": "id", "data_type": "int", "is_nullable": "NO", "max_length": None},
            {"column_name": "name", "data_type": "varchar", "is_nullable": "YES", "max_length": 100},
        ]
        store.save_cached_schema("users", cols)
        store.save_table_knowledge("users", description="User accounts", aliases=["accounts"])
        tables = store.list_tables()
        assert len(tables) == 1
        t = tables[0]
        assert t["description"] == "User accounts"
        assert "accounts" in t["aliases"]
        assert t["column_count"] == 2

    def test_multiple_tables_sorted(self, store):
        cols = [{"column_name": "id", "data_type": "int", "is_nullable": "NO", "max_length": None}]
        store.save_cached_schema("zebra", cols)
        store.save_cached_schema("apple", cols)
        tables = store.list_tables()
        assert tables[0]["table_fqn"] == "apple"
        assert tables[1]["table_fqn"] == "zebra"


# ---------------------------------------------------------------------------
# Column name case-insensitivity (Task F)
# ---------------------------------------------------------------------------

class TestColumnCaseInsensitivity:
    def test_save_mixed_case_retrieve_lowercase(self, store):
        """Save with mixed-case column name, retrieve with lowercase."""
        store.save_column_knowledge("dbo.orders", "OrderDate", description="timestamp")
        result = store.get_column_knowledge("dbo.orders", "orderdate")
        assert result is not None
        assert result["description"] == "timestamp"

    def test_save_lowercase_retrieve_mixed_case(self, store):
        """Save with lowercase, retrieve with mixed case."""
        store.save_column_knowledge("dbo.orders", "orderdate", description="ts")
        result = store.get_column_knowledge("dbo.orders", "OrderDate")
        assert result is not None
        assert result["description"] == "ts"

    def test_save_uppercase_retrieve_lowercase(self, store):
        """Save with uppercase, retrieve with lowercase."""
        store.save_column_knowledge("dbo.users", "EMAIL", description="contact email")
        result = store.get_column_knowledge("dbo.users", "email")
        assert result is not None
        assert result["description"] == "contact email"

    def test_upsert_with_different_case_same_row(self, store):
        """Save twice with different cases — should upsert the same row, not create two."""
        store.save_column_knowledge("dbo.orders", "Status", description="initial")
        store.save_column_knowledge("dbo.orders", "STATUS", description="updated")
        result = store.get_column_knowledge("dbo.orders", "status")
        assert result is not None
        assert result["description"] == "updated"

    def test_migration_lowercases_existing_rows(self, tmp_path, monkeypatch):
        """
        A store that already has mixed-case column_name rows (pre-v0.1.11) gets
        them migrated to lowercase on the next open.
        """
        import sqlite3

        monkeypatch.setenv("AMNESIC_HOME", str(tmp_path))
        db = tmp_path / "knowledge_migration_test.db"

        # Create a legacy database with a mixed-case column_name entry
        raw = sqlite3.connect(str(db))
        raw.execute(
            "CREATE TABLE column_knowledge "
            "(table_fqn TEXT NOT NULL, column_name TEXT NOT NULL, "
            "description TEXT DEFAULT '', enum_values TEXT DEFAULT '{}', "
            "foreign_key TEXT DEFAULT '', example_values TEXT DEFAULT '[]', "
            "PRIMARY KEY (table_fqn, column_name))"
        )
        raw.execute(
            "CREATE TABLE table_knowledge "
            "(table_fqn TEXT PRIMARY KEY, description TEXT DEFAULT '', aliases TEXT DEFAULT '[]')"
        )
        raw.execute(
            "INSERT INTO column_knowledge (table_fqn, column_name, description) "
            "VALUES ('dbo.orders', 'OrderDate', 'created timestamp')"
        )
        raw.commit()
        raw.close()

        # Open via KnowledgeStore — migration runs on __init__
        store = KnowledgeStore("migration_test")

        # Should be retrievable under lowercase
        result = store.get_column_knowledge("dbo.orders", "orderdate")
        assert result is not None
        assert result["description"] == "created timestamp"


# ---------------------------------------------------------------------------
# Secure file permissions (Task G)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(os.name == "nt", reason="File permission tests require POSIX")
class TestSecureFilePermissions:
    def test_knowledge_db_is_owner_only(self, tmp_path, monkeypatch):
        """
        On POSIX, knowledge_*.db files must be created with 0o600 so that
        secrets/annotations are not world-readable.
        """
        monkeypatch.setenv("AMNESIC_HOME", str(tmp_path))
        KnowledgeStore("perms_test")
        db_file = tmp_path / "knowledge_perms_test.db"
        assert db_file.exists(), "knowledge db was not created"
        mode = stat.S_IMODE(os.stat(db_file).st_mode)
        assert mode == 0o600, f"Expected 0o600, got {oct(mode)}"


# ---------------------------------------------------------------------------
# v0.2 deprecation-column migration
# ---------------------------------------------------------------------------

class TestDeprecationMigration:
    """The v0.2 store adds deprecated_at + deprecated_reason to the knowledge
    tables. Fresh stores get them via CREATE; pre-v0.2 stores get them via an
    idempotent ALTER migration on first open, with no data loss."""

    @staticmethod
    def _write_pre_v02_db(path: Path) -> None:
        """Create a pre-v0.2 knowledge DB (no deprecation columns) with data."""
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path))
        conn.execute(
            "CREATE TABLE table_knowledge ("
            "table_fqn TEXT PRIMARY KEY, description TEXT DEFAULT '', "
            "aliases TEXT DEFAULT '[]')"
        )
        conn.execute(
            "CREATE TABLE column_knowledge ("
            "table_fqn TEXT NOT NULL, column_name TEXT NOT NULL, "
            "description TEXT DEFAULT '', enum_values TEXT DEFAULT '{}', "
            "foreign_key TEXT DEFAULT '', example_values TEXT DEFAULT '[]', "
            "PRIMARY KEY (table_fqn, column_name))"
        )
        conn.execute(
            "INSERT INTO table_knowledge (table_fqn, description, aliases) "
            "VALUES ('dbo.orders', 'Order records', '[\"sales\"]')"
        )
        conn.execute(
            "INSERT INTO column_knowledge (table_fqn, column_name, description) "
            "VALUES ('dbo.orders', 'status', 'Order status')"
        )
        conn.commit()
        conn.close()

    def test_fresh_store_has_deprecation_columns(self, store):
        for table in ("table_knowledge", "column_knowledge"):
            cols = {
                r["name"]
                for r in store._conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            assert "deprecated_at" in cols, f"{table} missing deprecated_at"
            assert "deprecated_reason" in cols, f"{table} missing deprecated_reason"

    def test_migration_adds_columns_and_preserves_data(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AMNESIC_HOME", str(tmp_path))
        db_path = knowledge_path("test_conn")
        self._write_pre_v02_db(db_path)

        store = KnowledgeStore("test_conn")  # opening triggers the migration

        # Columns were added to both knowledge tables.
        for table in ("table_knowledge", "column_knowledge"):
            cols = {
                r["name"]
                for r in store._conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            assert "deprecated_at" in cols
            assert "deprecated_reason" in cols

        # Pre-existing data is intact.
        tk = store.get_table_knowledge("dbo.orders")
        assert tk["description"] == "Order records"
        assert tk["aliases"] == ["sales"]
        ck = store.get_column_knowledge("dbo.orders", "status")
        assert ck["description"] == "Order status"

        # New columns default to "not deprecated".
        row = store._conn.execute(
            "SELECT deprecated_at FROM table_knowledge WHERE table_fqn='dbo.orders'"
        ).fetchone()
        assert row["deprecated_at"] is None
        store.close()

    def test_migration_is_idempotent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AMNESIC_HOME", str(tmp_path))
        s1 = KnowledgeStore("test_conn")
        s1.save_table_knowledge("dbo.orders", description="Orders")
        s1.close()
        # Second open must not error and must keep the columns + data.
        s2 = KnowledgeStore("test_conn")
        cols = {
            r["name"]
            for r in s2._conn.execute("PRAGMA table_info(table_knowledge)").fetchall()
        }
        assert "deprecated_at" in cols
        assert s2.get_table_knowledge("dbo.orders")["description"] == "Orders"
        s2.close()


# ---------------------------------------------------------------------------
# v0.2 deprecation (db_deprecate backing)
# ---------------------------------------------------------------------------

class TestDeprecation:
    def test_table_not_deprecated_by_default(self, store):
        store.save_table_knowledge("dbo.orders", description="Orders")
        tk = store.get_table_knowledge("dbo.orders")
        assert tk["deprecated"] is False
        assert tk["deprecated_reason"] == ""

    def test_deprecate_table_sets_flag_and_reason(self, store):
        store.save_table_knowledge("dbo.orders", description="Orders")
        store.set_table_deprecated("dbo.orders", deprecated=True, reason="legacy")
        tk = store.get_table_knowledge("dbo.orders")
        assert tk["deprecated"] is True
        assert tk["deprecated_reason"] == "legacy"
        assert tk["deprecated_at"] is not None
        # Description is preserved.
        assert tk["description"] == "Orders"

    def test_deprecate_table_without_prior_annotation_creates_row(self, store):
        store.set_table_deprecated("dbo.ghost", deprecated=True, reason="gone soon")
        tk = store.get_table_knowledge("dbo.ghost")
        assert tk is not None
        assert tk["deprecated"] is True

    def test_undo_table_deprecation_clears_flag(self, store):
        store.set_table_deprecated("dbo.orders", deprecated=True, reason="legacy")
        store.set_table_deprecated("dbo.orders", deprecated=False)
        tk = store.get_table_knowledge("dbo.orders")
        assert tk["deprecated"] is False
        assert tk["deprecated_reason"] == ""

    def test_deprecate_column_sets_flag(self, store):
        store.save_column_knowledge("dbo.orders", "status", description="status code")
        store.set_column_deprecated("dbo.orders", "status", deprecated=True, reason="use status_v2")
        ck = store.get_column_knowledge("dbo.orders", "status")
        assert ck["deprecated"] is True
        assert ck["deprecated_reason"] == "use status_v2"
        assert ck["description"] == "status code"

    def test_undo_column_deprecation(self, store):
        store.set_column_deprecated("dbo.orders", "status", deprecated=True, reason="x")
        store.set_column_deprecated("dbo.orders", "status", deprecated=False)
        ck = store.get_column_knowledge("dbo.orders", "status")
        assert ck["deprecated"] is False

    def test_deprecation_surfaces_in_get_all_column_knowledge(self, store):
        store.save_column_knowledge("dbo.orders", "status", description="s")
        store.set_column_deprecated("dbo.orders", "status", deprecated=True, reason="r")
        rows = {c["column_name"]: c for c in store.get_all_column_knowledge("dbo.orders")}
        assert rows["status"]["deprecated"] is True
        assert rows["status"]["deprecated_reason"] == "r"

    def test_deprecation_surfaces_in_search(self, store):
        store.save_table_knowledge("dbo.orders", description="customer payment orders")
        store.set_table_deprecated("dbo.orders", deprecated=True, reason="legacy")
        results = {r["table_fqn"]: r for r in store.search("payment", target="tables")}
        assert results["dbo.orders"]["deprecated"] is True
        assert results["dbo.orders"]["deprecated_reason"] == "legacy"

    def test_non_deprecated_search_result_flag_false(self, store):
        store.save_table_knowledge("dbo.invoices", description="billing invoices")
        results = {r["table_fqn"]: r for r in store.search("billing", target="tables")}
        assert results["dbo.invoices"]["deprecated"] is False


# ---------------------------------------------------------------------------
# v0.2 hard delete (db_forget backing)
# ---------------------------------------------------------------------------

class TestForget:
    def _seed(self, store):
        store.save_table_knowledge("dbo.orders", description="Orders", aliases=["sales"])
        store.save_column_knowledge("dbo.orders", "status", description="status code")
        store.save_column_knowledge("dbo.orders", "user_id", foreign_key="users.id")
        store.save_relationship("dbo.orders", "user_id", "dbo.users", "id")

    def test_forget_table_no_cascade_keeps_columns_and_rels(self, store):
        self._seed(store)
        counts = store.forget_table("dbo.orders", cascade=False)
        assert counts["removed_table"] is True
        assert counts["removed_columns"] == 0
        assert counts["removed_relationships"] == 0
        # table annotation gone...
        assert store.get_table_knowledge("dbo.orders") is None
        # ...but columns + relationships remain.
        assert store.get_column_knowledge("dbo.orders", "status") is not None
        assert len(store.get_all_relationships()) == 1

    def test_forget_column_only(self, store):
        self._seed(store)
        removed = store.forget_column("dbo.orders", "status")
        assert removed is True
        assert store.get_column_knowledge("dbo.orders", "status") is None
        # other column + table untouched
        assert store.get_column_knowledge("dbo.orders", "user_id") is not None
        assert store.get_table_knowledge("dbo.orders") is not None

    def test_forget_cascade_removes_everything(self, store):
        self._seed(store)
        counts = store.forget_table("dbo.orders", cascade=True)
        assert counts["removed_table"] is True
        assert counts["removed_columns"] == 2
        assert counts["removed_relationships"] == 1
        assert store.get_table_knowledge("dbo.orders") is None
        assert store.get_column_knowledge("dbo.orders", "status") is None
        assert store.get_all_relationships() == []

    def test_forget_missing_table_reports_zero(self, store):
        counts = store.forget_table("dbo.nope", cascade=True)
        assert counts["removed_table"] is False
        assert counts["removed_columns"] == 0
        assert counts["removed_relationships"] == 0

    def test_forget_keeps_fts_consistent(self, store):
        store.save_table_knowledge("dbo.orders", description="customer payment records")
        # present in search before
        assert any(r["table_fqn"] == "dbo.orders" for r in store.search("payment"))
        store.forget_table("dbo.orders")
        # gone from search after (delete trigger fired)
        assert not any(r["table_fqn"] == "dbo.orders" for r in store.search("payment"))

    def test_cascade_only_affects_target_table(self, store):
        self._seed(store)
        store.save_table_knowledge("dbo.invoices", description="Invoices")
        store.save_column_knowledge("dbo.invoices", "amount", description="amt")
        store.forget_table("dbo.orders", cascade=True)
        # sibling table fully intact
        assert store.get_table_knowledge("dbo.invoices") is not None
        assert store.get_column_knowledge("dbo.invoices", "amount") is not None
