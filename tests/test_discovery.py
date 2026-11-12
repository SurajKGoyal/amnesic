"""
Tests for db_discover_enums (amnesic/tools/discovery.py).

Uses the demo SQLite database (build_demo_db) plus custom SQLite databases
for edge-case scenarios, following the fixture pattern in test_query.py.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from amnesic._demo import build_demo_db
from amnesic.drivers import clear_engine_cache
from amnesic.readonly import assert_readonly
from amnesic.store import close_store, get_store
from amnesic.tools.discovery import _COLUMN_PROBE_CAP, build_enum_sql, db_discover_enums
from amnesic.tools.knowledge import db_annotate


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def demo_conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """
    Spin up the standard demo SQLite DB and configure a 'demo' connection.
    Tears down engine and store caches after each test so the next test
    starts completely fresh.
    """
    db_path = build_demo_db(tmp_path / "demo.sqlite3")
    toml = f"""
[connections.demo]
driver = "sqlite"
database = "{db_path}"
"""
    (tmp_path / "connections.toml").write_text(toml)
    monkeypatch.setenv("AMNESIC_CONFIG", str(tmp_path / "connections.toml"))
    monkeypatch.setenv("AMNESIC_HOME", str(tmp_path))

    yield "demo"

    clear_engine_cache()
    close_store("demo")


def _make_conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, db_path: Path) -> str:
    """Helper: write a connections.toml for an arbitrary SQLite DB."""
    toml = f"""
[connections.{name}]
driver = "sqlite"
database = "{db_path}"
"""
    (tmp_path / "connections.toml").write_text(toml)
    monkeypatch.setenv("AMNESIC_CONFIG", str(tmp_path / "connections.toml"))
    monkeypatch.setenv("AMNESIC_HOME", str(tmp_path))
    return name


@pytest.fixture()
def custom_conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """
    Fixture factory: returns a callable(create_fn) where create_fn receives a
    db_path and populates the SQLite file, then returns the connection name.
    """
    _names: list[str] = []

    def factory(create_fn, name: str = "custom") -> str:
        db_path = tmp_path / f"{name}.sqlite3"
        create_fn(db_path)
        _make_conn(tmp_path, monkeypatch, name, db_path)
        _names.append(name)
        return name

    yield factory

    for n in _names:
        clear_engine_cache()
        close_store(n)


# ---------------------------------------------------------------------------
# Happy-path: demo DB
# ---------------------------------------------------------------------------

class TestDiscoverEnumsHappyPath:
    def test_orders_status_proposed(self, demo_conn: str) -> None:
        """status column should be a candidate with 5 distinct values sorted by count DESC."""
        result = db_discover_enums("orders", connection=demo_conn)

        col_names = [c["column"] for c in result["candidates"]]
        assert "status" in col_names, f"status not in candidates: {col_names}"

        status = next(c for c in result["candidates"] if c["column"] == "status")
        # Demo has exactly 5 order statuses (1–5)
        assert status["distinct_count"] == 5
        # Counts sum to total rows (100 orders, all with non-null status)
        total = sum(v["count"] for v in status["values"])
        assert total == 100
        # Sorted by count DESC
        counts = [v["count"] for v in status["values"]]
        assert counts == sorted(counts, reverse=True)
        # suggested_action contains all five value keys
        sa = status["suggested_action"]
        for i in range(1, 6):
            assert f'"{i}"' in sa, f"key '{i}' missing from suggested_action: {sa}"

    def test_products_category_proposed(self, demo_conn: str) -> None:
        """category column (6 distinct text values) should be a candidate."""
        result = db_discover_enums("products", connection=demo_conn)
        col_names = [c["column"] for c in result["candidates"]]
        assert "category" in col_names

        category = next(c for c in result["candidates"] if c["column"] == "category")
        # Demo has 6 categories
        assert category["distinct_count"] == 6
        assert len(category["values"]) == 6

    def test_small_table_unique_columns_not_enum(self, demo_conn: str) -> None:
        """customers has 20 rows, so name/email/signup_date fall under the default
        max_distinct=25 — the repetition rule must still reject them, because an
        enum's values repeat and these are (nearly) unique."""
        result = db_discover_enums("customers", connection=demo_conn)
        assert result["candidates"] == []
        for col in ("name", "email", "signup_date"):
            assert col in result["not_enum"], f"{col} not in not_enum: {result['not_enum']}"

    def test_key_columns_skipped_not_probed(self, demo_conn: str) -> None:
        """id and *_id are keys: orders.customer_id has only 20 distinct values
        over 100 rows, so without the name rule it would pass as an enum."""
        result = db_discover_enums("orders", connection=demo_conn)
        skipped = {s["column"]: s["reason"] for s in result["skipped"]}
        assert "id" in skipped and "customer_id" in skipped
        assert "db_discover_relationships" in skipped["customer_id"]
        assert [c["column"] for c in result["candidates"]] == ["status"]

    def test_response_shape(self, demo_conn: str) -> None:
        """Every required top-level key must be present with the right types."""
        result = db_discover_enums("orders", connection=demo_conn)
        assert isinstance(result["table"], str)
        assert isinstance(result["connection"], str)
        assert isinstance(result["candidates"], list)
        assert isinstance(result["not_enum"], list)
        assert isinstance(result["skipped"], list)
        assert isinstance(result["sampled"], bool)
        assert isinstance(result["sample_rows"], int)
        assert isinstance(result["note"], str)

    def test_sampled_flag_true_by_default(self, demo_conn: str) -> None:
        """Default sample_rows=10000 means sampled=True."""
        result = db_discover_enums("orders", connection=demo_conn)
        assert result["sampled"] is True
        assert result["sample_rows"] == 10000

    def test_note_contains_proposal_text(self, demo_conn: str) -> None:
        """Note must tell the caller these are proposals, not saved annotations."""
        result = db_discover_enums("orders", connection=demo_conn)
        assert "proposal" in result["note"].lower() or "proposals" in result["note"].lower()

    def test_connection_in_result(self, demo_conn: str) -> None:
        result = db_discover_enums("orders", connection=demo_conn)
        assert result["connection"] == demo_conn

    def test_table_fqn_in_result(self, demo_conn: str) -> None:
        """SQLite FQN is just the table name in lowercase."""
        result = db_discover_enums("orders", connection=demo_conn)
        assert result["table"] == "orders"


