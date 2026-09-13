"""Supabase-backed persistence for audits, prospects, and delta tracking.

Replaces the flat-JSON `~/.geo-prospects/prospects.json` store used by the
source geo-prospect/geo-compare skills with real relational storage. Schema
lives in supabase/migrations/0001_init.sql — run it against your project
before using this module.

Credentials are read from environment variables only:
    SUPABASE_URL
    SUPABASE_SERVICE_ROLE_KEY   (server-side use only — never ship to a client)
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from supabase import Client, create_client

from seo_geo_aeo.core.scoring import CompositeResult


class SupabaseConfigError(RuntimeError):
    """Raised when required Supabase environment variables are missing."""


@dataclass(frozen=True)
class ProspectRecord:
    id: str | None
    domain: str
    company: str | None
    status: str  # lead | qualified | proposal | won | lost
    contact_email: str | None = None
    monthly_value: float | None = None


def get_client() -> Client:
    """Build a Supabase client from environment variables.

    Raises SupabaseConfigError with an actionable message if credentials are
    missing, rather than letting the underlying library raise a generic error.
    """
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise SupabaseConfigError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set. Copy .env.example to "
            ".env, fill in your project's values, and load it before running the CLI."
        )
    return create_client(url, key)


class AuditStore:
    """CRUD wrapper around the `audits`, `prospects`, and `audit_findings` tables."""

    def __init__(self, client: Client | None = None) -> None:
        self.client = client or get_client()

    def save_audit(
        self,
        *,
        domain: str,
        profile: str,
        result: CompositeResult,
        prospect_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist a CompositeResult and its findings. Returns the inserted audit row."""
        audit_row = {
            "domain": domain,
            "profile": profile,
            "overall_score": result.overall_score,
            "rating": result.rating(),
            "dimension_scores": {
                dim: {"score": ds.score, "raw": ds.raw} for dim, ds in result.dimension_scores.items()
            },
            "weights": result.weights,
            "prospect_id": prospect_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        inserted = self.client.table("audits").insert(audit_row).execute()
        audit_id = inserted.data[0]["id"]

        if result.findings:
            finding_rows = [
                {
                    "audit_id": audit_id,
                    "severity": f.severity.value,
                    "title": f.title,
                    "detail": f.detail,
                    "page_url": f.page_url,
                }
                for f in result.findings
            ]
            self.client.table("audit_findings").insert(finding_rows).execute()

        return inserted.data[0]

    def latest_audits_for_domain(self, domain: str, limit: int = 2) -> list[dict[str, Any]]:
        response = (
            self.client.table("audits")
            .select("*")
            .eq("domain", domain)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return response.data

    def upsert_prospect(self, prospect: ProspectRecord) -> dict[str, Any]:
        row = {k: v for k, v in asdict(prospect).items() if v is not None}
        response = self.client.table("prospects").upsert(row, on_conflict="domain").execute()
        return response.data[0]

    def list_prospects(self, status: str | None = None) -> list[dict[str, Any]]:
        query = self.client.table("prospects").select("*")
        if status:
            query = query.eq("status", status)
        return query.order("updated_at", desc=True).execute().data
