"""
Tests for schema-cache staleness reporting in db_get_schema (#12):
cached_at / cache_age_days / stale / hint, the TTL resolution order, and the
backwards-compatible migration for stores that predate the cached_at column.

No real DB required — the schema fetch is injected and the store is a real
SQLite knowledge file under a tmp AMNESIC_HOME.
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from amnesic._paths import knowledge_path
from amnesic.config import ConnectionConfig
from amnesic.store import KnowledgeStore


@pytest.fixture()
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> KnowledgeStore:
    monkeypatch.setenv("AMNESIC_HOME", str(tmp_path))
    monkeypatch.delenv("AMNESIC_SCHEMA_TTL_DAYS", raising=False)
    return KnowledgeStore("test_conn")


COLUMNS = [
    {"column_name": "id", "data_type": "int", "is_nullable": "NO", "max_length": None},
    {"column_name": "name", "data_type": "varchar", "is_nullable": "YES", "max_length": 255},
]


def _get_schema(store, monkeypatch, *, ttl_env=None, ttl_conn=None):
    conn_cfg = ConnectionConfig(
        name="test_conn",
        driver="sqlite",
        database=":memory:",
        schema_cache_ttl_days=ttl_conn,
    )
    monkeypatch.setattr("amnesic.tools.schema.load_config", lambda: {"test_conn": conn_cfg})
    monkeypatch.setattr(
        "amnesic.tools.schema.resolve_connection", lambda _c, _conns: conn_cfg
    )
    monkeypatch.setattr("amnesic.tools.schema.get_store", lambda _name: store)
    monkeypatch.setattr(
        "amnesic.tools.schema._fetch_schema_from_db", lambda _t, _c: [dict(c) for c in COLUMNS]
    )
    if ttl_env is None:
        monkeypatch.delenv("AMNESIC_SCHEMA_TTL_DAYS", raising=False)
    else:
        monkeypatch.setenv("AMNESIC_SCHEMA_TTL_DAYS", str(ttl_env))
    from amnesic.tools.schema import db_get_schema

    return db_get_schema("users", connection="test_conn")


def _backdate(store, days: int, fmt: str = "%Y-%m-%dT%H:%M:%SZ") -> None:
    old = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(fmt)
    with store._lock:
        store._conn.execute("UPDATE schema_cache SET cached_at = ?", (old,))
        store._conn.commit()


class TestSchemaCacheStaleness:
    def test_fresh_fetch_stamps_current_timestamp(self, store, monkeypatch):
        result = _get_schema(store, monkeypatch)
        assert result["cached"] is False
        assert result["cache_age_days"] == 0
        assert result["stale"] is False
        stamp = datetime.strptime(result["cached_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        assert datetime.now(timezone.utc) - stamp < timedelta(minutes=5)

    def test_cached_result_reports_fresh_age(self, store, monkeypatch):
        _get_schema(store, monkeypatch)  # populate the cache
        result = _get_schema(store, monkeypatch)
        assert result["cached"] is True
        assert result["cache_age_days"] == 0
        assert result["stale"] is False
        assert "hint" not in result

    def test_backdated_stamp_is_stale_with_hint(self, store, monkeypatch):
        _get_schema(store, monkeypatch)
        _backdate(store, 183)
        result = _get_schema(store, monkeypatch)
        assert result["cached"] is True
        assert result["cache_age_days"] == 183
        assert result["stale"] is True
        assert "force_refresh=true" in result["hint"]
        assert "183 days ago" in result["hint"]

    def test_env_ttl_zero_suppresses_flag(self, store, monkeypatch):
        _get_schema(store, monkeypatch)
        _backdate(store, 400)
        result = _get_schema(store, monkeypatch, ttl_env=0)
        assert result["cache_age_days"] == 400
        assert result["stale"] is None
        assert "hint" not in result

    def test_connection_ttl_overrides_env(self, store, monkeypatch):
        _get_schema(store, monkeypatch)
        _backdate(store, 45)
        # Env disables staleness reporting, but the connection pins 30 days,
        # so 45 must still be stale — per-connection wins.
        result = _get_schema(store, monkeypatch, ttl_env=0, ttl_conn=30)
        assert result["stale"] is True

    def test_connection_ttl_zero_can_disable_despite_default(self, store, monkeypatch):
        _get_schema(store, monkeypatch)
        _backdate(store, 45)
        result = _get_schema(store, monkeypatch, ttl_conn=0)
        assert result["cache_age_days"] == 45
        assert result["stale"] is None

    def test_legacy_sqlite_stamp_format_is_parsed(self, store, monkeypatch):
        _get_schema(store, monkeypatch)
        _backdate(store, 100, fmt="%Y-%m-%d %H:%M:%S")
        result = _get_schema(store, monkeypatch)
        assert result["cache_age_days"] == 100
        assert result["stale"] is True


class TestCachedAtMigration:
    def test_pre_migration_store_opens_migrates_and_reports_unknown_age(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("AMNESIC_HOME", str(tmp_path))
        monkeypatch.delenv("AMNESIC_SCHEMA_TTL_DAYS", raising=False)

        # Hand-build a legacy knowledge file whose schema_cache lacks the
        # cached_at column entirely (pre-staleness layout).
        db_path = knowledge_path("legacy_conn")
        db_path.parent.mkdir(parents=True, exist_ok=True)
        legacy = sqlite3.connect(db_path)
        legacy.execute(
            "CREATE TABLE schema_cache ("
            " table_fqn TEXT NOT NULL, column_name TEXT NOT NULL,"
            " data_type TEXT, is_nullable TEXT, max_length INTEGER,"
            " PRIMARY KEY (table_fqn, column_name))"
        )
        legacy.execute(
            "INSERT INTO schema_cache (table_fqn, column_name, data_type, is_nullable)"
            " VALUES ('users', 'id', 'int', 'NO')"
        )
        legacy.commit()
        legacy.close()

        # Opening the store must migrate without touching the data...
        store = KnowledgeStore("legacy_conn")
        cols = {
            row["name"]
            for row in store._conn.execute("PRAGMA table_info(schema_cache)").fetchall()
        }
        assert "cached_at" in cols
        assert store.get_cached_schema("users") == [
            {"column_name": "id", "data_type": "int", "is_nullable": "NO", "max_length": None}
        ]

        # ...and db_get_schema must report unknown age, not a bogus one.
        result = _get_schema(store, monkeypatch)
        assert result["cached"] is True
        assert result["cached_at"] is None
        assert result["cache_age_days"] is None
        assert result["stale"] is None