# ---------------------------------------------------------------------------
# Skipping: already-annotated columns and force flag
# ---------------------------------------------------------------------------

class TestDiscoverEnumsSkipping:
    def test_annotated_column_skipped(self, demo_conn: str) -> None:
        """A column that already has enum_values should land in skipped (not candidates)."""
        # Annotate status before discovery
        db_annotate(
            table="orders",
            connection=demo_conn,
            column="status",
            enum_values={"1": "pending", "2": "paid"},
        )
        result = db_discover_enums("orders", connection=demo_conn)

        candidate_cols = [c["column"] for c in result["candidates"]]
        assert "status" not in candidate_cols

        skipped_cols = [s["column"] for s in result["skipped"]]
        assert "status" in skipped_cols

        reason = next(s["reason"] for s in result["skipped"] if s["column"] == "status")
        assert "enum_values" in reason

    def test_force_reprobe_annotated_column(self, demo_conn: str) -> None:
        """force=True must re-probe an already-annotated column."""
        db_annotate(
            table="orders",
            connection=demo_conn,
            column="status",
            enum_values={"1": "pending"},
        )
        result = db_discover_enums("orders", connection=demo_conn, force=True)

        candidate_cols = [c["column"] for c in result["candidates"]]
        assert "status" in candidate_cols


# ---------------------------------------------------------------------------
# max_distinct boundary
# ---------------------------------------------------------------------------

class TestMaxDistinctBoundary:
    def _create_boundary_db(self, db_path: Path, n: int) -> None:
        """Create a table with column a having exactly n distinct values and b having n+1."""
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE bounds (a INTEGER, b INTEGER)")
        # Each value repeats 3 times so the repetition rule doesn't interfere:
        # a has exactly n distinct values, b has n+1.
        for _ in range(3):
            for i in range(1, n + 1):
                conn.execute("INSERT INTO bounds (a, b) VALUES (?, ?)", (i, i))
            conn.execute("INSERT INTO bounds (a, b) VALUES (?, ?)", (1, n + 1))
        conn.commit()
        conn.close()

    def test_exactly_max_distinct_is_candidate(self, custom_conn) -> None:
        n = 5
        conn_name = custom_conn(lambda p: self._create_boundary_db(p, n), name="boundary")
        result = db_discover_enums("bounds", connection=conn_name, max_distinct=n)
        col_names = [c["column"] for c in result["candidates"]]
        assert "a" in col_names, f"a (exactly {n} distinct) should be a candidate"

    def test_max_distinct_plus_one_is_not_enum(self, custom_conn) -> None:
        n = 5
        conn_name = custom_conn(lambda p: self._create_boundary_db(p, n), name="boundary2")
        result = db_discover_enums("bounds", connection=conn_name, max_distinct=n)
        assert "b" in result["not_enum"], f"b ({n+1} distinct) should be in not_enum"


