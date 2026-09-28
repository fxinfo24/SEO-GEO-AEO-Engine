"""Storage tests that need no database."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from seo_geo_aeo.storage.postgres_store import (
    AuditStore,
    StoreConfigError,
    StoreError,
    _normalize_domain,
    _normalize_row,
    apply_migrations,
    get_conninfo,
)


def test_get_conninfo_requires_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(StoreConfigError, match="DATABASE_URL"):
        get_conninfo()


def test_get_conninfo_rejects_non_postgres_scheme(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "mysql://u:p@host/db")

    with pytest.raises(StoreConfigError, match="postgresql://"):
        get_conninfo()


def test_get_conninfo_accepts_both_postgres_schemes(monkeypatch):
    for url in ("postgresql://u:p@h/db", "postgres://u:p@h/db?sslmode=require"):
        monkeypatch.setenv("DATABASE_URL", url)
        assert get_conninfo() == url


def test_get_conninfo_accepts_libpq_keyword_form(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "host=localhost port=5432 dbname=app user=me")

    assert get_conninfo() == "host=localhost port=5432 dbname=app user=me"


def test_config_error_is_a_store_error():
    """The CLI catches StoreError once and must therefore catch config errors too."""
    assert issubclass(StoreConfigError, StoreError)


def test_audit_store_reads_database_url_from_env(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(StoreConfigError):
        AuditStore()


def test_normalize_row_converts_postgres_types_to_json_friendly():
    uid = UUID("12345678-1234-5678-1234-567812345678")
    ts = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

    row = _normalize_row({"id": uid, "score": Decimal("81.5"), "at": ts, "n": 3, "d": {"a": 1}})

    assert row == {
        "id": "12345678-1234-5678-1234-567812345678",
        "score": 81.5,
        "at": "2026-01-02T03:04:05+00:00",
        "n": 3,
        "d": {"a": 1},
    }
    assert isinstance(row["score"], float)


def test_normalize_domain_lowercases_and_strips():
    assert _normalize_domain("  Example.COM ") == "example.com"


def test_normalize_domain_rejects_empty():
    with pytest.raises(ValueError):
        _normalize_domain("   ")


def test_unreachable_database_raises_store_error_not_raw_psycopg():
    # Port 1 refuses instantly; the CLI must get a StoreError, not a traceback.
    store = AuditStore("postgresql://u:p@127.0.0.1:1/db")

    with pytest.raises(StoreError, match="connect"):
        store.list_prospects()


def test_apply_migrations_rejects_directory_without_sql(tmp_path: Path):
    with pytest.raises(StoreError, match="No .sql migrations"):
        apply_migrations("postgresql://u:p@127.0.0.1:1/db", tmp_path)
