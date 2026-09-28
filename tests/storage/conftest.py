"""Fixtures for storage tests that need a real PostgreSQL.

Set TEST_DATABASE_URL to a throwaway database, e.g.:

    docker run -d --name pg-test -e POSTGRES_PASSWORD=test -p 55432:5432 postgres:16-alpine
    export TEST_DATABASE_URL=postgresql://postgres:test@localhost:55432/postgres

Each test gets its own schema (created before, dropped after), so tests are
isolated and the target database is left clean. Without TEST_DATABASE_URL the
integration tests skip locally — but with REQUIRE_DB_TESTS=1 (set in CI) a
missing database is a hard failure, so CI can never go green by silently
skipping the whole storage suite.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from seo_geo_aeo.storage.postgres_store import AuditStore, apply_migrations

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


@pytest.fixture
def schema_conninfo() -> Iterator[str]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        if os.environ.get("REQUIRE_DB_TESTS") == "1":
            pytest.fail("REQUIRE_DB_TESTS=1 but TEST_DATABASE_URL is not set")
        pytest.skip("TEST_DATABASE_URL not set; skipping PostgreSQL integration tests")

    schema = f"t_{uuid4().hex[:12]}"
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema)))
    try:
        yield make_conninfo(url, options=f"-c search_path={schema}")
    finally:
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(schema)))


@pytest.fixture
def store(schema_conninfo: str) -> AuditStore:
    """An AuditStore on a freshly migrated, isolated schema."""
    apply_migrations(schema_conninfo, MIGRATIONS_DIR)
    return AuditStore(schema_conninfo)