# ---------------------------------------------------------------------------
# Column probe cap
# ---------------------------------------------------------------------------

class TestColumnProbeCap:
    def _create_wide_db(self, db_path: Path) -> None:
        """Create a table with _COLUMN_PROBE_CAP + 2 integer columns."""
        n_cols = _COLUMN_PROBE_CAP + 2
        col_defs = ", ".join(f"c{i} INTEGER" for i in range(n_cols))
        conn = sqlite3.connect(str(db_path))
        conn.execute(f"CREATE TABLE wide ({col_defs})")
        vals = ", ".join("1" for _ in range(n_cols))
        conn.execute(f"INSERT INTO wide VALUES ({vals})")
        conn.commit()
        conn.close()

    def test_extra_columns_reported_as_skipped(self, custom_conn) -> None:
        conn_name = custom_conn(self._create_wide_db, name="wide")
        result = db_discover_enums("wide", connection=conn_name)

        probed = len(result["candidates"]) + len(result["not_enum"])
        assert probed <= _COLUMN_PROBE_CAP

        over_cap = [s for s in result["skipped"] if "cap" in s["reason"].lower()]
        # Exactly 2 columns should be reported as over-cap
        assert len(over_cap) == 2


# ---------------------------------------------------------------------------
# sample_rows behaviour
# ---------------------------------------------------------------------------

class TestSampleRows:
    def test_sample_rows_bounds_scan(self, demo_conn: str) -> None:
        """With sample_rows=10, counts for status must sum to exactly 10."""
        result = db_discover_enums("orders", connection=demo_conn, sample_rows=10)
        assert result["sampled"] is True
        assert result["sample_rows"] == 10

        status = next(
            (c for c in result["candidates"] if c["column"] == "status"), None
        )
        # May or may not be a candidate (depends on which 10 rows get sampled),
        # but if it is, counts must sum to ≤ 10.
        if status:
            total = sum(v["count"] for v in status["values"])
            assert total <= 10

    def test_sample_rows_zero_is_full_scan(self, demo_conn: str) -> None:
        """sample_rows=0 → sampled=False and counts sum to full table size."""
        result = db_discover_enums("orders", connection=demo_conn, sample_rows=0)
        assert result["sampled"] is False
        assert result["sample_rows"] == 0
        # Note must NOT warn about rare values when it's a full scan
        assert "sample_rows=0" not in result["note"]

        status = next(
            (c for c in result["candidates"] if c["column"] == "status"), None
        )
        assert status is not None
        total = sum(v["count"] for v in status["values"])
        assert total == 100  # all 100 orders counted

    def test_sampled_note_mentions_rare_values(self, demo_conn: str) -> None:
        """When sampled=True, the note should mention that rare values may be missing."""
        result = db_discover_enums("orders", connection=demo_conn, sample_rows=100)
        assert result["sampled"] is True
        assert "rare" in result["note"].lower() or "missing" in result["note"].lower()


# ---------------------------------------------------------------------------
# NULL handling
# ---------------------------------------------------------------------------

class TestNullHandling:
    def _create_null_db(self, db_path: Path) -> None:
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE nulls (all_null INTEGER, partial INTEGER)")
        for _ in range(5):
            conn.execute("INSERT INTO nulls VALUES (NULL, NULL)")
        # Add some non-null rows to partial so it's a real candidate
        for _ in range(2):
            for i in range(1, 4):
                conn.execute("INSERT INTO nulls (all_null, partial) VALUES (NULL, ?)", (i,))
        conn.commit()
        conn.close()

    def test_all_null_column_is_skipped(self, custom_conn) -> None:
        conn_name = custom_conn(self._create_null_db, name="nulls")
        result = db_discover_enums("nulls", connection=conn_name)

        all_col_names = (
            [c["column"] for c in result["candidates"]]
            + result["not_enum"]
            + [s["column"] for s in result["skipped"]]
        )
        assert "all_null" in all_col_names

        # It must be in skipped (not candidates or not_enum)
        skipped_cols = [s["column"] for s in result["skipped"]]
        assert "all_null" in skipped_cols
        reason = next(s["reason"] for s in result["skipped"] if s["column"] == "all_null")
        assert "null" in reason.lower()

    def test_partial_null_column_probed_normally(self, custom_conn) -> None:
        conn_name = custom_conn(self._create_null_db, name="nulls2")
        result = db_discover_enums("nulls", connection=conn_name, max_distinct=10)
        # partial has 3 distinct non-null values → candidate
        candidate_cols = [c["column"] for c in result["candidates"]]
        assert "partial" in candidate_cols


