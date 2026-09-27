"""
SEO/GEO/AEO scoring modules package.
"""
from __future__ import annotations

from seo_geo_aeo.modules.brand_authority import extract_same_as_links, score_brand_authority
from seo_geo_aeo.modules.content_quality import score_content_quality
from seo_geo_aeo.modules.eeat import score_eeat
from seo_geo_aeo.modules.geo_citability import score_citability
from seo_geo_aeo.modules.geo_crawlers import analyze_crawler_access
from seo_geo_aeo.modules.geo_schema import score_schema
from seo_geo_aeo.modules.platform_optimization import score_platform_optimization
from seo_geo_aeo.modules.seo_technical import score_on_page_seo
from seo_geo_aeo.modules.technical_seo import score_technical_seo

__all__ = [
    "analyze_crawler_access",
    "extract_same_as_links",
    "score_brand_authority",
    "score_citability",
    "score_content_quality",
    "score_eeat",
    "score_on_page_seo",
    "score_platform_optimization",
    "score_schema",
    "score_technical_seo",
]