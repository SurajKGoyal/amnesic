"""
Unit tests for amnesic._update_check — the CLI "newer version exists" notice.

No real network calls: _fetch_latest_from_pypi is always monkeypatched. The
24h cache is redirected to a tmp file. `now` is injected for time control.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from amnesic import _update_check as uc


@pytest.fixture()
def cache_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the update-check cache + config dir to a tmp location."""
    path = tmp_path / ".update_check"
    monkeypatch.setattr(uc, "_cache_path", lambda: path)
    monkeypatch.setattr(uc, "config_dir", lambda: tmp_path)
    monkeypatch.delenv(uc._OPT_OUT_ENV, raising=False)
    return path


# ---------------------------------------------------------------------------
# Version parsing
# ---------------------------------------------------------------------------

class TestParseVersion:
    def test_basic(self):
        assert uc._parse_version("0.2.2") == (0, 2, 2)

    def test_more_segments(self):
        assert uc._parse_version("1.10.3") == (1, 10, 3)
        assert uc._parse_version("1.2") > uc._parse_version("1.1.9")

    def test_prerelease_truncates_at_non_numeric(self):
        # '0.2.3rc1' -> (0, 2, 3) — the rc suffix is dropped, but it still
        # compares as >= the numeric core (good enough for a nudge).
        assert uc._parse_version("0.2.3rc1") == (0, 2, 3)

    def test_local_suffix_truncates(self):
        assert uc._parse_version("0.2.2+local") == (0, 2, 2)

    def test_garbage_is_zero(self):
        assert uc._parse_version("not-a-version") == (0,)

    def test_ordering(self):
        assert uc._parse_version("0.2.10") > uc._parse_version("0.2.9")
        assert uc._parse_version("0.3.0") > uc._parse_version("0.2.99")


# ---------------------------------------------------------------------------
# check_for_update
# ---------------------------------------------------------------------------

class TestCheckForUpdate:
    def test_newer_returns_notice(self, cache_file, monkeypatch):
        monkeypatch.setattr(uc, "_fetch_latest_from_pypi", lambda timeout=2.0: "0.3.0")
        notice = uc.check_for_update("0.2.2")
        assert notice is not None
        assert "0.2.2" in notice and "0.3.0" in notice

    def test_same_returns_none(self, cache_file, monkeypatch):
        monkeypatch.setattr(uc, "_fetch_latest_from_pypi", lambda timeout=2.0: "0.2.2")
        assert uc.check_for_update("0.2.2") is None

    def test_older_latest_returns_none(self, cache_file, monkeypatch):
        # Defensive: if PyPI somehow reports an older version, never nag.
        monkeypatch.setattr(uc, "_fetch_latest_from_pypi", lambda timeout=2.0: "0.2.0")
        assert uc.check_for_update("0.2.2") is None

    def test_opt_out_env_short_circuits(self, cache_file, monkeypatch):
        monkeypatch.setenv(uc._OPT_OUT_ENV, "1")

        def _boom(timeout=2.0):
            raise AssertionError("network must not be touched when opted out")

        monkeypatch.setattr(uc, "_fetch_latest_from_pypi", _boom)
        assert uc.check_for_update("0.2.2") is None

    def test_fetch_failure_is_silent(self, cache_file, monkeypatch):
        monkeypatch.setattr(uc, "_fetch_latest_from_pypi", lambda timeout=2.0: None)
        assert uc.check_for_update("0.2.2") is None


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------

class TestCache:
    def test_fresh_cache_skips_network(self, cache_file, monkeypatch):
        cache_file.write_text(json.dumps({"checked_at": 1_000_000, "latest": "0.9.9"}))

        def _boom(timeout=2.0):
            raise AssertionError("fresh cache must not hit the network")

        monkeypatch.setattr(uc, "_fetch_latest_from_pypi", _boom)
        # now only 1h after checked_at -> within the 24h TTL
        assert uc.get_latest_version(now=1_000_000 + 3600) == "0.9.9"

    def test_stale_cache_refetches(self, cache_file, monkeypatch):
        cache_file.write_text(json.dumps({"checked_at": 1_000_000, "latest": "0.9.9"}))
        monkeypatch.setattr(uc, "_fetch_latest_from_pypi", lambda timeout=2.0: "1.0.0")
        # now is 25h later -> TTL expired
        later = 1_000_000 + uc._CACHE_TTL_SECONDS + 3600
        assert uc.get_latest_version(now=later) == "1.0.0"
        # and the cache was refreshed
        assert json.loads(cache_file.read_text())["latest"] == "1.0.0"

    def test_fetch_result_is_cached(self, cache_file, monkeypatch):
        monkeypatch.setattr(uc, "_fetch_latest_from_pypi", lambda timeout=2.0: "2.0.0")
        assert uc.get_latest_version(now=5_000_000) == "2.0.0"
        assert cache_file.exists()
        assert json.loads(cache_file.read_text())["latest"] == "2.0.0"

    def test_corrupt_cache_is_ignored(self, cache_file, monkeypatch):
        cache_file.write_text("{not json")
        monkeypatch.setattr(uc, "_fetch_latest_from_pypi", lambda timeout=2.0: "3.0.0")
        assert uc.get_latest_version(now=5_000_000) == "3.0.0"
