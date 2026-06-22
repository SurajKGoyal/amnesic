"""Best-effort "a newer amnesic is on PyPI" notice — CLI only, never the server.

Design constraints (all enforced here):
  * Runs only for interactive CLI use, never in MCP-server mode — a stray line
    on stdout/stderr can corrupt the stdio JSON-RPC stream a client reads.
  * Result is cached 24h in a small state file so we hit PyPI at most once a day,
    not on every invocation.
  * Opt-out via AMNESIC_NO_UPDATE_CHECK (any non-empty value).
  * Fail-silent: any network/parse/permission error returns None. A version
    check must never break a real command.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from amnesic._paths import config_dir

_CACHE_TTL_SECONDS = 24 * 60 * 60
_PYPI_URL = "https://pypi.org/pypi/amnesic/json"
_OPT_OUT_ENV = "AMNESIC_NO_UPDATE_CHECK"


def _cache_path() -> Path:
    return config_dir() / ".update_check"


def _parse_version(v: str) -> tuple[int, ...]:
    """Parse a PEP 440-ish version into a comparable int tuple.

    Pre-release / local suffixes (e.g. '1.2.3rc1', '1.2.3+local') are truncated
    at the first non-numeric segment — good enough for a "newer exists" nudge
    without pulling in `packaging`.
    """
    parts: list[int] = []
    for chunk in v.strip().split("."):
        num = ""
        for ch in chunk:
            if ch.isdigit():
                num += ch
            else:
                break
        if num == "":
            break
        parts.append(int(num))
    return tuple(parts) or (0,)


def _read_cache() -> dict | None:
    try:
        raw = _cache_path().read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        return data
    except (OSError, ValueError):
        return None


def _write_cache(latest: str) -> None:
    try:
        config_dir().mkdir(parents=True, exist_ok=True)
        _cache_path().write_text(
            json.dumps({"checked_at": int(time.time()), "latest": latest}),
            encoding="utf-8",
        )
    except OSError:
        pass  # cache is an optimization, never a hard requirement


def _fetch_latest_from_pypi(timeout: float = 2.0) -> str | None:
    """Return the latest released version string from PyPI, or None on failure."""
    import urllib.request

    try:
        with urllib.request.urlopen(_PYPI_URL, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        version = payload.get("info", {}).get("version")
        return version if isinstance(version, str) and version else None
    except Exception:
        return None


def get_latest_version(*, now: float | None = None) -> str | None:
    """Latest amnesic version on PyPI, using the 24h cache when fresh.

    `now` is injectable for tests. Returns None on any failure (fail-silent).
    """
    current_time = time.time() if now is None else now

    cache = _read_cache()
    if cache is not None:
        checked_at = cache.get("checked_at", 0)
        latest = cache.get("latest")
        if (
            isinstance(checked_at, (int, float))
            and isinstance(latest, str)
            and current_time - checked_at < _CACHE_TTL_SECONDS
        ):
            return latest

    latest = _fetch_latest_from_pypi()
    if latest:
        _write_cache(latest)
    return latest


def check_for_update(current: str, *, now: float | None = None) -> str | None:
    """Return a one-line upgrade notice if a newer amnesic exists, else None.

    Honors the opt-out env var and is fully fail-silent.
    """
    if os.environ.get(_OPT_OUT_ENV):
        return None

    try:
        latest = get_latest_version(now=now)
    except Exception:
        return None

    if not latest:
        return None

    try:
        if _parse_version(latest) > _parse_version(current):
            return (
                f"A newer amnesic is available: {current} -> {latest}. "
                f"Upgrade with your installer (e.g. `uv tool upgrade amnesic` or "
                f"`pipx upgrade amnesic`). Silence this with {_OPT_OUT_ENV}=1."
            )
    except Exception:
        return None
    return None
