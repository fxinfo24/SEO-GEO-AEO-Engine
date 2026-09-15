# SEO / GEO / AEO Engine

A deterministic Python engine that audits a URL (or a crawled site) for visibility across
traditional search (**SEO**), AI answer engines (**GEO** — Generative Engine Optimization),
and answer/voice assistants (**AEO**). One fetch, one parse, six scoring dimensions,
markdown + PDF output, optional Supabase-backed history.

Repo: https://github.com/fxinfo24/SEO-GEO-AEO-Engine

---

## Read this first: what this actually is

This is **v0.3 of a working tool with real, verifiable gaps.** It is not a finished
product, it has never been used against a paying client, and parts of it have never
executed successfully even once. Everything below is stated plainly so you can decide
what to trust.

**What genuinely works, verified against live sites:**

- Fetching, parsing, and scoring real pages — plain HTTP and headless Chromium
- The six scoring modules produce real, deterministic numbers from real page data
- Multi-page same-origin crawling with cross-page finding deduplication
- Markdown and PDF report generation, single-profile and combined multi-profile
- SSRF protection on every request, including sub-resources of rendered pages

**What has never been run successfully, not even once:**

- `--save`, `compare`, and `prospects`. The Supabase project exists and the schema is
  migrated, but no service-role key has ever been supplied, so **every line of
  `storage/supabase_client.py` is untested against a real database.** It compiles. That
  is all anyone can currently claim about it.

**What does not exist:**

- **Tests. There are zero.** `tests/` is an empty directory. `pytest` is declared as a
  dev dependency and has never been run. Every "verified" claim in this README comes
  from manual runs against live sites during development, not from a suite you can
  re-run. Treat refactors accordingly.

---

## The scores are not what they look like

This is the single most important thing to understand before showing anyone a number
from this tool.

Each profile declares a set of weighted dimensions. Not all of those dimensions have a
module behind them. `CompositeScorer` renormalizes over whatever actually ran — which
means **a profile can return a confident-looking `/100` while silently measuring only a
fraction of what it claims to measure.**

Actual coverage, machine-verified against the code:

| Profile | Declared weight actually backed by a module | Declared but never produced | Computed then discarded |
|---|---|---|---|
| **GEO** | **100%** | — | — |
| **AEO** | **85%** | `live_citation` (15%) | `platform_optimization` |
| **SEO** | **40%** | `technical_seo` (35%), `content_quality` (25%) | — |

Reproduce it yourself:

```bash
python3 -c "
from seo_geo_aeo.core.scoring import PROFILE_WEIGHTS
from seo_geo_aeo.core.orchestrator import _PAGE_DIMENSIONS, _DOMAIN_DIMENSIONS
for p in ('seo','aeo','geo'):
    declared = set(PROFILE_WEIGHTS[p]); produced = set(_PAGE_DIMENSIONS[p]) | set(_DOMAIN_DIMENSIONS[p])
    print(p, f'{sum(PROFILE_WEIGHTS[p][d] for d in declared & produced):.0%}', sorted(declared - produced))
"
```

**What this means in practice:**

- **GEO is the only profile you should quote as-is.** All six of its dimensions are real.
- **AEO is close** — it's missing live citation testing (actually querying ChatGPT/
  Perplexity to see whether the page gets cited), which is the entire point of the "AEO"
  label. It also computes `platform_optimization` and then throws it away, because that
  dimension isn't in the AEO weight table. That's wasted work and an inconsistency, not
  a design decision.
- **SEO is the weakest and most misleading.** A "66.9/100 SEO score" is really an
  on-page + schema score wearing an SEO label. Core crawlability, indexability, Core Web
  Vitals, and content-quality analysis — 60% of what the profile claims to weigh — do not
  exist as modules. `seo_technical.py` exists and is decent, but it registers under
  `on_page`, not `technical_seo`.

Fixing this means either building the missing modules or honestly rewriting the weight
tables to match reality. Until one of those happens, the SEO number should not go in
front of a client.

---

## Why it exists

Search visibility fractured. A page can rank well on Google and never be cited by
ChatGPT, because the two weigh largely different signals — per Ahrefs' Dec 2025 citation
data, Wikipedia accounts for ~47.9% of ChatGPT's cited domains and Reddit ~46.7% of
Perplexity's, concentrations that have no equivalent in classic SERPs.

