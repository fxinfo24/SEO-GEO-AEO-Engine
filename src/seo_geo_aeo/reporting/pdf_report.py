"""Renders CompositeResult(s) into client-ready PDFs, using reportlab.

Two entry points:
- render_pdf_report: single-profile report (existing behavior).
- render_comprehensive_pdf_report: combines SEO + AEO + GEO results for the
  same domain into one document — an executive summary page, a deduplicated
  cross-profile priority-actions list, then a full breakdown section per
  profile.

Port of geo-report-pdf's intent — the source skill referenced a script path
that didn't exist in the repo, so this is a fresh implementation against
the same CompositeResult/Finding data model the markdown report uses, kept
in sync with it rather than duplicating scoring logic.
"""

from __future__ import annotations

from datetime import UTC, datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from seo_geo_aeo.core.scoring import CompositeResult, Finding, Severity

_SEVERITY_COLOR = {
    Severity.CRITICAL: colors.HexColor("#b91c1c"),
    Severity.HIGH: colors.HexColor("#c2410c"),
    Severity.MEDIUM: colors.HexColor("#a16207"),
    Severity.LOW: colors.HexColor("#15803d"),
}

_RATING_COLOR = {
    "Excellent": colors.HexColor("#15803d"),
    "Good": colors.HexColor("#4d7c0f"),
    "Fair": colors.HexColor("#a16207"),
    "Poor": colors.HexColor("#c2410c"),
    "Critical": colors.HexColor("#b91c1c"),
}

_SEVERITY_ORDER = {Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.MEDIUM: 2, Severity.LOW: 3}

# Canonical display order — SEO first (most familiar), then AEO, then GEO
# (broadest/newest surface), so a reader moves from familiar to novel.
_PROFILE_ORDER = ["seo", "aeo", "geo"]
_PROFILE_LABEL = {"seo": "SEO", "aeo": "AEO", "geo": "GEO"}


def _build_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("ReportTitle", parent=base["Title"], fontSize=20, spaceAfter=4),
        "subtitle": ParagraphStyle("ReportSubtitle", parent=base["Normal"], fontSize=11, textColor=colors.grey),
        "h2": ParagraphStyle("SectionHeading", parent=base["Heading2"], spaceBefore=18, spaceAfter=8),
        "h3": ParagraphStyle("SubHeading", parent=base["Heading3"], spaceBefore=10, spaceAfter=6),
        "body": ParagraphStyle("Body", parent=base["Normal"], fontSize=9.5, leading=13),
        "small": ParagraphStyle("Small", parent=base["Normal"], fontSize=8.5, leading=11, textColor=colors.grey),
        "score_big": ParagraphStyle(
            "ScoreBig", parent=base["Normal"], fontSize=36, leading=40, alignment=1
        ),
        "score_medium": ParagraphStyle(
            "ScoreMedium", parent=base["Normal"], fontSize=24, leading=28, alignment=1
        ),
    }


def _coverage_text(result: CompositeResult) -> str:
    """Mirrors markdown_report._coverage_line — see its docstring for why
    this exists. Both renderers must show this; neither is allowed to omit
    it in favor of the other."""
    pct = f"{result.measured_weight:.0%}"
    if result.unmeasured_weight <= 0:
        return f"Measured coverage: {pct} of declared dimensions"
    dims = ", ".join(result.unmeasured_dimensions)
    return f"Measured coverage: {pct} of declared dimensions (unmeasured: {dims})"


def _score_card(profile: str, result: CompositeResult, styles: dict[str, ParagraphStyle]) -> Table:
    """One profile's score as a compact colored card, used in the summary row."""
    rating_color = _RATING_COLOR.get(result.rating(), colors.black)
    score_style = ParagraphStyle("CardScore", parent=styles["score_medium"], textColor=rating_color)
    label_style = ParagraphStyle("CardLabel", parent=styles["body"], alignment=1, fontSize=11)
    rating_style = ParagraphStyle(
        "CardRating", parent=styles["body"], alignment=1, textColor=rating_color, fontSize=10
    )
    coverage_style = ParagraphStyle(
        "CardCoverage", parent=styles["small"], alignment=1, fontSize=7.5
    )
    cell = [
        [Paragraph(f"<b>{_PROFILE_LABEL[profile]}</b>", label_style)],
        [Paragraph(f"{result.overall_score}", score_style)],
        [Paragraph(f"/100 · {result.rating()}", rating_style)],
        [Paragraph(f"{result.measured_weight:.0%} measured", coverage_style)],
    ]
    table = Table(cell, colWidths=[2.0 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#d1d5db")),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f9fafb")),
                ("TOPPADDING", (0, 0), (-1, 0), 10),
                ("BOTTOMPADDING", (0, -1), (-1, -1), 10),
                ("TOPPADDING", (0, 1), (-1, 1), 2),
                ("BOTTOMPADDING", (0, 1), (-1, 1), 2),
            ]
        )
    )
    return table


