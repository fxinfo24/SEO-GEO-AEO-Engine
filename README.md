# SEO / GEO / AEO Engine

A deterministic Python engine that audits a URL (or a crawled site) for visibility across
traditional search (**SEO**), AI answer engines (**GEO** — Generative Engine Optimization),
and answer/voice assistants (**AEO**). One fetch, one parse, scoring dimensions per
profile, markdown + PDF output, optional Supabase-backed history.

Repo: https://github.com/fxinfo24/SEO-GEO-AEO-Engine

---

## Read this first: what this actually is

This is **v0.4 of a working tool with real, verifiable gaps.** It has never been used
against a paying client. Everything below is stated plainly so you can decide what to
trust — including things found by auditing this project's own code against its own
claims, not just things found on live sites.

**What genuinely works, verified against live sites:**

- Fetching, parsing, and scoring real pages — plain HTTP and headless Chromium
- All scoring modules produce real, deterministic numbers from real page data
- Multi-page same-origin crawling with cross-page finding deduplication
- Markdown and PDF report generation, single-profile and combined multi-profile
- SSRF protection on every request, including sub-resources of rendered pages
- A `measured`/`unmeasured` mechanism: a dimension that couldn't actually run (no API
  key, external call failed) is excluded from the composite score entirely, rather than
  contributing a placeholder number that looks like a real measurement

**What has never been run successfully, not even once:**

- `--save`, `compare`, and `prospects`. The Supabase project exists and the schema is
  migrated, but no service-role key has ever been supplied, so **every line of
  `storage/supabase_client.py` is untested against a real database.** It compiles. That
  is all anyone can currently claim about it.

**What exists but is thin:**

- **39 tests, 33% line coverage.** Real, and a real improvement from zero — but most of
  that coverage sits in a handful of modules (`geo_citability` 93%, `technical_seo` 91%,
  `scoring` 82%, `content_quality` 69%). `cli.py`, `render_fetcher.py`, `pdf_report.py`,
  `storage/`, `eeat.py`, `geo_schema.py`, `brand_authority.py`,
  `platform_optimization.py`, and `seo_technical.py` have **zero** automated test
  coverage. Every "verified" claim about those still means "manually run against a live
  site during development," not "covered by a test you can re-run."

---

## The scores are not what they look like

This is the single most important thing to understand before showing anyone a number
from this tool. It was also the thing most wrong about this file until this pass — an
earlier version of this README claimed AEO was 85% covered and SEO was 40% covered.
Both numbers were correct when written and **stale by the time you'd have read them**,
because a separate work session had already added `technical_seo.py`, `content_quality.py`,
and `live_citation.py` without the README being updated to match. That gap — code moving
faster than its own documentation — is exactly the kind of thing this section exists to
prevent from happening silently again.

Each profile declares a set of weighted dimensions. `CompositeScorer` renormalizes over
whatever actually ran, so a profile can return a confident `/100` while measuring less
than it claims — unless coverage is 100% and nothing gets discarded.

Actual coverage, machine-verified, with an automated test (`tests/core/test_dimension_coverage.py`)
that now fails the build if this table goes stale again:

| Profile | Declared weight backed by a real module | Never produced | Computed then discarded |
|---|---|---|---|
| **GEO** | **100%** | — | — |
| **AEO** | **100%** | — | — |
| **SEO** | **100%** | — | — |

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

**Coverage being 100% is not the same as quality being uniform.** Read the next section —
one dimension in particular (`live_citation`) is real, wired in, and still something you
should not treat as authoritative.

## `live_citation`: read this before trusting the number

Despite the name, this dimension does **not** test whether ChatGPT, Perplexity, or any
other AI system has actually cited the page. It sends page content to an LLM (via
OpenRouter, currently a specific free model) and asks it to *guess* a 0-100
citation-likelihood score. That's a model opinion about the page, not an observation of
real-world citation behavior. A genuine live-citation test would need a fixed query set,
real answer-engine API calls, URL extraction from real responses, and repeated runs to
account for nondeterminism — none of that exists.

