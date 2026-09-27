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


def _coverage_line(result: CompositeResult) -> str:
    """One-line coverage statement — never let a score stand alone without it.

    Per RoadMap.md Phase 2.3: 'Do not hide this information in debug-only
    output.' The underlying measured_weight/unmeasured_weight/
    unmeasured_dimensions data already existed on CompositeResult; this is
    what actually surfaces it to a reader instead of the report.
    """
    pct = f"{result.measured_weight:.0%}"
    if result.unmeasured_weight <= 0:
        return f"**Measured coverage: {pct} of declared dimensions**"
    dims = ", ".join(result.unmeasured_dimensions)
    return f"**Measured coverage: {pct} of declared dimensions** (unmeasured: {dims})"


def render_markdown_report(domain: str, result: CompositeResult) -> str:
    lines: list[str] = []
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    lines.append(f"# {result.profile.upper()} Audit Report: {domain}")
    lines.append(f"\n**Audit date:** {now}  ")
    lines.append(f"**Overall score: {result.overall_score}/100 ({result.rating()})**  ")
    lines.append(f"{_coverage_line(result)}\n")

    lines.append("## Score Breakdown\n")
    lines.append("| Dimension | Score | Weight | Status |")
    lines.append("|---|---|---|---|")
    for dim, weight in sorted(result.weights.items(), key=lambda kv: -kv[1]):
        ds = result.dimension_scores[dim]
        lines.append(
            f"| {dim.replace('_', ' ').title()} | {ds.score}/100 | {weight:.0%} | Measured |"
        )
    for dim in result.unmeasured_dimensions:
        declared_weight = result.declared_weights.get(dim, 0.0)
        ds = result.dimension_scores.get(dim)
        reason = ds.unmeasured_reason if ds and ds.unmeasured_reason else "not produced"
        lines.append(
            f"| {dim.replace('_', ' ').title()} | — | {declared_weight:.0%} | "
            f"*Not measured — {reason}* |"
        )
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
