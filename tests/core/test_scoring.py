"""Core invariant tests for scoring.py — the composite scorer, measured/
unmeasured exclusion, and cross-page aggregation."""
from __future__ import annotations

import pytest

from seo_geo_aeo.core.scoring import (
    PROFILE_WEIGHTS,
    CompositeScorer,
    DimensionScore,
    Finding,
    Severity,
    aggregate_dimension_scores,
)


@pytest.mark.parametrize("profile", ["seo", "aeo", "geo"])
def test_profile_weights_sum_to_one(profile: str):
    total = sum(PROFILE_WEIGHTS[profile].values())
    assert total == pytest.approx(1.0, abs=1e-6), (
        f"{profile} weights sum to {total}, not 1.0 — a renormalized score built "
        f"from this table would silently distort every dimension's contribution."
    )


def test_dimension_score_rejects_out_of_range():
    with pytest.raises(ValueError):
        DimensionScore(dimension="x", score=101.0)
    with pytest.raises(ValueError):
        DimensionScore(dimension="x", score=-1.0)


def test_composite_scorer_excludes_unmeasured_dimensions():
    """A DimensionScore with measured=False must not count toward the
    overall score, and its weight must be reported as unmeasured, not
    silently folded into the other dimensions' renormalization."""
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
            unmeasured_reason="test",
        ),
    }
    scorer = CompositeScorer("geo")
    result = scorer.combine(scores)

    assert "platform_optimization" not in result.weights
    assert "platform_optimization" in result.unmeasured_dimensions
    assert result.unmeasured_weight == pytest.approx(0.10, abs=1e-6)
    assert result.measured_weight == pytest.approx(0.90, abs=1e-6)


def test_composite_scorer_renormalizes_over_measured_only():
    """With one dimension excluded, the remaining weights should be
    renormalized so overall_score still reflects a 0-100 scale correctly,
    not silently scaled down by the missing dimension's weight."""
    scores = {
        "ai_citability": DimensionScore(dimension="ai_citability", score=100.0),
        "content_eeat": DimensionScore(dimension="content_eeat", score=100.0),
        "brand_authority": DimensionScore(dimension="brand_authority", score=100.0),
        "technical_geo": DimensionScore(dimension="technical_geo", score=100.0),
        "schema": DimensionScore(dimension="schema", score=100.0),
        "platform_optimization": DimensionScore(
            dimension="platform_optimization", score=0.0, measured=False
        ),
    }
    result = CompositeScorer("geo").combine(scores)
    # All measured dimensions are 100 -> overall must be 100, not 90
    # (i.e. not accidentally treating the unmeasured dimension as a 0).
    assert result.overall_score == pytest.approx(100.0, abs=0.1)


def test_composite_scorer_raises_when_nothing_measured():
    scores = {
        "ai_citability": DimensionScore(
            dimension="ai_citability", score=0.0, measured=False
        )
    }
    with pytest.raises(ValueError):
        CompositeScorer("geo").combine(scores)


def test_aggregate_dimension_scores_averages_and_dedupes_findings():
    finding = Finding(
        severity=Severity.HIGH, title="No H1 found", detail="Page has zero H1 tags."
    )
    page_scores = [
        DimensionScore(dimension="on_page", score=80.0, findings=[finding]),
        DimensionScore(dimension="on_page", score=60.0, findings=[finding]),
        DimensionScore(dimension="on_page", score=100.0, findings=[]),
    ]
    aggregated = aggregate_dimension_scores("on_page", page_scores)

    assert aggregated.score == pytest.approx(80.0, abs=0.1)  # mean of 80/60/100
    assert len(aggregated.findings) == 1  # deduped by title, not listed 3x
    assert "2 pages" in aggregated.findings[0].detail
    assert aggregated.raw["pages_scored"] == 3


def test_aggregate_dimension_scores_rejects_empty_input():
    with pytest.raises(ValueError):
        aggregate_dimension_scores("on_page", [])


def test_aggregate_preserves_unmeasured_flag():
    """Regression: the aggregate used to rebuild DimensionScore without
    passing `measured` through, so it silently defaulted to True.

    A dimension whose pages all failed — e.g. ai_citation_likelihood with an
    unreachable API, which returns score=0.0 with measured=False — was
    laundered into a *measured zero*. The composite then counted a failed
    API call as a real result: the site-wide report showed
    "Ai Citation Likelihood 0.0/100 Measured" at 100% coverage while every
    page had actually failed.
    """
    page_scores = [
        DimensionScore(
            dimension="ai_citation_likelihood",
            score=0.0,
            measured=False,
            unmeasured_reason="OpenRouter API call failed or returned no extractable score.",
            findings=[],
        )
        for _ in range(3)
    ]

    aggregated = aggregate_dimension_scores("ai_citation_likelihood", page_scores)

    assert aggregated.measured is False, "failed dimension must not aggregate to measured=True"
    assert aggregated.unmeasured_reason is not None
    assert "OpenRouter" in aggregated.unmeasured_reason


def test_aggregate_excludes_unmeasured_pages_from_mean():
    """A placeholder zero from a failed page must not drag the mean down."""
    page_scores = [
        DimensionScore(dimension="on_page", score=80.0),
        DimensionScore(dimension="on_page", score=60.0),
        DimensionScore(
            dimension="on_page",
            score=0.0,
            measured=False,
            unmeasured_reason="fetch failed",
        ),
    ]

    aggregated = aggregate_dimension_scores("on_page", page_scores)

    # Mean of the measured pages only (80+60)/2 = 70, not (80+60+0)/3 = 46.7.
    assert aggregated.score == pytest.approx(70.0, abs=0.1)
    assert aggregated.measured is False
    assert aggregated.raw["pages_scored"] == 3
    assert aggregated.raw["pages_measured"] == 2


def test_aggregate_all_measured_stays_measured():
    """The fix must not mark healthy dimensions unmeasured."""
    page_scores = [
        DimensionScore(dimension="on_page", score=80.0),
        DimensionScore(dimension="on_page", score=60.0),
    ]

    aggregated = aggregate_dimension_scores("on_page", page_scores)

    assert aggregated.measured is True
    assert aggregated.unmeasured_reason is None
    assert aggregated.score == pytest.approx(70.0, abs=0.1)


def test_failed_dimension_is_excluded_from_composite():
    """End-to-end: an all-failed dimension must not drag the overall score."""
    weights = {"on_page": 0.5, "ai_citation_likelihood": 0.5}
    scores = {
        "on_page": DimensionScore(dimension="on_page", score=80.0),
        "ai_citation_likelihood": aggregate_dimension_scores(
            "ai_citation_likelihood",
            [
                DimensionScore(
                    dimension="ai_citation_likelihood",
                    score=0.0,
                    measured=False,
                    unmeasured_reason="api failed",
                )
            ],
        ),
    }

    result = CompositeScorer("geo", weights=weights).combine(scores)

    assert "ai_citation_likelihood" in result.unmeasured_dimensions
    # 80 stands alone with its weight renormalized to 1.0, not averaged with 0.
    assert result.overall_score == pytest.approx(80.0, abs=0.1)
