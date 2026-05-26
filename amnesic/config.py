"""
TOML-based connection config loader with ${ENV_VAR} expansion.

Supports two-level hierarchy [connections.product.env] and flat [connections.name].
A section is a leaf connection if it contains a 'driver' key.

Canonical connection names use dot notation: orders.prod, orders.staging, analytics.

Config location (in priority order):
  1. AMNESIC_CONFIG env var
  2. ~/.config/amnesic/connections.toml
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib  # Python 3.11+
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        raise ImportError("tomllib not available — requires Python 3.11+")


_ENV_VAR_RE = re.compile(r"\$\{([^}]+)\}")

_DRIVER_DEFAULT_SCHEMA = {
    "mssql": "dbo",
    "postgres": "public",
    "postgresql": "public",
    "mysql": "public",
    "sqlite": "",
}


class ConfigError(Exception):
    """Raised for configuration problems (missing vars, invalid format, etc.)."""


@dataclass
class ConnectionConfig:
    name: str
    driver: str
    server: str = ""
    port: int = 0
    database: str = ""
    user: str = ""
    password: str = ""
    tunnel_script: str = ""
    default_schema: str = ""

    def __post_init__(self) -> None:
        if not self.default_schema:
            self.default_schema = _DRIVER_DEFAULT_SCHEMA.get(self.driver.lower(), "")


def _expand_env_vars(value: str) -> str:
    """Expand ${VAR} references in a string. Raises ConfigError if a var is missing."""
    def replace(match: re.Match) -> str:  # type: ignore[type-arg]
        var_name = match.group(1)
        val = os.environ.get(var_name)
        if val is None:
            raise ConfigError(
                f"Environment variable '${{{var_name}}}' is not set. "
                f"Export it before starting amnesic."
            )
        return val

    return _ENV_VAR_RE.sub(replace, value)


def _expand_section(section: dict[str, Any]) -> dict[str, Any]:
    """Recursively expand ${VAR} in all string values of a dict."""
    result: dict[str, Any] = {}
    for key, value in section.items():
        if isinstance(value, str):
            result[key] = _expand_env_vars(value)
        elif isinstance(value, dict):
            result[key] = _expand_section(value)
        else:
            result[key] = value
    return result


def _is_leaf_connection(section: dict[str, Any]) -> bool:
    """A section is a leaf (actual connection) if it has a 'driver' key."""
    return "driver" in section


def _parse_connections(
    data: dict[str, Any], prefix: str = ""
) -> dict[str, ConnectionConfig]:
    """
    Recursively walk the connections table, returning a flat dict of
    {canonical_name: ConnectionConfig}.
    """
    connections: dict[str, ConnectionConfig] = {}

    for key, section in data.items():
        if not isinstance(section, dict):
            continue

        full_name = f"{prefix}.{key}" if prefix else key

        if _is_leaf_connection(section):
            expanded = _expand_section(section)
            try:
                conn = ConnectionConfig(
                    name=full_name,
                    driver=expanded.get("driver", ""),
                    server=expanded.get("server", ""),
                    port=int(expanded.get("port", 0)),
                    database=expanded.get("database", ""),
                    user=expanded.get("user", ""),
                    password=expanded.get("password", ""),
                    tunnel_script=expanded.get("tunnel_script", ""),
                    default_schema=expanded.get("default_schema", ""),
                )
            except (TypeError, ValueError) as exc:
                raise ConfigError(
                    f"Invalid config for connection '{full_name}': {exc}"
                ) from exc
            connections[full_name] = conn
        else:
            # Product group — recurse
            connections.update(_parse_connections(section, prefix=full_name))

    return connections


def load_config(path: str | Path | None = None) -> dict[str, ConnectionConfig]:
    """
    Load and parse the connections.toml config file.

    Returns a dict mapping canonical connection names to ConnectionConfig objects.
    Order is preserved (insertion order = TOML order).

    Raises:
        ConfigError: for missing files, missing env vars, or invalid structure.
    """
    if path is None:
        env_path = os.environ.get("AMNESIC_CONFIG")
        if env_path:
            path = Path(env_path)
        else:
            path = Path.home() / ".config" / "amnesic" / "connections.toml"

    path = Path(path)
    if not path.exists():
        raise ConfigError(
            f"Config file not found: {path}\n"
            f"Run 'amnesic init' to create a template."
        )

    try:
        with open(path, "rb") as fh:
            raw = tomllib.load(fh)
    except Exception as exc:
        raise ConfigError(f"Failed to parse config at {path}: {exc}") from exc

    connections_raw = raw.get("connections", {})
    if not isinstance(connections_raw, dict):
        raise ConfigError("Config must have a [connections] section.")

    return _parse_connections(connections_raw)


def get_default_connection_name(connections: dict[str, ConnectionConfig]) -> str:
    """Return the first defined connection name."""
    if not connections:
        raise ConfigError("No connections defined in config.")
    return next(iter(connections))


def resolve_connection(
    connection: str | None,
    connections: dict[str, ConnectionConfig],
) -> ConnectionConfig:
    """
    Resolve a connection name (or None → first connection) to a ConnectionConfig.

    Raises:
        ConfigError: if the named connection is not found.
    """
    if connection is None:
        name = get_default_connection_name(connections)
    else:
        name = connection

    if name not in connections:
        available = ", ".join(connections.keys())
        raise ConfigError(
            f"Connection '{name}' not found. Available: {available}"
        )
    return connections[name]
