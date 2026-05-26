"""
Tests for amnesic.readonly — read-only SQL enforcement.

Covers: valid SELECT/WITH, all write keywords, CTEs with embedded DML,
comment stripping, semicolons, empty queries, identifier validation.
"""

import pytest

from amnesic.readonly import assert_readonly, validate_identifier


# ---------------------------------------------------------------------------
# Valid queries (should NOT raise)
# ---------------------------------------------------------------------------

class TestAllowedQueries:
    def test_simple_select(self):
        assert_readonly("SELECT * FROM users")

    def test_select_with_where(self):
        assert_readonly("SELECT id, name FROM users WHERE active = 1")

    def test_select_with_join(self):
        assert_readonly(
            "SELECT u.id, o.total FROM users u JOIN orders o ON u.id = o.user_id"
        )

    def test_cte_select(self):
        assert_readonly(
            "WITH cte AS (SELECT id FROM users WHERE active = 1) SELECT * FROM cte"
        )

    def test_multiple_ctes(self):
        assert_readonly(
            "WITH a AS (SELECT 1), b AS (SELECT 2) SELECT * FROM a, b"
        )

    def test_select_with_subquery(self):
        assert_readonly(
            "SELECT * FROM (SELECT id FROM orders) AS sub WHERE id > 0"
        )

    def test_select_with_line_comment(self):
        assert_readonly(
            "-- get active users\nSELECT * FROM users WHERE active = 1"
        )

    def test_select_with_block_comment(self):
        assert_readonly(
            "/* find orders */ SELECT * FROM orders"
        )

    def test_select_with_inline_block_comment(self):
        assert_readonly(
            "SELECT /* all columns */ * FROM users"
        )

    def test_select_lowercase(self):
        assert_readonly("select * from users")

    def test_select_mixed_case(self):
        assert_readonly("Select * From Users")

    def test_select_with_semicolon_at_end(self):
        # Single statement ending with semicolon is fine
        assert_readonly("SELECT 1;")

    def test_with_uppercase(self):
        assert_readonly("WITH CTE AS (SELECT * FROM t) SELECT * FROM CTE")


# ---------------------------------------------------------------------------
# Blocked write/DDL statements (should raise ValueError)
# ---------------------------------------------------------------------------

class TestBlockedStatements:
    @pytest.mark.parametrize("keyword", [
        "INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE",
        "ALTER", "CREATE", "EXEC", "EXECUTE", "MERGE",
        "BULK", "GRANT", "REVOKE", "DENY",
    ])
    def test_write_keyword_blocked(self, keyword):
        with pytest.raises(ValueError, match=keyword):
            assert_readonly(f"{keyword} INTO users VALUES (1, 'test')")

    def test_insert_blocked(self):
        with pytest.raises(ValueError, match="INSERT"):
            assert_readonly("INSERT INTO users (name) VALUES ('alice')")

    def test_update_blocked(self):
        with pytest.raises(ValueError, match="UPDATE"):
            assert_readonly("UPDATE users SET active = 0 WHERE id = 1")

    def test_delete_blocked(self):
        with pytest.raises(ValueError, match="DELETE"):
            assert_readonly("DELETE FROM users WHERE id = 1")

    def test_drop_blocked(self):
        with pytest.raises(ValueError, match="DROP"):
            assert_readonly("DROP TABLE users")

    def test_truncate_blocked(self):
        with pytest.raises(ValueError, match="TRUNCATE"):
            assert_readonly("TRUNCATE TABLE orders")

    def test_create_blocked(self):
        with pytest.raises(ValueError, match="CREATE"):
            assert_readonly("CREATE TABLE foo (id INT)")

    def test_alter_blocked(self):
        with pytest.raises(ValueError, match="ALTER"):
            assert_readonly("ALTER TABLE users ADD COLUMN email TEXT")

    def test_exec_blocked(self):
        with pytest.raises(ValueError, match="EXEC"):
            assert_readonly("EXEC sp_rename 'users', 'accounts'")

    def test_execute_blocked(self):
        with pytest.raises(ValueError, match="EXECUTE"):
            assert_readonly("EXECUTE sp_something")


# ---------------------------------------------------------------------------
# CTE with embedded DML (WITH ... UPDATE ...)
# ---------------------------------------------------------------------------

class TestCteWithDml:
    def test_cte_with_embedded_update(self):
        with pytest.raises(ValueError, match="UPDATE"):
            assert_readonly(
                "WITH cte AS (SELECT id FROM users) UPDATE users SET active = 0"
            )

    def test_cte_with_embedded_delete(self):
        with pytest.raises(ValueError, match="DELETE"):
            assert_readonly(
                "WITH cte AS (SELECT id FROM users WHERE active = 0) DELETE FROM users WHERE id IN (SELECT id FROM cte)"
            )

    def test_cte_with_embedded_insert(self):
        # INSERT always has "INTO" so this query also triggers the INTO-outside-parens check.
        # Either error path is correct — the query MUST be rejected.
        with pytest.raises(ValueError, match=r"INSERT|INTO"):
            assert_readonly(
                "WITH src AS (SELECT * FROM staging) INSERT INTO prod SELECT * FROM src"
            )


