# amnesic — the MCP server with the most ironic name in the registry

**Persistent semantic memory for your SQL databases. The name is ironic — it remembers everything.**

*"The MCP server with the most ironic name in the registry. It's anything but amnesic — it remembers your database so your AI doesn't have to."*

---

## The problem

Every session with an AI starts cold. You spend the first few minutes re-explaining what tables exist, what a `status` column value of `3` means, which FK connects `orders` to `users`. Then the session ends, and you do it all over again tomorrow.

**amnesic fixes this.** It gives your AI a persistent SQLite knowledge store — one per database — that survives across sessions. Annotate a status enum once; every future session sees those labels automatically. Discover FK relationships once; every future JOIN query uses that graph.

---

## Install

Pick the driver extras matching the database(s) you'll use:

```bash
# Isolated install (recommended) — pipx is usually pre-installed
pipx install "amnesic[mssql]"        # or [postgres], [mysql], [all]

# Or with uv (single-binary alternative)
uv tool install "amnesic[mssql]"

# Or plain pip (puts amnesic in your active Python env)
pip install "amnesic[mssql]"

# Core only (SQLite works out of the box, no driver extras needed)
pipx install amnesic
```

**Install uv** (if you want to use `uv tool install` or `uvx`):

```bash
brew install uv                                             # macOS (Homebrew)
curl -LsSf https://astral.sh/uv/install.sh | sh            # Linux / macOS
powershell -c "irm https://astral.sh/uv/install.ps1 | iex" # Windows
```

After install, `amnesic --help` should work from any terminal — you're ready to run `amnesic init`.

---

## Setup (90 seconds)

```bash
$ pip install amnesic
$ amnesic init
# interactive wizard guides you through your first connection
```

The wizard:
- asks for your database type, host, credentials
- tests the connection before saving anything
- stores your password securely in `~/.config/amnesic/.env` (chmod 600)
- writes the connection block to `~/.config/amnesic/connections.toml`

Then add amnesic to your AI client (mcp.json snippet below) and restart.

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

Once amnesic is installed with the right driver extras (see [Install](#install)), the `amnesic` command is on your PATH. Use the same snippet across every MCP client:

### Claude Code

Add to `~/.claude/mcp.json`:

```json
{
  "mcpServers": {
    "amnesic": {
      "command": "amnesic"
    }
  }
}
```

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

Swap `mssql` for `postgres`, `mysql`, or `all` to match the driver(s) you need.

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

| Database | Driver | Extra |
|----------|--------|-------|
| PostgreSQL | psycopg2 | `pip install "amnesic[postgres]"` |
| MySQL / MariaDB | pymysql | `pip install "amnesic[mysql]"` |
| Microsoft SQL Server | pymssql | `pip install "amnesic[mssql]"` |
| SQLite | built-in | no extra needed |

---

## Security

- **Read-only enforcement**: two layers — static SQL analysis rejects any write/DDL statement before a connection opens, plus every query runs inside an immediately-rolled-back transaction.
- **No credentials in responses**: `db_list_connections` strips passwords and usernames from output.
- **Credentials via env vars**: `${ENV_VAR}` expansion at load time — secrets never touch the config file on disk.
- **Secure .env storage**: `amnesic init` / `amnesic set-secret` always chmod 600 the `.env` file after writing. On macOS/Linux, the `.env` file is `chmod 0o600` so only your user can read it. On Windows, the `.env` file lives in `%APPDATA%` which is restricted to your user profile by default — file ACLs are handled by Windows itself.
- **Identifier validation**: table names, schema names, and database names are validated against `[A-Za-z0-9_]` before any interpolation into SQL.

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
