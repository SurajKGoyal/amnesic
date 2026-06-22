"""
amnesic CLI

Commands:
  amnesic init              — interactive setup wizard (first-time)
  amnesic init --template   — write the blank commented template (for hand-editing)
  amnesic add               — add another connection to existing config
  amnesic set-secret NAME   — set or rotate a secret in ~/.config/amnesic/.env
  amnesic test [connection] — verify connectivity for one or all connections
"""

from datetime import datetime, timezone
from pathlib import Path
import json
import re

import click
from rich.console import Console
from rich.table import Table

from amnesic import __version__
from amnesic._paths import config_dir, connections_path, env_path, knowledge_path
from amnesic._update_check import check_for_update

_EXPORT_FORMAT_VERSION = 1

_CONFIG_DIR = config_dir()
_CONFIG_FILE = connections_path()
_ENV_FILE = env_path()

_ENV_VAR_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")

_TEMPLATE = """\
# amnesic connections.toml
# Documentation: https://github.com/SurajKGoyal/amnesic
#
# Two styles are supported:
#
#   Flat (single env):
#     [connections.mydb]
#     driver = "postgres"
#     ...
#
#   Nested (multiple envs):
#     [connections.myproduct.prod]
#     driver = "mssql"
#     ...
#     [connections.myproduct.staging]
#     driver = "mssql"
#     ...
#
# Use ${ENV_VAR} for credentials — never hardcode passwords here.
#
# Supported drivers: mssql, postgres, mysql, sqlite

# Example: PostgreSQL connection
# [connections.mydb]
# driver = "postgres"
# server = "localhost"
# port = 5432
# database = "mydb"
# user = "${MYDB_USER}"
# password = "${MYDB_PASSWORD}"

# Example: MSSQL with SSH tunnel
# [connections.orders.prod]
# driver = "mssql"
# server = "localhost"
# port = 11433
# database = "OrdersDB"
# user = "${ORDERS_USER}"
# password = "${ORDERS_PASSWORD}"
# tunnel_script = "/path/to/mssql-tunnel.sh"   # macOS / Linux (bash)
# tunnel_script = "C:/scripts/mssql-tunnel.ps1"  # Windows (PowerShell)

# Example: SQLite (no credentials needed)
# [connections.local]
# driver = "sqlite"
# database = "/absolute/path/to/local.db"      # macOS / Linux
# database = "C:/path/to/local.db"             # Windows (use forward slashes)
"""

_ENV_EXAMPLE = """\
# amnesic credentials — EXAMPLE FILE (safe to commit; contains no real secrets).
#
# Copy the keys you need into a sibling `.env` file (same directory). amnesic
# loads `.env` on startup and expands ${VAR} references in connections.toml.
# `.env` is chmod 600 and gitignored — real secrets live there, never here.
#
# Format: KEY=value   (no quotes, no spaces around '=')
#
# Each network connection's password env var is <CONNECTION_NAME>_PASSWORD,
# uppercased with dots replaced by underscores. Examples:
#
#   # connection "analytics"      -> ANALYTICS_PASSWORD
#   ANALYTICS_PASSWORD=changeme
#
#   # connection "orders.prod"    -> ORDERS_PROD_PASSWORD
#   ORDERS_PROD_PASSWORD=changeme
#
# You can also reference any ${VAR} you like in connections.toml and define it
# here — the names above are just amnesic's default convention.
"""

console = Console()


def _write_env_example() -> Path:
    """Write the commented .env.example next to connections.toml. Returns its path."""
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    example_path = _ENV_FILE.parent / ".env.example"
    example_path.write_text(_ENV_EXAMPLE, encoding="utf-8")
    return example_path


def _maybe_print_update_notice() -> None:
    """Print a one-line 'newer amnesic exists' notice to stderr, fail-silent.

    Skipped under pytest so the test suite never makes a network call. Goes to
    stderr so it can never pollute machine-readable stdout (e.g. `amnesic
    export` piped to a file).
    """
    import os

    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    try:
        notice = check_for_update(__version__)
    except Exception:
        return
    if notice:
        click.echo(notice, err=True)


