"""Locks in the declared-weight-vs-implemented-module coverage fix.

Before this fix: GEO 100%, AEO 85% (missing live_citation), SEO 40%
(missing technical_seo + content_quality), and AEO additionally computed
platform_optimization only to have CompositeScorer discard it. This test
exists so that discrepancy can never silently regress — if someone adds a
dimension to PROFILE_WEIGHTS without wiring a module (or vice versa), this
fails immediately instead of shipping a profile that quietly measures less
than its own score implies.
"""
from __future__ import annotations

import pytest

from seo_geo_aeo.core.orchestrator import _DOMAIN_DIMENSIONS, _PAGE_DIMENSIONS
from seo_geo_aeo.core.scoring import PROFILE_WEIGHTS


@pytest.mark.parametrize("profile", ["seo", "aeo", "geo"])
def test_every_declared_dimension_has_a_module(profile: str):
    declared = set(PROFILE_WEIGHTS[profile])
    produced = set(_PAGE_DIMENSIONS[profile]) | set(_DOMAIN_DIMENSIONS[profile])
    never_produced = declared - produced
    assert not never_produced, (
        f"{profile} declares weight for {sorted(never_produced)} but no module "
        f"produces {'it' if len(never_produced) == 1 else 'them'} — the composite "
        f"score silently renormalizes over less than the profile claims to measure."
    )


@pytest.mark.parametrize("profile", ["seo", "aeo", "geo"])
def test_no_dimension_is_computed_and_discarded(profile: str):
    declared = set(PROFILE_WEIGHTS[profile])
    produced = set(_PAGE_DIMENSIONS[profile]) | set(_DOMAIN_DIMENSIONS[profile])
    discarded = produced - declared
    assert not discarded, (
        f"{profile} produces {sorted(discarded)} but PROFILE_WEIGHTS[{profile!r}] "
        f"has no weight for {'it' if len(discarded) == 1 else 'them'} — that's wasted "
        f"computation (and, if the module makes network calls, wasted requests) for a "
        f"result CompositeScorer.combine() throws away every time."
    )
