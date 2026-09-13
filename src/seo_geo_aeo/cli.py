"""Command-line entrypoint for the SEO/GEO/AEO engine.

Usage:
    seo-geo-aeo audit https://example.com --profile geo
    seo-geo-aeo audit https://example.com --profile geo --pages 20
    seo-geo-aeo audit https://example.com --profile geo --pdf report.pdf
    seo-geo-aeo audit https://example.com --profile geo --render   # SPA / React sites
    seo-geo-aeo audit https://example.com --profile geo --brand-name "Example Co" --save --prospect
    seo-geo-aeo compare example.com
    seo-geo-aeo prospects --status lead
"""

from __future__ import annotations

import argparse
import logging
import sys
from urllib.parse import urlparse

from seo_geo_aeo.core.fetcher import FetchError, UnsafeURLError
from seo_geo_aeo.core.orchestrator import run_audit, run_site_audit
from seo_geo_aeo.reporting.markdown_report import render_markdown_report
from seo_geo_aeo.storage.supabase_client import AuditStore, ProspectRecord, SupabaseConfigError


def _domain_from_url(url: str) -> str:
    return urlparse(url).netloc or url


def cmd_audit(args: argparse.Namespace) -> int:
    crawl_meta = None
    try:
        if args.pages > 1:
            result, crawl_meta = run_site_audit(
                args.url,
                profile=args.profile,
                max_pages=args.pages,
                render=args.render,
                page_type_hint=args.page_type,
                brand_name=args.brand_name,
            )
        else:
            result = run_audit(
                args.url,
                profile=args.profile,
                render=args.render,
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
        except SupabaseConfigError as exc:
            print(f"Warning: --save requested but not saved: {exc}", file=sys.stderr)
            return 1
        prospect_id = None
        if args.prospect:
            prospect = store.upsert_prospect(
                ProspectRecord(id=None, domain=domain, status="lead", company=None)
            )
            prospect_id = prospect["id"]
        store.save_audit(domain=domain, profile=args.profile, result=result, prospect_id=prospect_id)
        print(f"Saved audit for {domain} to Supabase.")

    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    try:
        store = AuditStore()
    except SupabaseConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    audits = store.latest_audits_for_domain(args.domain, limit=2)
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
        d = latest_dim["score"] - prev_dim["score"]
        if abs(d) >= 0.1:
            arrow = "▲" if d > 0 else "▼"
            print(f"  {dim}: {prev_dim['score']} -> {latest_dim['score']} ({arrow}{abs(d):.1f})")

    return 0


def cmd_prospects(args: argparse.Namespace) -> int:
    try:
        store = AuditStore()
    except SupabaseConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    prospects = store.list_prospects(status=args.status)
    if not prospects:
        print("No prospects found.")
        return 0

    for p in prospects:
        value = f" (${p['monthly_value']}/mo)" if p.get("monthly_value") else ""
        print(f"{p['domain']:<40} {p['status']:<12}{value}")
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
        "--page-type", default="default", help="homepage|blog|pillar|product|service|about"
    )
    audit_parser.add_argument(
        "--brand-name", help="Enables the Wikipedia/Wikidata check in brand_authority"
    )
    audit_parser.add_argument("--domain", help="Domain label for storage (defaults to URL's netloc)")
    audit_parser.add_argument("--output", "-o", help="Write markdown report to this file instead of stdout")
    audit_parser.add_argument("--pdf", help="Also write a PDF report to this path")
    audit_parser.add_argument("--save", action="store_true", help="Persist this audit to Supabase")
    audit_parser.add_argument(
        "--prospect", action="store_true",
        help="Also create/update a prospect row for this domain (requires --save)",
    )
    audit_parser.set_defaults(func=cmd_audit)

    compare_parser = subparsers.add_parser(
        "compare", help="Compare the two most recent saved audits for a domain"
    )
    compare_parser.add_argument("domain")
    compare_parser.set_defaults(func=cmd_compare)

    prospects_parser = subparsers.add_parser("prospects", help="List tracked prospects")
    prospects_parser.add_argument("--status", choices=["lead", "qualified", "proposal", "won", "lost"])
    prospects_parser.set_defaults(func=cmd_prospects)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