@click.group(invoke_without_command=True)
@click.version_option(__version__, "-V", "--version", prog_name="amnesic")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """amnesic — the MCP server that remembers your database.

    With no subcommand:
      • If stdin is piped (an MCP client launched us) → start the MCP server.
      • If stdin is a TTY (you ran 'amnesic' in your terminal) → show this help.
    """
    import sys

    # MCP-server mode = no subcommand AND stdin is piped (a client launched us).
    # The update notice must NEVER run there: any stray byte corrupts the stdio
    # JSON-RPC stream. It's safe for every interactive CLI path.
    server_mode = ctx.invoked_subcommand is None and not sys.stdin.isatty()
    if not server_mode:
        _maybe_print_update_notice()

    if ctx.invoked_subcommand is not None:
        return

    if sys.stdin.isatty():
        # Interactive user — show help so they can discover subcommands.
        click.echo(ctx.get_help())
        return

    # Piped stdin — we're being invoked as an MCP server. Start it.
    from amnesic.server import main as _server_main

    _server_main()


@cli.command()
@click.option("--template", is_flag=True, default=False,
              help="Write a blank commented template instead of running the wizard.")
@click.option("--demo", is_flag=True, default=False,
              help="Add a zero-credential SQLite demo connection so you can try amnesic in 60s.")
def init(template: bool, demo: bool) -> None:
    """Set up amnesic for the first time (interactive wizard).

    Pass --template to write a blank config file for hand-editing instead.
    Pass --demo to skip the wizard and add a self-contained SQLite sample DB
    so you can try every tool without configuring any real credentials.
    """
    if template and demo:
        console.print("[red]--template and --demo are mutually exclusive.[/red]")
        raise SystemExit(2)

    # Always (re)write the credentials example so every init path leaves a
    # discoverable .env.example next to the config (closes issue #5).
    example_path = _write_env_example()

    if demo:
        _install_demo_connection()
        return

    if template:
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _CONFIG_FILE.write_text(_TEMPLATE)
        console.print(f"[green]Created:[/green] {_CONFIG_FILE}")
        console.print(f"[green]Created:[/green] {example_path} [dim](credentials reference)[/dim]")
        console.print()
        console.print("[bold]Next steps:[/bold]")
        console.print(f"  1. Edit [cyan]{_CONFIG_FILE}[/cyan] — add your database connections")
        console.print(f"  2. Put credentials in [cyan]{_ENV_FILE}[/cyan] (see [cyan]{example_path.name}[/cyan] for the format)")
        console.print(f"  3. Run [cyan]amnesic test[/cyan] to verify connectivity")
        console.print(f"  4. Add amnesic to your MCP client config (see README for snippet)")
        return

    if _CONFIG_FILE.exists():
        console.print(
            f"[yellow]Config already exists:[/yellow] {_CONFIG_FILE}\n"
            f"Use [cyan]amnesic add[/cyan] to add more connections, "
            f"or [cyan]amnesic init --template[/cyan] to overwrite with the empty template."
        )
        raise SystemExit(0)

    from amnesic._wizard import run_wizard
    run_wizard(welcome=True, loop=True)


