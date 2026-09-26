"""
Tests for the live citation scoring module.
"""
from __future__ import annotations

import os
from unittest.mock import patch

from seo_geo_aeo.core.parser import ParsedPage
from seo_geo_aeo.modules.live_citation import score_live_citation


def _make_page() -> ParsedPage:
    return ParsedPage(
        url="https://example.com",
        title="Test",
        meta_description=None,
        canonical=None,
        robots_meta=None,
        h1_count=0,
        headings=[],
        word_count=10,
        schema_blocks=[],
        images_total=0,
        images_missing_alt=0,
        internal_links=[],
        external_links=[],
        open_graph={},
        has_viewport_meta=False,
        author_byline_present=False,
        published_date=None,
        modified_date=None,
        raw_html="<html><body><p>Test</p></body></html>",
    )


def test_no_api_key_is_unmeasured_not_a_fake_score():
    """No OPENROUTER_API_KEY must produce measured=False, never a placeholder score.

    This used to return score=0.0 (a fake, misleadingly-real-looking number)
    that fed directly into the AEO composite. Now it must be explicitly
    excluded via measured=False.
    """
    with patch.dict(os.environ, {}, clear=True):
        result = score_live_citation(_make_page())

    assert result.dimension == "live_citation"
    assert result.measured is False
    assert result.unmeasured_reason is not None
    assert "API" in result.unmeasured_reason
    assert len(result.findings) >= 1


def test_api_failure_is_unmeasured_not_a_neutral_score():
    """A failed/unparseable API call must be measured=False, not a fake 50.0
    'neutral' score standing in for a real measurement."""
    with patch.dict(os.environ, {"OPENROUTER_API_KEY": "fake-key-for-test"}), patch(
        "seo_geo_aeo.modules.live_citation._call_openrouter_api"
    ) as mock_call:
        from seo_geo_aeo.modules.live_citation import _ApiCallResult

        mock_call.return_value = _ApiCallResult(
            score=None, response_text_length=0, extraction_method="none"
        )
        result = score_live_citation(_make_page())

    assert result.measured is False
    assert result.score == 0.0  # score is a placeholder; measured=False is what matters
    assert result.unmeasured_reason is not None


def test_successful_call_is_measured_with_real_score():
    """A successful, parseable API response should be measured=True with the
    extracted score and method recorded in raw data."""
    with patch.dict(os.environ, {"OPENROUTER_API_KEY": "fake-key-for-test"}), patch(
        "seo_geo_aeo.modules.live_citation._call_openrouter_api"
    ) as mock_call:
        from seo_geo_aeo.modules.live_citation import _ApiCallResult

        mock_call.return_value = _ApiCallResult(
            score=72.5, response_text_length=340, extraction_method="strict_marker"
        )
        result = score_live_citation(_make_page())

    assert result.measured is True
    assert result.score == 72.5
    assert result.raw["extraction_method"] == "strict_marker"
    assert result.raw["raw_response_length"] == 340
    assert result.raw["api_provider"] == "openrouter"
