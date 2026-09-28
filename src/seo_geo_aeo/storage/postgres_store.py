"""PostgreSQL persistence for audits, prospects, and delta tracking.

Provider-neutral: works against Neon, Supabase, RDS, or a local Postgres —
anything reachable via a standard `DATABASE_URL` (RoadMap.md Phase 6). The
schema lives in `migrations/` at the repo root and is applied with
`seo-geo-aeo db-migrate` (see `apply_migrations`).

Configuration is read from one environment variable:

    DATABASE_URL=postgresql://user:password@host/dbname?sslmode=require

Design notes:
- Every value reaches the database as a bound parameter. Findings contain
  scraped page content, so nothing here ever interpolates data into SQL; the
  only dynamic SQL (`upsert_prospect`) builds column lists from a fixed
  dataclass, quoted via `psycopg.sql.Identifier`.
- One short-lived connection per operation, no pool: this is a CLI, and it
  keeps Neon scale-to-zero and PgBouncer (transaction pooling) happy.
  `prepare_threshold=None` disables server-side prepared statements, which
  transaction-mode poolers do not support.
- Each write runs in a single transaction, so an audit is never persisted
  without its findings.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from seo_geo_aeo.core.scoring import CompositeResult

_CONNECT_TIMEOUT_SECONDS = 10
# Arbitrary constant: serializes concurrent `db-migrate` runs.
_MIGRATION_LOCK_ID = 727_401_001


class StoreError(RuntimeError):
    """A database operation failed (connection, constraint, migration, ...)."""


class StoreConfigError(StoreError):
    """Raised when required database configuration is missing or invalid."""


@dataclass(frozen=True)
class ProspectRecord:
    id: str | None
    domain: str
    company: str | None
    status: str  # lead | qualified | proposal | won | lost
    contact_email: str | None = None
    monthly_value: float | None = None


class AuditStoreProtocol(Protocol):
    """Persistence interface the CLI depends on (RoadMap.md Phase 6.4)."""

    def save_audit(
        self,
        *,
        domain: str,
        profile: str,
        result: CompositeResult,
        prospect_id: str | None = None,
    ) -> dict[str, Any]: ...

    def latest_audits_for_domain(self, domain: str, limit: int = 2) -> list[dict[str, Any]]: ...

    def upsert_prospect(self, prospect: ProspectRecord) -> dict[str, Any]: ...

    def ensure_prospect(self, domain: str) -> dict[str, Any]: ...

    def list_prospects(self, status: str | None = None) -> list[dict[str, Any]]: ...


def get_conninfo() -> str:
    """Return the connection string from `DATABASE_URL`, or raise an actionable error."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise StoreConfigError(
            "DATABASE_URL must be set (e.g. postgresql://user:password@host/dbname"
            "?sslmode=require). Copy .env.example to .env, fill it in, and load it before "
            "running the CLI."
        )
    # Accept postgresql:// URLs and libpq key/value strings ("host=... dbname=..."),
    # but catch the common mistake of pasting a URL for a different database.
    if "://" in url and not url.startswith(("postgresql://", "postgres://")):
        raise StoreConfigError("DATABASE_URL must start with postgresql:// or postgres://")
    return url


def _dumps(obj: Any) -> str:
    # default=str: `raw` dicts from scoring modules may hold non-JSON values
    # (sets, datetimes); stringify rather than fail an entire audit save.
    return json.dumps(obj, default=str)


def _jsonb(obj: Any) -> Jsonb:
    return Jsonb(obj, dumps=_dumps)


def _normalize_domain(domain: str) -> str:
    normalized = domain.strip().lower()
    if not normalized:
        raise ValueError("domain must be a non-empty string")
    return normalized


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    """Convert Postgres-native types to the JSON-friendly types callers expect
    (str UUIDs, float numerics, ISO timestamps) — what the previous
    PostgREST-backed store returned, so `cli.py` arithmetic keeps working.
    """
    out: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, UUID):
            out[key] = str(value)
        elif isinstance(value, Decimal):
            out[key] = float(value)
        elif isinstance(value, datetime):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out


def _connect(conninfo: str, **kwargs: Any) -> psycopg.Connection[dict[str, Any]]:
    try:
        return psycopg.connect(
            conninfo,
            row_factory=dict_row,
            prepare_threshold=None,
            connect_timeout=_CONNECT_TIMEOUT_SECONDS,
            **kwargs,
        )
    except psycopg.Error as exc:
        raise StoreError(f"Could not connect to the database: {exc}") from exc