def _install_demo_connection() -> None:
    """Build the sample SQLite DB and add (or refresh) the `demo` connection."""
    from amnesic._demo import build_demo_db
    from amnesic.config import invalidate_config_cache

    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    db_path = build_demo_db(_CONFIG_DIR / "sample_store.db")

    # TOML uses forward slashes universally — convert Windows backslashes.
    posix_path = db_path.as_posix()
    block = (
        "\n"
        "# Demo connection — a self-contained SQLite sample DB.\n"
        "# Generated by `amnesic init --demo`. Safe to delete.\n"
        "[connections.demo]\n"
        'driver = "sqlite"\n'
        f'database = "{posix_path}"\n'
    )

    if _CONFIG_FILE.exists():
        existing = _CONFIG_FILE.read_text(encoding="utf-8")
        if "[connections.demo]" in existing:
            # Idempotent: leave the file alone, just refresh the DB.
            action = "Refreshed"
        else:
            _CONFIG_FILE.write_text(existing.rstrip() + "\n" + block, encoding="utf-8")
            action = "Added"
    else:
        _CONFIG_FILE.write_text(_TEMPLATE.rstrip() + "\n" + block, encoding="utf-8")
        action = "Created config with"

    invalidate_config_cache()

    console.print(f"[green]✓ {action} the 'demo' connection.[/green]")
    console.print(f"  Sample DB: [cyan]{db_path}[/cyan]")
    console.print(f"  Config:    [cyan]{_CONFIG_FILE}[/cyan]")
    console.print()
    console.print("[bold]Try it:[/bold]")
    console.print(f"  [cyan]amnesic test demo[/cyan]   ← verify the demo connection works")
    console.print()
    console.print("[bold]Then in your AI client, after wiring amnesic:[/bold]")
    console.print(
        '  Ask things like '
        '[cyan]"list tables on the demo connection"[/cyan], '
        '[cyan]"what does status mean on orders?"[/cyan], or '
        '[cyan]"show top 5 customers by total spend"[/cyan].'
    )


@cli.command()
def add() -> None:
    """Add a new connection to an existing config (interactive wizard)."""
    from amnesic._wizard import run_wizard
    run_wizard(welcome=False, loop=False)


@cli.command("set-secret")
@click.argument("name")
def set_secret(name: str) -> None:
    """Set or update a secret in ~/.config/amnesic/.env (hidden input)."""
    if not _ENV_VAR_NAME_RE.match(name):
        console.print(
            f"[red]Invalid env var name:[/red] '{name}'\n"
            f"Must match ^[A-Z_][A-Z0-9_]*$ (uppercase letters, digits, underscores)."
        )
        raise SystemExit(1)

    value = click.prompt("Value", hide_input=True, confirmation_prompt=True)

    from amnesic._wizard import upsert_env_var
    from amnesic.config import invalidate_config_cache
    upsert_env_var(name, value)
    # The .env file feeds ${VAR} expansion during load_config. Invalidate the
    # cache so the next tool call re-expands passwords with the new value.
    invalidate_config_cache()
    console.print(f"[green]✓[/green] Set {name} in ~/.config/amnesic/.env")


def _require_connection(connection: str) -> list[str]:
    """Assert `connection` exists in connections.toml. Exits 1 otherwise.

    Uses a name-only lookup (no ${VAR} expansion) so local knowledge commands
    work even when an unrelated connection is missing its secret. Returns the
    list of all configured connection names.
    """
    from amnesic.config import ConfigError, list_connection_names

    try:
        names = list_connection_names()
    except ConfigError as exc:
        console.print(f"[red]Config error:[/red] {exc}")
        raise SystemExit(1) from exc

    if connection not in names:
        available = ", ".join(names) or "(none configured)"
        console.print(
            f"[red]Connection '{connection}' not found.[/red] Available: {available}"
        )
        raise SystemExit(1)
    return names


def _remove_connection_block(content: str, name: str) -> tuple[str, bool]:
    """Strip the [connections.{name}] block from a TOML string.

    Pure string surgery — never parses/re-serializes, so every other block keeps
    its exact bytes (comments, spacing, ordering). The block runs from its header
    line to the line before the next table header (or EOF), and its own leading
    blank separator line is absorbed too. Returns (new_content, found).
    """
    lines = content.splitlines()
    header = f"[connections.{name}]"

    start = next((i for i, ln in enumerate(lines) if ln.strip() == header), None)
    if start is None:
        return content, False

    end = len(lines)
    for j in range(start + 1, len(lines)):
        stripped = lines[j].strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            end = j
            break

    # Absorb the block's own leading blank separator so we don't leave a gap.
    s = start
    if s > 0 and lines[s - 1].strip() == "":
        s -= 1

    new_lines = lines[:s] + lines[end:]
    new_text = "\n".join(new_lines)
    if content.endswith("\n") and new_text and not new_text.endswith("\n"):
        new_text += "\n"
    return new_text, True