# ---------------------------------------------------------------------------
# Read-only guarantee
# ---------------------------------------------------------------------------

class TestReadOnlyGuarantee:
    def test_no_writes_to_knowledge_store(self, demo_conn: str) -> None:
        """
        db_discover_enums must not write any column_knowledge rows.
        The schema cache (schema_columns table) is written by db_get_schema —
        that's expected. Only column_knowledge must stay empty.
        """
        db_discover_enums("orders", connection=demo_conn)

        store = get_store(demo_conn)
        col_knowledge = store.get_all_column_knowledge("orders")
        assert col_knowledge == [], (
            "db_discover_enums must not write to column_knowledge; "
            f"found: {col_knowledge}"
        )

    def test_every_generated_sql_passes_assert_readonly(self, demo_conn: str) -> None:
        """
        Monkeypatch run_readonly to capture every SQL string emitted by
        db_discover_enums, then assert each one passes assert_readonly.
        """
        captured_sqls: list[str] = []

        import amnesic.tools.discovery as _discovery_mod
        original_run_readonly = _discovery_mod.run_readonly

        def capturing_run_readonly(conn_cfg, sql, **kwargs):
            captured_sqls.append(sql)
            return original_run_readonly(conn_cfg, sql, **kwargs)

        with patch.object(_discovery_mod, "run_readonly", side_effect=capturing_run_readonly):
            db_discover_enums("orders", connection=demo_conn)

        assert len(captured_sqls) > 0, "No SQL was captured — likely a fixture issue"
        for sql in captured_sqls:
            # assert_readonly raises ValueError on any write statement
            assert_readonly(sql)
            # Every discovery SQL must start with SELECT
            first_token = sql.strip().split()[0].upper()
            assert first_token == "SELECT", f"Non-SELECT statement captured: {sql!r}"

    def test_no_writes_on_multiple_tables(self, demo_conn: str) -> None:
        """Run discovery on all demo tables and verify no column_knowledge rows appear."""
        for table in ("orders", "products", "customers", "order_items"):
            db_discover_enums(table, connection=demo_conn)

        store = get_store(demo_conn)
        for table in ("orders", "products", "customers", "order_items"):
            assert store.get_all_column_knowledge(table) == []


# ---------------------------------------------------------------------------
# Identifier safety
# ---------------------------------------------------------------------------

class TestIdentifierSafety:
    def _create_unsafe_col_db(self, db_path: Path) -> None:
        """Create a table with an unsafe column name (space in name)."""
        conn = sqlite3.connect(str(db_path))
        # SQLite allows quoted column names with spaces
        conn.execute('CREATE TABLE unsafe_cols ("normal" INTEGER, "bad col" INTEGER)')
        conn.execute('INSERT INTO unsafe_cols VALUES (1, 10)')
        conn.commit()
        conn.close()

    def test_unsafe_column_name_is_skipped_not_interpolated(self, custom_conn) -> None:
        """
        A column name with a space fails validate_identifier and must land in
        skipped rather than being interpolated into SQL (injection risk).
        """
        conn_name = custom_conn(self._create_unsafe_col_db, name="unsafe")
        result = db_discover_enums("unsafe_cols", connection=conn_name)

        skipped_cols = [s["column"] for s in result["skipped"]]
        assert "bad col" in skipped_cols

        # Safe column proceeds normally
        # (might be candidate or not_enum, but definitely not in skipped for safety reason)
        reason = next(s["reason"] for s in result["skipped"] if s["column"] == "bad col")
        assert "unsafe" in reason.lower() or "identifier" in reason.lower()

        # 'normal' must NOT be in skipped for the same reason
        normal_skip = [
            s for s in result["skipped"]
            if s["column"] == "normal" and "identifier" in s["reason"].lower()
        ]
        assert normal_skip == []


# ---------------------------------------------------------------------------
# SQL generation unit tests (no live DB)
# ---------------------------------------------------------------------------