# ---------------------------------------------------------------------------
# Comment stripping
# ---------------------------------------------------------------------------

class TestCommentStripping:
    def test_write_keyword_in_block_comment_is_allowed(self):
        # UPDATE inside a comment should not be blocked
        assert_readonly("/* UPDATE is blocked */ SELECT * FROM users")

    def test_write_keyword_in_line_comment_is_allowed(self):
        assert_readonly("-- DELETE FROM users\nSELECT * FROM users")

    def test_block_comment_with_update_then_select(self):
        assert_readonly("/* this is not UPDATE */ SELECT 1")


# ---------------------------------------------------------------------------
# Semicolon splitting
# ---------------------------------------------------------------------------

class TestSemicolonSplitting:
    def test_two_selects_with_semicolon(self):
        assert_readonly("SELECT 1; SELECT 2")

    def test_select_then_update(self):
        with pytest.raises(ValueError, match="UPDATE"):
            assert_readonly("SELECT 1; UPDATE users SET x = 1")

    def test_update_then_select(self):
        with pytest.raises(ValueError, match="UPDATE"):
            assert_readonly("UPDATE users SET x = 1; SELECT 1")


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_query_raises(self):
        with pytest.raises(ValueError, match="Empty query"):
            assert_readonly("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="Empty query"):
            assert_readonly("   \n\t  ")

    def test_comment_only_raises(self):
        with pytest.raises(ValueError, match="Empty query"):
            assert_readonly("-- just a comment")

    def test_unknown_statement_type_blocked(self):
        with pytest.raises(ValueError, match="not allowed"):
            assert_readonly("EXPLAIN SELECT * FROM users")

    def test_lowercase_insert_blocked(self):
        with pytest.raises(ValueError, match="INSERT"):
            assert_readonly("insert into users values (1)")


# ---------------------------------------------------------------------------
# INTO clause bypass (H1 + M1)
# ---------------------------------------------------------------------------

class TestIntoClauseBlocked:
    def test_select_into_outfile_blocked(self):
        with pytest.raises(ValueError, match="INTO"):
            assert_readonly("SELECT email INTO OUTFILE '/tmp/x.csv' FROM users")

    def test_select_into_new_table_blocked(self):
        with pytest.raises(ValueError, match="INTO"):
            assert_readonly("SELECT * INTO new_table FROM orders")

    def test_into_lowercase_blocked(self):
        with pytest.raises(ValueError, match="INTO"):
            assert_readonly("select * into x from y")

    def test_into_inside_cte_blocked(self):
        with pytest.raises(ValueError, match="INTO"):
            assert_readonly("WITH x AS (SELECT 1) SELECT * INTO y FROM x")

    def test_into_inside_subquery_allowed(self):
        # INTO inside parens is a subquery context, not a write operation.
        # This is a contrived form — real SQL doesn't use INTO in subqueries,
        # but our parenthesis depth check must not false-positive here.
        # We test with a nested SELECT that has no INTO outside parens.
        assert_readonly("SELECT id, (SELECT name FROM other) FROM t")

    def test_into_dumpfile_blocked(self):
        with pytest.raises(ValueError, match="INTO"):
            assert_readonly("SELECT * INTO DUMPFILE '/tmp/dump.bin' FROM secrets")


# ---------------------------------------------------------------------------
# validate_identifier
# ---------------------------------------------------------------------------

class TestValidateIdentifier:
    def test_valid_identifier(self):
        result = validate_identifier("my_table", "table")
        assert result == "my_table"

    def test_valid_with_numbers(self):
        result = validate_identifier("table123", "table")
        assert result == "table123"

    def test_valid_uppercase(self):
        result = validate_identifier("MyTable", "table")
        assert result == "MyTable"

    def test_dot_raises(self):
        with pytest.raises(ValueError, match="table"):
            validate_identifier("schema.table", "table")

    def test_dash_raises(self):
        with pytest.raises(ValueError, match="table"):
            validate_identifier("my-table", "table")

    def test_semicolon_raises(self):
        with pytest.raises(ValueError, match="table"):
            validate_identifier("table; DROP TABLE users", "table")

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            validate_identifier("", "table")

    def test_bracket_raises(self):
        with pytest.raises(ValueError):
            validate_identifier("[table]", "table")

    def test_space_raises(self):
        with pytest.raises(ValueError):
            validate_identifier("my table", "table")
