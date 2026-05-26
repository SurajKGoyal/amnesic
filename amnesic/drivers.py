"""
SQLAlchemy engine factory, cached per connection name.

Supports: PostgreSQL (psycopg2), MySQL (pymysql), MSSQL (pymssql), SQLite.

get_engine(conn) returns a cached Engine, creating one on first call.
URL-encodes user/password to handle special characters.
Starts tunnel first if conn.tunnel_script is set.
"""

import threading
from urllib.parse import quote_plus

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from amnesic.config import ConnectionConfig
from amnesic import tunnel

_engine_cache: dict[str, Engine] = {}
_engine_lock = threading.Lock()

_URL_TEMPLATES = {
    "postgres":   "postgresql+psycopg2://{user}:{password}@{server}:{port}/{database}",
    "postgresql": "postgresql+psycopg2://{user}:{password}@{server}:{port}/{database}",
    "mysql":      "mysql+pymysql://{user}:{password}@{server}:{port}/{database}",
    "mssql":      "mssql+pymssql://{user}:{password}@{server}:{port}/{database}",
    "sqlite":     "sqlite:///{database}",
}


def _build_url(conn: ConnectionConfig) -> str:
    driver = conn.driver.lower()
    template = _URL_TEMPLATES.get(driver)
    if template is None:
        supported = ", ".join(_URL_TEMPLATES.keys())
        raise ValueError(
            f"Unsupported driver '{conn.driver}'. Supported: {supported}"
        )

    if driver == "sqlite":
        return template.format(database=conn.database)

    return template.format(
        user=quote_plus(conn.user),
        password=quote_plus(conn.password),
        server=conn.server,
        port=conn.port,
        database=conn.database,
    )


def get_engine(conn: ConnectionConfig) -> Engine:
    """
    Return a cached SQLAlchemy Engine for the given connection.

    Calls ensure_tunnel() first if conn.tunnel_script is set.
    Engines are cached per connection name — one pool per connection.

    Raises:
        ValueError: for unsupported drivers.
        RuntimeError: if the tunnel script fails.
    """
    with _engine_lock:
        if conn.name in _engine_cache:
            return _engine_cache[conn.name]

        if conn.tunnel_script:
            tunnel.ensure_tunnel(conn)

        url = _build_url(conn)
        engine = create_engine(
            url,
            pool_pre_ping=True,
            pool_recycle=3600,
        )
        _engine_cache[conn.name] = engine
        return engine


def clear_engine_cache() -> None:
    """Dispose and remove all cached engines. Useful for testing."""
    with _engine_lock:
        for engine in _engine_cache.values():
            engine.dispose()
        _engine_cache.clear()