class TestBuildEnumSql:
    """Unit-test build_enum_sql directly for quoting and TOP vs LIMIT shape."""

    def test_sqlite_with_sample(self) -> None:
        sql = build_enum_sql(
            tbl_ref="orders",
            col_quoted='"status"',
            driver="sqlite",
            sample_rows=10000,
            cap=26,
        )
        assert sql.startswith("SELECT v, COUNT(*) AS n FROM")
        assert '"status"' in sql
        assert "LIMIT 10000" in sql
        assert "LIMIT 26" in sql
        assert_readonly(sql)

    def test_sqlite_full_scan(self) -> None:
        sql = build_enum_sql(
            tbl_ref="orders",
            col_quoted='"status"',
            driver="sqlite",
            sample_rows=0,
            cap=26,
        )
        # No LIMIT in the inner subquery, only the outer cap
        inner, _, outer = sql.partition(") s")
        assert "LIMIT" not in inner
        assert "LIMIT 26" in outer
        assert_readonly(sql)

    def test_postgres_quoting(self) -> None:
        sql = build_enum_sql(
            tbl_ref='"public"."orders"',
            col_quoted='"status"',
            driver="postgresql",
            sample_rows=10000,
            cap=26,
        )
        assert '"status"' in sql
        assert '"public"."orders"' in sql
        assert "LIMIT" in sql
        # No TOP keyword
        assert "TOP" not in sql
        assert_readonly(sql)

    def test_mysql_quoting(self) -> None:
        sql = build_enum_sql(
            tbl_ref="`mydb`.`orders`",
            col_quoted="`status`",
            driver="mysql",
            sample_rows=10000,
            cap=26,
        )
        assert "`status`" in sql
        assert "`mydb`.`orders`" in sql
        assert "LIMIT" in sql
        assert "TOP" not in sql
        assert_readonly(sql)

    def test_mssql_uses_top(self) -> None:
        sql = build_enum_sql(
            tbl_ref="[mydb].[dbo].[orders]",
            col_quoted="[status]",
            driver="mssql",
            sample_rows=10000,
            cap=26,
        )
        assert "[status]" in sql
        assert "[mydb].[dbo].[orders]" in sql
        assert "TOP (26)" in sql
        assert "TOP (10000)" in sql
        # No LIMIT keyword
        assert "LIMIT" not in sql
        assert_readonly(sql)

    def test_mssql_full_scan_no_inner_top(self) -> None:
        sql = build_enum_sql(
            tbl_ref="[mydb].[dbo].[orders]",
            col_quoted="[status]",
            driver="mssql",
            sample_rows=0,
            cap=26,
        )
        # Outer TOP for the cap should still be there
        assert "TOP (26)" in sql
        # No TOP for the inner subquery (no sample limit)
        # Expect exactly one TOP occurrence (the outer one)
        assert sql.count("TOP") == 1
        assert_readonly(sql)

    def test_all_dialects_produce_readonly_sql(self) -> None:
        """Paranoia check: all dialect outputs pass assert_readonly."""
        cases = [
            ("sqlite", "orders", '"status"'),
            ("postgresql", '"public"."orders"', '"status"'),
            ("mysql", "`db`.`orders`", "`status`"),
            ("mssql", "[db].[dbo].[orders]", "[status]"),
        ]
        for driver, tbl_ref, col_quoted in cases:
            for sample_rows in (1000, 0):
                sql = build_enum_sql(
                            tbl_ref=tbl_ref,
                    col_quoted=col_quoted,
                    driver=driver,
                    sample_rows=sample_rows,
                    cap=26,
                )
                assert_readonly(sql)

    def test_nulls_excluded_in_all_dialects(self) -> None:
        """Every generated SQL must include the IS NOT NULL filter."""
        cases = [
            ("sqlite", "orders", '"status"'),
            ("postgresql", '"public"."orders"', '"status"'),
            ("mysql", "`db`.`orders`", "`status`"),
            ("mssql", "[db].[dbo].[orders]", "[status]"),
        ]
        for driver, tbl_ref, col_quoted in cases:
            sql = build_enum_sql(
                    tbl_ref=tbl_ref,
                col_quoted=col_quoted,
                driver=driver,
                sample_rows=1000,
                cap=26,
            )
            assert "IS NOT NULL" in sql, f"IS NOT NULL missing for driver={driver}: {sql}"


# ---------------------------------------------------------------------------
# Verify db_query behaviour is unchanged (regression guard)
# ---------------------------------------------------------------------------

