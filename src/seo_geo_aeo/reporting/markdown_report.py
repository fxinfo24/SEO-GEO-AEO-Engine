"""Renders a CompositeResult into a client-readable markdown report."""

from __future__ import annotations

from datetime import UTC, datetime

from seo_geo_aeo.core.scoring import CompositeResult, Severity

_SEVERITY_LABEL = {
    Severity.CRITICAL: "🔴 Critical",
    Severity.HIGH: "🟠 High",
    Severity.MEDIUM: "🟡 Medium",
    Severity.LOW: "🟢 Low",
}


def render_markdown_report(domain: str, result: CompositeResult) -> str:
    lines: list[str] = []
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    lines.append(f"# {result.profile.upper()} Audit Report: {domain}")
    lines.append(f"\n**Audit date:** {now}  ")
    lines.append(f"**Overall score: {result.overall_score}/100 ({result.rating()})**\n")

    lines.append("## Score Breakdown\n")
    lines.append("| Dimension | Score | Weight |")
    lines.append("|---|---|---|")
    for dim, weight in sorted(result.weights.items(), key=lambda kv: -kv[1]):
        ds = result.dimension_scores[dim]
        lines.append(f"| {dim.replace('_', ' ').title()} | {ds.score}/100 | {weight:.0%} |")
    lines.append("")

    findings = result.findings
    if findings:
        lines.append("## Findings (highest priority first)\n")
        lines.append("| Severity | Finding | Detail | Page |")
        lines.append("|---|---|---|---|")
        for f in findings:
            page = f.page_url or "—"
            lines.append(f"| {_SEVERITY_LABEL[f.severity]} | {f.title} | {f.detail} | {page} |")
        lines.append("")
    else:
        lines.append("## Findings\n\nNo issues found across the scored dimensions.\n")

    critical_and_high = [f for f in findings if f.severity in (Severity.CRITICAL, Severity.HIGH)]
    if critical_and_high:
        lines.append("## Priority Actions\n")
        for i, f in enumerate(critical_and_high[:5], start=1):
            lines.append(f"{i}. **{f.title}** — {f.detail}")
        lines.append("")

    return "\n".join(lines)
