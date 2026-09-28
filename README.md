# SEO / GEO / AEO Engine

A deterministic Python engine that audits a URL (or a crawled site) for visibility across
traditional search (**SEO**), AI answer engines (**GEO** — Generative Engine Optimization),
and answer/voice assistants (**AEO**). One fetch, one parse, scoring dimensions per
profile, markdown + PDF output, optional PostgreSQL-backed history (Neon, Supabase, or any Postgres).

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

**Verified against a real database:**

- Storage is plain PostgreSQL via `psycopg` and `DATABASE_URL` — Neon, Supabase, or any
  Postgres. `storage/postgres_store.py` has 100% line coverage from integration tests
  that run against a real PostgreSQL 16 (local Docker; a `postgres:16` service container
  in CI). They cover migrations (idempotent, all-or-nothing), atomic audit-plus-findings
  writes, CHECK constraints, the `updated_at` trigger, JSONB round-trips, and
  SQL-injection-hostile finding text. The atomicity test was mutation-checked: it fails
  if the transaction boundary is removed. `db-migrate`, `--save`, `compare`, and
  `prospects` are exercised end to end through the CLI.
- **Not verified:** a hosted Neon instance. The code is provider-neutral and passes on
  Postgres 16, but nobody has run it against Neon's pooler or scale-to-zero behaviour.

**What exists but is thin:**

- **150 tests, 79% line coverage.** CI runs ruff, mypy, pytest (against a real Postgres), and a
  gitleaks scan of full history — all blocking. Weakest remaining: `crawler.py` 25%,
  `render_fetcher.py` 38% (the SSRF route-decision logic is tested; a real Chromium
  session is not), `ai_citation_likelihood.py` 48%, `pdf_report.py` 48%,
  `orchestrator.py` 66% (the site-audit path is untested), `content_quality.py` 70%.
  There are no fixture pages for the 11 page types `RoadMap.md` Phase 0.2 specifies.

---

## The scores are not what they look like

This is the single most important thing to understand before showing anyone a number
from this tool. It was also the thing most wrong about this file until this pass — an
earlier version of this README claimed AEO was 85% covered and SEO was 40% covered.
Both numbers were correct when written and **stale by the time you'd have read them**,
because a separate work session had already added `technical_seo.py`, `content_quality.py`,
and `live_citation.py` (since renamed `ai_citation_likelihood`) without the README being updated to match. That gap — code moving
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
one dimension in particular (`ai_citation_likelihood`) is real, wired in, and still something you
should not treat as authoritative.

## `ai_citation_likelihood`: read this before trusting the number

Renamed from `live_citation` because that name implied a live check. This dimension does **not** test whether ChatGPT, Perplexity, or any
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

AEO additionally weighs `ai_citation_likelihood` (15%, see caveats above) and drops
`platform_optimization`. SEO uses `technical_seo`, `on_page`, `content_quality`, and
`schema` — the first and third are the modules added in the Sep 20 session and are
**not yet covered by the bug-hunting pass this README documents beyond the two fixes
listed above.** Assume they have not been fully audited.

## Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium        # only if you need --render
cp .env.example .env               # DATABASE_URL for --save/compare/prospects; OPENROUTER_API_KEY for ai_citation_likelihood
seo-geo-aeo db-migrate             # once per database: applies ./migrations
```

Python 3.12 is what this is developed and tested on. **3.14 is untested**: it previously
failed because `pydantic-core` (pulled in by the since-removed Supabase client) had no
3.14 wheel and fell back to a Rust source build that hung. That dependency is gone, but
nobody has re-tried 3.14, so use 3.12. CI is pinned to 3.12.

Run the tests: `pytest -v --cov=seo_geo_aeo --cov-report=term-missing`

The storage tests need a throwaway PostgreSQL and skip without one:

```bash
docker run -d --name pg-test -e POSTGRES_PASSWORD=test -p 55432:5432 postgres:16-alpine
export TEST_DATABASE_URL=postgresql://postgres:test@localhost:55432/postgres
pytest            # each test gets its own schema and drops it afterwards
```

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

# PostgreSQL-backed (set DATABASE_URL; run `seo-geo-aeo db-migrate` once first)
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
- **Genuine live AI citation testing** — see the `ai_citation_likelihood` section above
- **Real client-side render performance** — only server response time is captured
- **IndexNow / Bing WMT / Knowledge Panel / Google Business Profile status**

`platform_optimization` deserves a specific caveat: the source rubric's heaviest weights
were ranking position and community discussion volume, neither of which is obtainable
here. What remains is a proxy built from on-page signals and declared social presence.

## How it differs from commercial tools

Honestly: it's narrower, it's free, it's yours, and it shows its work.

- **Deterministic.** Same page, same score, twice. No LLM re-deriving a rubric per run
  for the six original dimensions — `ai_citation_likelihood` is the one exception, and it's
  labeled as such.
- **One data model across three profiles**, so a fix's effect on all three is visible at
  once — rather than three subscriptions with three incompatible scales.
- **Explicit about gaps**, including its own. The coverage table, the `unmeasured` lists,
  and the "bugs found and fixed" section above are the differentiator. Commercial tools
  in this space either charge for the missing data or quietly paper over its absence —
  and, presumably, don't publish their own bug list.
- **SSRF-guarded by default.** Every fetch resolves DNS and rejects loopback, private,
  link-local, and reserved ranges before connecting; validates every redirect hop
  *before* following it (an unsafe redirect target is never requested); and
  applies the identical check to every sub-resource a rendered page requests. None of the
  53 source skills had any SSRF protection whatsoever.
- **Self-hosted.** Your Python, your Postgres database, no per-audit metering.

What commercial tools have that this doesn't: rank tracking, backlink indexes, real
citation monitoring, mature test coverage, and a support contract.

## Architecture

```
src/seo_geo_aeo/
├── core/
│   ├── fetcher.py         # SSRF-guarded, robots.txt-aware HTTP fetcher
│   ├── render_fetcher.py  # Headless Chromium; same SSRF guard per sub-request (38% coverage)
│   ├── crawler.py         # Same-origin BFS crawl, page-budgeted (25% coverage)
│   ├── parser.py          # HTML → structured page data + JSON-LD extraction (86% coverage)
│   ├── scoring.py         # Weighted composite scorer, measured/unmeasured, aggregation (98% coverage)
│   └── orchestrator.py    # fetch → parse → modules → composite (66% coverage)
├── modules/                # 9 scoring modules; 48%-100% coverage
├── reporting/               # markdown_report (76%), pdf_report (48%)
└── storage/                 # postgres_store — psycopg, atomic writes, migrations runner (100% coverage)
migrations/                  # plain-SQL schema, applied by `db-migrate`
```

## Status

v0.6. Storage moved from the Supabase client to plain PostgreSQL and is integration-tested
against a real database; redirect hops are validated before being followed; `report`
fetches a page once for all profiles; the `on_page`/`technical_seo` naming collision and
the misleading `live_citation` name are resolved. `RoadMap.md` tracks what remains — it is
a multi-session plan, not a checklist one pass clears.

Next, in the order that would matter most:

1. **Run it once against a hosted Neon database** (pooled connection string) and find out
   what a real provider does that a local container doesn't.
2. **Fixture pages for the 11 page types** (`RoadMap.md` Phase 0.2) and `orchestrator`/
   `crawler` tests for the site-audit path.
3. **Share crawled pages across profiles** for `report --pages N` (single-page reports
   already share one fetch; multi-page crawls still crawl once per profile).
4. **Fetch limits** (`RoadMap.md` Phase 10): maximum response size and a total audit
   timeout are not enforced yet; redirect count (10) and per-request timeout are.
5. Decide whether `ai_citation_likelihood`'s LLM-guess approach is worth keeping,
   replacing with a real citation test, or removing.

MIT-licensed dependencies; the source skills were Apache-2.0 and MIT.