def _delete_knowledge_files(connection: str) -> bool:
    """Close the store, then unlink its SQLite file plus WAL/SHM sidecars.

    Returns True if the main .db file existed and was removed.
    """
    from amnesic.store import close_store

    close_store(connection)
    db_path = knowledge_path(connection)
    removed = db_path.exists()
    for suffix in ("", "-wal", "-shm"):
        sidecar = db_path.with_name(db_path.name + suffix)
        try:
            sidecar.unlink()
        except FileNotFoundError:
            pass
    return removed


@cli.command()
@click.argument("connection")
@click.option("--output", "-o", type=click.Path(dir_okay=False, writable=True),
              default=None, help="Write JSON to this file. Omit to print to stdout.")
def export(connection: str, output: str | None) -> None:
    """Export a connection's annotations + relationships to JSON.

    Dumps table/column annotations and the relationship graph (not the
    re-derivable schema cache) for handing knowledge to a teammate. Round-trips
    losslessly with `amnesic import`.
    """
    from amnesic.store import get_store

    _require_connection(connection)
    store = get_store(connection)

    knowledge = store.export_knowledge()
    payload = {
        "format_version": _EXPORT_FORMAT_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "source": connection,
        "knowledge": knowledge,
    }
    text = json.dumps(payload, indent=2)

    if output:
        Path(output).write_text(text + "\n", encoding="utf-8")
        console.print(
            f"[green]✓[/green] Exported {len(knowledge['tables'])} tables, "
            f"{len(knowledge['columns'])} columns, "
            f"{len(knowledge['relationships'])} relationships → [cyan]{output}[/cyan]"
        )
    else:
        # JSON is the only thing on stdout — safe to pipe/redirect.
        click.echo(text)


@cli.command(name="import")
@click.argument("connection")
@click.argument("file", type=click.Path(exists=True, dir_okay=False))
def import_(connection: str, file: str) -> None:
    """Import annotations + relationships from a JSON export into CONNECTION.

    Unconditional upsert — a handoff loads everything. Existing annotations with
    the same key are overwritten; everything else is left untouched.
    """
    from amnesic.store import get_store

    _require_connection(connection)

    try:
        payload = json.loads(Path(file).read_text(encoding="utf-8"))
    except ValueError as exc:
        console.print(f"[red]Invalid JSON in {file}:[/red] {exc}")
        raise SystemExit(1) from exc

    fv = payload.get("format_version")
    if fv != _EXPORT_FORMAT_VERSION:
        console.print(
            f"[red]Unsupported export format_version: {fv!r}[/red] "
            f"(this amnesic understands version {_EXPORT_FORMAT_VERSION})."
        )
        raise SystemExit(1)

    knowledge = payload.get("knowledge") or {}
    store = get_store(connection)
    counts = store.import_knowledge(knowledge)
    console.print(
        f"[green]✓[/green] Imported {counts['tables']} tables, "
        f"{counts['columns']} columns, {counts['relationships']} relationships "
        f"into '{connection}'."
    )


@cli.command()
@click.argument("connection")
@click.option("--delete-knowledge", is_flag=True, default=False,
              help="Also delete the connection's knowledge_*.db file.")
@click.option("--yes", "-y", is_flag=True, default=False,
              help="Skip confirmation prompts.")
