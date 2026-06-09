"""Tests for db_detect_drift — uses the pure _compute_drift core, no live DB."""

from pathlib import Path

import pytest

from amnesic.store import KnowledgeStore
from amnesic.tools.drift import _compute_drift


@pytest.fixture()
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> KnowledgeStore:
    monkeypatch.setenv("AMNESIC_HOME", str(tmp_path))
    return KnowledgeStore("drift_conn")


class TestDetectDrift:
    def test_orphaned_table_detected(self, store):
        # Annotate a table that is NOT in the live schema.
        store.save_table_knowledge("dbo.gone", description="dropped table")
        live = {"dbo.orders"}
        result = _compute_drift(store, live, lambda fqn: set())
        assert "dbo.gone" in result["orphaned_tables"]
        assert result["summary"]["orphaned_tables"] == 1

    def test_orphaned_column_detected(self, store):
        # Table exists live, but the annotated column is gone.
        store.save_column_knowledge("dbo.orders", "old_col", description="removed")
        live = {"dbo.orders"}
        # live columns for dbo.orders do NOT include old_col
        result = _compute_drift(store, live, lambda fqn: {"id", "status"})
        assert {"table": "dbo.orders", "column": "old_col"} in result["orphaned_columns"]

    def test_live_column_not_flagged(self, store):
        store.save_column_knowledge("dbo.orders", "status", description="ok")
        live = {"dbo.orders"}
        result = _compute_drift(store, live, lambda fqn: {"id", "status"})
        assert result["orphaned_columns"] == []

    def test_undocumented_table_detected(self, store):
        # A live table with no annotation at all.
        live = {"dbo.orders", "dbo.invoices"}
        store.save_table_knowledge("dbo.orders", description="documented")
        result = _compute_drift(store, live, lambda fqn: set())
        assert "dbo.invoices" in result["undocumented_tables"]
        assert "dbo.orders" not in result["undocumented_tables"]

    def test_table_with_only_column_annotation_is_documented(self, store):
        # column-annotated tables count as documented (not undocumented).
        store.save_column_knowledge("dbo.orders", "status", description="s")
        live = {"dbo.orders"}
        result = _compute_drift(store, live, lambda fqn: {"status"})
        assert "dbo.orders" not in result["undocumented_tables"]

    def test_orphaned_table_not_double_counted_as_column(self, store):
        # Whole table gone → reported as orphaned table, its columns NOT also
        # listed as orphaned columns.
        store.save_column_knowledge("dbo.gone", "x", description="c")
        live = {"dbo.orders"}
        result = _compute_drift(store, live, lambda fqn: set())
        assert "dbo.gone" in result["orphaned_tables"]
        assert result["orphaned_columns"] == []

    def test_undocumented_list_capped_count_accurate(self, store):
        live = {f"dbo.t{i}" for i in range(150)}
        result = _compute_drift(store, live, lambda fqn: set())
        assert len(result["undocumented_tables"]) == 100  # list capped
        assert result["undocumented_truncated"] is True
        assert result["summary"]["undocumented_tables"] == 150  # true count
