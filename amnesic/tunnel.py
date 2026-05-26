"""
Optional SSH tunnel management.

Ported from coding-agent/tools/db_query.py with one change: tunnel config
comes from ConnectionConfig instead of global settings.

The function is idempotent — if the port is already reachable it returns
immediately without touching the tunnel script.
"""

import socket
import subprocess
from pathlib import Path

from amnesic.config import ConnectionConfig


def ensure_tunnel(conn: ConnectionConfig) -> None:
    """
    Ensure an SSH tunnel is running for the given connection.

    No-op if conn.tunnel_script is empty or if the port is already open.

    Steps:
      1. Socket check (server, port) with 2s timeout — return if already open.
      2. Expand ~ in tunnel_script path.
      3. Raise if the script file does not exist.
      4. Run the script via bash with a 15s timeout.
      5. Raise RuntimeError with stderr if it exits non-zero.

    Raises:
        RuntimeError: if the tunnel script is missing or exits with an error.
    """
    if not conn.tunnel_script:
        return

    # Step 1: check if port is already reachable
    try:
        with socket.create_connection((conn.server, conn.port), timeout=2):
            return  # already up
    except OSError:
        pass

    # Step 2: expand ~
    script = Path(conn.tunnel_script).expanduser()

    # Step 3: verify script exists
    if not script.exists():
        raise RuntimeError(f"Tunnel script not found: {script}")

    # Step 4: run the script
    result = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        timeout=15,
    )

    # Step 5: check exit code
    if result.returncode != 0:
        raise RuntimeError(f"Tunnel failed: {result.stderr.strip()}")
