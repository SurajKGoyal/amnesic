"""
db_list_connections — list configured connections without exposing secrets.
"""

from amnesic.config import load_config


def db_list_connections() -> dict:
    """
    List all configured database connections — never exposes passwords or usernames.

    Returns connection names, drivers, databases, and servers so you can choose
    which connection to pass to other tools. Credentials are stripped from output.

    Returns:
        {connections: [{name, driver, database, server}]}
    """
    connections = load_config()
    connection_list = [
        {
            "name": conn.name,
            "driver": conn.driver,
            "database": conn.database,
            "server": conn.server,
        }
        for conn in connections.values()
    ]
    return {"connections": connection_list}
