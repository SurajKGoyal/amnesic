# amnesic — the MCP server with the most ironic name in the registry

[![PyPI version](https://img.shields.io/pypi/v/amnesic.svg)](https://pypi.org/project/amnesic/)
[![Downloads](https://img.shields.io/pypi/dm/amnesic.svg)](https://pypistats.org/packages/amnesic)
[![Python](https://img.shields.io/pypi/pyversions/amnesic.svg)](https://pypi.org/project/amnesic/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![MCP Registry](https://img.shields.io/badge/MCP-Registry-7d6ad9.svg)](https://registry.modelcontextprotocol.io)
[![GitHub stars](https://img.shields.io/github/stars/SurajKGoyal/amnesic?style=social)](https://github.com/SurajKGoyal/amnesic)

**Persistent semantic memory for your SQL databases. The name is ironic — it remembers everything.**

*"The MCP server with the most ironic name in the registry. It's anything but amnesic — it remembers your database so your AI doesn't have to."*

**Works with** Claude Code · Claude Desktop · Cursor · VS Code · Cline · Windsurf — any [MCP-compatible client](https://modelcontextprotocol.io/clients).

**Available on** [Official MCP Registry](https://registry.modelcontextprotocol.io) · [Claude Code plugin marketplace](https://github.com/SurajKGoyal/amnesic-marketplace)

> 🔒 **Read-only by design.** amnesic refuses to execute `INSERT`, `UPDATE`, `DELETE`, `DROP`, `TRUNCATE`, `ALTER`, `CREATE`, `EXEC`, `MERGE`, `GRANT`, `REVOKE` — and any write statement smuggled inside a `WITH` CTE. Two layers of defense: static SQL analysis rejects the statement before connecting, **and** every query runs inside a transaction that is immediately rolled back. Safe to point at prod. [Details ↓](#safety--read-only-enforcement)

---

## The problem

Every session with an AI starts cold. You spend the first few minutes re-explaining what tables exist, what a `status` column value of `3` means, which FK connects `orders` to `users`. Then the session ends, and you do it all over again tomorrow.

**amnesic fixes this.** It gives your AI a persistent SQLite knowledge store — one per database — that survives across sessions. Annotate a status enum once; every future session sees those labels automatically. Discover FK relationships once; every future JOIN query uses that graph.

---

## Quickstart (90 seconds)

```bash
pipx install amnesic            # install the core
amnesic init                    # interactive wizard
```

> ⚡ **Try it without credentials.** Run `amnesic init --demo` instead — it adds a self-contained SQLite sample DB (e-commerce schema: customers / products / orders with FKs and an enum column) so you can exercise every tool in under a minute. Great for a first look before pointing amnesic at a real database.

The wizard asks which database type you're connecting to and tells you the **one** command to run if its driver isn't installed yet — you never need to guess extras up front.

The wizard:
- Asks for your database type, host, and credentials
- Tests the connection before saving anything
- Stores the password securely in `~/.config/amnesic/.env` (chmod 600)
- Writes the connection block to `~/.config/amnesic/connections.toml`

Then [add amnesic to your AI client](#add-to-your-ai-client) and restart.

<details>
<summary><b>Don't have <code>pipx</code>? Or prefer <code>uv</code> / plain <code>pip</code>?</b></summary>

<br/>

**Install `pipx`** (one-time):

```bash
brew install pipx                                  # macOS
sudo apt install pipx                              # Linux (Debian/Ubuntu)
python -m pip install --user pipx                  # Windows / generic
```

**Or use `uv`** (single-binary alternative — fast, no Python required):

```bash
brew install uv                                            # macOS
curl -LsSf https://astral.sh/uv/install.sh | sh            # Linux / macOS
powershell -c "irm https://astral.sh/uv/install.ps1 | iex" # Windows

uv tool install amnesic
```

**Or plain `pip`** (installs into your active Python env):

```bash
pip install amnesic
```

> Whichever you pick, `amnesic init` asks which database you'll connect to and prints the one extra command to install that driver — no need to commit to extras up front.

</details>

After install, `amnesic --help` works from any terminal.

### Where amnesic stores things

| File | macOS / Linux | Windows |
|---|---|---|
| Config | `~/.config/amnesic/connections.toml` | `%APPDATA%\amnesic\connections.toml` |
| Secrets | `~/.config/amnesic/.env` (chmod 600) | `%APPDATA%\amnesic\.env` (user profile ACL) |
| Knowledge | `~/.config/amnesic/knowledge_<name>.db` | `%APPDATA%\amnesic\knowledge_<name>.db` |

Set `$AMNESIC_HOME` (or `$XDG_CONFIG_HOME` on Linux) to override the location.

### Adding more connections later

```bash
amnesic add          # add another connection to existing config
amnesic test         # verify all connections
amnesic test orders.prod  # verify one connection
```

### Setting and rotating passwords

`amnesic init` and `amnesic add` save your password automatically — for the typical setup flow, you never need to think about this section.

Use `set-secret` when you need to change a stored password later — IT rotated it, you mistyped it during setup, or you're hand-editing the config.

```bash
$ amnesic set-secret ORDERS_PROD_PASSWORD
Value: ****            ← hidden input (your typing is invisible)
Confirm: ****
✓ Set ORDERS_PROD_PASSWORD in ~/.config/amnesic/.env
```

**What's the variable name?** It's the env var your `connections.toml` references for that connection's password. The wizard auto-generates these as `<CONNECTION_NAME_UPPERCASE_WITH_UNDERSCORES>_PASSWORD`:

| Connection name | Generated env var |
|---|---|
| `orders.prod` | `ORDERS_PROD_PASSWORD` |
| `analytics` | `ANALYTICS_PASSWORD` |
| `drive.staging` | `DRIVE_STAGING_PASSWORD` |

To see the exact name your config uses, check `~/.config/amnesic/connections.toml` — anything inside `${...}` is the variable to pass to `set-secret`.

**Under the hood**: writes (or replaces) the line in `~/.config/amnesic/.env`, sets file permission to `chmod 600` (only your user can read it), preserves all other entries.

---

## Add to your AI client

Once amnesic is installed with the right driver extras (see [Quickstart](#quickstart-90-seconds)), the `amnesic` command is on your PATH. Use the same snippet across every MCP client:

### Claude Code

**One-line install** (recommended — no JSON editing). Inside Claude Code:

```
/plugin marketplace add SurajKGoyal/amnesic-marketplace
/plugin install amnesic@amnesic
```

That wires amnesic as an MCP server automatically. Source: [SurajKGoyal/amnesic-marketplace](https://github.com/SurajKGoyal/amnesic-marketplace).

<details>
<summary><b>Or wire it by hand — edit <code>~/.claude/mcp.json</code></b></summary>

<br/>

```json
{
  "mcpServers": {
    "amnesic": {
      "command": "amnesic"
    }
  }
}
```

</details>

### Claude Desktop

Add to your platform's Claude Desktop config:

- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
- **Linux**: `~/.config/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "amnesic": {
      "command": "amnesic"
    }
  }
}
```

### Cursor

**One-click install** — click the button below and Cursor wires it up for you:

<a href="https://cursor.com/install-mcp?name=amnesic&config=eyJjb21tYW5kIjoiYW1uZXNpYyJ9"><img src="https://cursor.com/deeplink/mcp-install-dark.svg" alt="Add amnesic to Cursor" height="32"></a>

<details>
<summary><b>Or wire it by hand — edit <code>.cursor/mcp.json</code></b></summary>

<br/>

Add to `.cursor/mcp.json` in your project (or `~/.cursor/mcp.json` globally):

```json
{
  "mcpServers": {
    "amnesic": {
      "command": "amnesic"
    }
  }
}
```

</details>

### Without a global install (ephemeral)

If you'd rather not install amnesic on your system, use `uvx` or `pipx` to fetch it each time the MCP client starts. Note the driver extras must be passed explicitly:

```json
// uvx — requires `uv` installed (see Install section for per-OS instructions)
{
  "mcpServers": {
    "amnesic": {
      "command": "uvx",
      "args": ["--from", "amnesic[mssql]", "amnesic"]
    }
  }
}

// pipx — usually pre-installed via Homebrew or system package manager
{
  "mcpServers": {
    "amnesic": {
      "command": "pipx",
      "args": ["run", "--spec", "amnesic[mssql]", "amnesic"]
    }
  }
}
```

For multiple drivers, comma-separate inside the brackets — e.g. `amnesic[postgres,mssql]` or use `amnesic[all]` for everything.

### VS Code (with MCP extension)

Add to `.vscode/mcp.json`:

```json
{
  "servers": {
    "amnesic": {
      "type": "stdio",
      "command": "amnesic"
    }
  }
}
```

---

## Tools

| Tool | Description |
|------|-------------|
| `db_list_connections()` | List all configured connections (no secrets exposed) |
| `db_list_tables(connection)` | All known tables with descriptions and column counts |
| `db_search(query, connection, target, limit)` | BM25 search over table/column descriptions and aliases |
| `db_get_schema(table, connection)` | Column schema merged with saved annotations |
| `db_query(sql, connection)` | Execute a read-only SELECT query |
| `db_annotate(table, connection, ...)` | Persist semantic annotations for tables/columns |
| `db_sync_knowledge(from, to)` | Copy annotations between connections (e.g. staging → prod) |
| `db_discover_relationships(connection)` | Discover all FK relationships from the live DB |
| `db_get_relationships(table, connection)` | Navigate the FK graph for JOIN planning |

---

## Searching the knowledge base

For large schemas, `db_list_tables` is impractical — you'd dump 500+ rows into Claude's context. Use `db_search` to find the relevant tables/columns by keyword instead:

```
"What table tracks customer payments?"
  → db_search("payments")
    Top results:
      - dbo.payments  (table) "Customer payment records..."
      - dbo.orders.payment_method  (column) "Mode of payment..."
```

`db_search` uses **SQLite FTS5 with BM25 ranking** — fast, local, no embeddings or external services. Search syntax supports:

| Syntax | Effect |
|--------|--------|
| `payment` | Match the word (with stemming — also matches "payments", "paying") |
| `"payment method"` | Exact phrase |
| `pay*` | Prefix match — "payment", "payable", etc. |
| `payment AND status` | Both terms required |
| `payment OR refund` | Either term |

Results return ranked table/column rows with descriptions and highlighted snippets.

---

## The knowledge layer

The core differentiator. Every annotation survives restarts, model updates, and new sessions.

### Session 1 — you discover something

```
You: What does status=3 mean in the orders table?
AI: Let me check. [runs db_query: SELECT DISTINCT status FROM dbo.orders]
    I see values 1, 2, 3, 4. Let me look at some examples...
    Based on the data, 3 appears to be "cancelled".

You: Save that. And status=1 is "pending", 2 is "confirmed", 4 is "delivered".
AI: [calls db_annotate]
    db_annotate(
      table="dbo.orders",
      column="status",
      column_description="Order lifecycle status",
      enum_values={"1": "pending", "2": "confirmed", "3": "cancelled", "4": "delivered"}
    )
    Saved. Future sessions will see these labels automatically.
```

### Session 2 — the knowledge is already there

```
You: How many cancelled orders are there this month?
AI: [calls db_get_schema("dbo.orders")]
    Schema response includes:
      column: "status"
      description: "Order lifecycle status"
      enum_values: {"1": "pending", "2": "confirmed", "3": "cancelled", "4": "delivered"}

    [writes correct SQL immediately]
    SELECT COUNT(*) FROM dbo.orders WHERE status = 3 AND ...
```

No re-discovery. No wasted turns. The annotation persisted.

---

## Relationship graph

Understand your schema's JOIN structure once, reuse it forever.

```
AI: [db_discover_relationships(connection="orders.prod")]
    Discovered 47 foreign key relationships.

AI: [db_get_relationships(table="orders", depth=2)]
    neighbors:
      orders → users (via user_id → id)
      orders → order_items (via id ← order_id)
    paths:
      orders -> users
      orders -> order_items
      order_items -> products
```

Now the AI knows exactly how to JOIN across your schema without guessing.

---

## Sync between environments

Build up annotations in staging, then promote to prod:

```
db_sync_knowledge(from_connection="orders.staging", to_connection="orders.prod")
```

Returns `{synced: [...], skipped: [{table, reason}], warnings: [{table, column, reason}]}`.

Tables missing from the target schema cache are skipped with a clear reason. Columns missing from target schema are warned but don't block the rest of the sync.

---

## Advanced: hand-edit the TOML

If you prefer to manage the config file yourself, generate a blank template:

```bash
amnesic init --template
```

This writes `~/.config/amnesic/connections.toml` with commented examples and exits — no wizard. Edit the file directly:

```toml
# ~/.config/amnesic/connections.toml

# Nested style: [connections.product.env]
[connections.orders.prod]
driver = "mssql"
server = "localhost"
port = 11433
database = "OrdersDB"
user = "${ORDERS_USER}"
password = "${ORDERS_PROD_PASSWORD}"
tunnel_script = "~/.scripts/mssql-tunnel.sh"     # macOS / Linux (bash)
# tunnel_script = "C:/scripts/mssql-tunnel.ps1"  # Windows (PowerShell)

[connections.orders.staging]
driver = "mssql"
server = "localhost"
port = 11434
database = "OrdersDB_Staging"
user = "${ORDERS_USER}"
password = "${ORDERS_STAGING_PASSWORD}"

# Flat style: [connections.name]
[connections.analytics]
driver = "postgres"
server = "analytics.company.com"
port = 5432
database = "warehouse"
user = "${ANALYTICS_DB_USER}"
password = "${ANALYTICS_DB_PASSWORD}"

# SQLite — no credentials needed
[connections.local]
driver = "sqlite"
database = "/absolute/path/to/local.db"       # macOS / Linux
# database = "C:/path/to/local.db"            # Windows (use forward slashes)
```

Use `${ENV_VAR}` for credentials — never hardcode passwords.

Secrets are loaded from `~/.config/amnesic/.env` automatically (format: `KEY=VALUE`, one per line, `#` for comments). For each `${VAR_NAME}` referenced in your TOML, populate the matching `.env` entry with [`amnesic set-secret VAR_NAME`](#setting-and-rotating-passwords) (hidden input, chmod 600), or write `.env` yourself.

Canonical connection names use dot notation: `orders.prod`, `orders.staging`, `analytics`, `local`.

---

## Supported databases

| Database | Python driver | Installed by |
|----------|---------------|--------------|
| PostgreSQL | `psycopg2-binary` | wizard nudge when you pick Postgres, or `amnesic[postgres]` extras |
| MySQL / MariaDB | `pymysql` | wizard nudge when you pick MySQL, or `amnesic[mysql]` extras |
| Microsoft SQL Server | `pymssql` | wizard nudge when you pick MSSQL, or `amnesic[mssql]` extras |
| SQLite | stdlib `sqlite3` | always available — no extra |

---

## Safety & read-only enforcement

amnesic is built to be safe to point at production databases.

### Why your AI can't damage your data

Every query passes through **two independent layers** before reaching the database:

1. **Static analysis** (in `amnesic/readonly.py`) — the SQL is tokenized and rejected if it contains any of:
   `INSERT`, `UPDATE`, `DELETE`, `DROP`, `TRUNCATE`, `ALTER`, `CREATE`, `EXEC`, `EXECUTE`, `MERGE`, `BULK`, `GRANT`, `REVOKE`, `DENY`.
   This includes write statements smuggled inside CTEs (`WITH x AS (SELECT ...) UPDATE ...` is caught and refused).
2. **Transaction rollback** — even if a write statement somehow gets past the static check, the query runs inside `BEGIN TRANSACTION ... ROLLBACK` so nothing is ever committed. Belt and suspenders.

Only `SELECT` and `WITH ... SELECT` reach the database. Comments are stripped before analysis so `/* DELETE FROM users */` can't be used to hide an attack.

### Other safety measures

- **No credentials in responses**: `db_list_connections` strips passwords and usernames from its output. The AI can see *which* connections exist, never *how to authenticate* to them.
- **Credentials via env vars only**: `${ENV_VAR}` expansion at config-load time — passwords never touch `connections.toml` on disk.
- **Secure `.env` storage**: on macOS/Linux `chmod 0o600` (owner read/write only); on Windows the `.env` lives in `%APPDATA%` which is restricted to your user profile by Windows ACL.
- **Identifier validation**: table/schema/database names are checked against `[A-Za-z0-9_]+` before any string interpolation into SQL.
- **Tested**: 40+ unit tests in `tests/test_readonly.py` cover every write keyword, comment-stripping edge case, CTE-with-write attempts, semicolon-separated multi-statements, and identifier injection attempts. `pytest tests/test_readonly.py` to verify on your machine.

---

## Is this safe with my data?

amnesic is local-only and protocol-only. It doesn't introduce a new external trust boundary — **the trust boundary is wherever your MCP client sends data, not amnesic itself.** Choosing the AI client decides the policy that applies to your rows.

```
your DB → amnesic (local) → MCP client → your AI deployment
                                          ↑ trust boundary lives here
```

The honest question to ask, whether you're indie or enterprise:

**Do I trust my AI client with the data in this database?**

If yes — and for most setups, the answer is yes — you're good. That covers:

- Solo devs on Claude Pro / Cursor / Copilot using their own projects, dev DBs, or test data
- Side projects querying personal SQLite or self-hosted Postgres
- Open-source maintainers working with public schemas
- Teams on enterprise AI with explicit isolation: **AWS Bedrock** (tenant + IAM), **Azure OpenAI** (region-pinned, your subscription), **Anthropic Enterprise** (zero data retention, training opt-out), **Vertex AI** (your GCP project), **self-hosted** (Ollama, vLLM, on-prem Claude/GPT — data never leaves the network)
- Anyone on a paid AI plan with zero-retention guarantees and a DPA covering your use

### Worth a closer look if

- Your DB holds **data belonging to other people** (users, customers, patients) and you haven't verified your AI provider's terms cover that processing
- You're on **consumer-tier AI** (free / personal Pro) AND working with regulated data — **PHI** (HIPAA covered entity), **cardholder data** (PCI-DSS), **restricted PII** under GDPR / India's DPDP Act
- Your employer has an **explicit policy** restricting external AI tool use on prod DBs
- You're under **data-residency** rules where rows can't leave a specific region

### Data minimization is built in

A property of the design, not an afterthought: the annotation layer means the AI answers most schema questions from a **local SQLite knowledge file** — no `db_query` runs, no row data is sent anywhere.

- *"What does status=3 mean?"* → resolves from your saved annotation
- *"How do orders join users?"* → resolves from the FK graph
- *"Which tables have a `created_at` column?"* → resolves from schema cache

For purely structural exploration, six tools never touch your data: `db_list_tables`, `db_get_schema`, `db_search`, `db_annotate`, `db_discover_relationships`, `db_get_relationships`. They return metadata only.

That's measurably less data movement than a "naked" SQL MCP — which has to run `SELECT DISTINCT status FROM orders` every time the AI is confused about an enum. amnesic answers it once from local annotations.

> **Disclaimer**: amnesic is provided as-is under the MIT License (no warranty, no liability — see [LICENSE](LICENSE)). This section is not legal or compliance advice. Your use of amnesic, and the AI client you connect it to, is your responsibility. If you handle regulated data, consult your security / compliance team before pointing it at production.

---

## Roadmap

What's coming: knowledge lifecycle management (v0.2 — `db_deprecate`, drift detection, export/import for team handoff), query intelligence (v0.3 — `db_explain`, query history), team sharing (v0.4), and more. See [ROADMAP.md](./ROADMAP.md) for the full picture.

Have an idea? [Open an issue.](https://github.com/SurajKGoyal/amnesic/issues/new)

---

## Track usage

[pypistats.org/packages/amnesic](https://pypistats.org/packages/amnesic)

---

## License

MIT — see [LICENSE](LICENSE).

---

## MCP Registry

This server is registered on the [official MCP Registry](https://registry.modelcontextprotocol.io).

```
mcp-name: io.github.SurajKGoyal/amnesic
```
