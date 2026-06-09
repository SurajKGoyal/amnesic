"""The CLI exposes --version / -V reading the package version."""

from click.testing import CliRunner

from amnesic import __version__
from amnesic.cli import cli


def test_long_version_flag():
    result = CliRunner().invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output
    assert "amnesic" in result.output


def test_short_version_flag():
    result = CliRunner().invoke(cli, ["-V"])
    assert result.exit_code == 0
    assert __version__ in result.output