def remove(connection: str, delete_knowledge: bool, yes: bool) -> None:
    """Remove CONNECTION from connections.toml.

    Structure-preserving: only the target [connections.X] block is deleted; every
    other block keeps its exact formatting. The knowledge file is kept by default
    (pass --delete-knowledge to drop it too).
    """
    if not _CONFIG_FILE.exists():
        console.print(f"[yellow]No config file at[/yellow] {_CONFIG_FILE}")
        raise SystemExit(1)

    content = _CONFIG_FILE.read_text(encoding="utf-8")
    new_content, found = _remove_connection_block(content, connection)
    if not found:
        console.print(
            f"[red]Connection '{connection}' not found in[/red] {_CONFIG_FILE}"
        )
        raise SystemExit(1)

    if not yes and not click.confirm(
        f"Remove connection '{connection}' from {_CONFIG_FILE}?", default=False
    ):
        console.print("Aborted.")
        raise SystemExit(0)

    _CONFIG_FILE.write_text(new_content, encoding="utf-8")
    from amnesic.config import invalidate_config_cache
    invalidate_config_cache()
    console.print(f"[green]✓[/green] Removed '{connection}' from config.")

    kpath = knowledge_path(connection)
    if delete_knowledge:
        if not kpath.exists():
            console.print(f"[dim]No knowledge file at {kpath}.[/dim]")
        elif yes or click.confirm(f"Delete knowledge file {kpath}?", default=False):
            _delete_knowledge_files(connection)
            console.print(f"[green]✓[/green] Deleted knowledge file.")
        else:
            console.print(f"[dim]Knowledge kept at {kpath}.[/dim]")
    elif kpath.exists():
        console.print(
            f"[dim]Knowledge kept at {kpath} "
            f"(use --delete-knowledge to remove it).[/dim]"
        )


@cli.command()
@click.argument("connection")
@click.option("--yes", "-y", is_flag=True, default=False,
              help="Skip the confirmation prompt.")
def clear(connection: str, yes: bool) -> None:
    """Erase all stored knowledge for CONNECTION (keeps the config entry).

    Wipes annotations, relationships, and the cached schema. The connection stays
    in connections.toml and the knowledge file is reused — it's just emptied.
    """
    from amnesic.store import get_store

    _require_connection(connection)

    if not yes and not click.confirm(
        f"Erase ALL stored knowledge for '{connection}'? This cannot be undone.",
        default=False,
    ):
        console.print("Aborted.")
        raise SystemExit(0)

    store = get_store(connection)
    c = store.clear_knowledge()
    console.print(
        f"[green]✓[/green] Cleared '{connection}': removed {c['tables']} tables, "
        f"{c['columns']} columns, {c['relationships']} relationships, "
        f"{c['schema_cache']} cached columns."
    )


@cli.command()
@click.argument("connection", required=False)
def test(connection: str | None) -> None:
    """Test connectivity for one or all configured connections."""
    from amnesic.config import ConfigError, load_config
    from amnesic.drivers import get_engine

    try:
        connections = load_config()
    except ConfigError as exc:
        console.print(f"[red]Config error:[/red] {exc}")
        raise SystemExit(1) from exc

    if not connections:
        console.print("[yellow]No connections configured.[/yellow] Run [cyan]amnesic init[/cyan] first.")
        raise SystemExit(1)

    if connection is not None and connection not in connections:
        available = ", ".join(connections.keys())
        console.print(f"[red]Connection '{connection}' not found.[/red] Available: {available}")
        raise SystemExit(1)

    targets = (
        {connection: connections[connection]}
        if connection is not None
        else connections
    )

    table = Table(title="Connection Test Results", show_header=True, header_style="bold")
    table.add_column("Connection", style="cyan")
    table.add_column("Driver")
    table.add_column("Database")
    table.add_column("Status", justify="center")
    table.add_column("Details")

    any_failed = False

    for name, conn_cfg in targets.items():
        try:
            from sqlalchemy import text

            engine = get_engine(conn_cfg)
            with engine.connect() as conn_db:
                conn_db.execute(text("SELECT 1"))
            table.add_row(
                name,
                conn_cfg.driver,
                conn_cfg.database,
                "[green]✓[/green]",
                "OK",
            )
        except Exception as exc:
            any_failed = True
            error_msg = str(exc).splitlines()[0][:80]
            table.add_row(
                name,
                conn_cfg.driver,
                conn_cfg.database,
                "[red]✗[/red]",
                f"[red]{error_msg}[/red]",
            )

    console.print(table)

    if any_failed:
        raise SystemExit(1)
