# Installing amnesic — instructions for AI coding agents

> This file is for AI agents (Cline, etc.) that auto-install MCP servers. Human readers should follow [README.md](./README.md) instead.

amnesic is a Python MCP server that gives the user persistent semantic memory of their SQL databases. It is distributed via PyPI as `amnesic` and exposes a stdio MCP server.

## What to do

1. **Install amnesic** so the `amnesic` command is on the user's PATH:
   ```bash
   pipx install amnesic
   ```
   If `pipx` is unavailable, fall back to `uv tool install amnesic` or `pip install --user amnesic`.

2. **Register the MCP server** in the host's MCP configuration with this entry:
   ```json
   {
     "amnesic": {
       "command": "amnesic"
     }
   }
   ```
   No env vars, transport flags, or special args are required — amnesic auto-detects stdio.

3. **Tell the user how to configure their database**. Do not attempt to configure connections yourself. Instead instruct the user to run one of:
   - `amnesic init` — interactive wizard for a real database (asks for driver, host, credentials, stores password securely in `~/.config/amnesic/.env`).
   - `amnesic init --demo` — zero-credential path that adds a self-contained SQLite sample DB so they can try every tool in under a minute without configuring credentials.

4. **Verify**: have the user run `amnesic test` (or `amnesic test <connection-name>`) to confirm the connection works before they invoke any MCP tool.

## What amnesic provides (9 MCP tools)

- `db_list_connections`, `db_list_tables`, `db_search`, `db_get_schema`
- `db_query` (read-only, two layers of write protection)
- `db_annotate`, `db_sync_knowledge`
- `db_discover_relationships`, `db_get_relationships`

All tools accept a `connection` argument that defaults to the first configured connection.

## Safety notes

- amnesic is **read-only by design**: static SQL analysis rejects write keywords (`INSERT`, `UPDATE`, `DELETE`, `DROP`, etc.) before connecting, and every query runs inside an immediately-rolled-back transaction. Safe to point at production.
- Credentials live in `~/.config/amnesic/.env` (chmod 600), never in the TOML or in any tool response.
- amnesic adds no new external trust boundary — the trust boundary is wherever the host MCP client sends data.

## Links

- Source: https://github.com/SurajKGoyal/amnesic
- PyPI: https://pypi.org/project/amnesic/
- MCP Registry: https://registry.modelcontextprotocol.io (search "amnesic")
