"""
Tests for amnesic.tunnel — SSH tunnel management.

Verifies that subprocess.TimeoutExpired is caught and re-raised as RuntimeError
with a descriptive message (Task E / H6).

Skipped on Windows: shell scripts require bash.
"""

import os
import stat
from pathlib import Path

import pytest

from amnesic.config import ConnectionConfig
from amnesic.tunnel import ensure_tunnel


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def make_conn(tunnel_script: str, server: str = "127.0.0.1", port: int = 19999) -> ConnectionConfig:
    """ConnectionConfig pointing at a port that is almost certainly closed."""
    return ConnectionConfig(
        name="test",
        driver="mssql",
        server=server,
        port=port,
        database="testdb",
        user="u",
        password="p",
        tunnel_script=tunnel_script,
    )


# ---------------------------------------------------------------------------
# TimeoutExpired → RuntimeError
# ---------------------------------------------------------------------------

@pytest.mark.skipif(os.name == "nt", reason="Shell script tests require bash (POSIX only)")
class TestTunnelTimeout:
    def test_slow_script_raises_runtime_error(self, tmp_path: Path):
        """
        A tunnel script that sleeps longer than the 15s timeout must raise
        RuntimeError (not subprocess.TimeoutExpired) with a descriptive message.
        """
        script = tmp_path / "slow_tunnel.sh"
        script.write_text("#!/bin/bash\nsleep 30\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

        conn = make_conn(tunnel_script=str(script))

        with pytest.raises(RuntimeError, match="did not complete"):
            ensure_tunnel(conn)

    def test_slow_script_not_timeout_expired(self, tmp_path: Path):
        """Ensure the original TimeoutExpired is NOT propagated — it must be wrapped."""
        import subprocess

        script = tmp_path / "slow_tunnel.sh"
        script.write_text("#!/bin/bash\nsleep 30\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

        conn = make_conn(tunnel_script=str(script))

        with pytest.raises(RuntimeError):
            ensure_tunnel(conn)

        # Verify by trying directly — just a sanity assertion
        try:
            ensure_tunnel(conn)
        except RuntimeError:
            pass  # expected
        except subprocess.TimeoutExpired:
            pytest.fail("TimeoutExpired leaked out of ensure_tunnel — must be wrapped in RuntimeError")
        except Exception:
            pass  # other errors (port check pass etc.) are fine

    def test_missing_script_raises_runtime_error(self, tmp_path: Path):
        """Non-existent script raises RuntimeError (existing behaviour, not regressed)."""
        conn = make_conn(tunnel_script=str(tmp_path / "nonexistent.sh"))
        with pytest.raises(RuntimeError, match="not found"):
            ensure_tunnel(conn)

    def test_no_tunnel_script_is_noop(self):
        """When tunnel_script is empty, ensure_tunnel returns immediately."""
        conn = make_conn(tunnel_script="")
        # Should not raise — no tunnel needed
        ensure_tunnel(conn)

    def test_failing_script_raises_runtime_error(self, tmp_path: Path):
        """A script that exits non-zero raises RuntimeError."""
        script = tmp_path / "fail_tunnel.sh"
        script.write_text("#!/bin/bash\nexit 1\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

        conn = make_conn(tunnel_script=str(script))
        with pytest.raises(RuntimeError, match="Tunnel failed"):
            ensure_tunnel(conn)
