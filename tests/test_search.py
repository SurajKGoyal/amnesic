"""Tests for BM25 search over the knowledge layer."""
import sqlite3

import pytest

from amnesic.store import KnowledgeStore


@pytest.fixture()
def populated_store(tmp_path, monkeypatch):
    """Store seeded with a few tables and columns for searching."""
    monkeypatch.setenv("AMNESIC_HOME", str(tmp_path))
    s = KnowledgeStore("test")
    # Tables
    s.save_table_knowledge(
        "dbo.payments",
        description="Customer payment records with Razorpay integration",
        aliases=["transactions", "billing"],
    )
    s.save_table_knowledge(
        "dbo.users",
        description="User account directory",
        aliases=["customers", "accounts"],
    )
    s.save_table_knowledge(
        "dbo.orders",
        description="Order lifecycle tracking",
        aliases=["bookings"],
    )
    # Columns
    s.save_column_knowledge(
        "dbo.users", "email",
        description="Primary contact email address",
    )
    s.save_column_knowledge(
        "dbo.orders", "status",
        description="Order lifecycle state",
        enum_values={"1": "pending", "2": "shipped", "3": "delivered"},
    )
    s.save_column_knowledge(
        "dbo.payments", "amount",
        description="Total payment amount in INR",
    )
    return s


class TestSearchBasic:
    def test_finds_table_by_description(self, populated_store):
        results = populated_store.search("payment")
        names = [r["table_fqn"] for r in results]
        assert "dbo.payments" in names

    def test_finds_table_by_alias(self, populated_store):
        results = populated_store.search("transactions")
        names = [r["table_fqn"] for r in results]
        assert "dbo.payments" in names

    def test_finds_column_by_description(self, populated_store):
        results = populated_store.search("email")
        emails = [r for r in results if r["column_name"] == "email"]
        assert len(emails) >= 1

    def test_finds_by_enum_value(self, populated_store):
        results = populated_store.search("delivered")
        statuses = [r for r in results if r["column_name"] == "status"]
        assert len(statuses) >= 1

    def test_no_match_returns_empty(self, populated_store):
        results = populated_store.search("zzzzz_nonexistent_term_xyz")
        assert results == []

    def test_malformed_query_returns_empty(self, populated_store):
        # Unbalanced quote — FTS5 raises OperationalError, we catch it
        results = populated_store.search('"unbalanced')
        assert results == []


class TestSearchTargetFilter:
    def test_target_tables_only(self, populated_store):
        results = populated_store.search("dbo", target="tables")
        for r in results:
            assert r["target_type"] == "table"

    def test_target_columns_only(self, populated_store):
        results = populated_store.search("email", target="columns")
        for r in results:
            assert r["target_type"] == "column"

    def test_invalid_target_raises(self, populated_store):
        with pytest.raises(ValueError):
            populated_store.search("foo", target="bogus")


class TestSearchRanking:
    def test_better_match_ranks_higher(self, populated_store):
        # "payment" should rank dbo.payments above unrelated rows
        results = populated_store.search("payment")
        assert results[0]["table_fqn"] == "dbo.payments"

    def test_snippet_includes_highlight(self, populated_store):
        results = populated_store.search("Razorpay")
        assert any("<mark>" in r["snippet"] for r in results)


class TestSearchTriggers:
    def test_trigger_updates_fts_on_insert(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AMNESIC_HOME", str(tmp_path))
        s = KnowledgeStore("trigtest")
        s.save_table_knowledge("dbo.fresh", description="freshly inserted")
        results = s.search("freshly")
        names = [r["table_fqn"] for r in results]
        assert "dbo.fresh" in names

    def test_trigger_updates_fts_on_description_update(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AMNESIC_HOME", str(tmp_path))
        s = KnowledgeStore("trigtest2")
        s.save_table_knowledge("dbo.changing", description="initial keyword apple")
        # First search finds it
        assert any(r["table_fqn"] == "dbo.changing" for r in s.search("apple"))
        # Update description
        s.save_table_knowledge("dbo.changing", description="new keyword banana")
        # Old keyword no longer matches
        assert not any(r["table_fqn"] == "dbo.changing" for r in s.search("apple"))
        # New keyword does
        assert any(r["table_fqn"] == "dbo.changing" for r in s.search("banana"))


class TestMalformedFtsQueries:
    """H2: any malformed FTS query must return [] rather than raise."""

    def test_star_alone_returns_empty(self, populated_store):
        # sqlite3 raises OperationalError("unknown special query: ") for bare "*"
        results = populated_store.search("*")
        assert results == []

    def test_unbalanced_quote_returns_empty(self, populated_store):
        results = populated_store.search('"unbalanced')
        assert results == []

    def test_bare_and_returns_empty(self, populated_store):
        results = populated_store.search("AND")
        assert results == []

    def test_empty_string_returns_empty(self, populated_store):
        results = populated_store.search("")
        assert results == []


class TestSearchBackfill:
    def test_backfill_existing_knowledge_into_fts(self, tmp_path, monkeypatch):
        """Pre-existing knowledge_<conn>.db without an FTS index gets backfilled on open."""
        monkeypatch.setenv("AMNESIC_HOME", str(tmp_path))
        db = tmp_path / "knowledge_legacy.db"
        c = sqlite3.connect(str(db))
        c.execute(
            "CREATE TABLE table_knowledge "
            "(table_fqn TEXT PRIMARY KEY, description TEXT DEFAULT '', aliases TEXT DEFAULT '[]')"
        )
        c.execute(
            "CREATE TABLE column_knowledge "
            "(table_fqn TEXT, column_name TEXT, description TEXT DEFAULT '', "
            "enum_values TEXT DEFAULT '{}', foreign_key TEXT DEFAULT '', "
            "example_values TEXT DEFAULT '[]', PRIMARY KEY (table_fqn, column_name))"
        )
        c.execute("INSERT INTO table_knowledge VALUES ('legacy.table', 'imported legacy widget', '[]')")
        c.execute(
            "INSERT INTO column_knowledge VALUES "
            "('legacy.table', 'legacy_col', 'legacy column desc', '{}', '', '[]')"
        )
        c.commit()
        c.close()
        # Open via KnowledgeStore — should backfill the FTS index
        s = KnowledgeStore("legacy")
        results = s.search("widget")
        assert any(r["table_fqn"] == "legacy.table" for r in results)
        results2 = s.search("legacy column")
        assert any(r["column_name"] == "legacy_col" for r in results2)
