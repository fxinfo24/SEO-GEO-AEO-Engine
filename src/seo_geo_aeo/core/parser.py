"""HTML -> structured page data, shared by every scoring module.

Ports the extraction logic that was duplicated across seo-audit, geo-audit,
seo-page, geo-content, geo-schema, and eeat-audit in the source masterlist
into one parse pass so scoring modules never re-parse the same HTML.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag


@dataclass
class HeadingBlock:
    level: int
    text: str
    following_text: str


@dataclass
class ParsedPage:
    url: str
    title: str | None
    meta_description: str | None
    canonical: str | None
    robots_meta: str | None
    h1_count: int
    headings: list[HeadingBlock]
    word_count: int
    schema_blocks: list[dict]
    images_total: int
    images_missing_alt: int
    internal_links: list[str]
    external_links: list[str]
    open_graph: dict[str, str]
    has_viewport_meta: bool
    author_byline_present: bool
    published_date: str | None
    modified_date: str | None
    raw_html: str = field(repr=False)


def _text_or_none(tag: Tag | None) -> str | None:
    if tag is None:
        return None
    text = tag.get_text(strip=True)
    return text or None


def _extract_schema_blocks(soup: BeautifulSoup) -> list[dict]:
    blocks: list[dict] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            blocks.append({"@parse_error": True, "raw": raw[:500]})
            continue
        if isinstance(parsed, list):
            blocks.extend(item for item in parsed if isinstance(item, dict))
        elif isinstance(parsed, dict):
            if "@graph" in parsed and isinstance(parsed["@graph"], list):
                blocks.extend(item for item in parsed["@graph"] if isinstance(item, dict))
            else:
                blocks.append(parsed)
    return blocks


def _extract_headings(soup: BeautifulSoup) -> list[HeadingBlock]:
    headings: list[HeadingBlock] = []
    for level in range(1, 7):
        for tag in soup.find_all(f"h{level}"):
            following_parts: list[str] = []
            for sibling in tag.find_next_siblings():
                if sibling.name and re.match(r"h[1-6]$", sibling.name):
                    break
                text = sibling.get_text(" ", strip=True)
                if text:
                    following_parts.append(text)
                if sum(len(p) for p in following_parts) > 400:
                    break
            headings.append(
                HeadingBlock(
                    level=level,
                    text=tag.get_text(strip=True),
                    following_text=" ".join(following_parts)[:400],
                )
            )
    return headings


def _classify_links(soup: BeautifulSoup, base_url: str) -> tuple[list[str], list[str]]:
    base_host = urlparse(base_url).netloc
    internal: list[str] = []
    external: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:")):
            continue
        absolute = urljoin(base_url, href)
        if urlparse(absolute).netloc == base_host:
            internal.append(absolute)
        else:
            external.append(absolute)
    return internal, external


def parse_page(url: str, html: str) -> ParsedPage:
    """Parse raw HTML for `url` into a ParsedPage used by all scoring modules."""
    soup = BeautifulSoup(html, "html.parser")

    title = _text_or_none(soup.find("title"))

    meta_description = None
    md_tag = soup.find("meta", attrs={"name": re.compile(r"^description$", re.IGNORECASE)})
    if md_tag and md_tag.get("content"):
        meta_description = md_tag["content"].strip()

    canonical = None
    canon_tag = soup.find("link", attrs={"rel": re.compile(r"^canonical$", re.IGNORECASE)})
    if canon_tag and canon_tag.get("href"):
        canonical = urljoin(url, canon_tag["href"])

    robots_meta = None
    robots_tag = soup.find("meta", attrs={"name": re.compile(r"^robots$", re.IGNORECASE)})
    if robots_tag and robots_tag.get("content"):
        robots_meta = robots_tag["content"].strip()

    h1_tags = soup.find_all("h1")
    headings = _extract_headings(soup)

    body_text = soup.get_text(" ", strip=True)
    word_count = len(body_text.split())

    schema_blocks = _extract_schema_blocks(soup)

    images = soup.find_all("img")
    images_missing_alt = sum(1 for img in images if not img.get("alt", "").strip())

    internal_links, external_links = _classify_links(soup, url)

    open_graph: dict[str, str] = {}
    for tag in soup.find_all("meta", attrs={"property": re.compile(r"^og:", re.IGNORECASE)}):
        if tag.get("content"):
            open_graph[tag["property"]] = tag["content"]

    has_viewport = soup.find("meta", attrs={"name": re.compile(r"^viewport$", re.IGNORECASE)}) is not None

    author_byline_present = bool(
        soup.find(attrs={"rel": re.compile(r"^author$", re.IGNORECASE)})
        or soup.find(class_=re.compile(r"author", re.IGNORECASE))
        or soup.find(attrs={"itemprop": "author"})
    )

    published_date = None
    modified_date = None
    for block in schema_blocks:
        if "datePublished" in block and not published_date:
            published_date = str(block["datePublished"])
        if "dateModified" in block and not modified_date:
            modified_date = str(block["dateModified"])
    if published_date is None:
        pub_tag = soup.find("meta", attrs={"property": re.compile(r"article:published_time", re.IGNORECASE)})
        if pub_tag and pub_tag.get("content"):
            published_date = pub_tag["content"]

    return ParsedPage(
        url=url,
        title=title,
        meta_description=meta_description,
        canonical=canonical,
        robots_meta=robots_meta,
        h1_count=len(h1_tags),
        headings=headings,
        word_count=word_count,
        schema_blocks=schema_blocks,
        images_total=len(images),
        images_missing_alt=images_missing_alt,
        internal_links=internal_links,
        external_links=external_links,
        open_graph=open_graph,
        has_viewport_meta=has_viewport,
        author_byline_present=author_byline_present,
        published_date=published_date,
        modified_date=modified_date,
        raw_html=html,
    )
