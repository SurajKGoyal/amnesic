"""
Tests for amnesic.tools.schema — specifically the batch column-knowledge lookup
introduced in v0.1.12 (C2: eliminate 444x N+1 on db_get_schema).

No real DB connection required — schema is injected via the store directly.
"""

from pathlib import Path
from unittest.mock import patch, call

import pytest

from amnesic.store import KnowledgeStore


@pytest.fixture()
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> KnowledgeStore:
    monkeypatch.setenv("AMNESIC_HOME", str(tmp_path))
    return KnowledgeStore("test_conn")


class TestBatchColumnKnowledgeLookup:
    """
    Verify that _merge_knowledge_into_schema uses get_all_column_knowledge
    (one query) instead of get_column_knowledge (one query per column).

    We seed a store with 50 column annotations, then call _merge_knowledge_into_schema
    and assert that get_column_knowledge is never called — the batch path is used
    exclusively.
    """

    def test_no_per_column_queries_for_50_columns(self, store, monkeypatch):
        """get_column_knowledge must NOT be called — only the batch path is used."""
        from amnesic.store import get_store

        # Seed table schema with 50 annotated columns
        table_fqn = "dbo.bigtest"
        n = 50
        for i in range(n):
            store.save_column_knowledge(
                table_fqn,
                f"col_{i}",
                description=f"Column {i} description",
            )

        # Patch get_store to return our isolated store
        monkeypatch.setattr("amnesic.tools.schema.get_store", lambda _conn_name: store)

        # Build the columns list as it would come from schema_cache
        columns = [
            {"column_name": f"col_{i}", "data_type": "int", "is_nullable": "YES", "max_length": None}
            for i in range(n)
        ]

        # Spy on the per-column method — it must never be called
        with patch.object(store, "get_column_knowledge", wraps=store.get_column_knowledge) as per_col_spy:
            from amnesic.tools.schema import _merge_knowledge_into_schema
            result = _merge_knowledge_into_schema(table_fqn, columns, "test_conn")

            assert per_col_spy.call_count == 0, (
                f"get_column_knowledge was called {per_col_spy.call_count} time(s) "
                f"— expected 0 (batch path must be used)"
            )

        # Sanity: all 50 columns returned, annotations merged correctly
        assert len(result["columns"]) == n
        for i, col in enumerate(result["columns"]):
            assert col["description"] == f"Column {i} description"

    def test_batch_lookup_called_at_most_once(self, store, monkeypatch):
        """get_all_column_knowledge must be called exactly once regardless of column count."""
        from amnesic.store import get_store

        table_fqn = "dbo.bulk"
        for i in range(10):
            store.save_column_knowledge(table_fqn, f"c_{i}", description=f"desc {i}")

        monkeypatch.setattr("amnesic.tools.schema.get_store", lambda _conn_name: store)

        columns = [
            {"column_name": f"c_{i}", "data_type": "varchar", "is_nullable": "YES", "max_length": 100}
            for i in range(10)
        ]

        with patch.object(store, "get_all_column_knowledge", wraps=store.get_all_column_knowledge) as bulk_spy:
            from amnesic.tools.schema import _merge_knowledge_into_schema
            _merge_knowledge_into_schema(table_fqn, columns, "test_conn")

            assert bulk_spy.call_count == 1, (
                f"get_all_column_knowledge called {bulk_spy.call_count} time(s), expected exactly 1"
            )

    def test_case_insensitive_column_lookup(self, store, monkeypatch):
        """Annotations stored with any case are found by the batch lookup (v0.1.11 convention)."""
        from amnesic.store import get_store

        table_fqn = "dbo.casetest"
        # Column names are stored lowercase by save_column_knowledge (v0.1.11 fix)
        store.save_column_knowledge(table_fqn, "OrderDate", description="order timestamp")
        store.save_column_knowledge(table_fqn, "UserId", description="user reference")

        monkeypatch.setattr("amnesic.tools.schema.get_store", lambda _conn_name: store)

        # Schema from DB may return mixed-case column names
        columns = [
            {"column_name": "OrderDate", "data_type": "datetime", "is_nullable": "YES", "max_length": None},
            {"column_name": "UserId", "data_type": "int", "is_nullable": "NO", "max_length": None},
        ]

        from amnesic.tools.schema import _merge_knowledge_into_schema
        result = _merge_knowledge_into_schema(table_fqn, columns, "test_conn")

        descriptions = {col["column_name"]: col.get("description", "") for col in result["columns"]}
        assert descriptions["OrderDate"] == "order timestamp"
        assert descriptions["UserId"] == "user reference"

    def test_unannotated_table_returns_columns_unchanged(self, store, monkeypatch):
        """No annotations stored — columns pass through without modification."""
        monkeypatch.setattr("amnesic.tools.schema.get_store", lambda _conn_name: store)

        columns = [
            {"column_name": "id", "data_type": "int", "is_nullable": "NO", "max_length": None},
            {"column_name": "name", "data_type": "varchar", "is_nullable": "YES", "max_length": 255},
        ]

        from amnesic.tools.schema import _merge_knowledge_into_schema
        result = _merge_knowledge_into_schema("dbo.empty", columns, "test_conn")

        assert len(result["columns"]) == 2
        for col in result["columns"]:
            assert "description" not in col


class TestDeprecationSurfacing:
    """db_get_schema must surface deprecation so the AI is warned off stale items."""

    def test_deprecated_column_flagged_in_merge(self, store, monkeypatch):
        store.save_column_knowledge("dbo.orders", "legacy_status", description="old")
        store.set_column_deprecated("dbo.orders", "legacy_status", deprecated=True, reason="use status_v2")
        monkeypatch.setattr("amnesic.tools.schema.get_store", lambda _c: store)
        from amnesic.tools.schema import _merge_knowledge_into_schema
        columns = [{"column_name": "legacy_status", "data_type": "int"}]
        result = _merge_knowledge_into_schema("dbo.orders", columns, "test_conn")
        col = result["columns"][0]
        assert col["deprecated"] is True
        assert col["deprecated_reason"] == "use status_v2"

    def test_non_deprecated_column_has_no_flag(self, store, monkeypatch):
        store.save_column_knowledge("dbo.orders", "status", description="ok")
        monkeypatch.setattr("amnesic.tools.schema.get_store", lambda _c: store)
        from amnesic.tools.schema import _merge_knowledge_into_schema
        columns = [{"column_name": "status", "data_type": "int"}]
        result = _merge_knowledge_into_schema("dbo.orders", columns, "test_conn")
        assert "deprecated" not in result["columns"][0]

    def test_deprecated_table_flagged_in_merge(self, store, monkeypatch):
        store.set_table_deprecated("dbo.orders", deprecated=True, reason="dropping Q3")
        monkeypatch.setattr("amnesic.tools.schema.get_store", lambda _c: store)
        from amnesic.tools.schema import _merge_knowledge_into_schema
        result = _merge_knowledge_into_schema("dbo.orders", [], "test_conn")
        assert result["table_deprecated"] is True
        assert result["table_deprecated_reason"] == "dropping Q3"
