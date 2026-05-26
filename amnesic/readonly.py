"""
Read-only SQL enforcement.

Ported verbatim from coding-agent/tools/db_query.py with driver-agnostic
public API.  Two exported functions:

  assert_readonly(sql)            — raises ValueError on any write statement
  validate_identifier(value, label) — raises ValueError on unsafe identifiers
"""

import re

# ---------------------------------------------------------------------------
# Identifier validation
# ---------------------------------------------------------------------------

_IDENT_RE = re.compile(r"^[A-Za-z0-9_]+$")


def validate_identifier(value: str, label: str) -> str:
    """
    Reject any identifier that isn't a plain alphanumeric/underscore name.

    Returns the value unchanged so callers can use: name = validate_identifier(raw, 'table').

    Raises:
        ValueError: if the value contains characters outside [A-Za-z0-9_].
    """
    if not _IDENT_RE.match(value):
        raise ValueError(
            f"Invalid {label} '{value}': only letters, digits, and underscores are allowed."
        )
    return value


# ---------------------------------------------------------------------------
# Read-only enforcement
# ---------------------------------------------------------------------------

_WRITE_KEYWORDS = {
    "INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE",
    "ALTER", "CREATE", "EXEC", "EXECUTE", "MERGE",
    "BULK", "GRANT", "REVOKE", "DENY",
}


def assert_readonly(sql: str) -> None:
    """
    Raise ValueError if sql contains any write/DDL/DCL statement.

    Strategy:
      1. Strip line comments (-- ...) and block comments (/* ... */)
      2. Split on semicolons — each statement is checked independently
      3. Each non-empty statement must start with SELECT or WITH
      4. WITH statements must not contain DML after the first AS keyword
         (catches: WITH cte AS (SELECT ...) UPDATE ...)

    Raises:
        ValueError: if any write or DDL statement is detected.
    """
    # Remove block comments /* ... */
    clean = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    # Remove line comments -- ...
    clean = re.sub(r"--[^\n]*", " ", clean)

    statements = [s.strip() for s in clean.split(";") if s.strip()]
    if not statements:
        raise ValueError("Empty query.")

    for stmt in statements:
        tokens = stmt.split()
        if not tokens:
            continue
        first_token = tokens[0].upper()
        if first_token in _WRITE_KEYWORDS:
            raise ValueError(
                f"Write operation not allowed: {first_token}. Only SELECT queries are permitted."
            )
        if first_token not in ("SELECT", "WITH"):
            raise ValueError(
                f"Statement type '{first_token}' is not allowed. Only SELECT/WITH queries are permitted."
            )

        # Reject INTO outside of parentheses.
        # Catches: SELECT INTO OUTFILE/DUMPFILE (MySQL filesystem write) and
        #          SELECT * INTO new_table (MSSQL/Postgres table creation).
        # INTO inside a subquery (depth > 0) is not a write operation.
        upper_tokens = [t.upper() for t in tokens]
        depth = 0
        for tok in upper_tokens:
            depth += tok.count("(") - tok.count(")")
            if tok == "INTO" and depth == 0:
                raise ValueError(
                    f"INTO clause not allowed: SELECT INTO OUTFILE/DUMPFILE/<table> can write "
                    f"to the database or filesystem and is rejected by amnesic's read-only enforcement."
                )

        # For CTEs (WITH ...), scan tokens after the first AS for DML keywords.
        # Catches: WITH x AS (SELECT ...) UPDATE ...
        if first_token == "WITH":
            upper_tokens = [t.upper() for t in tokens]
            try:
                as_idx = upper_tokens.index("AS")
            except ValueError:
                as_idx = 0
            for tok in upper_tokens[as_idx:]:
                if tok in _WRITE_KEYWORDS:
                    raise ValueError(
                        f"Write operation inside CTE not allowed: {tok}. Only SELECT queries are permitted."
                    )