class TestDbQueryUnchanged:
    """
    Spot-check that refactoring db_query to use run_readonly didn't change
    its observable behaviour. Full coverage lives in test_query.py.
    """

    @pytest.fixture()
    def simple_conn(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
        db_path = tmp_path / "simple.sqlite3"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE t (id INTEGER, v TEXT)")
        for i in range(1, 6):
            conn.execute("INSERT INTO t VALUES (?, ?)", (i, f"r{i}"))
        conn.commit()
        conn.close()

        toml = f"""
[connections.simple]
driver = "sqlite"
database = "{db_path}"
"""
        (tmp_path / "connections.toml").write_text(toml)
        monkeypatch.setenv("AMNESIC_CONFIG", str(tmp_path / "connections.toml"))
        monkeypatch.setenv("AMNESIC_HOME", str(tmp_path))

        yield "simple"

        clear_engine_cache()
        close_store("simple")

    def test_query_returns_rows(self, simple_conn: str) -> None:
        from amnesic.tools.query import db_query
        result = db_query("SELECT * FROM t ORDER BY id", connection=simple_conn)
        assert result["row_count"] == 5
        assert result["truncated"] is False

    def test_truncation_still_works(self, simple_conn: str) -> None:
        from amnesic.tools.query import db_query
        result = db_query("SELECT * FROM t ORDER BY id", connection=simple_conn, max_rows=3)
        assert result["row_count"] == 3
        assert result["truncated"] is True

    def test_exact_max_rows_not_truncated(self, simple_conn: str) -> None:
        from amnesic.tools.query import db_query
        result = db_query("SELECT * FROM t ORDER BY id", connection=simple_conn, max_rows=5)
        assert result["row_count"] == 5
        assert result["truncated"] is False


# ---------------------------------------------------------------------------
# Review follow-ups: columns filter, suggested_action escaping, table validation
# ---------------------------------------------------------------------------

class TestReviewFollowUps:
    def test_columns_filter_reaches_one_column_case_insensitively(self, demo_conn: str) -> None:
        result = db_discover_enums("order_items", connection=demo_conn, columns=["QUANTITY"])
        assert [c["column"] for c in result["candidates"]] == ["quantity"]
        assert result["not_enum"] == []

    def test_columns_filter_reaches_columns_past_the_cap(self, custom_conn) -> None:
        n_cols = _COLUMN_PROBE_CAP + 2
        last = f"c{n_cols - 1}"

        def build(db_path: Path) -> None:
            conn = sqlite3.connect(str(db_path))
            conn.execute(f"CREATE TABLE wide ({', '.join(f'c{i} INTEGER' for i in range(n_cols))})")
            for v in (1, 1, 2, 2):
                conn.execute(f"INSERT INTO wide VALUES ({', '.join(str(v) for _ in range(n_cols))})")
            conn.commit()
            conn.close()

        conn_name = custom_conn(build, name="wide_cols")
        default = db_discover_enums("wide", connection=conn_name)
        assert last in [s["column"] for s in default["skipped"]]
        targeted = db_discover_enums("wide", connection=conn_name, columns=[last])
        assert [c["column"] for c in targeted["candidates"]] == [last]

    def test_suggested_action_escapes_quotes_in_values(self, custom_conn) -> None:
        def build(db_path: Path) -> None:
            conn = sqlite3.connect(str(db_path))
            conn.execute("CREATE TABLE q (kind TEXT)")
            for v in ('a"b', 'a"b', "plain", "plain"):
                conn.execute("INSERT INTO q VALUES (?)", (v,))
            conn.commit()
            conn.close()

        conn_name = custom_conn(build, name="quotes")
        sa = db_discover_enums("q", connection=conn_name)["candidates"][0]["suggested_action"]
        template = sa.split("enum_values=", 1)[1].rstrip(")")
        import json as _json
        assert set(_json.loads(template)) == {'a"b', "plain"}

    def test_unsafe_table_name_rejected_before_sql(self, demo_conn: str, monkeypatch) -> None:
        """On a schema-cache hit db_get_schema never re-validates the table name,
        so discovery must validate it itself before interpolating."""
        import amnesic.tools.discovery as disc

        monkeypatch.setattr(
            disc, "db_get_schema",
            lambda table, connection: {"table": "x", "columns": [
                {"column_name": "status", "data_type": "INTEGER", "max_length": None}]},
        )
        ran = []
        monkeypatch.setattr(disc, "run_readonly", lambda *a, **k: ran.append(a) or ([], False))
        with pytest.raises(ValueError, match="Invalid table"):
            disc.db_discover_enums('orders" --', connection=demo_conn)
        assert ran == []
