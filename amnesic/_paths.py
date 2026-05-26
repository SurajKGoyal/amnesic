"""Cross-platform path resolution for amnesic.

Config directory selection order:
  1. $AMNESIC_HOME env var (if set) — for advanced users / testing
  2. Platform default:
     - Windows: %APPDATA%\\amnesic    (typically C:\\Users\\<u>\\AppData\\Roaming\\amnesic)
     - macOS / Linux / *BSD: $XDG_CONFIG_HOME/amnesic if set, else ~/.config/amnesic

All other amnesic files (.env, connections.toml, knowledge_*.db) live inside
this directory. Subdirectories are created on demand.
"""

import os
import sys
from pathlib import Path


def config_dir() -> Path:
    """Return the amnesic config directory for the current OS."""
    override = os.environ.get("AMNESIC_HOME")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "amnesic"
        # Fallback if APPDATA missing
        return Path.home() / "AppData" / "Roaming" / "amnesic"
    # macOS / Linux / *BSD
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "amnesic"
    return Path.home() / ".config" / "amnesic"


def connections_path() -> Path:
    return config_dir() / "connections.toml"


def env_path() -> Path:
    return config_dir() / ".env"


def knowledge_path(conn_name: str) -> Path:
    """Path to the SQLite knowledge store for a connection. Replaces dots with underscores."""
    safe = conn_name.replace(".", "_")
    return config_dir() / f"knowledge_{safe}.db"


def secure_file(path: Path) -> None:
    """Best-effort: restrict file to owner-only read/write.

    - On POSIX: chmod 0o600.
    - On Windows: no-op (file inherits user-profile ACLs which are already
      user-only by default; setting NTFS ACLs explicitly requires win32api).
    """
    if os.name == "posix":
        try:
            os.chmod(path, 0o600)
        except OSError:
            # Filesystems without permission support (FAT, network drives) — skip
            pass