def _breakdown_table(result: CompositeResult) -> Table:
    rows = [["Dimension", "Score", "Weight", "Status"]]
    for dim, weight in sorted(result.weights.items(), key=lambda kv: -kv[1]):
        ds = result.dimension_scores[dim]
        rows.append([dim.replace("_", " ").title(), f"{ds.score}/100", f"{weight:.0%}", "Measured"])
    for dim in result.unmeasured_dimensions:
        declared_weight = result.declared_weights.get(dim, 0.0)
        ds = result.dimension_scores.get(dim)
        reason = ds.unmeasured_reason if ds and ds.unmeasured_reason else "not produced"
        rows.append(
            [dim.replace("_", " ").title(), "—", f"{declared_weight:.0%}", f"Not measured — {reason}"]
        )
    table = Table(rows, colWidths=[2.4 * inch, 1.0 * inch, 1.0 * inch, 1.8 * inch])
    row_styles = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d1d5db")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]
    n_measured = len(result.weights)
    for i in range(1, len(rows)):
        if i <= n_measured:
            row_styles.append(("BACKGROUND", (0, i), (-1, i),
                                colors.white if i % 2 else colors.HexColor("#f3f4f6")))
        else:
            row_styles.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#fef3c7")))
            row_styles.append(("TEXTCOLOR", (0, i), (-1, i), colors.HexColor("#78716c")))
    table.setStyle(TableStyle(row_styles))
    return table


def _findings_table(findings: list[Finding], styles: dict[str, ParagraphStyle]) -> Table:
    rows = [["Severity", "Finding", "Detail"]]
    for f in findings:
        severity_cell = Paragraph(
            f'<font color="{_SEVERITY_COLOR[f.severity].hexval()}"><b>{f.severity.value.upper()}</b></font>',
            styles["body"],
        )
        rows.append(
            [severity_cell, Paragraph(f.title, styles["body"]), Paragraph(f.detail, styles["body"])]
        )
    table = Table(rows, colWidths=[0.9 * inch, 2.0 * inch, 3.3 * inch], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, 0), 9.5),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d1d5db")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def render_pdf_report(domain: str, result: CompositeResult, output_path: str) -> str:
    """Write a single-profile PDF report for `result` to `output_path`."""
    styles = _build_styles()
    doc = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
    )
    story = []

    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    story.append(Paragraph(f"{result.profile.upper()} Audit Report", styles["title"]))
    story.append(Paragraph(f"{domain} — generated {now}", styles["subtitle"]))
    story.append(Spacer(1, 0.25 * inch))

    rating_color = _RATING_COLOR.get(result.rating(), colors.black)
    score_style = ParagraphStyle("ScoreColored", parent=styles["score_big"], textColor=rating_color)
    story.append(Paragraph(f"{result.overall_score}/100", score_style))
    story.append(
        Paragraph(
            f'<para alignment="center"><font color="{rating_color.hexval()}"><b>{result.rating()}</b></font></para>',
            styles["body"],
        )
    )
    story.append(
        Paragraph(
            f'<para alignment="center">{_coverage_text(result)}</para>', styles["small"]
        )
    )
    story.append(Spacer(1, 0.3 * inch))

    story.append(Paragraph("Score Breakdown", styles["h2"]))
    story.append(_breakdown_table(result))

    findings = result.findings
    story.append(Paragraph("Findings", styles["h2"]))
    if findings:
        story.append(_findings_table(findings, styles))
    else:
        story.append(Paragraph("No issues found across the scored dimensions.", styles["body"]))

    critical_and_high = [f for f in findings if f.severity in (Severity.CRITICAL, Severity.HIGH)]
    if critical_and_high:
        story.append(PageBreak())
        story.append(Paragraph("Priority Actions", styles["h2"]))
        for i, f in enumerate(critical_and_high[:8], start=1):
            story.append(Paragraph(f"<b>{i}. {f.title}</b> — {f.detail}", styles["body"]))
            story.append(Spacer(1, 0.08 * inch))

    doc.build(story)
    return output_path