This started as a port of `aeo-seo-geo-masterlist`, a collection of 53 SEO/GEO/AEO
"Claude skills." An audit of all 53 found **exactly one** that shipped executable code.
The rest were markdown files instructing an LLM to re-read a rubric and eyeball a page —
including one that referenced a PDF-generation script absent from its own repo. The
rubrics themselves were well-sourced (Ahrefs Dec 2025, Princeton/Georgia Tech GEO
research, Google's Dec 2025 Quality Rater Guidelines). The execution was prose.

So the rubrics got ported into deterministic Python: same page in, same score out, every
time, with history you can diff. That part of the premise holds. The part where all
three profiles are equally complete does not yet — see above.

## What it measures

| Dimension | What it checks | GEO weight |
|---|---|---|
| AI Citability | Per-block extractability: direct-answer openings, self-containment, sourced stats, structure | 25% |
| Brand Authority | Wikipedia/Wikidata entity existence (live API), reachability of schema-declared social profiles | 20% |
| Content E-E-A-T | Author bylines, first-person experience language, sourcing, freshness, internal linking | 20% |
| Technical GEO | robots.txt access for 14 AI crawlers (GPTBot, ClaudeBot, PerplexityBot, Google-Extended…) tiered by impact, plus llms.txt | 15% |
| Schema | JSON-LD coverage, Organization/Person entity graph, deprecated types | 10% |
| Platform Optimization | Per-platform readiness for Google AIO, ChatGPT, Perplexity, Gemini, Bing Copilot | 10% |

## Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
playwright install chromium        # only if you need --render
cp .env.example .env               # only if you want --save/compare/prospects
```

Python 3.12 is what this is developed and run on. **3.14 does not work** — `pydantic-core`
(a Supabase dependency) has no 3.14 wheel and falls back to a Rust source build that
hangs indefinitely. This cost an hour to diagnose; use 3.12.

## Usage

```bash
# Single page, GEO profile (the one to trust)
seo-geo-aeo audit https://example.com --profile geo

# SPA / React site — server HTML is an empty shell without this
seo-geo-aeo audit https://example.com --profile geo --render

# Crawl and aggregate across pages
seo-geo-aeo audit https://example.com --profile geo --pages 20

# Enables the live Wikipedia/Wikidata lookup in brand_authority
seo-geo-aeo audit https://example.com --profile geo --brand-name "Example Co"

# Combined SEO+AEO+GEO PDF: score cards, cross-profile priorities, per-profile detail
seo-geo-aeo report https://example.com --pdf report.pdf

# When a site's WAF hangs the default bot UA and the page is slow
seo-geo-aeo report https://example.com --render --timeout 60 \
  --user-agent "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36" \
  --pdf report.pdf

# Supabase-backed — code path has never successfully executed, see above
seo-geo-aeo audit https://example.com --save --prospect
seo-geo-aeo compare example.com
seo-geo-aeo prospects --status lead
```

## Things that will bite you, learned the hard way

Every item here was hit during development against real sites. None are hypothetical.

**The default User-Agent gets silently hung by some WAFs.** `SEOGeoAeoEngine/1.0`
self-identifies as a bot, which is the ethically correct thing to do. Wordfence-class
WAFs frequently respond to unrecognized bot signatures by hanging the connection rather
than returning 403. On a live WordPress site this produced a ~20s timeout on plain HTTP
*and* on `--render`, while a browser UA against the identical URL returned 200 in 7.5s.
The first diagnosis was "the origin server is slow." That was wrong. **If a fetch times
out on a site you can load in a browser, try `--user-agent` before concluding anything.**

**`networkidle` is a trap on ad-heavy pages.** Playwright's `networkidle` never fires
when ad networks, analytics, and chat widgets keep polling. It was the original default
and it timed out on pages that had loaded fine. The default is now `load`. Use
`--wait-until networkidle` only for SPAs you know fetch data after the load event.

**Render timing genuinely varies run to run.** The same URL with the same flags took
13s once and >30s another time, purely from third-party ad load variance. That's why
`--timeout` exists. A timeout is not proof a site is broken.

**Bot protection can defeat `--render` entirely, and neither flag helps.** On one live
SPA, Cloudflare's challenge script kept the DOM pinned to a loading spinner under every
wait strategy and UA tried. This is worth reporting *as a finding* — if headless Chromium
can't get through, GPTBot and PerplexityBot plausibly can't either, which means the site
is invisible to AI search because of its own WAF config. That's a real diagnosis, not a
tool failure.

**`--render` is slow.** Browser launch plus JS execution is seconds per page. A combined
three-profile `report --render` run on one URL took ~8 minutes, because each profile
re-fetches independently. Don't run `--pages 50 --render` casually.

## What it cannot measure, and does not pretend to

These are absent because they need paid APIs or a live search pass. Every affected
dimension records them in `raw["unmeasured"]` rather than substituting a plausible
number:

- **Search ranking position** — any platform, any query
- **Backlink authority / Domain Rating** — needs Ahrefs/Semrush-class data
- **Third-party mention volume and sentiment** — `brand_authority` checks whether a site
  *declares* a YouTube/Reddit/LinkedIn profile via schema `sameAs` and whether that URL
  resolves. It cannot see whether anyone is actually talking about the brand there. A
  site with three dead social links and zero mentions scores identically to one with an
  active community, as long as the URLs return 200.
- **Follower/subscriber counts**
- **Live AI citation testing** — nothing here queries ChatGPT or Perplexity to check
  whether a page is cited in practice. This is the `live_citation` gap that makes the
  AEO profile 85% rather than complete.
- **Real client-side render performance** — only server response time is captured
- **IndexNow / Bing WMT / Knowledge Panel / Google Business Profile status**

`platform_optimization` deserves a specific caveat: the source rubric's heaviest weights
were ranking position and community discussion volume, neither of which is obtainable
here. What remains is a proxy built from on-page signals and declared social presence. It
is directionally useful and it is not the rubric it was ported from.

## How it differs from commercial tools

Honestly: it's narrower, it's free, it's yours, and it shows its work.

- **Deterministic.** Same page, same score, twice. No LLM re-deriving a rubric per run,
  so no drift to explain away.
- **One data model across three profiles**, so a fix's effect on all three is visible at
  once — rather than three subscriptions with three incompatible scales.
- **Explicit about gaps.** The `unmeasured` lists and the coverage table above are the
  differentiator. Commercial tools in this space either charge for that data or quietly
  paper over its absence.
- **SSRF-guarded by default.** Every fetch resolves DNS and rejects loopback, private,
  link-local, and reserved ranges before connecting; re-validates after redirects; and
  applies the identical check to every sub-resource a rendered page requests. None of the
  53 source skills had any SSRF protection whatsoever.
- **Self-hosted.** Your Python, your Supabase project, no per-audit metering.

What commercial tools have that this doesn't: rank tracking, backlink indexes, real
citation monitoring, test coverage, and a support contract.

## Architecture

```
src/seo_geo_aeo/
├── core/
│   ├── fetcher.py         # SSRF-guarded, robots.txt-aware HTTP fetcher
│   ├── render_fetcher.py  # Headless Chromium; same SSRF guard per sub-request
│   ├── crawler.py         # Same-origin BFS crawl, page-budgeted
│   ├── parser.py          # HTML → structured page data + JSON-LD extraction
│   ├── scoring.py         # Weighted composite scorer, cross-page aggregation
│   └── orchestrator.py    # fetch → parse → modules → composite
├── modules/               # The six scoring dimensions + seo_technical
├── reporting/             # markdown_report, pdf_report (single + comprehensive)
└── storage/               # supabase_client — compiles, never executed live
```

## Status

v0.3. Working tool, honest gaps, no test suite, Supabase path unproven.

Roadmap, in the order that would matter most:

1. **Write tests.** Nothing else should ship first.
2. **Fix the SEO profile** — build `technical_seo` and `content_quality`, or rewrite the
   weight table to stop claiming them.
3. **Run the Supabase path once** with a real key and find out what breaks.
4. Resolve the AEO `platform_optimization` compute-then-discard inconsistency.
5. `live_citation` module, if a reliable way to test AI citation exists.
6. Share one fetch across profiles in `report` instead of re-fetching per profile.

MIT-licensed dependencies; the source skills were Apache-2.0 and MIT.
