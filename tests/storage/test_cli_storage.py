"""CLI-level tests for the database-backed commands: db-migrate, audit --save,
compare, prospects. Run against a real PostgreSQL via the storage fixtures."""
from __future__ import annotations

from pathlib import Path

import pytest

from seo_geo_aeo import cli
from seo_geo_aeo.core.scoring import CompositeResult, CompositeScorer, DimensionScore
from seo_geo_aeo.storage.postgres_store import AuditStore, ProspectRecord

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _result(technical: float = 80.0, *, schema_measured: bool = True) -> CompositeResult:
    dims = {
        "technical_seo": DimensionScore("technical_seo", technical),
        "on_page": DimensionScore("on_page", 60.0),
        "content_quality": DimensionScore("content_quality", 70.0),
        "schema": (
            DimensionScore("schema", 50.0)
            if schema_measured
            else DimensionScore("schema", 0.0, measured=False, unmeasured_reason="not run")
        ),
    }
    return CompositeScorer("seo").combine(dims)


@pytest.fixture
def db_env(schema_conninfo: str, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("DATABASE_URL", schema_conninfo)
    return schema_conninfo


def test_db_migrate_applies_then_reports_up_to_date(db_env: str, capsys):
    assert cli.main(["db-migrate", "--dir", str(MIGRATIONS_DIR)]) == 0
    assert "Applied 2 migration(s)" in capsys.readouterr().out

    assert cli.main(["db-migrate", "--dir", str(MIGRATIONS_DIR)]) == 0
    assert "already up to date" in capsys.readouterr().out


def test_db_migrate_fails_cleanly_without_database_url(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert cli.main(["db-migrate", "--dir", str(MIGRATIONS_DIR)]) == 1
    assert "DATABASE_URL" in capsys.readouterr().err


def test_compare_reports_delta_and_skips_unmeasured_dimensions(store: AuditStore, db_env: str, capsys):
    """Both dims must be measured to be diffed: an unmeasured dimension stores a
    placeholder 0, and diffing it against a real score would fake a regression."""
    store.save_audit(domain="example.com", profile="seo", result=_result(60.0, schema_measured=True))
    store.save_audit(domain="example.com", profile="seo", result=_result(90.0, schema_measured=False))

    assert cli.main(["compare", "example.com"]) == 0

    out = capsys.readouterr().out
    assert "up" in out
    assert "technical_seo: 60.0 -> 90.0" in out
    assert "schema" not in out


def test_compare_needs_two_audits(store: AuditStore, db_env: str, capsys):
    store.save_audit(domain="example.com", profile="seo", result=_result())

    assert cli.main(["compare", "example.com"]) == 1
    assert "at least 2 saved audits" in capsys.readouterr().out


def test_compare_reports_database_errors_instead_of_a_traceback(monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/db")

    assert cli.main(["compare", "example.com"]) == 1
    assert "connect" in capsys.readouterr().err


def test_prospects_lists_and_filters(store: AuditStore, db_env: str, capsys):
    store.upsert_prospect(ProspectRecord(id=None, domain="a.com", company=None, status="lead"))
    store.upsert_prospect(
        ProspectRecord(id=None, domain="b.com", company=None, status="won", monthly_value=250.0)
    )

    assert cli.main(["prospects", "--status", "won"]) == 0

    out = capsys.readouterr().out
    assert "b.com" in out and "$250.0/mo" in out
    assert "a.com" not in out


def test_prospects_empty(store: AuditStore, db_env: str, capsys):
    assert cli.main(["prospects"]) == 0
    assert "No prospects found." in capsys.readouterr().out


def test_audit_save_prospect_persists_and_does_not_demote_existing_prospect(
    store: AuditStore, db_env: str, monkeypatch, capsys
):
    monkeypatch.setattr(cli, "run_audit", lambda *a, **k: _result())
    store.upsert_prospect(ProspectRecord(id=None, domain="example.com", company="Acme", status="won"))

    rc = cli.main(["audit", "https://example.com", "--profile", "seo", "--save", "--prospect", "-o", "/dev/null"])

    assert rc == 0
    assert "Saved audit for example.com" in capsys.readouterr().out
    audits = store.latest_audits_for_domain("example.com")
    assert len(audits) == 1
    prospect = store.list_prospects()[0]
    assert prospect["status"] == "won"
    assert audits[0]["prospect_id"] == prospect["id"]


def test_audit_save_without_database_url_returns_nonzero_with_warning(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(cli, "run_audit", lambda *a, **k: _result())

    rc = cli.main(["audit", "https://example.com", "--profile", "seo", "--save", "-o", "/dev/null"])

    assert rc == 1
    assert "not saved" in capsys.readouterr().err
