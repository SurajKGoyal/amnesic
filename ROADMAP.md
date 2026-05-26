# amnesic — Roadmap

A living document of what's coming. The order reflects current priority — items higher up are likely to ship sooner. Feedback, requests, and PRs welcome via [GitHub Issues](https://github.com/SurajKGoyal/amnesic/issues).

---

## v0.1.0 — Foundation ✅ *Shipped*

The minimum viable amnesic.

- 8 MCP tools: `db_query`, `db_get_schema`, `db_list_tables`, `db_annotate`, `db_sync_knowledge`, `db_list_connections`, `db_discover_relationships`, `db_get_relationships`
- 4 drivers: PostgreSQL, MySQL, MSSQL, SQLite
- SQLite-backed knowledge store: schema cache + annotations + FK graph
- Named connection profiles with `[product.env]` hierarchy
- Two-layer read-only enforcement
- Optional SSH tunnel per connection
- Works with Claude Code, Claude Desktop, Cursor, VS Code, Windsurf

---

## v0.2.0 — Knowledge Lifecycle Management 🔜 *Next*

When developers rotate projects, annotations shouldn't die with their access. v0.2 makes knowledge portable and handles schema drift gracefully.

**New MCP tools**
- `db_deprecate(table, connection, column=None, reason="")` — soft-delete: mark a table or column as deprecated. AI sees deprecation warnings on future calls.
- `db_detect_drift(connection)` — compare annotations against current schema; surface orphaned annotations (column was annotated but no longer exists) and undocumented tables.

**New CLI commands**
- `amnesic export <connection> [--output file.json]` — dump all annotations + relationships as portable JSON
- `amnesic import <connection> <file.json>` — bulk import (the receiving teammate's flow)
- `amnesic remove <connection>` — drop from config, optionally delete knowledge file
- `amnesic clear <connection>` — wipe knowledge for a connection, keep config entry

**Store changes**
- Backwards-compatible migration: add `deprecated_at` + `deprecated_reason` columns to `table_knowledge` and `column_knowledge`. Existing knowledge files auto-upgrade on first load.

---

## v0.3.0 — Query Intelligence

Make Claude smarter about what it just queried.

- `db_explain(sql, connection)` — return the DB's actual query plan (EXPLAIN for Postgres/MySQL/SQLite, SHOWPLAN_XML for MSSQL)
- `db_query_history(connection, limit=20)` — list recent queries run via amnesic (local, opt-in)
- Truncation insights — when a query result hits `max_rows`, return aggregate stats about what was dropped (count, min/max of common columns)
- Suggested indexes from observed query patterns

---

## v0.4.0 — Team & Sharing

For when more than one developer needs the same knowledge.

- `amnesic push <connection>` / `amnesic pull <connection>` — back the knowledge file to a Git repo or S3 bucket
- Knowledge file versioning — track who annotated what, when
- Conflict resolution for concurrent annotations (last-writer-wins by default, with `--strategy=merge` option)
- Optional remote knowledge backend (HTTP API spec for teams running their own)

---

## v0.5.0 — Advanced Discovery

Auto-populate annotations where signals are clear enough.

- **Enum auto-discovery**: when a column has < N distinct integer or short-string values, query for them and suggest `db_annotate` calls
- **Soft-FK inference**: detect implicit FK relationships from column naming patterns (`*_id` columns matching primary keys) when explicit constraints are absent — common in legacy MSSQL schemas
- **Common JOIN pattern surfacing**: track which tables get JOINed together most often, suggest those edges in the relationship graph

---

## v0.6.0 — Observability

Help the human understand what the AI is doing with their DB.

- Built-in query logging (opt-in, redacted) — see every query the AI ran, when, against which connection
- Schema change detection — diff current schema against last cached snapshot; flag drift
- "Hot tables" report — most-queried tables in last N days

---

## v1.0.0 — Stable API

The signal to the world: "you can build on this."

- API stability commitment for all 8+ MCP tools
- Comprehensive docs site (likely MkDocs or similar)
- Plugin/extension system for custom drivers (DuckDB, ClickHouse, Snowflake)
- First-class async support throughout
- Performance SLA: schema fetch < 100ms p99, annotation lookup < 1ms

---

## Long-term / No Timeline

Ideas that need more thought or community signal before committing.

- **Cloud-native warehouses**: ClickHouse, Snowflake, BigQuery, DuckDB as first-class drivers
- **NoSQL**: MongoDB semantic annotations (collections + field-level metadata)
- **Dry-run mode**: explain what a query will do without executing it
- **Cost awareness**: warn before running expensive queries on BigQuery / Snowflake (estimated bytes scanned, $)
- **IDE integration**: direct DBeaver / DataGrip plugins that read amnesic annotations
- **Knowledge graph search**: vector embeddings over table descriptions for semantic "find tables related to X" queries
- **Audit log**: append-only log of every mutation to the knowledge store (for compliance-sensitive orgs)

---

## How to Contribute

- Open an issue with a use case before writing code — saves rework
- For new drivers, follow the structure in `amnesic/drivers.py` and `amnesic/tools/schema.py`
- Tests live in `tests/`. New tools must include unit tests.
- Run `pytest tests/` before opening a PR.

Anything missing from this roadmap that you'd find useful? [Open an issue.](https://github.com/SurajKGoyal/amnesic/issues/new)