It was also, until this pass, silently broken in a specific way: with no
`OPENROUTER_API_KEY` set, it returned a **fake score of `0.0`** that fed directly into
the AEO composite as if "no citation potential" had been measured. If the API call
failed, it returned a **fake `50.0`** "neutral" score, equally fabricated. Both have been
replaced with `measured=False` — the dimension is now excluded from the composite
entirely when it can't actually run, with the reason recorded in
`unmeasured_reason`. This is the general pattern (`DimensionScore.measured`,
`CompositeResult.unmeasured_dimensions`) that should be used everywhere a check might not
be able to run, not just here.

**Privacy note:** page content (title, meta description, up to ~1500 chars of
heading/body text) is sent to a third-party API for every audit that includes this
dimension. There is no opt-out beyond unsetting `OPENROUTER_API_KEY`, and no redaction.
Do not run this against pages containing anything sensitive.

## Bugs found and fixed while implementing this pass

These were real, in the codebase, silently producing wrong numbers. Listed because
knowing what kind of bug this project tends to produce is more useful than a vague
"tests were added" claim.

1. **`technical_seo._score_crawlability` was a no-op.** It accepted a `fetcher` argument
   and never called it — a `try/except` wrapped a bare `pass`, with a comment admitting
   "for simplicity." It always returned a near-perfect score for a robots.txt check that
   never happened. Now it actually calls `fetcher.is_allowed()`.
2. **`content_quality._score_originality` had unreachable dead code.** The "no
   first-person language" finding was nested inside `if first_person_matches > 0:` but
   itself checked `if first_person_matches == 0:` — a contradiction that could never be
   true. The finding could never fire, no matter how third-person the content was. Fixed
   by de-nesting the condition.
3. **`live_citation` returned fabricated scores instead of "unmeasured."** Covered above.
4. **AEO computed `platform_optimization` and threw it away every time** — it ran real
   network calls (schema/sameAs checks) for a dimension not present in
   `PROFILE_WEIGHTS["aeo"]`, so `CompositeScorer.combine()` silently discarded the
   result. Removed from AEO's dimension list rather than added to its weight table, since
   the 10% GEO weight for that dimension was never validated for AEO specifically.

All four are now covered by regression tests that fail if the bug is reintroduced.

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
time, with history you can diff. That part of the premise holds.

## What it measures

GEO profile weights shown; SEO and AEO have their own weight tables in
`core/scoring.py::PROFILE_WEIGHTS`.

| Dimension | What it checks | GEO weight |
|---|---|---|
| AI Citability | Per-block extractability: direct-answer openings, self-containment, sourced stats, structure | 25% |
| Brand Authority | Wikipedia/Wikidata entity existence (live API), reachability of schema-declared social profiles | 20% |
| Content E-E-A-T | Author bylines, first-person experience language, sourcing, freshness, internal linking | 20% |
| Technical GEO | robots.txt access for 14 AI crawlers (GPTBot, ClaudeBot, PerplexityBot, Google-Extended…) tiered by impact, plus llms.txt | 15% |
| Schema | JSON-LD coverage, Organization/Person entity graph, deprecated types | 10% |
| Platform Optimization | Per-platform readiness for Google AIO, ChatGPT, Perplexity, Gemini, Bing Copilot | 10% |

AEO additionally weighs `live_citation` (15%, see caveats above) and drops
`platform_optimization`. SEO uses `technical_seo`, `on_page`, `content_quality`, and
`schema` — the first and third are the modules added in the Sep 20 session and are
**not yet covered by the bug-hunting pass this README documents beyond the two fixes
listed above.** Assume they have not been fully audited.

## Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium        # only if you need --render
cp .env.example .env               # only if you want --save/compare/prospects, or live_citation
```

Python 3.12 is what this is developed and run on. **3.14 does not work** — `pydantic-core`
(a Supabase dependency) has no 3.14 wheel and falls back to a Rust source build that
hangs indefinitely. This cost an hour to diagnose; use 3.12. CI is pinned to 3.12 for
the same reason.

Run the tests: `pytest -v --cov=seo_geo_aeo --cov-report=term-missing`

## Usage

```bash
# Single page
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
re-fetches independently — this is a known inefficiency, not yet fixed (see Status).

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
- **Genuine live AI citation testing** — see the `live_citation` section above
- **Real client-side render performance** — only server response time is captured
- **IndexNow / Bing WMT / Knowledge Panel / Google Business Profile status**

`platform_optimization` deserves a specific caveat: the source rubric's heaviest weights
were ranking position and community discussion volume, neither of which is obtainable
here. What remains is a proxy built from on-page signals and declared social presence.

## How it differs from commercial tools

Honestly: it's narrower, it's free, it's yours, and it shows its work.

- **Deterministic.** Same page, same score, twice. No LLM re-deriving a rubric per run
  for the six original dimensions — `live_citation` is the one exception, and it's
  labeled as such.
- **One data model across three profiles**, so a fix's effect on all three is visible at
  once — rather than three subscriptions with three incompatible scales.
- **Explicit about gaps**, including its own. The coverage table, the `unmeasured` lists,
  and the "bugs found and fixed" section above are the differentiator. Commercial tools
  in this space either charge for the missing data or quietly paper over its absence —
  and, presumably, don't publish their own bug list.
- **SSRF-guarded by default.** Every fetch resolves DNS and rejects loopback, private,
  link-local, and reserved ranges before connecting; re-validates after redirects; and
  applies the identical check to every sub-resource a rendered page requests. None of the
  53 source skills had any SSRF protection whatsoever.
- **Self-hosted.** Your Python, your Supabase project, no per-audit metering.

What commercial tools have that this doesn't: rank tracking, backlink indexes, real
citation monitoring, mature test coverage, and a support contract.

## Architecture

```
src/seo_geo_aeo/
├── core/
│   ├── fetcher.py         # SSRF-guarded, robots.txt-aware HTTP fetcher
│   ├── render_fetcher.py  # Headless Chromium; same SSRF guard per sub-request (0% test coverage)
│   ├── crawler.py         # Same-origin BFS crawl, page-budgeted (25% coverage)
│   ├── parser.py          # HTML → structured page data + JSON-LD extraction (30% coverage)
│   ├── scoring.py         # Weighted composite scorer, measured/unmeasured, aggregation (82% coverage)
│   └── orchestrator.py    # fetch → parse → modules → composite (23% coverage)
├── modules/                # 9 scoring modules; coverage ranges 0%-93%, see Install
├── reporting/               # markdown_report, pdf_report (0% coverage)
└── storage/                 # supabase_client — compiles, never executed live, 0% coverage
```

## Status

v0.4. All three profiles now measure 100% of what they declare; a test suite and CI now
exist; four real scoring bugs were found and fixed while getting here. Still true: no
live Supabase run, thin test coverage outside a handful of modules, and `RoadMap.md` in
this repo has a longer list of architectural work (shared-fetch refactor across
profiles, Postgres/Neon evaluation, security hardening depth, full per-module fixture
suite) that this pass did not attempt — it's a multi-session plan, not a checklist one
pass clears.

Next, in the order that would matter most:

1. **Run the Supabase path once** with a real key and find out what breaks.
2. **Extend test coverage** to the zero-coverage modules listed above, starting with
   `orchestrator.py` and `crawler.py` since they're the load-bearing glue.
3. **Audit `technical_seo.py` and `content_quality.py`** as thoroughly as this pass
   audited the rest — they're new, coverage on them is real but partial, and this pass
   only caught the two bugs it happened to hit, not necessarily all of them.
4. Share one fetch across profiles in `report` instead of re-fetching per profile.
5. Decide whether `live_citation`'s LLM-guess approach is worth keeping, replacing with a
   real citation test, or removing.

MIT-licensed dependencies; the source skills were Apache-2.0 and MIT.
