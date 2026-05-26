"""
amnesic setup wizard — interactive connection setup helpers.

Public API:
  run_wizard(welcome, loop)              — interactive wizard flow
  append_connection_to_toml(name, conn)  — append [connections.X] block to TOML
  upsert_env_var(name, value)            — update/insert in .env, chmod 600
  test_connection(conn_cfg)              — returns (ok, error_msg)
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import click

from amnesic._paths import config_dir, connections_path, env_path, secure_file

_CONFIG_DIR = config_dir()
_CONFIG_FILE = connections_path()
_ENV_FILE = env_path()

_CONN_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)?$")
_ENV_VAR_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")

_DRIVER_DEFAULTS: dict[str, dict[str, Any]] = {
    "postgres": {"port": 5432, "default_schema": "public",  "needs_creds": True},
    "mysql":    {"port": 3306, "default_schema": "",        "needs_creds": True},
    "mssql":    {"port": 1433, "default_schema": "dbo",     "needs_creds": True},
    "sqlite":   {"port": 0,    "default_schema": "",        "needs_creds": False},
}

_DRIVER_CHOICES = {
    "1": "postgres",
    "2": "mysql",
    "3": "mssql",
    "4": "sqlite",
}

_DRIVER_LABELS = {
    "postgres": "PostgreSQL",
    "mysql":    "MySQL / MariaDB",
    "mssql":    "Microsoft SQL Server (MSSQL)",
    "sqlite":   "SQLite",
}


def _env_var_for_conn(name: str) -> str:
    """Derive the env var name for a connection's password.

    e.g. "drive.prod" -> "DRIVE_PROD_PASSWORD"
    """
    return name.upper().replace(".", "_") + "_PASSWORD"


def _prompt_connection_name() -> str:
    """Prompt for a valid connection name, retrying until valid."""
    while True:
        name = click.prompt("? Connection name (e.g. orders.prod, analytics)")
        if _CONN_NAME_RE.match(name):
            return name
        click.echo(
            "  [red]Invalid name.[/red] Use lowercase letters, digits, underscores,"
            " with at most one dot (e.g. orders.prod)."
        )
        click.echo(
            "  Name must match: ^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*)?$"
        )


def _prompt_driver() -> str:
    """Prompt for driver selection, retrying until a valid choice is made."""
    click.echo("? Database type:")
    click.echo("  1) PostgreSQL")
    click.echo("  2) MySQL / MariaDB")
    click.echo("  3) Microsoft SQL Server (MSSQL)")
    click.echo("  4) SQLite")
    while True:
        choice = click.prompt("? Choose [1-4]")
        if choice in _DRIVER_CHOICES:
            return _DRIVER_CHOICES[choice]
        click.echo("  Please enter a number between 1 and 4.")


# Python package that amnesic actually imports for each driver, and the
# install command snippet we suggest if it's missing.
_DRIVER_PACKAGE = {
    "postgres": ("psycopg2",      "psycopg2-binary"),
    "mysql":    ("pymysql",       "pymysql"),
    "mssql":    ("pymssql",       "pymssql"),
    "sqlite":   ("sqlite3",       None),  # stdlib, always available
}


def _is_driver_installed(driver: str) -> bool:
    """Check whether the Python driver package for `driver` is importable."""
    import importlib.util

    package_name, _ = _DRIVER_PACKAGE[driver]
    return importlib.util.find_spec(package_name) is not None


def _print_missing_driver_help(driver: str) -> None:
    """Show actionable install commands for a missing driver, then exit."""
    package_name, pip_name = _DRIVER_PACKAGE[driver]
    label = _DRIVER_LABELS[driver]

    click.echo("")
    click.echo(f"  ✗ The {label} driver ('{package_name}') is not installed in this amnesic.")
    click.echo("")
    click.echo("  Install it with ONE of these (match how you installed amnesic):")
    click.echo("")
    click.echo(f"      pipx inject amnesic {pip_name}")
    click.echo(f"      uv tool install --with {pip_name} --reinstall amnesic")
    click.echo(f"      pip install {pip_name}")
    click.echo("")
    click.echo("  Then re-run `amnesic init` (or `amnesic add`) to continue.")
    click.echo("")
    raise SystemExit(1)


def _prompt_driver_with_check() -> str:
    """Prompt for a driver, then verify the Python package is installed."""
    driver = _prompt_driver()
    if not _is_driver_installed(driver):
        _print_missing_driver_help(driver)
    return driver


def _collect_connection_inputs(welcome: bool = True) -> dict[str, Any] | None:
    """
    Run one pass of the connection input prompts.

    Returns a dict with keys: name, driver, server, port, database,
    user, password, tunnel_script — or None if the user aborted.
    """
    name = _prompt_connection_name()
    driver = _prompt_driver_with_check()
    defaults = _DRIVER_DEFAULTS[driver]

    if driver == "sqlite":
        raw_path = click.prompt("? Path to database file")
        db_path = str(Path(raw_path).expanduser())
        return {
            "name": name,
            "driver": driver,
            "server": "",
            "port": 0,
            "database": db_path,
            "user": "",
            "password": "",
            "tunnel_script": "",
        }

    # Network database
    server = click.prompt("? Server hostname", default="localhost")
    port = click.prompt("? Port", default=defaults["port"], type=int)
    database = click.prompt("? Database name")
    user = click.prompt("? Username")
    password = click.prompt("? Password (hidden, will be saved to ~/.config/amnesic/.env)", hide_input=True)

    tunnel_script = ""
    if click.confirm("? Use an SSH tunnel?", default=False):
        while True:
            raw_tunnel = click.prompt("? Tunnel script path")
            tunnel_path = Path(raw_tunnel).expanduser()
            if tunnel_path.exists():
                tunnel_script = str(tunnel_path)
                break
            if click.confirm(
                f"  Warning: '{tunnel_path}' does not exist. Use anyway?",
                default=False,
            ):
                tunnel_script = str(tunnel_path)
                break

    return {
        "name": name,
        "driver": driver,
        "server": server,
        "port": port,
        "database": database,
        "user": user,
        "password": password,
        "tunnel_script": tunnel_script,
    }


def test_connection(conn_cfg: "ConnectionConfig") -> tuple[bool, str]:  # type: ignore[name-defined]
    """
    Try SELECT 1 against the connection. Returns (ok, error_message).
    Lazy-imports SQLAlchemy for fast CLI startup.
    """
    try:
        from sqlalchemy import text

        from amnesic.drivers import get_engine

        engine = get_engine(conn_cfg)
        with engine.connect() as conn_db:
            conn_db.execute(text("SELECT 1"))
        return True, ""
    except Exception as exc:
        return False, str(exc).splitlines()[0][:120]


def _build_conn_cfg(inputs: dict[str, Any]) -> "ConnectionConfig":  # type: ignore[name-defined]
    """Build a ConnectionConfig from collected wizard inputs."""
    from amnesic.config import ConnectionConfig

    return ConnectionConfig(
        name=inputs["name"],
        driver=inputs["driver"],
        server=inputs["server"],
        port=inputs["port"],
        database=inputs["database"],
        user=inputs["user"],
        password=inputs["password"],
        tunnel_script=inputs["tunnel_script"],
    )


def _save_connection(inputs: dict[str, Any]) -> None:
    """Persist the connection to connections.toml and password to .env."""
    name = inputs["name"]
    driver = inputs["driver"]

    conn_dict: dict[str, Any] = {"driver": driver}

    if driver == "sqlite":
        conn_dict["database"] = inputs["database"]
    else:
        env_var = _env_var_for_conn(name)
        conn_dict["server"] = inputs["server"]
        conn_dict["port"] = inputs["port"]
        conn_dict["database"] = inputs["database"]
        conn_dict["user"] = inputs["user"]
        conn_dict["password"] = "${" + env_var + "}"
        if inputs["tunnel_script"]:
            conn_dict["tunnel_script"] = inputs["tunnel_script"]

        upsert_env_var(env_var, inputs["password"])
        click.echo(
            f"  ✓ Password saved to ~/.config/amnesic/.env (chmod 600)"
        )

    append_connection_to_toml(name, conn_dict)
    click.echo(
        f"  ✓ Connection '{name}' saved to ~/.config/amnesic/connections.toml"
    )


def _run_single_connection(welcome: bool) -> None:
    """Run the wizard for a single connection, with retry on test failure."""
    while True:
        inputs = _collect_connection_inputs(welcome=welcome)

        click.echo()
        click.echo("  Testing connection...", nl=False)

        conn_cfg = _build_conn_cfg(inputs)
        ok, error_msg = test_connection(conn_cfg)

        if ok:
            click.echo(" ✓ Connected.")
            click.echo()
            _save_connection(inputs)
            return
        else:
            click.echo(f" ✗ Failed.")
            click.echo(f"  Error: {error_msg}")
            click.echo()
            if not click.confirm("? Retry with different inputs?", default=False):
                raise SystemExit(1)
            click.echo()


def run_wizard(welcome: bool = True, loop: bool = True) -> None:
    """
    Run the interactive connection setup wizard.

    Args:
        welcome: Print the welcome banner before the first prompt.
        loop:    After saving a connection, offer to add another.
    """
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    if welcome:
        click.echo()
        click.echo("Welcome to amnesic — the MCP server that remembers your database.")
        click.echo("Let's set up your first connection.")
        click.echo()

    _run_single_connection(welcome=welcome)

    if loop:
        click.echo()
        while click.confirm("? Add another connection?", default=False):
            click.echo()
            _run_single_connection(welcome=False)
            click.echo()

    click.echo()
    click.echo("Next steps:")
    click.echo(f"  1. Run [cyan]amnesic test[/cyan] to verify connectivity")
    click.echo(f"  2. Add amnesic to your MCP client config (see README for snippet)")
    click.echo(f"  3. Start your AI client and ask: 'what databases do I have access to?'")
    click.echo()


def append_connection_to_toml(name: str, conn_dict: dict[str, Any]) -> None:
    """
    Append a [connections.{name}] block to connections.toml.

    Uses plain string concatenation — never parses and re-serializes the file,
    so existing comments and formatting are always preserved.
    """
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    lines: list[str] = [f"\n[connections.{name}]"]
    for key, value in conn_dict.items():
        if isinstance(value, str):
            lines.append(f'{key} = "{value}"')
        elif isinstance(value, int):
            lines.append(f"{key} = {value}")
        else:
            lines.append(f'{key} = "{value}"')

    block = "\n".join(lines) + "\n"

    with open(_CONFIG_FILE, "a") as fh:
        fh.write(block)


def upsert_env_var(name: str, value: str) -> None:
    """
    Insert or replace NAME=VALUE in ~/.config/amnesic/.env.

    Preserves all other lines and their order.
    Creates the file if it doesn't exist.
    Always sets chmod 600 after writing.
    """
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    existing_lines: list[str] = []
    if _ENV_FILE.exists():
        existing_lines = _ENV_FILE.read_text().splitlines()

    # Rebuild: replace the matching key line, preserve everything else
    new_lines: list[str] = []
    replaced = False
    for line in existing_lines:
        stripped = line.strip()
        if (
            stripped
            and not stripped.startswith("#")
            and "=" in stripped
            and stripped.split("=", 1)[0].strip() == name
        ):
            new_lines.append(f"{name}={value}")
            replaced = True
        else:
            new_lines.append(line)

    if not replaced:
        new_lines.append(f"{name}={value}")

    content = "\n".join(new_lines)
    if content and not content.endswith("\n"):
        content += "\n"

    _ENV_FILE.write_text(content)
    secure_file(_ENV_FILE)
