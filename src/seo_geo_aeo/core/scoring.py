"""Shared scoring primitives.

Every dimension module (seo_technical, geo_crawlers, geo_citability, ...)
returns a `DimensionScore`. The `CompositeScorer` combines them with the
weights defined by whichever report profile is active (SEO / GEO / AEO have
different weightings per the source rubrics).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class Finding:
    severity: Severity
    title: str
    detail: str
    page_url: str | None = None


@dataclass
class DimensionScore:
    """Result of one scoring module (e.g. 'ai_citability', 'schema').

    `measured` defaults to True. A module sets it False when it could not
    actually perform its check (no API key, external service unreachable,
    etc.) — `score` is then ignored by `CompositeScorer.combine()`, which
    excludes the dimension from weighting entirely rather than letting an
    arbitrary placeholder number (0.0, 50.0, ...) masquerade as a real
    measurement. `unmeasured_reason` should say why in one sentence.
    """

    dimension: str
    score: float  # 0-100
    findings: list[Finding] = field(default_factory=list)
    raw: dict = field(default_factory=dict)  # module-specific debug data
    measured: bool = True
    unmeasured_reason: str | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.score <= 100:
            raise ValueError(f"{self.dimension} score {self.score} out of range 0-100")


@dataclass
class CompositeResult:
    """Weighted profile result with explicit measurement-coverage metadata.

    `weights` holds only the dimensions actually used to compute
    `overall_score` (present in dimension_scores AND measured=True).
    `declared_weights` is the profile's full weight table, for comparison —
    a caller can always tell exactly how much of the declared rubric was
    real versus how much was excluded, rather than the score silently
    representing 100% renormalized confidence over a partial measurement.
    """

    profile: str
    overall_score: float
    dimension_scores: dict[str, DimensionScore]
    weights: dict[str, float]
    declared_weights: dict[str, float]

    @property
    def measured_weight(self) -> float:
        """Fraction (0-1) of the declared profile weight actually measured."""
        return round(sum(self.weights.values()), 4)

    @property
    def unmeasured_weight(self) -> float:
        return round(1.0 - self.measured_weight, 4)

    @property
    def unmeasured_dimensions(self) -> list[str]:
        """Declared dimensions that did not contribute to overall_score,
        whether because no module ran or the module reported measured=False."""
        return sorted(set(self.declared_weights) - set(self.weights))

    @property
    def findings(self) -> list[Finding]:
        all_findings: list[Finding] = []
        for dim in self.dimension_scores.values():
            all_findings.extend(dim.findings)
        order = {Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.MEDIUM: 2, Severity.LOW: 3}
        return sorted(all_findings, key=lambda f: order[f.severity])

    def rating(self) -> str:
        return rating_for_score(self.overall_score)


def rating_for_score(score: float) -> str:
    if score >= 90:
        return "Excellent"
    if score >= 75:
        return "Good"
    if score >= 60:
        return "Fair"
    if score >= 40:
        return "Poor"
    return "Critical"


# Weight profiles ported from the source rubrics (geo-audit, aeo-audit,
# geo-report SKILL.md files). Keys must match DimensionScore.dimension names
# produced by the registered modules.
PROFILE_WEIGHTS: dict[str, dict[str, float]] = {
    "geo": {
        "ai_citability": 0.25,
        "brand_authority": 0.20,
        "content_eeat": 0.20,
        "technical_geo": 0.15,
        "schema": 0.10,
        "platform_optimization": 0.10,
    },
    "seo": {
        "technical_seo": 0.35,
        "on_page": 0.25,
        "content_quality": 0.25,
        "schema": 0.15,
    },
    "aeo": {
        "ai_citation_likelihood": 0.15,
        "ai_citability": 0.20,
        "brand_authority": 0.20,
        "content_eeat": 0.20,
        "technical_geo": 0.15,
        "schema": 0.10,
    },
}


def aggregate_dimension_scores(dimension: str, page_scores: list[DimensionScore]) -> DimensionScore:
    """Combine one dimension's per-page scores into a single site-level score.

    Score is the mean across the pages that were actually measured. Findings
    are deduplicated by title (a "No H1 found" on 15 pages becomes one
    finding noting the count) and capped so a large crawl doesn't produce an
    unreadable report.

    A dimension is only `measured` when every page scored it. If any page
    reported `measured=False` — an unreachable API, a missing key — that
    page's placeholder zero is excluded from the mean rather than averaged
    in as a real score, and the aggregate inherits `measured=False` so the
    composite scorer drops the dimension instead of treating a failed check
    as a genuine result.
    """
    if not page_scores:
        raise ValueError(f"Cannot aggregate zero page scores for dimension {dimension!r}")

    measured_pages = [ds for ds in page_scores if ds.measured]
    unmeasured_pages = [ds for ds in page_scores if not ds.measured]
    all_measured = not unmeasured_pages

    # With nothing measured there is no score to report; mean over the
    # measured subset instead of over placeholder zeros.
    score_basis = measured_pages or page_scores
    mean_score = sum(ds.score for ds in score_basis) / len(score_basis)

    findings_by_title: dict[str, list[Finding]] = {}
    for ds in page_scores:
        for f in ds.findings:
            findings_by_title.setdefault(f.title, []).append(f)

    order = {Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.MEDIUM: 2, Severity.LOW: 3}
    aggregated_findings: list[Finding] = []
    for title, occurrences in sorted(findings_by_title.items(), key=lambda kv: order[kv[1][0].severity]):
        sample = occurrences[0]
        count = len(occurrences)
        detail = sample.detail if count == 1 else f"{sample.detail} (found on {count} pages)"
        aggregated_findings.append(
            Finding(severity=sample.severity, title=title, detail=detail, page_url=sample.page_url)
        )

    unmeasured_reason = None
    if not all_measured:
        reasons = {ds.unmeasured_reason for ds in unmeasured_pages if ds.unmeasured_reason}
        unmeasured_reason = "; ".join(sorted(reasons)) or (
            f"{len(unmeasured_pages)} of {len(page_scores)} pages did not measure this dimension."
        )

    return DimensionScore(
        dimension=dimension,
        score=round(mean_score, 1),
        measured=all_measured,
        unmeasured_reason=unmeasured_reason,
        findings=aggregated_findings,
        raw={
            "pages_scored": len(page_scores),
            "pages_measured": len(measured_pages),
            "per_page_scores": [ds.score for ds in page_scores],
        },
    )


class CompositeScorer:
    """Combines DimensionScore results into a weighted CompositeResult.

    Missing dimensions (a module that wasn't run) are excluded and the
    remaining weights are renormalized, so a partial run still produces a
    meaningful score instead of silently treating "not run" as zero.
    """

    def __init__(self, profile: str, weights: dict[str, float] | None = None) -> None:
        if profile not in PROFILE_WEIGHTS and weights is None:
            raise ValueError(
                f"Unknown profile {profile!r}; pass explicit weights or use one of "
                f"{sorted(PROFILE_WEIGHTS)}"
            )
        self.profile = profile
        self.weights = weights or PROFILE_WEIGHTS[profile]

    def combine(self, dimension_scores: dict[str, DimensionScore]) -> CompositeResult:
        applicable = {
            k: v
            for k, v in self.weights.items()
            if k in dimension_scores and dimension_scores[k].measured
        }
        if not applicable:
            raise ValueError(
                f"None of the scored dimensions {list(dimension_scores)} match profile "
                f"{self.profile!r} weights {list(self.weights)}, or all matches were "
                f"unmeasured"
            )
        weight_sum = sum(applicable.values())
        overall = sum(
            dimension_scores[dim].score * (weight / weight_sum) for dim, weight in applicable.items()
        )
        return CompositeResult(
            profile=self.profile,
            overall_score=round(overall, 1),
            dimension_scores=dimension_scores,
            weights=applicable,
            declared_weights=dict(self.weights),
        )
