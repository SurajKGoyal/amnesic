"""
Tests for db_query — specifically the truncated flag fix (C4).

Uses SQLite in-memory via a temporary connections.toml so no real DB is needed.
"""

import os
import sqlite3
import stat
from pathlib import Path

import pytest

from amnesic.tools.query import db_query


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def sqlite_conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """
    Create a temporary SQLite database with 10 rows and a connections.toml
    pointing at it. Returns the connection name.
    """
    db_file = tmp_path / "testdb.sqlite3"

    # Seed the database
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE nums (id INTEGER PRIMARY KEY, val TEXT)")
    for i in range(1, 11):
        conn.execute("INSERT INTO nums VALUES (?, ?)", (i, f"row{i}"))
    conn.commit()
    conn.close()

    # Write connections.toml
    toml_content = f"""
[connections.test]
driver = "sqlite"
database = "{db_file}"
"""
    toml_path = tmp_path / "connections.toml"
    toml_path.write_text(toml_content)

    monkeypatch.setenv("AMNESIC_CONFIG", str(toml_path))
    monkeypatch.setenv("AMNESIC_HOME", str(tmp_path))

    return "test"


# ---------------------------------------------------------------------------
# truncated flag tests (C4)
# ---------------------------------------------------------------------------

class TestTruncatedFlag:
    def test_exact_max_rows_not_truncated(self, sqlite_conn):
        """
        Querying exactly N rows with max_rows=N must report truncated=False.
        Before the fix, len(rows) == max_rows was always True here.
        """
        result = db_query(
            "SELECT * FROM nums ORDER BY id LIMIT 5",
            connection=sqlite_conn,
            max_rows=5,
        )
        assert result["row_count"] == 5
        assert result["truncated"] is False

    def test_fewer_than_max_rows_not_truncated(self, sqlite_conn):
        """Fewer rows than max_rows — never truncated."""
        result = db_query(
            "SELECT * FROM nums ORDER BY id LIMIT 3",
            connection=sqlite_conn,
            max_rows=10,
        )
        assert result["row_count"] == 3
        assert result["truncated"] is False

    def test_more_rows_than_max_is_truncated(self, sqlite_conn):
        """Table has 10 rows, max_rows=5 — truncated=True and exactly 5 rows returned."""
        result = db_query(
            "SELECT * FROM nums ORDER BY id",
            connection=sqlite_conn,
            max_rows=5,
        )
        assert result["row_count"] == 5
        assert result["truncated"] is True

    def test_all_rows_fit_not_truncated(self, sqlite_conn):
        """All 10 rows fit in max_rows=10 — not truncated."""
        result = db_query(
            "SELECT * FROM nums ORDER BY id",
            connection=sqlite_conn,
            max_rows=10,
        )
        assert result["row_count"] == 10
        assert result["truncated"] is False

    def test_all_rows_fit_max_rows_larger(self, sqlite_conn):
        """All 10 rows, max_rows=20 — not truncated."""
        result = db_query(
            "SELECT * FROM nums ORDER BY id",
            connection=sqlite_conn,
            max_rows=20,
        )
        assert result["row_count"] == 10
        assert result["truncated"] is False

    def test_rows_are_correct_dicts(self, sqlite_conn):
        """Returned rows are proper dicts with expected keys."""
        result = db_query(
            "SELECT * FROM nums ORDER BY id LIMIT 2",
            connection=sqlite_conn,
            max_rows=10,
        )
        assert len(result["rows"]) == 2
        assert "id" in result["rows"][0]
        assert "val" in result["rows"][0]
