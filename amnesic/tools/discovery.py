"""
db_discover_enums — propose enum values for low-cardinality columns.

Scans candidate columns (integer types, short strings) for distinct values and
returns proposals. Never writes to the knowledge store — the agent (or human)
supplies meaning; this tool supplies the values.

Design rationale for the sampling approach:
  A full GROUP BY on a 100M-row status column is cheap — O(n) scan, tiny result.
  But on wide tables with many columns, running N full scans in one call adds up.
  The LIMIT-bounded subquery approach samples at most `sample_rows` rows cheaply
  (storage engine stops after the first N rows), then groups only those. The trade-
  off: rare values in a large table may be missed. Users can re-run with
  sample_rows=0 for a full scan when completeness matters.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal

from amnesic.config import load_config, resolve_connection
from amnesic.readonly import validate_identifier
from amnesic.tools.query import run_readonly
from amnesic.tools.schema import db_get_schema, normalize_fqn


# ---------------------------------------------------------------------------
# Type classification
# ---------------------------------------------------------------------------

_INTEGER_TYPES = frozenset({
    "int", "integer", "smallint", "tinyint", "bigint", "mediumint",
})

_STRING_BASE_TYPES = frozenset({
    "char", "varchar", "nchar", "nvarchar", "text",
    "character varying", "character", "ntext", "tinytext", "mediumtext",
})

# Exclude these explicitly — boolean/bit columns are never enum candidates here.
_SKIP_TYPES = frozenset({
    "boolean", "bool", "bit",
})

# Hard cap: probe at most this many columns per call, regardless of how many
# candidates the schema has. Report the rest as skipped so callers know.
_COLUMN_PROBE_CAP = 20

def _looks_like_key(col_name: str) -> bool:
    """`id`, `customer_id`, `orderId`, `CustomerID` are keys, not enums.

    A small parent table makes a foreign key look low-cardinality, but
    labelling key values is meaningless — relationships are
    db_discover_relationships' job.
    """
    if col_name.lower() == "id" or col_name.lower().endswith("_id"):
        return True
    # camelCase / PascalCase suffix: orderId, CustomerID (but not "paid", "valid")
    return bool(re.search(r"[a-z0-9](Id|ID)$", col_name))


def _normalize_type(data_type: str) -> tuple[str, int | None]:
    """
    Return (base_type_lower, parsed_max_length_from_type_name).

    Handles both bare names ("varchar") and size-decorated names ("varchar(32)").
    For drivers that return max_length separately (Postgres/MSSQL/MySQL), the
    caller should prefer the schema column's max_length over parsed_max_length.
    For SQLite, which packs the size into the type name, parsed_max_length is the
    only source available.
    """
    base = data_type.split("(")[0].strip().lower()
    parsed_len: int | None = None
    if "(" in data_type and ")" in data_type:
        try:
            parsed_len = int(data_type.split("(")[1].split(")")[0].strip())
        except (ValueError, IndexError):
            pass
    return base, parsed_len


def _is_enum_candidate(data_type: str, max_length) -> bool:
    """
    Return True if this column type should be probed for enum values.

    Integer types: always a candidate (no length threshold).
    String types: candidate only when max_length is None (unbounded, e.g. TEXT)
      or <= 32 — long varchars are almost certainly free-text, not enums.
    Boolean/bit: never a candidate (would always look like a 2-value enum but
      isn't useful to annotate).
    """
    base, parsed_len = _normalize_type(data_type)

    if base in _SKIP_TYPES:
        return False

    if base in _INTEGER_TYPES:
        return True

    if base in _STRING_BASE_TYPES:
        # Use the schema-supplied max_length first; fall back to what's encoded
        # in the type name (SQLite declares VARCHAR(32) in the type column).
        effective_len = max_length if max_length is not None else parsed_len
        return effective_len is None or effective_len <= 32

    return False


# ---------------------------------------------------------------------------
# SQL generation
# ---------------------------------------------------------------------------

def _quote_identifier(name: str, driver: str) -> str:
    """Quote a validated column or table identifier per dialect."""
    if driver == "mysql":
        return f"`{name}`"
    if driver == "mssql":
        return f"[{name}]"
    # sqlite, postgres, postgresql — ANSI double-quotes
    return f'"{name}"'


def _table_ref(driver: str, db: str, schema: str, table_name: str) -> str:
    """
    Build the qualified table reference for the FROM clause.

    Matches the identifier conventions in schema.py's _fetch_schema_from_db:
      sqlite:     table_name
      postgres:   "schema"."table_name"
      mysql:      `db`.`table_name`
      mssql:      [db].[schema].[table_name]
    """
    if driver == "sqlite":
        return table_name
    if driver in ("postgres", "postgresql"):
        return f'"{schema}"."{table_name}"'
    if driver == "mysql":
        return f"`{db}`.`{table_name}`"
    if driver == "mssql":
        return f"[{db}].[{schema}].[{table_name}]"
    # Fallback for unknown drivers
    return table_name


def build_enum_sql(
    tbl_ref: str,
    col_quoted: str,
    driver: str,
    sample_rows: int,
    cap: int,
) -> str:
    """
    Build the enum-discovery SQL for one column.

    Exported for direct unit testing of SQL shape (quoting, TOP vs LIMIT) without
    a live database. All integer arguments (sample_rows, cap) are caller-validated
    ints; they are safe to interpolate directly.

    sample_rows=0 means a full scan (no LIMIT on the inner subquery).
    cap should be max_distinct+1 so a single overflow row is detectable.
    """
    if driver == "mssql":
        if sample_rows > 0:
            inner = (
                f"SELECT TOP ({sample_rows}) {col_quoted} AS v "
                f"FROM {tbl_ref} WHERE {col_quoted} IS NOT NULL"
            )
        else:
            inner = (
                f"SELECT {col_quoted} AS v "
                f"FROM {tbl_ref} WHERE {col_quoted} IS NOT NULL"
            )
        return (
            f"SELECT TOP ({cap}) v, COUNT(*) AS n "
            f"FROM ({inner}) s GROUP BY v ORDER BY n DESC"
        )
    else:
        # sqlite, postgres, mysql
        if sample_rows > 0:
            inner = (
                f"SELECT {col_quoted} AS v "
                f"FROM {tbl_ref} WHERE {col_quoted} IS NOT NULL LIMIT {sample_rows}"
            )
        else:
            inner = (
                f"SELECT {col_quoted} AS v "
                f"FROM {tbl_ref} WHERE {col_quoted} IS NOT NULL"
            )
        return (
            f"SELECT v, COUNT(*) AS n "
            f"FROM ({inner}) s GROUP BY v ORDER BY n DESC LIMIT {cap}"
        )


# ---------------------------------------------------------------------------
# Value serialisation
# ---------------------------------------------------------------------------

def _safe_value(v) -> str | int | float | bool | None:
    """
    Convert a database value to a JSON-serialisable scalar.

    Decimal: convert integer-valued ones to int, others to string.
    bytes: hex-encode (unusual in enum columns but defensive).
    Everything else: coerce to str as a last resort.
    """
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return v
    if isinstance(v, Decimal):
        try:
            int_val = int(v)
            if Decimal(int_val) == v:
                return int_val
        except (ValueError, OverflowError):
            pass
        return str(v)
    if isinstance(v, bytes):
        return v.hex()
    return str(v)




# ---------------------------------------------------------------------------
# Public tool function
# ---------------------------------------------------------------------------

def db_discover_enums(
    table: str,
    connection: str | None = None,
    max_distinct: int = 25,
    sample_rows: int = 10000,
    force: bool = False,
    columns: list[str] | None = None,
) -> dict:
    """
    Propose enum values for low-cardinality columns in a table.

    Scans integer and short-string columns (max_length <= 32) for distinct values,
    bounded by sample_rows. Returns proposals only — never writes to the knowledge
    store. Columns already annotated with enum_values are skipped unless force=True.

    Use db_annotate to persist labels once confirmed with the user.

    Args:
        table:        Table name (required), optionally schema-qualified.
        connection:   Connection name. Defaults to first defined.
        max_distinct: Columns with more than N distinct values are not enum
                      candidates and land in not_enum (default 25).
        sample_rows:  Probe at most this many rows per column (default 10000).
                      Use 0 for a full scan — accurate but slower on large tables.
        force:        Re-probe columns that already have enum_values annotations
                      (default False — skip them).
        columns:      Probe only these columns (case-insensitive). Use it to reach
                      columns past the per-call cap, or to target one column.

    Returns:
        {table, connection, candidates, not_enum, skipped, sampled, sample_rows, note}
        candidates: [{column, data_type, distinct_count, values: [{value, count}],
                      suggested_action}]
        suggested_action: a db_annotate() call template with discovered values as
                          string keys and "<meaning>" placeholders.
    """
    connections = load_config()
    conn_cfg = resolve_connection(connection, connections)
    driver = conn_cfg.driver.lower()

    # Reuse db_get_schema to populate/reuse the schema cache and get columns with
    # any existing annotations already merged in (enum_values, etc.).
    schema_result = db_get_schema(table=table, connection=conn_cfg.name)
    fqn: str = schema_result["table"]
    schema_columns: list[dict] = schema_result.get("columns", [])

    # Build the driver-specific table reference identifiers.
    _, db_name, schema_name, table_name = normalize_fqn(table, conn_cfg)
    # Validate here rather than relying on db_get_schema: on a cache hit it never
    # re-validates, and these parts are interpolated into SQL below.
    validate_identifier(table_name, "table")
    if db_name:
        validate_identifier(db_name, "database")
    if schema_name:
        validate_identifier(schema_name, "schema")
    tbl_ref = _table_ref(driver, db_name, schema_name, table_name)
    wanted = {c.lower() for c in columns} if columns else None

    candidates_out: list[dict] = []
    not_enum: list[str] = []
    skipped: list[dict] = []
    to_probe: list[dict] = []

    # First pass: classify columns into to_probe or skipped.
    for col in schema_columns:
        col_name: str = col.get("column_name", "")
        if wanted is not None and col_name.lower() not in wanted:
            continue
        data_type: str = col.get("data_type", "")
        max_length = col.get("max_length")

        # Identifier safety — reject anything that can't be safely quoted.
        try:
            validate_identifier(col_name, "column")
        except ValueError:
            skipped.append({
                "column": col_name,
                "reason": "unsafe identifier — contains characters outside [A-Za-z0-9_]; skipped to prevent SQL injection",
            })
            continue

        # Type filter
        if not _is_enum_candidate(data_type, max_length):
            continue

        if _looks_like_key(col_name):
            skipped.append({
                "column": col_name,
                "reason": "looks like a key column; use db_discover_relationships for keys",
            })
            continue

        # Already-annotated guard
        if not force and col.get("enum_values"):
            skipped.append({
                "column": col_name,
                "reason": "already annotated with enum_values; pass force=True to re-probe",
            })
            continue

        to_probe.append(col)

    # Hard cap: at most _COLUMN_PROBE_CAP columns probed per call.
    if len(to_probe) > _COLUMN_PROBE_CAP:
        over = to_probe[_COLUMN_PROBE_CAP:]
        to_probe = to_probe[:_COLUMN_PROBE_CAP]
        for col in over:
            skipped.append({
                "column": col["column_name"],
                "reason": (
                    f"probe cap of {_COLUMN_PROBE_CAP} columns per call reached; "
                    "re-run with columns=[...] to probe it"
                ),
            })

    # Second pass: probe each candidate column.
    cap = max_distinct + 1  # fetch one extra so overflow is detectable

    for col in to_probe:
        col_name = col["column_name"]
        data_type = col.get("data_type", "")
        col_quoted = _quote_identifier(col_name, driver)

        sql = build_enum_sql(
            tbl_ref=tbl_ref,
            col_quoted=col_quoted,
            driver=driver,
            sample_rows=int(sample_rows),
            cap=int(cap),
        )

        # run_readonly enforces assert_readonly + a rolled-back transaction.
        rows, _ = run_readonly(conn_cfg, sql)

        if not rows:
            # All-NULL or truly empty table — nothing to propose.
            skipped.append({
                "column": col_name,
                "reason": "no non-null values found",
            })
            continue

        distinct_count = len(rows)

        if distinct_count > max_distinct:
            # More than max_distinct groups returned — column is not an enum.
            not_enum.append(col_name)
            continue

        # An enum's values repeat. If the average value appears fewer than
        # twice, the column is mostly unique (names, dates, prices in a small
        # table) and merely looks low-cardinality because the table is small.
        rows_scanned = sum(int(row.get("n", 0)) for row in rows)
        if distinct_count * 2 > rows_scanned:
            not_enum.append(col_name)
            continue

        # Build value list (already sorted COUNT DESC by the query).
        values = [
            {"value": _safe_value(row.get("v")), "count": int(row.get("n", 0))}
            for row in rows
        ]

        # suggested_action: db_annotate template with actual values as string keys.
        # json.dumps escapes quotes/backslashes in values, and stringifies keys
        # to match db_annotate's {"1": "label"} format.
        enum_template = json.dumps({str(rv["value"]): "<meaning>" for rv in values})
        suggested = (
            f'db_annotate(table="{fqn}", column="{col_name}", '
            f"enum_values={enum_template})"
        )

        candidates_out.append({
            "column": col_name,
            "data_type": data_type,
            "distinct_count": distinct_count,
            "values": values,
            "suggested_action": suggested,
        })

    sampled = sample_rows > 0
    note = (
        "Values are proposals. Infer each label from context, confirm with the user, "
        "then save with db_annotate."
    )
    if sampled:
        note += (
            f" Sample of up to {sample_rows} rows used — rare values may be missing. "
            "Use sample_rows=0 for a full scan."
        )

    return {
        "table": fqn,
        "connection": conn_cfg.name,
        "candidates": candidates_out,
        "not_enum": not_enum,
        "skipped": skipped,
        "sampled": sampled,
        "sample_rows": sample_rows,
        "note": note,
    }
