"""Cross-platform path resolution tests."""
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from amnesic._paths import config_dir, connections_path, env_path, knowledge_path


def test_amnesic_home_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """$AMNESIC_HOME wins over platform defaults."""
    monkeypatch.setenv("AMNESIC_HOME", str(tmp_path / "custom"))
    assert config_dir() == tmp_path / "custom"


def test_amnesic_home_expands_tilde(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AMNESIC_HOME", "~/amnesic-test")
    assert config_dir() == Path.home() / "amnesic-test"


def test_xdg_config_home_on_posix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """XDG_CONFIG_HOME used on POSIX when AMNESIC_HOME unset."""
    if sys.platform == "win32":
        return  # XDG not applicable on Windows
    monkeypatch.delenv("AMNESIC_HOME", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert config_dir() == tmp_path / "amnesic"


def test_macos_linux_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """POSIX default: ~/.config/amnesic when XDG_CONFIG_HOME unset."""
    if sys.platform == "win32":
        return
    monkeypatch.delenv("AMNESIC_HOME", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert config_dir() == Path.home() / ".config" / "amnesic"


def test_windows_uses_appdata(monkeypatch: pytest.MonkeyPatch) -> None:
    """On Windows, %APPDATA%\\amnesic is used."""
    with patch("amnesic._paths.sys.platform", "win32"):
        monkeypatch.delenv("AMNESIC_HOME", raising=False)
        monkeypatch.setenv("APPDATA", "C:\\Users\\test\\AppData\\Roaming")
        result = config_dir()
        assert result == Path("C:\\Users\\test\\AppData\\Roaming") / "amnesic"


def test_helpers_compose_from_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """connections_path, env_path, knowledge_path all relative to config_dir."""
    monkeypatch.setenv("AMNESIC_HOME", str(tmp_path))
    assert connections_path() == tmp_path / "connections.toml"
    assert env_path() == tmp_path / ".env"
    assert knowledge_path("drive.prod") == tmp_path / "knowledge_drive_prod.db"
    assert knowledge_path("simple") == tmp_path / "knowledge_simple.db"
