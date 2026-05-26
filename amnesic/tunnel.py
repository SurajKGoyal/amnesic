"""
Optional SSH tunnel management.

Ported from coding-agent/tools/db_query.py with one change: tunnel config
comes from ConnectionConfig instead of global settings.

The function is idempotent — if the port is already reachable it returns
immediately without touching the tunnel script.
"""

import os
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
      4. Pick interpreter based on script extension and platform.
      5. Run the script with a 15s timeout.
      6. Raise RuntimeError with stderr if it exits non-zero.

    Supported script types:
      .sh              — bash (macOS / Linux)
      .ps1             — PowerShell (Windows)
      .bat / .cmd      — cmd.exe (Windows)
      no extension     — passed directly (uses shebang on POSIX; unsupported on Windows)

    Raises:
        RuntimeError: if the tunnel script is missing, unsupported, or exits with an error.
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

    # Step 4: pick interpreter based on extension
    suffix = script.suffix.lower()
    if suffix == ".sh":
        cmd = ["bash", str(script)]
    elif suffix == ".ps1":
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)]
    elif suffix in (".bat", ".cmd"):
        cmd = ["cmd.exe", "/c", str(script)]
    else:
        # No extension or unknown — let OS decide (uses shebang on POSIX)
        if os.name == "nt":
            raise RuntimeError(
                f"Tunnel script extension '{suffix or '(none)'}' not supported on Windows. "
                f"Use .ps1, .bat, or .cmd."
            )
        cmd = [str(script)]

    # Step 5: run the script
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=15,
    )

    # Step 6: check exit code
    if result.returncode != 0:
        raise RuntimeError(f"Tunnel failed: {result.stderr.strip()}")
