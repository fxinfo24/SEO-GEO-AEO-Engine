"""Integration tests for AuditStore against a real PostgreSQL (see conftest.py).

These exist because the storage layer was previously untested against any real
database (RoadMap.md item 6): constraint behaviour, transactionality, JSONB
round-trips, and the trigger cannot be verified with mocks.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest

from seo_geo_aeo.core.scoring import (
    PROFILE_WEIGHTS,
    CompositeResult,
    CompositeScorer,
    DimensionScore,
    Finding,
    Severity,
)
from seo_geo_aeo.storage.postgres_store import (
    AuditStore,
    ProspectRecord,
    StoreError,
    apply_migrations,
)

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _result(technical: float = 80.0, *, unmeasured_schema: bool = False) -> CompositeResult:
    dims = {
        "technical_seo": DimensionScore("technical_seo", technical, raw={"crawlable": True}),
        "on_page": DimensionScore(
            "on_page",
            60.0,
            findings=[Finding(Severity.HIGH, "Missing H1", "No H1 found", "https://example.com/")],
        ),
        "content_quality": DimensionScore(
            "content_quality", 70.0, findings=[Finding(Severity.LOW, "Thin content", "Short page")]
        ),
        "schema": (
            DimensionScore("schema", 0.0, measured=False, unmeasured_reason="module not run")
            if unmeasured_schema
            else DimensionScore("schema", 50.0)
        ),
    }
    return CompositeScorer("seo").combine(dims)


def _fetch_all(conninfo: str, query: str) -> list[dict]:
    with psycopg.connect(conninfo, row_factory=psycopg.rows.dict_row) as conn:
        return conn.execute(query).fetchall()


# --- migrations ----------------------------------------------------------------------


def test_migrations_apply_in_order_and_are_idempotent(schema_conninfo: str):
    first = apply_migrations(schema_conninfo, MIGRATIONS_DIR)
    second = apply_migrations(schema_conninfo, MIGRATIONS_DIR)

    assert first == ["0001_init", "0002_coverage_and_constraints"]
    assert second == []
    versions = [r["version"] for r in _fetch_all(schema_conninfo, "select version from schema_migrations")]
    assert sorted(versions) == first


def test_failed_migration_rolls_back_the_whole_run(schema_conninfo: str, tmp_path: Path):
    (tmp_path / "0001_ok.sql").write_text("create table should_not_persist (x int);")
    (tmp_path / "0002_broken.sql").write_text("create table oops (;")

    with pytest.raises(StoreError, match="no changes were applied"):
        apply_migrations(schema_conninfo, tmp_path)

    tables = _fetch_all(
        schema_conninfo,
        "select table_name from information_schema.tables where table_schema = current_schema()",
    )
    assert tables == []


# --- save_audit ------------------------------------------------------------------------


def test_save_audit_persists_audit_findings_and_coverage_metadata(store: AuditStore, schema_conninfo: str):
    saved = store.save_audit(domain="example.com", profile="seo", result=_result(unmeasured_schema=True))

    assert saved["domain"] == "example.com"
    assert saved["profile"] == "seo"
    assert saved["declared_weights"] == PROFILE_WEIGHTS["seo"]
    assert saved["unmeasured_dimensions"] == ["schema"]
    assert saved["measured_weight"] < 1.0
    assert saved["unmeasured_weight"] > 0.0
    assert saved["dimension_scores"]["schema"]["measured"] is False
    assert saved["dimension_scores"]["schema"]["unmeasured_reason"] == "module not run"
    assert saved["dimension_scores"]["technical_seo"]["raw"] == {"crawlable": True}

    findings = _fetch_all(schema_conninfo, "select * from audit_findings order by severity")
    assert {f["title"] for f in findings} == {"Missing H1", "Thin content"}
    assert all(str(f["audit_id"]) == saved["id"] for f in findings)


def test_save_audit_returns_json_friendly_types(store: AuditStore):
    """cli.py's compare command does float arithmetic on overall_score and
    indexes dimension_scores as a dict — Decimal/UUID/datetime would break it."""
    saved = store.save_audit(domain="example.com", profile="seo", result=_result())

    assert isinstance(saved["overall_score"], float)
    assert isinstance(saved["id"], str)
    assert isinstance(saved["created_at"], str)
    datetime.fromisoformat(saved["created_at"])
    assert isinstance(saved["dimension_scores"], dict)


def test_save_audit_is_atomic_when_a_finding_insert_fails(store: AuditStore, schema_conninfo: str):
    """A finding violating NOT NULL must roll back the audit row too — never an
    audit persisted without its findings (RoadMap.md Phase 6.5)."""
    result = _result()
    result.dimension_scores["on_page"].findings.append(
        Finding(Severity.HIGH, None, "bad finding")  # type: ignore[arg-type]
    )

    with pytest.raises(StoreError):
        store.save_audit(domain="example.com", profile="seo", result=result)

    assert _fetch_all(schema_conninfo, "select id from audits") == []
    assert _fetch_all(schema_conninfo, "select id from audit_findings") == []


def test_save_audit_rejects_unknown_profile_via_check_constraint(store: AuditStore, schema_conninfo: str):
    with pytest.raises(StoreError):
        store.save_audit(domain="example.com", profile="bogus", result=_result())

    assert _fetch_all(schema_conninfo, "select id from audits") == []


def test_save_audit_rejects_domain_with_whitespace(store: AuditStore):
    with pytest.raises(StoreError):
        store.save_audit(domain="exa mple.com", profile="seo", result=_result())


def test_save_audit_stringifies_non_json_raw_values(store: AuditStore):
    result = _result()
    result.dimension_scores["technical_seo"].raw = {"tags": {"a"}, "at": datetime(2026, 1, 1, tzinfo=UTC)}

    saved = store.save_audit(domain="example.com", profile="seo", result=result)

    raw = saved["dimension_scores"]["technical_seo"]["raw"]
    assert raw["at"] == "2026-01-01 00:00:00+00:00"
    assert raw["tags"] == "{'a'}"


def test_hostile_finding_text_is_stored_verbatim_not_executed(store: AuditStore, schema_conninfo: str):
    """Findings carry scraped, attacker-controlled page text. It must be bound
    as data, never interpolated into SQL."""
    payload = "'); drop table audits; --"
    result = _result()
    result.dimension_scores["on_page"].findings.append(Finding(Severity.LOW, payload, payload))

    store.save_audit(domain="example.com", profile="seo", result=result)

    stored = _fetch_all(schema_conninfo, "select title from audit_findings")
    assert payload in {r["title"] for r in stored}
    assert len(_fetch_all(schema_conninfo, "select id from audits")) == 1


# --- latest_audits_for_domain ---------------------------------------------------------


def test_latest_audits_newest_first_and_respects_limit(store: AuditStore):
    for score in (50.0, 60.0, 70.0):
        store.save_audit(domain="example.com", profile="seo", result=_result(technical=score))

    latest_two = store.latest_audits_for_domain("example.com", limit=2)

    assert len(latest_two) == 2
    assert latest_two[0]["overall_score"] > latest_two[1]["overall_score"]
    assert len(store.latest_audits_for_domain("example.com", limit=10)) == 3


def test_latest_audits_matches_domain_case_insensitively(store: AuditStore):
    store.save_audit(domain="Example.COM", profile="seo", result=_result())

    assert len(store.latest_audits_for_domain("example.com")) == 1
    assert len(store.latest_audits_for_domain("EXAMPLE.com")) == 1


def test_latest_audits_isolated_per_domain(store: AuditStore):
    store.save_audit(domain="a.com", profile="seo", result=_result())
    store.save_audit(domain="b.com", profile="seo", result=_result())

    assert len(store.latest_audits_for_domain("a.com")) == 1


def test_latest_audits_injection_string_is_just_a_domain_that_matches_nothing(store: AuditStore):
    store.save_audit(domain="example.com", profile="seo", result=_result())

    assert store.latest_audits_for_domain("x' or '1'='1") == []


def test_latest_audits_rejects_non_positive_limit(store: AuditStore):
    with pytest.raises(ValueError):
        store.latest_audits_for_domain("example.com", limit=0)


# --- prospects -------------------------------------------------------------------------


def test_upsert_prospect_inserts_then_updates_without_nulling_existing_fields(store: AuditStore):
    created = store.upsert_prospect(
        ProspectRecord(
            id=None, domain="acme.com", company="Acme", status="lead",
            contact_email="a@acme.com", monthly_value=500.0,
        )
    )
    updated = store.upsert_prospect(
        ProspectRecord(id=None, domain="acme.com", company=None, status="qualified")
    )

    assert updated["id"] == created["id"]
    assert updated["status"] == "qualified"
    assert updated["company"] == "Acme"
    assert updated["contact_email"] == "a@acme.com"
    assert updated["monthly_value"] == 500.0
    assert len(store.list_prospects()) == 1


def test_upsert_prospect_trigger_bumps_updated_at(store: AuditStore):
    created = store.upsert_prospect(ProspectRecord(id=None, domain="acme.com", company=None, status="lead"))
    updated = store.upsert_prospect(ProspectRecord(id=None, domain="acme.com", company=None, status="won"))

    assert datetime.fromisoformat(updated["updated_at"]) > datetime.fromisoformat(created["updated_at"])


def test_upsert_prospect_rejects_invalid_status(store: AuditStore):
    with pytest.raises(StoreError):
        store.upsert_prospect(ProspectRecord(id=None, domain="acme.com", company=None, status="hot"))


def test_ensure_prospect_creates_lead_once(store: AuditStore):
    first = store.ensure_prospect("Acme.com")
    second = store.ensure_prospect("acme.com")

    assert first["status"] == "lead"
    assert first["domain"] == "acme.com"
    assert second["id"] == first["id"]
    assert len(store.list_prospects()) == 1


def test_ensure_prospect_does_not_reset_an_advanced_status(store: AuditStore):
    """Regression for a real bug in the old CLI flow: `audit --save --prospect`
    upserted status='lead' on every run, demoting won/proposal prospects."""
    store.upsert_prospect(ProspectRecord(id=None, domain="acme.com", company="Acme", status="won"))

    ensured = store.ensure_prospect("acme.com")

    assert ensured["status"] == "won"
    assert ensured["company"] == "Acme"


def test_list_prospects_filters_by_status_and_orders_by_recent_update(store: AuditStore):
    store.upsert_prospect(ProspectRecord(id=None, domain="old.com", company=None, status="lead"))
    store.upsert_prospect(ProspectRecord(id=None, domain="won.com", company=None, status="won"))
    store.upsert_prospect(ProspectRecord(id=None, domain="new.com", company=None, status="lead"))

    leads = store.list_prospects(status="lead")

    assert [p["domain"] for p in leads] == ["new.com", "old.com"]
    assert [p["domain"] for p in store.list_prospects(status="won")] == ["won.com"]


def test_audit_links_to_prospect_and_survives_prospect_deletion(store: AuditStore, schema_conninfo: str):
    prospect = store.ensure_prospect("acme.com")
    saved = store.save_audit(
        domain="acme.com", profile="seo", result=_result(), prospect_id=prospect["id"]
    )
    assert saved["prospect_id"] == prospect["id"]

    with psycopg.connect(schema_conninfo) as conn:
        conn.execute("delete from prospects")

    remaining = store.latest_audits_for_domain("acme.com")
    assert len(remaining) == 1
    assert remaining[0]["prospect_id"] is None
