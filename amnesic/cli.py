"""
amnesic CLI — init and test commands.

  amnesic init    — create config directory and template connections.toml
  amnesic test    — verify connectivity for one or all configured connections
"""

from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

_CONFIG_DIR = Path.home() / ".config" / "amnesic"
_CONFIG_FILE = _CONFIG_DIR / "connections.toml"

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
# tunnel_script = "~/.scripts/mssql-tunnel.sh"

# Example: SQLite (no credentials needed)
# [connections.local]
# driver = "sqlite"
# database = "/Users/me/data/local.db"
"""

console = Console()


@click.group()
def cli() -> None:
    """amnesic — the MCP server that remembers your database."""


@cli.command()
def init() -> None:
    """Create the config directory and write a template connections.toml."""
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    if _CONFIG_FILE.exists():
        console.print(
            f"[yellow]Config already exists:[/yellow] {_CONFIG_FILE}\n"
            f"Edit it to add or update connections."
        )
    else:
        _CONFIG_FILE.write_text(_TEMPLATE)
        console.print(f"[green]Created:[/green] {_CONFIG_FILE}")

    console.print()
    console.print("[bold]Next steps:[/bold]")
    console.print(f"  1. Edit [cyan]{_CONFIG_FILE}[/cyan] — add your database connections")
    console.print(f"  2. Export any [cyan]${{ENV_VAR}}[/cyan] credentials your connections need")
    console.print(f"  3. Run [cyan]amnesic test[/cyan] to verify connectivity")
    console.print(f"  4. Add amnesic to your MCP client config (see README for snippet)")


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
