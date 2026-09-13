"""AI citability scoring: how extractable/quotable a page's content blocks are.

Ports the 5-category rubric from aeo-audit's `citability-rubric.md` /
geo-citability SKILL.md (Answer Block Quality 30%, Self-Containment 25%,
Structural Readability 20%, Statistical Density 15%, Uniqueness 10%) into a
deterministic scorer instead of an LLM re-reading the rubric each time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from seo_geo_aeo.core.parser import HeadingBlock, ParsedPage
from seo_geo_aeo.core.scoring import DimensionScore, Finding, Severity

_DEFINITION_PATTERN = re.compile(
    r"^\s*[\w\s\-']{2,40}\s+(is|refers to|means|are)\b", re.IGNORECASE
)
_STAT_PATTERN = re.compile(
    r"(\$\d[\d,]*(\.\d+)?|\b\d{1,3}(\.\d+)?%|\b\d{4}\b(?!\s?(?:AM|PM))|\b\d[\d,]{2,}\b)"
)
_VAGUE_QUANTIFIERS = re.compile(
    r"\b(many|most|several|some|a lot of|a significant (?:percentage|number) of)\b",
    re.IGNORECASE,
)
_PRONOUN_OPEN = re.compile(r"^\s*(it|this|that|they|these|those|he|she)\b", re.IGNORECASE)


@dataclass
class BlockScore:
    heading: str
    word_count: int
    answer_quality: float
    self_containment: float
    structure: float
    stat_density: float
    uniqueness: float

    @property
    def overall(self) -> float:
        return (
            self.answer_quality * 0.30
            + self.self_containment * 0.25
            + self.structure * 0.20
            + self.stat_density * 0.15
            + self.uniqueness * 0.10
        )


def _score_answer_quality(first_sentence: str, block_text: str) -> float:
    score = 30.0
    if _DEFINITION_PATTERN.match(first_sentence):
        score += 40.0
    words = block_text.split()
    first_60 = " ".join(words[:60])
    if _STAT_PATTERN.search(first_60):
        score += 15.0
    if 40 <= len(first_60.split()) <= 60:
        score += 15.0
    return min(score, 100.0)


def _score_self_containment(first_sentence: str, block_text: str) -> float:
    score = 40.0
    if not _PRONOUN_OPEN.match(first_sentence):
        score += 25.0
    if _STAT_PATTERN.search(block_text):
        score += 20.0
    word_count = len(block_text.split())
    if 50 <= word_count <= 200:
        score += 15.0
    return min(score, 100.0)


def _score_structure(heading: HeadingBlock, block_text: str) -> float:
    score = 20.0
    if heading.text.strip().endswith("?") or re.match(
        r"^(what|how|why|when|where|who|which)\b", heading.text.strip(), re.IGNORECASE
    ):
        score += 25.0
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", block_text) if s.strip()]
    if sentences:
        avg_len = sum(len(s.split()) for s in sentences) / len(sentences)
        if avg_len <= 25:
            score += 25.0
    if re.search(r"[-*]\s|\d\.\s", block_text):
        score += 15.0
    if "|" in block_text:  # crude table-in-markdown signal
        score += 15.0
    return min(score, 100.0)


def _score_stat_density(block_text: str) -> float:
    word_count = max(len(block_text.split()), 1)
    stat_hits = len(_STAT_PATTERN.findall(block_text))
    vague_hits = len(_VAGUE_QUANTIFIERS.findall(block_text))
    per_500 = stat_hits / word_count * 500
    score = min(per_500 * 20.0, 90.0)
    score -= min(vague_hits * 5.0, 20.0)
    return max(min(score, 100.0), 0.0)


def _score_uniqueness(block_text: str) -> float:
    # Heuristic proxy: first-party language signals ("our analysis", "we
    # surveyed") correlate with original data per the source rubric. A true
    # uniqueness score requires comparing against competitor pages, which is
    # out of scope for a single-page pass — this is a conservative estimate.
    signals = re.findall(
        r"\b(our (?:analysis|research|survey|data|study)|we (?:surveyed|tested|found|analyzed))\b",
        block_text,
        re.IGNORECASE,
    )
    return min(40.0 + len(signals) * 20.0, 100.0)


def score_citability(page: ParsedPage) -> DimensionScore:
    """Score a single ParsedPage's citability (0-100), block by block."""
    if not page.headings:
        return DimensionScore(
            dimension="ai_citability",
            score=20.0,
            findings=[
                Finding(
                    severity=Severity.HIGH,
                    title="No heading structure found",
                    detail="Content with no H2/H3 blocks cannot be scored per-section and is "
                    "unlikely to be extracted cleanly by AI systems.",
                    page_url=page.url,
                )
            ],
            raw={"block_scores": []},
        )

    block_scores: list[BlockScore] = []
    for heading in page.headings:
        if heading.level not in (2, 3) or not heading.following_text:
            continue
        text = heading.following_text
        first_sentence_match = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)
        first_sentence = first_sentence_match[0] if first_sentence_match else text
        block_scores.append(
            BlockScore(
                heading=heading.text,
                word_count=len(text.split()),
                answer_quality=_score_answer_quality(first_sentence, text),
                self_containment=_score_self_containment(first_sentence, text),
                structure=_score_structure(heading, text),
                stat_density=_score_stat_density(text),
                uniqueness=_score_uniqueness(text),
            )
        )

    if not block_scores:
        return DimensionScore(
            dimension="ai_citability",
            score=25.0,
            findings=[
                Finding(
                    severity=Severity.MEDIUM,
                    title="Headings present but no scorable content beneath them",
                    detail="H2/H3 tags exist but have little or no following text.",
                    page_url=page.url,
                )
            ],
            raw={"block_scores": []},
        )

    overall = sum(b.overall for b in block_scores) / len(block_scores)
    coverage = sum(1 for b in block_scores if b.overall >= 70) / len(block_scores)

    findings: list[Finding] = []
    weakest = sorted(block_scores, key=lambda b: b.overall)[:3]
    for block in weakest:
        if block.overall < 60:
            findings.append(
                Finding(
                    severity=Severity.MEDIUM if block.overall >= 40 else Severity.HIGH,
                    title=f"Low-citability block: \"{block.heading}\"",
                    detail=(
                        f"Score {block.overall:.0f}/100. Rewrite the opening sentence as a direct "
                        "answer/definition, add a sourced statistic in the first 60 words, and "
                        "keep the block to 50-200 self-contained words."
                    ),
                    page_url=page.url,
                )
            )

    return DimensionScore(
        dimension="ai_citability",
        score=round(overall, 1),
        findings=findings,
        raw={
            "block_scores": [b.__dict__ | {"overall": b.overall} for b in block_scores],
            "citability_coverage_pct": round(coverage * 100, 1),
        },
    )
