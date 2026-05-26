"""
Unit tests for amnesic._wizard helpers.

No real DB connections, no interactive prompts — only the pure helper functions.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from amnesic._wizard import append_connection_to_toml, upsert_env_var, _CONFIG_DIR, _CONFIG_FILE, _ENV_FILE


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    Redirect _wizard module globals to a tmp directory so tests never touch
    ~/.config/amnesic.  Returns the tmp config dir path.
    """
    import amnesic._wizard as wizard_mod

    monkeypatch.setattr(wizard_mod, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(wizard_mod, "_CONFIG_FILE", tmp_path / "connections.toml")
    monkeypatch.setattr(wizard_mod, "_ENV_FILE", tmp_path / ".env")
    return tmp_path


# ---------------------------------------------------------------------------
# append_connection_to_toml
# ---------------------------------------------------------------------------


def test_append_connection_to_toml_creates_file(isolated_config: Path) -> None:
    """If connections.toml does not yet exist, the function creates it."""
    conn_dict = {
        "driver": "postgres",
        "server": "localhost",
        "port": 5432,
        "database": "mydb",
        "user": "alice",
        "password": "${MYDB_PASSWORD}",
    }
    append_connection_to_toml("mydb", conn_dict)

    config_file = isolated_config / "connections.toml"
    assert config_file.exists()
    content = config_file.read_text()
    assert "[connections.mydb]" in content
    assert 'driver = "postgres"' in content
    assert 'server = "localhost"' in content
    assert "port = 5432" in content
    assert 'password = "${MYDB_PASSWORD}"' in content


def test_append_connection_to_toml_preserves_existing_content(isolated_config: Path) -> None:
    """Existing TOML content (including comments) survives; new block is appended."""
    config_file = isolated_config / "connections.toml"
    original = (
        "# amnesic connections\n"
        "# keep this comment\n"
        "\n"
        "[connections.old]\n"
        'driver = "sqlite"\n'
        'database = "/tmp/old.db"\n'
    )
    config_file.write_text(original)

    append_connection_to_toml("new", {"driver": "mysql", "server": "db.example.com", "port": 3306, "database": "newdb", "user": "bob", "password": "${NEW_PASSWORD}"})

    content = config_file.read_text()
    # Original content preserved exactly
    assert "# keep this comment" in content
    assert "[connections.old]" in content
    assert 'driver = "sqlite"' in content
    # New block appended
    assert "[connections.new]" in content
    assert 'driver = "mysql"' in content
    assert 'server = "db.example.com"' in content


# ---------------------------------------------------------------------------
# upsert_env_var
# ---------------------------------------------------------------------------


def test_upsert_env_var_inserts_new(isolated_config: Path) -> None:
    """Inserting a key that does not yet exist appends it to .env."""
    upsert_env_var("MY_SECRET", "hunter2")

    env_file = isolated_config / ".env"
    assert env_file.exists()
    assert "MY_SECRET=hunter2" in env_file.read_text()


def test_upsert_env_var_replaces_existing(isolated_config: Path) -> None:
    """Updating an existing key replaces it in-place, no duplicates."""
    env_file = isolated_config / ".env"
    env_file.write_text("MY_SECRET=oldvalue\nOTHER=unchanged\n")

    upsert_env_var("MY_SECRET", "newvalue")

    content = env_file.read_text()
    assert "MY_SECRET=newvalue" in content
    assert "MY_SECRET=oldvalue" not in content
    # Exactly one occurrence of the key
    assert content.count("MY_SECRET=") == 1


def test_upsert_env_var_preserves_other_keys(isolated_config: Path) -> None:
    """Other keys in .env are never touched when upserting an unrelated key."""
    env_file = isolated_config / ".env"
    env_file.write_text(
        "# database secrets\n"
        "ALPHA=aaa\n"
        "BETA=bbb\n"
    )

    upsert_env_var("GAMMA", "ggg")

    content = env_file.read_text()
    assert "ALPHA=aaa" in content
    assert "BETA=bbb" in content
    assert "# database secrets" in content
    assert "GAMMA=ggg" in content


def test_upsert_env_var_sets_chmod_600(isolated_config: Path) -> None:
    """After any write, the .env file must have mode 0o600."""
    upsert_env_var("SECURE_KEY", "s3cr3t")

    env_file = isolated_config / ".env"
    file_mode = stat.S_IMODE(os.stat(env_file).st_mode)
    assert file_mode == 0o600, f"Expected 0o600, got {oct(file_mode)}"
