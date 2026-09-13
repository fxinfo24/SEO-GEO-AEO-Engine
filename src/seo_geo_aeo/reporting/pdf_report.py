"""Renders a CompositeResult into a client-ready PDF, using reportlab.

Port of geo-report-pdf's intent — the source skill referenced a script path
that didn't exist in the repo, so this is a fresh implementation against
the same CompositeResult/Finding data model the markdown report uses, kept
in sync with it rather than duplicating scoring logic.
"""

from __future__ import annotations

from datetime import datetime, timezone

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

from seo_geo_aeo.core.scoring import CompositeResult, Severity

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


def _build_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("ReportTitle", parent=base["Title"], fontSize=20, spaceAfter=4),
        "subtitle": ParagraphStyle("ReportSubtitle", parent=base["Normal"], fontSize=11, textColor=colors.grey),
        "h2": ParagraphStyle("SectionHeading", parent=base["Heading2"], spaceBefore=18, spaceAfter=8),
        "body": ParagraphStyle("Body", parent=base["Normal"], fontSize=9.5, leading=13),
        "score_big": ParagraphStyle(
            "ScoreBig", parent=base["Normal"], fontSize=36, leading=40, alignment=1
        ),
    }


def render_pdf_report(domain: str, result: CompositeResult, output_path: str) -> str:
    """Write a PDF report for `result` to `output_path`. Returns the path written."""
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

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    story.append(Paragraph(f"{result.profile.upper()} Audit Report", styles["title"]))
    story.append(Paragraph(f"{domain} — generated {now}", styles["subtitle"]))
    story.append(Spacer(1, 0.25 * inch))

    rating_color = _RATING_COLOR.get(result.rating(), colors.black)
    score_style = ParagraphStyle(
        "ScoreColored", parent=styles["score_big"], textColor=rating_color
    )
    story.append(Paragraph(f"{result.overall_score}/100", score_style))
    story.append(
        Paragraph(
            f'<para alignment="center"><font color="{rating_color.hexval()}"><b>{result.rating()}</b></font></para>',
            styles["body"],
        )
    )
    story.append(Spacer(1, 0.3 * inch))

    story.append(Paragraph("Score Breakdown", styles["h2"]))
    breakdown_rows = [["Dimension", "Score", "Weight"]]
    for dim, weight in sorted(result.weights.items(), key=lambda kv: -kv[1]):
        ds = result.dimension_scores[dim]
        breakdown_rows.append([dim.replace("_", " ").title(), f"{ds.score}/100", f"{weight:.0%}"])
    breakdown_table = Table(breakdown_rows, colWidths=[3.2 * inch, 1.5 * inch, 1.5 * inch])
    breakdown_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 9.5),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f4f6")]),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d1d5db")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(breakdown_table)

    findings = result.findings
    if findings:
        story.append(Paragraph("Findings", styles["h2"]))
        finding_rows = [["Severity", "Finding", "Detail"]]
        for f in findings:
            severity_cell = Paragraph(
                f'<font color="{_SEVERITY_COLOR[f.severity].hexval()}"><b>{f.severity.value.upper()}</b></font>',
                styles["body"],
            )
            finding_rows.append(
                [severity_cell, Paragraph(f.title, styles["body"]), Paragraph(f.detail, styles["body"])]
            )
        findings_table = Table(finding_rows, colWidths=[0.9 * inch, 2.0 * inch, 3.3 * inch], repeatRows=1)
        findings_table.setStyle(
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
        story.append(findings_table)
    else:
        story.append(Paragraph("Findings", styles["h2"]))
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
