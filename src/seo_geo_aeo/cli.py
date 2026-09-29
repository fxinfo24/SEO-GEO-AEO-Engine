"""Command-line entrypoint for the SEO/GEO/AEO engine.

Usage:
    seo-geo-aeo audit https://example.com --profile geo
    seo-geo-aeo audit https://example.com --profile geo --pages 20
    seo-geo-aeo audit https://example.com --profile geo --pdf report.pdf
    seo-geo-aeo audit https://example.com --profile geo --user-agent "Mozilla/5.0 ..."
    seo-geo-aeo audit https://example.com --profile geo --brand-name "Example Co" --save --prospect
    seo-geo-aeo report https://example.com --pdf full-report.pdf
    seo-geo-aeo compare example.com
    seo-geo-aeo prospects --status lead
    seo-geo-aeo db-migrate
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from urllib.parse import urlparse

from seo_geo_aeo.core.fetcher import Deadline, FetchError, SafeFetcher, UnsafeURLError
from seo_geo_aeo.core.orchestrator import run_audit, run_profiles, run_site_audit
from seo_geo_aeo.core.scoring import CompositeResult
from seo_geo_aeo.reporting.markdown_report import render_markdown_report
from seo_geo_aeo.storage.postgres_store import (
    AuditStore,
    StoreError,
    apply_migrations,
    get_conninfo,
)


def _domain_from_url(url: str) -> str:
    return urlparse(url).netloc or url


def _build_fetcher(args: argparse.Namespace) -> SafeFetcher:
    """A shared SafeFetcher for this invocation, carrying the total-audit
    Deadline (RoadMap.md Phase 10). Always constructed (never None) so the
    deadline is enforced even when --user-agent wasn't passed: orchestrator's
    own `fetcher or SafeFetcher()` fallback would otherwise build a fresh,
    un-timed fetcher and silently drop it. --audit-timeout 0 means unbounded.
    """
    deadline = Deadline(seconds=None if args.audit_timeout <= 0 else args.audit_timeout)
    if args.user_agent:
        return SafeFetcher(user_agent=args.user_agent, deadline=deadline)
    return SafeFetcher(deadline=deadline)


def cmd_audit(args: argparse.Namespace) -> int:
    fetcher = _build_fetcher(args)
    crawl_meta = None
    try:
        if args.pages > 1:
            result, crawl_meta = run_site_audit(
                args.url,
                profile=args.profile,
                max_pages=args.pages,
                render=args.render,
                render_wait_until=args.wait_until,
                render_timeout_seconds=args.timeout,
                fetcher=fetcher,
                page_type_hint=args.page_type,
                brand_name=args.brand_name,
            )
        else:
            result = run_audit(
                args.url,
                profile=args.profile,
                render=args.render,
                render_wait_until=args.wait_until,
                render_timeout_seconds=args.timeout,
                fetcher=fetcher,
                page_type_hint=args.page_type,
                brand_name=args.brand_name,
            )
    except UnsafeURLError as exc:
        print(f"Refused to audit: {exc}", file=sys.stderr)
        return 2
    except FetchError as exc:
        print(f"Fetch failed: {exc}", file=sys.stderr)
        return 1

    domain = args.domain or _domain_from_url(args.url)

    if crawl_meta:
        print(
            f"Crawled {crawl_meta['pages_crawled']} page(s); "
            f"{len(crawl_meta['failed_urls'])} failed; "
            f"{crawl_meta['skipped_offsite_links']} offsite links skipped.",
            file=sys.stderr,
        )

    report = render_markdown_report(domain, result)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(report)
        print(f"Report written to {args.output}")
    else:
        print(report)

    if args.pdf:
        from seo_geo_aeo.reporting.pdf_report import render_pdf_report

        render_pdf_report(domain, result, args.pdf)
        print(f"PDF written to {args.pdf}")

    if args.save:
        try:
            store = AuditStore()
            prospect_id = None
            if args.prospect:
                # ensure_prospect, not upsert: auditing an existing 'won'/'proposal'
                # prospect must not reset its status back to 'lead'.
                prospect_id = store.ensure_prospect(domain)["id"]
            store.save_audit(
                domain=domain, profile=args.profile, result=result, prospect_id=prospect_id
            )
        except StoreError as exc:
            print(f"Warning: --save requested but not saved: {exc}", file=sys.stderr)
            return 1
        print(f"Saved audit for {domain} to the database.")

    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Run multiple profiles against one URL and combine them into one PDF."""
    fetcher = _build_fetcher(args)
    profiles = [p.strip() for p in args.profiles.split(",") if p.strip()]
    domain = args.domain or _domain_from_url(args.url)

    results: dict[str, CompositeResult] = {}
    if args.pages > 1:
        # Multi-page site audit: each profile still crawls independently.
        # Sharing fetched pages across profiles here would need a
        # SiteAuditContext (RoadMap.md Phase 7.1) that isn't built yet —
        # tracked as follow-up, not silently pretended to be solved.
        for profile in profiles:
            try:
                result, crawl_meta = run_site_audit(
                    args.url,
                    profile=profile,
                    max_pages=args.pages,
                    render=args.render,
                    render_wait_until=args.wait_until,
                    render_timeout_seconds=args.timeout,
                    fetcher=fetcher,
                    brand_name=args.brand_name,
                )
                print(
                    f"[{profile}] crawled {crawl_meta['pages_crawled']} page(s); "
                    f"{len(crawl_meta['failed_urls'])} failed",
                    file=sys.stderr,
                )
                results[profile] = result
                print(f"[{profile}] {result.overall_score}/100 ({result.rating()})", file=sys.stderr)
            except UnsafeURLError as exc:
                print(f"[{profile}] refused: {exc}", file=sys.stderr)
            except FetchError as exc:
                print(f"[{profile}] fetch failed: {exc}", file=sys.stderr)
    else:
        # Single page: fetch and parse once, score every requested profile
        # from that one shared page (RoadMap.md Phase 7) instead of
        # re-fetching per profile.
        try:
            results = run_profiles(
                args.url,
                profiles=profiles,
                render=args.render,
                render_wait_until=args.wait_until,
                render_timeout_seconds=args.timeout,
                fetcher=fetcher,
                brand_name=args.brand_name,
            )
            for profile, result in results.items():
                print(f"[{profile}] {result.overall_score}/100 ({result.rating()})", file=sys.stderr)
        except UnsafeURLError as exc:
            print(f"Refused: {exc}", file=sys.stderr)
        except FetchError as exc:
            print(f"Fetch failed: {exc}", file=sys.stderr)

    if not results:
        print("All profile audits failed; no report generated.", file=sys.stderr)
        return 1

    from seo_geo_aeo.reporting.pdf_report import render_comprehensive_pdf_report

    render_comprehensive_pdf_report(domain, results, args.pdf)
    print(f"Comprehensive PDF written to {args.pdf}")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    try:
        store = AuditStore()
        audits = store.latest_audits_for_domain(args.domain, limit=2)
    except StoreError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if len(audits) < 2:
        print(f"Need at least 2 saved audits for {args.domain} to compare; found {len(audits)}.")
        return 1

    latest, previous = audits[0], audits[1]
    delta = latest["overall_score"] - previous["overall_score"]
    direction = "up" if delta > 0 else "down" if delta < 0 else "unchanged"
    print(
        f"{args.domain}: {previous['overall_score']} -> {latest['overall_score']} "
        f"({direction} {abs(delta):.1f})"
    )

    for dim, latest_dim in latest["dimension_scores"].items():
        prev_dim = previous["dimension_scores"].get(dim)
        if prev_dim is None:
            continue
        # An unmeasured dimension stores score 0 as a placeholder; diffing it
        # against a real score would report a fake regression.
        if not latest_dim.get("measured", True) or not prev_dim.get("measured", True):
            continue
        d = latest_dim["score"] - prev_dim["score"]
        if abs(d) >= 0.1:
            arrow = "▲" if d > 0 else "▼"
            print(f"  {dim}: {prev_dim['score']} -> {latest_dim['score']} ({arrow}{abs(d):.1f})")

    return 0


