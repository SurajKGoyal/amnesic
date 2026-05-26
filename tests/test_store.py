"""
Tests for amnesic.store — SQLite knowledge store CRUD.

Uses a tmp directory so tests don't touch ~/.config/amnesic.
No real DB connection required.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from amnesic.store import KnowledgeStore


@pytest.fixture()
def store(tmp_path: Path) -> KnowledgeStore:
    """Return a KnowledgeStore backed by a tmp directory."""
    with patch("amnesic.store._CONFIG_DIR", tmp_path):
        return KnowledgeStore("test_conn")


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