def render_comprehensive_pdf_report(
    domain: str, results: dict[str, CompositeResult], output_path: str
) -> str:
    """Write one PDF combining SEO + AEO + GEO results for the same domain.

    `results` maps profile name ("seo"/"aeo"/"geo") to its CompositeResult —
    pass whichever subset you have; the report adapts to however many are
    present. Findings that appear under the same title in more than one
    profile (common between AEO and GEO, which share several dimensions)
    are shown once in the cross-profile summary with the affected profiles
    noted, rather than repeated — full per-profile findings still appear in
    each profile's own detailed section later in the document.
    """
    present = [p for p in _PROFILE_ORDER if p in results]
    if not present:
        raise ValueError("results must contain at least one of 'seo', 'aeo', 'geo'")

    styles = _build_styles()
    doc = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
    )
    story = []
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    # --- Cover / executive summary ---
    story.append(Paragraph("SEO / AEO / GEO Audit Report", styles["title"]))
    story.append(Paragraph(f"{domain} — generated {now}", styles["subtitle"]))
    story.append(Spacer(1, 0.3 * inch))

    cards = [_score_card(p, results[p], styles) for p in present]
    card_row = Table([cards], colWidths=[2.1 * inch] * len(cards))
    card_row.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER")]))
    story.append(card_row)
    story.append(Spacer(1, 0.35 * inch))

    # --- Cross-profile priority actions (deduplicated by finding title) ---
    findings_by_title: dict[str, tuple[Finding, list[str]]] = {}
    for p in present:
        for f in results[p].findings:
            if f.severity not in (Severity.CRITICAL, Severity.HIGH):
                continue
            if f.title in findings_by_title:
                findings_by_title[f.title][1].append(_PROFILE_LABEL[p])
            else:
                findings_by_title[f.title] = (f, [_PROFILE_LABEL[p]])

    if findings_by_title:
        story.append(Paragraph("Top Priority Actions (all profiles)", styles["h2"]))
        ordered = sorted(
            findings_by_title.values(), key=lambda item: _SEVERITY_ORDER[item[0].severity]
        )
        for i, (f, profiles) in enumerate(ordered[:10], start=1):
            tag = "/".join(sorted(profiles))
            story.append(
                Paragraph(f"<b>{i}. [{tag}] {f.title}</b> — {f.detail}", styles["body"])
            )
            story.append(Spacer(1, 0.08 * inch))
    else:
        story.append(Paragraph("Top Priority Actions (all profiles)", styles["h2"]))
        story.append(Paragraph("No critical or high-severity findings across any profile.", styles["body"]))

    # --- Per-profile detailed sections ---
    for p in present:
        result = results[p]
        story.append(PageBreak())
        story.append(Paragraph(f"{_PROFILE_LABEL[p]} — Detailed Report", styles["title"]))
        rating_color = _RATING_COLOR.get(result.rating(), colors.black)
        story.append(
            Paragraph(
                f'<font color="{rating_color.hexval()}"><b>{result.overall_score}/100 '
                f"({result.rating()})</b></font>",
                styles["body"],
            )
        )
        story.append(Paragraph(_coverage_text(result), styles["small"]))
        story.append(Spacer(1, 0.2 * inch))

        story.append(Paragraph("Score Breakdown", styles["h3"]))
        story.append(_breakdown_table(result))
        story.append(Spacer(1, 0.15 * inch))

        findings = result.findings
        story.append(Paragraph("Findings", styles["h3"]))
        if findings:
            story.append(_findings_table(findings, styles))
        else:
            story.append(Paragraph("No issues found across the scored dimensions.", styles["body"]))

    doc.build(story)
    return output_path