def cmd_prospects(args: argparse.Namespace) -> int:
    try:
        store = AuditStore()
        prospects = store.list_prospects(status=args.status)
    except StoreError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if not prospects:
        print("No prospects found.")
        return 0

    for p in prospects:
        value = f" (${p['monthly_value']}/mo)" if p.get("monthly_value") else ""
        print(f"{p['domain']:<40} {p['status']:<12}{value}")
    return 0


def cmd_db_migrate(args: argparse.Namespace) -> int:
    migrations_dir = Path(args.dir)
    try:
        applied = apply_migrations(get_conninfo(), migrations_dir)
    except StoreError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if applied:
        print(f"Applied {len(applied)} migration(s): {', '.join(applied)}")
    else:
        print("Database is already up to date.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="seo-geo-aeo", description="SEO/GEO/AEO audit engine")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit_parser = subparsers.add_parser("audit", help="Audit a URL (single page or site-wide crawl)")
    audit_parser.add_argument("url", help="Seed URL to audit, e.g. https://example.com")
    audit_parser.add_argument("--profile", choices=["seo", "geo", "aeo"], default="geo")
    audit_parser.add_argument(
        "--pages", type=int, default=1,
        help="Crawl up to N same-origin pages and aggregate scores (default: 1, single page)",
    )
    audit_parser.add_argument(
        "--render", action="store_true",
        help="Fetch via headless Chromium instead of plain HTTP — required for React/Vue/SPA "
        "sites where the server HTML is an empty shell. Slower; needs "
        "`playwright install chromium` run once.",
    )
    audit_parser.add_argument(
        "--wait-until", default="load", choices=["load", "domcontentloaded", "networkidle"],
        help="Playwright wait strategy for --render (default: load). Use 'networkidle' for "
        "SPAs that fetch data asynchronously after load — but ad/analytics-heavy sites often "
        "never go idle and will time out on that setting. Use 'domcontentloaded' for a "
        "faster, earlier snapshot.",
    )
    audit_parser.add_argument(
        "--timeout", type=float, default=30.0,
        help="Seconds to wait for --render's page load before giving up (default: 30). Some "
        "ad/analytics-heavy pages legitimately vary run-to-run — raise this before assuming "
        "a timeout means the site is broken.",
    )
    audit_parser.add_argument(
        "--page-type", default="default", help="homepage|blog|pillar|product|service|about"
    )
    audit_parser.add_argument(
        "--user-agent",
        help="Override the default bot User-Agent. Some sites' WAFs (Wordfence, Sucuri, "
        "generic bot-fight-mode) silently hang or block unrecognized bot signatures rather "
        "than returning a clean error — if a fetch times out on a site you know is up, try "
        "a standard browser UA here before assuming the site itself is down. robots.txt is "
        "still evaluated under whichever UA you pass.",
    )
    audit_parser.add_argument(
        "--brand-name", help="Enables the Wikipedia/Wikidata check in brand_authority"
    )
    audit_parser.add_argument(
        "--audit-timeout", type=float, default=300.0,
        help="Wall-clock seconds this whole audit (every fetch combined, not just one "
        "request) may take before failing (RoadMap.md Phase 10). Default: 300. A --render "
        "site crawl needs more headroom than the default single-page case; 0 disables it.",
    )
    audit_parser.add_argument("--domain", help="Domain label for storage (defaults to URL's netloc)")
    audit_parser.add_argument("--output", "-o", help="Write markdown report to this file instead of stdout")
    audit_parser.add_argument("--pdf", help="Also write a PDF report to this path")
    audit_parser.add_argument("--save", action="store_true", help="Persist this audit to PostgreSQL (needs DATABASE_URL)")
    audit_parser.add_argument(
        "--prospect", action="store_true",
        help="Also create a prospect row for this domain if absent (requires --save)",
    )
    audit_parser.set_defaults(func=cmd_audit)

    report_parser = subparsers.add_parser(
        "report", help="Run SEO+AEO+GEO (or a subset) against one URL and combine into one PDF"
    )
    report_parser.add_argument("url", help="Seed URL to audit, e.g. https://example.com")
    report_parser.add_argument(
        "--profiles", default="seo,aeo,geo",
        help="Comma-separated profiles to include (default: seo,aeo,geo)",
    )
    report_parser.add_argument(
        "--pages", type=int, default=1,
        help="Crawl up to N same-origin pages per profile and aggregate scores (default: 1)",
    )
    report_parser.add_argument("--render", action="store_true", help="See `audit --render`")
    report_parser.add_argument(
        "--wait-until", default="load", choices=["load", "domcontentloaded", "networkidle"]
    )
    report_parser.add_argument(
        "--timeout", type=float, default=30.0, help="See `audit --timeout`"
    )
    report_parser.add_argument("--user-agent", help="See `audit --user-agent`")
    report_parser.add_argument("--brand-name", help="Enables the Wikipedia/Wikidata check")
    report_parser.add_argument(
        "--audit-timeout", type=float, default=600.0,
        help="See `audit --audit-timeout`. Higher default here: up to 3 profiles' worth of "
        "fetches share this one budget.",
    )
    report_parser.add_argument("--domain", help="Domain label (defaults to URL's netloc)")
    report_parser.add_argument("--pdf", required=True, help="Path to write the combined PDF")
    report_parser.set_defaults(func=cmd_report)

    compare_parser = subparsers.add_parser(
        "compare", help="Compare the two most recent saved audits for a domain"
    )
    compare_parser.add_argument("domain")
    compare_parser.set_defaults(func=cmd_compare)

    prospects_parser = subparsers.add_parser("prospects", help="List tracked prospects")
    prospects_parser.add_argument("--status", choices=["lead", "qualified", "proposal", "won", "lost"])
    prospects_parser.set_defaults(func=cmd_prospects)

    migrate_parser = subparsers.add_parser(
        "db-migrate", help="Apply pending SQL migrations to the database in DATABASE_URL"
    )
    migrate_parser.add_argument(
        "--dir", default="migrations", help="Directory of *.sql migrations (default: ./migrations)"
    )
    migrate_parser.set_defaults(func=cmd_db_migrate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
