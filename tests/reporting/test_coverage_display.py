"""Tests that unmeasured-dimension coverage actually reaches the reports.

Per RoadMap.md Phase 2.3: the measured/unmeasured data existing on
CompositeResult is not enough — a report must display it. These tests
exist so that data model without a rendering path can never regress
silently again.
"""
from __future__ import annotations

from seo_geo_aeo.core.scoring import (
    CompositeResult,
    CompositeScorer,
    DimensionScore,
)
from seo_geo_aeo.reporting.markdown_report import render_markdown_report
from seo_geo_aeo.reporting.pdf_report import render_pdf_report


def _partial_geo_result() -> CompositeResult:
    scores = {
        "ai_citability": DimensionScore(dimension="ai_citability", score=80.0),
        "content_eeat": DimensionScore(dimension="content_eeat", score=60.0),
        "brand_authority": DimensionScore(dimension="brand_authority", score=40.0),
        "technical_geo": DimensionScore(dimension="technical_geo", score=90.0),
        "schema": DimensionScore(dimension="schema", score=70.0),
        "platform_optimization": DimensionScore(
            dimension="platform_optimization",
            score=0.0,
            measured=False,
            unmeasured_reason="Test: simulated failure",
        ),
    }
    return CompositeScorer("geo").combine(scores)


def test_markdown_report_shows_coverage_percentage():
    result = _partial_geo_result()
    report = render_markdown_report("example.com", result)

    assert "Measured coverage: 90%" in report


def test_markdown_report_lists_unmeasured_dimension_and_reason():
    result = _partial_geo_result()
    report = render_markdown_report("example.com", result)

    assert "platform_optimization" in report
    assert "Test: simulated failure" in report
    assert "Not measured" in report


def test_markdown_report_shows_full_coverage_without_unmeasured_note():
    scores = {
        "ai_citability": DimensionScore(dimension="ai_citability", score=100.0),
        "content_eeat": DimensionScore(dimension="content_eeat", score=100.0),
        "brand_authority": DimensionScore(dimension="brand_authority", score=100.0),
        "technical_geo": DimensionScore(dimension="technical_geo", score=100.0),
        "schema": DimensionScore(dimension="schema", score=100.0),
        "platform_optimization": DimensionScore(dimension="platform_optimization", score=100.0),
    }
    result = CompositeScorer("geo").combine(scores)
    report = render_markdown_report("example.com", result)

    assert "Measured coverage: 100%" in report
    assert "unmeasured:" not in report


def test_pdf_report_builds_with_unmeasured_dimension(tmp_path):
    """Doesn't assert on PDF binary content (not practical) — asserts the
    renderer doesn't crash when a result has unmeasured dimensions, since
    _breakdown_table now branches on that."""
    result = _partial_geo_result()
    output_path = tmp_path / "report.pdf"

    written = render_pdf_report("example.com", result, str(output_path))

    assert output_path.exists()
    assert output_path.stat().st_size > 0
    assert written == str(output_path)