class AuditStore:
    """CRUD wrapper around the `audits`, `prospects`, and `audit_findings` tables."""

    def __init__(self, conninfo: str | None = None) -> None:
        self._conninfo = conninfo or get_conninfo()

    @contextmanager
    def _transaction(self) -> Iterator[psycopg.Connection[dict[str, Any]]]:
        """Yield a connection inside one transaction: commit on success, roll back on error."""
        conn = _connect(self._conninfo)
        try:
            with conn:
                yield conn
        except psycopg.Error as exc:
            raise StoreError(f"Database operation failed: {exc}") from exc
        finally:
            conn.close()

    def save_audit(
        self,
        *,
        domain: str,
        profile: str,
        result: CompositeResult,
        prospect_id: str | None = None,
    ) -> dict[str, Any]:
        """Atomically persist a CompositeResult and its findings. Returns the audit row."""
        dimension_scores = {
            dim: {
                "score": ds.score,
                "raw": ds.raw,
                "measured": ds.measured,
                "unmeasured_reason": ds.unmeasured_reason,
            }
            for dim, ds in result.dimension_scores.items()
        }
        with self._transaction() as conn:
            audit = conn.execute(
                """
                insert into audits (
                    domain, profile, overall_score, rating, dimension_scores, weights,
                    declared_weights, measured_weight, unmeasured_weight,
                    unmeasured_dimensions, prospect_id
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::uuid)
                returning *
                """,
                (
                    _normalize_domain(domain),
                    profile,
                    result.overall_score,
                    result.rating(),
                    _jsonb(dimension_scores),
                    _jsonb(result.weights),
                    _jsonb(result.declared_weights),
                    result.measured_weight,
                    result.unmeasured_weight,
                    _jsonb(result.unmeasured_dimensions),
                    prospect_id,
                ),
            ).fetchone()
            if audit is None:  # pragma: no cover - INSERT ... RETURNING always yields a row
                raise StoreError("Audit insert returned no row")

            if result.findings:
                with conn.cursor() as cur:
                    cur.executemany(
                        """
                        insert into audit_findings (audit_id, severity, title, detail, page_url)
                        values (%s, %s, %s, %s, %s)
                        """,
                        [
                            (audit["id"], f.severity.value, f.title, f.detail, f.page_url)
                            for f in result.findings
                        ],
                    )
        return _normalize_row(audit)

    def latest_audits_for_domain(self, domain: str, limit: int = 2) -> list[dict[str, Any]]:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        with self._transaction() as conn:
            rows = conn.execute(
                "select * from audits where domain = %s order by created_at desc limit %s",
                (_normalize_domain(domain), limit),
            ).fetchall()
        return [_normalize_row(r) for r in rows]

    def upsert_prospect(self, prospect: ProspectRecord) -> dict[str, Any]:
        """Insert a prospect, or update the existing row for that domain.

        Only fields that are not None are written, so an upsert never nulls out
        data recorded earlier (e.g. a stored contact email).
        """
        fields = {k: v for k, v in asdict(prospect).items() if v is not None}
        fields["domain"] = _normalize_domain(prospect.domain)
        columns = list(fields)
        update_columns = [c for c in columns if c not in ("domain", "id")] or ["domain"]

        query = sql.SQL(
            "insert into prospects ({cols}) values ({vals}) "
            "on conflict (domain) do update set {sets} returning *"
        ).format(
            cols=sql.SQL(", ").join(sql.Identifier(c) for c in columns),
            vals=sql.SQL(", ").join(sql.Placeholder() for _ in columns),
            sets=sql.SQL(", ").join(
                sql.SQL("{c} = excluded.{c}").format(c=sql.Identifier(c)) for c in update_columns
            ),
        )
        with self._transaction() as conn:
            row = conn.execute(query, [fields[c] for c in columns]).fetchone()
        if row is None:  # pragma: no cover - upsert ... RETURNING always yields a row
            raise StoreError("Prospect upsert returned no row")
        return _normalize_row(row)

    def ensure_prospect(self, domain: str) -> dict[str, Any]:
        """Return the prospect for `domain`, creating it (status 'lead') only if absent.

        Unlike `upsert_prospect`, an existing prospect is returned untouched —
        auditing a 'won' prospect must not reset its status back to 'lead'.
        """
        normalized = _normalize_domain(domain)
        with self._transaction() as conn:
            row = conn.execute(
                "insert into prospects (domain) values (%s) on conflict (domain) do nothing "
                "returning *",
                (normalized,),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    "select * from prospects where domain = %s", (normalized,)
                ).fetchone()
        if row is None:  # pragma: no cover - only if the row is deleted mid-transaction
            raise StoreError(f"Could not create or find prospect {normalized!r}")
        return _normalize_row(row)

    def list_prospects(self, status: str | None = None) -> list[dict[str, Any]]:
        with self._transaction() as conn:
            if status:
                rows = conn.execute(
                    "select * from prospects where status = %s order by updated_at desc",
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute("select * from prospects order by updated_at desc").fetchall()
        return [_normalize_row(r) for r in rows]


def apply_migrations(conninfo: str, migrations_dir: Path) -> list[str]:
    """Apply any unapplied `*.sql` files from `migrations_dir`, in filename order.

    Applied versions are recorded in `schema_migrations`. The whole run is one
    transaction guarded by an advisory lock: concurrent runs serialize, and a
    failing migration leaves the database unchanged. Returns the versions
    applied by this call (empty if already up to date).
    """
    files = sorted(migrations_dir.glob("*.sql"))
    if not files:
        raise StoreError(f"No .sql migrations found in {migrations_dir}")

    applied_now: list[str] = []
    conn = _connect(conninfo)
    try:
        with conn:
            conn.execute("select pg_advisory_xact_lock(%s)", (_MIGRATION_LOCK_ID,))
            conn.execute(
                "create table if not exists schema_migrations ("
                "version text primary key, applied_at timestamptz not null default now())"
            )
            done = {r["version"] for r in conn.execute("select version from schema_migrations")}
            for path in files:
                if path.stem in done:
                    continue
                # No parameters => simple query protocol, so multi-statement files work.
                conn.execute(sql.SQL(path.read_text(encoding="utf-8")))  # type: ignore[arg-type]
                conn.execute("insert into schema_migrations (version) values (%s)", (path.stem,))
                applied_now.append(path.stem)
    except psycopg.Error as exc:
        raise StoreError(f"Migration failed (no changes were applied): {exc}") from exc
    finally:
        conn.close()
    return applied_now
