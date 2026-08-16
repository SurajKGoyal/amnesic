# amnesic — Roadmap

A living document of what's coming. The order reflects current priority — items higher up are likely to ship sooner. Feedback, requests, and PRs welcome via [GitHub Issues](https://github.com/SurajKGoyal/amnesic/issues).

> **🙌 Want to build one of these?** Every unshipped item below is filed as an issue with the design worked out — problem, proposed shape, files to touch, how to test. **Pick one and open a PR; you don't need to ask first.** Just comment on the issue so two people don't build the same thing. Start with [`good first issue`](https://github.com/SurajKGoyal/amnesic/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) or browse [all open issues](https://github.com/SurajKGoyal/amnesic/issues).

---

## Where this is going

amnesic is not trying to be a better query executor. [DBHub](https://github.com/bytebase/dbhub), [Postgres MCP Pro](https://github.com/crystaldba/postgres-mcp) and [Google's MCP Toolbox](https://github.com/googleapis/mcp-toolbox) are good at running SQL, reading execution plans, and tuning indexes, and competing with them on that ground would produce a worse version of a tool that already exists.

What none of them can do is remember what your data *means* — they're stateless by design. What the enterprise catalogs (DataHub, Atlan, Cube) can do, they do at the cost of a whole metadata platform. amnesic's lane is the gap between: **catalog-grade semantic memory at query-executor setup cost.**

Two consequences drive the roadmap:

1. **Persistent annotations alone aren't a moat** — any stateless server could bolt on a SQLite table in a weekend. Knowledge that *accumulates from use* is much harder to copy, and gets more valuable the longer you run it. That's why auto-discovery moved from v0.5 to v0.3.
2. **We still have to win the table stakes** — token discipline, schema completeness, freshness. Not to differentiate, but so nobody dismisses amnesic before reaching the part that's actually different.

Explicit non-goals: execution-plan analysis beyond a plain `EXPLAIN` passthrough, index tuning, database health diagnostics, lineage, and governance. Use a query executor or a catalog alongside amnesic — they compose fine.


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

## v0.1.5 — Search 🔍 *Shipped*

BM25-ranked full-text search over the knowledge layer via SQLite FTS5. Zero new dependencies.

- New tool: `db_search(query, connection, target, limit)`
- FTS5 virtual table per connection with auto-sync triggers
- Stemming and prefix matching for natural-language queries
- Backwards compatible: existing knowledge files auto-backfill the FTS index on next open

---

## v0.2.0 — Knowledge Lifecycle Management ✅ *Shipped*

When developers rotate projects, annotations shouldn't die with their access — and as schemas evolve, stale knowledge needs to be detected and retired. The lifecycle loop: **detect → deprecate → forget.**

**New MCP tools**
- `db_deprecate(table, connection, column=None, reason="", undo=False)` — soft-retire: flag a table or column annotation as stale without deleting it. Surfaced as a warning in `db_get_schema` and `db_search`. Reversible.
- `db_detect_drift(connection)` — audit annotations against the live schema; surface orphaned annotations (table/column no longer exists) and undocumented tables. Read-only.
- `db_forget(table, connection, column=None, cascade=False)` — hard-delete an annotation. Safe by default; `cascade=True` also removes a table's columns + relationships. Permanent.

**Store changes**
- Backwards-compatible migration: `deprecated_at` + `deprecated_reason` columns added to `table_knowledge` and `column_knowledge`. Existing knowledge files auto-upgrade on first load.

---

## v0.2.2 — Portable Knowledge & Connection DX ✅ *Shipped*

Round out the lifecycle line with CLI tooling for moving knowledge between machines, cleaning up connections, and a few DX niceties.

**New CLI commands**
- `amnesic export <conn> [-o file]` / `amnesic import <conn> <file>` — portable knowledge JSON (annotations + relationships, not the re-derivable schema cache) for team handoff or staging→prod promotion. Lossless round-trip incl. deprecation flags; `format_version` guard rejects unknown payloads.
- `amnesic remove <conn> [--delete-knowledge]` — drop a connection from `connections.toml` with surgical string edits (every other block kept byte-for-byte). Knowledge file kept unless asked.
- `amnesic clear <conn>` — wipe stored knowledge but keep the config entry.

**DX**
- `amnesic init` now writes a commented `.env.example` next to `connections.toml` (closes #5).
- One-line "a newer amnesic is on PyPI" notice on CLI commands — 24h cached, opt-out via `AMNESIC_NO_UPDATE_CHECK`, fail-silent, and **never** runs in MCP-server mode.
- Local commands (`export`/`import`/`clear`/`remove`) resolve connections by name only — a broken sibling connection's missing secret can't block them.

---

## v0.3.0 — Earn the Memory 🚧 *Next*

The headline: **knowledge that accumulates without anyone typing it.** Today every annotation is hand-written, which means amnesic is only as good as the discipline of the person using it. These three items let the first contact with a database produce knowledge on its own — and they're specifically strongest on the legacy schemas where live introspection returns nothing useful.

**Auto-accumulating knowledge**

- **Enum auto-discovery** ([#13](https://github.com/SurajKGoyal/amnesic/issues/13)) — when a column has fewer than N distinct integer or short-string values, query for them and propose an annotation. Turns "what does `status = 3` mean?" from a question into a prompt the AI can answer from a candidate list.
- **Soft-FK inference** ([#14](https://github.com/SurajKGoyal/amnesic/issues/14)) — detect implicit relationships from naming patterns (`*_id` columns matching primary keys) where no FK constraint exists. This is the legacy-MSSQL reality, and it's exactly where a live-introspection server returns an empty set because there is no constraint to read.
- **JOIN-pattern learning** (not yet filed — depends on `db_query_history`) — track which tables actually get joined together in queries run via `db_query`, and promote those edges into the relationship graph. Knowledge derived from usage, which a stateless server cannot have by construction.

**Table stakes** — the things that get amnesic dismissed before anyone reaches the knowledge layer:

- **Response token budget** ([#9](https://github.com/SurajKGoyal/amnesic/issues/9)) — a shared byte budget applied to every tool response, degrading gracefully (drop rows/columns, report what was dropped) rather than dumping 50k tokens into the context window. Today only `db_detect_drift` caps its output; `db_query` at `max_rows=500` on a wide table is a genuine context bomb.
- **Smaller tool surface** ([#10](https://github.com/SurajKGoyal/amnesic/issues/10)) — 12 tools cost roughly 2.7k tokens of definitions plus a 0.7k instructions block, burned in every session before anything happens. Consolidate: the three lifecycle tools (`db_annotate` / `db_deprecate` / `db_forget`) become one tool with an `action`; the three read tools (`db_list_tables` / `db_get_schema` / `db_search`) become one with a `detail_level` of `names` / `summary` / `full`. Target: under 1.5k.
- **Richer schema fetch** ([#11](https://github.com/SurajKGoyal/amnesic/issues/11)) — primary keys, indexes, and defaults alongside the columns. An agent can't tell whether its `WHERE` clause hits an index today, so it writes slow queries against production.
- **Cache freshness** ([#12](https://github.com/SurajKGoyal/amnesic/issues/12)) — stamp `fetched_at` on cached schema and return `stale: true` past a threshold, instead of silently serving a cache that may be months old.

**Query intelligence** (unchanged in scope, lower priority than the above)

- `db_explain(sql, connection)` ([#15](https://github.com/SurajKGoyal/amnesic/issues/15)) — the DB's own query plan (EXPLAIN for Postgres/MySQL/SQLite, SHOWPLAN_XML for MSSQL). A passthrough, not an analyzer.
- `db_query_history(connection, limit=20)` — recent queries run via amnesic (local, opt-in). Also the data source for JOIN-pattern learning.
- Truncation insights — when a result hits the budget, return aggregate stats about what was dropped.

---

## v0.4.0 — Team & Sharing

For when more than one developer needs the same knowledge.

- `amnesic push <connection>` / `amnesic pull <connection>` — back the knowledge file to a Git repo or S3 bucket
- Knowledge file versioning — track who annotated what, when
- Conflict resolution for concurrent annotations (last-writer-wins by default, with `--strategy=merge` option)
- Optional remote knowledge backend (HTTP API spec for teams running their own)

---

## v0.5.0 — Advanced Discovery

> The three auto-discovery items that used to live here **moved up to v0.3.0** — they're the differentiator, not a late nice-to-have.

Remaining, once the basics land:

- **Confidence scoring** on auto-derived annotations, so a human-written description always outranks an inferred one
- **Bulk review flow** — `amnesic review <conn>`, a CLI pass to accept/reject a batch of proposed annotations in one sitting
- **Cross-connection inference** — reuse what was learned on staging to propose annotations on prod when the schemas match

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

**Everything above that hasn't shipped is up for grabs.** Each item is filed as an issue with the problem, the proposed shape, the files to touch, and how to verify it — so you can start writing code rather than reverse-engineering intent.

1. Browse [open issues](https://github.com/SurajKGoyal/amnesic/issues) — [`good first issue`](https://github.com/SurajKGoyal/amnesic/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) if you're new to the codebase, [`help wanted`](https://github.com/SurajKGoyal/amnesic/issues?q=is%3Aissue+is%3Aopen+label%3A%22help+wanted%22) for the bigger pieces.
2. **Comment on the issue to claim it.** That's the only coordination needed — no approval required, no waiting on a maintainer.
3. Open the PR. Reference the issue number.

Notes:

- Proposing something *not* on the roadmap? Open an issue with the use case first — it saves you rework if the answer is "that's a deliberate non-goal" (see [Where this is going](#where-this-is-going)).
- New drivers: follow the structure in `amnesic/drivers.py` and `amnesic/tools/schema.py`.
- Tests live in `tests/`. New tools must include unit tests. Run `pytest tests/` before opening a PR.
- Anything touching `db_query` or `readonly.py` needs a test proving the read-only guarantee still holds. That invariant is the reason people point amnesic at prod.

Anything missing that you'd find useful? [Open an issue.](https://github.com/SurajKGoyal/amnesic/issues/new)
