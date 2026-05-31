"""
amnesic CLI

Commands:
  amnesic init              — interactive setup wizard (first-time)
  amnesic init --template   — write the blank commented template (for hand-editing)
  amnesic add               — add another connection to existing config
  amnesic set-secret NAME   — set or rotate a secret in ~/.config/amnesic/.env
  amnesic test [connection] — verify connectivity for one or all connections
"""

from pathlib import Path
import re

import click
from rich.console import Console
from rich.table import Table

from amnesic._paths import config_dir, connections_path, env_path

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

console = Console()


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx: click.Context) -> None:
    """amnesic — the MCP server that remembers your database.

    With no subcommand:
      • If stdin is piped (an MCP client launched us) → start the MCP server.
      • If stdin is a TTY (you ran 'amnesic' in your terminal) → show this help.
    """
    if ctx.invoked_subcommand is not None:
        return

    import sys

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

    if demo:
        _install_demo_connection()
        return

    if template:
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _CONFIG_FILE.write_text(_TEMPLATE)
        console.print(f"[green]Created:[/green] {_CONFIG_FILE}")
        console.print()
        console.print("[bold]Next steps:[/bold]")
        console.print(f"  1. Edit [cyan]{_CONFIG_FILE}[/cyan] — add your database connections")
        console.print(f"  2. Export any [cyan]${{ENV_VAR}}[/cyan] credentials your connections need")
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
