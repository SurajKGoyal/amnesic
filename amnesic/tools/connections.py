"""
db_list_connections — list configured connections without exposing secrets.
"""

from amnesic.config import ConfigNotFoundError, load_config


def db_list_connections() -> dict:
    """
    List all configured database connections — never exposes passwords or usernames.

    Returns connection names, drivers, databases, and servers so you can choose
    which connection to pass to other tools. Credentials are stripped from output.

    Returns:
        {connections: [{name, driver, database, server}][, hint]}
        On a first run (no connections.toml yet) connections is [] and hint
        says what the user should run — the server's instructions tell the
        model to look for exactly that. A config that exists but is broken
        still raises, so its error reaches the user.
    """
    try:
        connections = load_config()
    except ConfigNotFoundError:
        return {
            "connections": [],
            "hint": "No connections configured yet. Ask the user to run "
                    "'amnesic init' (or 'amnesic init --demo' to try a sample database) "
                    "in their terminal.",
        }
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
