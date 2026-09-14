# SEO/GEO/AEO Engine

Repo: https://github.com/fxinfo24/SEO-GEO-AEO-Engine

A single Python engine that audits a site's visibility across three surfaces
that used to require three different toolchains: traditional search engines
(**SEO**), AI answer engines like ChatGPT/Perplexity/Gemini (**GEO** —
Generative Engine Optimization), and voice/answer assistants (**AEO** —
Answer Engine Optimization). It fetches a page (or crawls a site), scores it
across six weighted dimensions, and returns a number, a prioritized finding
list, and — optionally — a PDF, a saved Supabase record, and a delta against
the last time you ran it.

## The problem this solves

Search visibility fractured. A page can rank #1 on Google and still never
get cited by ChatGPT, because ChatGPT and Google weigh almost entirely
different signals — Wikipedia and Reddit dominate AI citations in a way
neither dominates classic SERPs. Auditing for both used to mean either:

1. **Paying for 3-4 separate SaaS tools** (an SEO crawler, a GEO/AI-visibility
   tracker, a schema validator, a PDF report generator) that don't share
   data, so you can't see how a fix to one score moved the others, and
2. **Re-running the same manual checklist by hand** — or worse, asking an
   LLM to "audit this page" fresh each time, which re-derives a score from
   a written rubric with no guarantee two runs agree, and produces no
   history to compare against.

This engine exists because auditing the 53 separate "SEO/GEO/AEO Claude
skill" files that inspired it found exactly **one** that shipped real,
executable scoring code. Everything else in that space — including tools
that reference PDF-generation scripts that don't actually exist in their own
repo — is prose instructing an LLM to re-read a rubric and eyeball a page
each time. Good rubrics (Ahrefs Dec 2025 citation data, Princeton/Georgia
Tech GEO research, Google's Dec 2025 Quality Rater Guidelines), no
determinism, no persistence, no way to prove a score didn't drift between
runs.

## What it does

**Fetches and scores a page or a whole site** across six dimensions in one
pass:

| Dimension | What it measures | Weight (GEO profile) |
|---|---|---|
| AI Citability | How extractable/quotable each content block is — direct-answer openings, self-containment, sourced stats, structure | 25% |
| Brand Authority | Wikipedia/Wikidata entity existence (real API check) + reachability of declared YouTube/Reddit/LinkedIn/GitHub profiles | 20% |
| Content E-E-A-T | Author bylines, first-person experience language, sourcing, freshness, internal-link authority signals | 20% |
| Technical GEO | Per-crawler robots.txt access for GPTBot, ClaudeBot, PerplexityBot, Google-Extended, and 10 others, tiered by impact + llms.txt presence | 15% |
| Schema | JSON-LD coverage, Organization/Person entity graph, deprecated-type detection | 10% |
| Platform Optimization | Per-platform readiness for Google AI Overviews, ChatGPT, Perplexity, Gemini, Bing Copilot individually | 10% |

Separate weighting profiles exist for `--profile seo` (traditional
technical/on-page) and `--profile aeo` (answer-engine-weighted).

**Renders JavaScript when the site needs it.** React/Vue/SPA marketing
sites deliver an empty `<div id="root">` shell over plain HTTP — a normal
crawler (and most AI crawlers) sees nothing. `--render` runs the page
through headless Chromium first, with the exact same SSRF guard applied to
every sub-resource request the rendered page makes, not just the initial
fetch.

**Crawls a whole site, not just one URL.** `--pages N` does a same-origin
BFS crawl up to N pages and aggregates every dimension across the crawl,
deduplicating repeated findings ("No H1 found — 14 pages") instead of
listing them 14 times.

**Tracks history and outreach, not just point-in-time scores.** Every audit
can be saved to Supabase; `compare` shows the delta since the last run,
per-dimension; `prospects` tracks domains as an outreach pipeline (lead →
qualified → proposal → won/lost) — useful if you're running these audits as
part of client acquisition, not just self-auditing.

**Ships both a markdown report and a client-ready PDF** from the same
scoring run — no separate report-generation pass.

## How this is different from a typical SEO/GEO tool

- **One engine, one data model, six dimensions in one pass** — not three
  subscriptions with three different scoring scales that don't reconcile.
- **Deterministic.** The same page scores the same way twice. No LLM
  re-derives the rubric at request time, so there's no run-to-run drift to
  explain to a client.
- **Open about what it can't measure.** Search ranking position, backlink
  Domain Rating, third-party mention sentiment/volume, and IndexNow
  submission status are flagged explicitly as `unmeasured` in every result's
  raw data rather than being faked with a plausible-looking number. Every
  other tool in this category either charges extra for those (via paid
  third-party APIs) or quietly guesses.
- **Security-first by default, not bolted on.** Every fetch — plain HTTP or
  headless-browser — resolves DNS and rejects loopback/private/link-local/
  reserved ranges before connecting, re-validates after every redirect, and
  applies the identical check to every sub-resource request a rendered page
  makes. None of the source material this was built from had any SSRF
  protection at all.
- **Self-hosted, not a black-box SaaS.** It's your Python and your Supabase
  project — no per-audit metering, no vendor lock-in on the data.

## Setup

```bash
cd seo-geo-aeo-engine
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
playwright install chromium   # only needed for --render
cp .env.example .env          # fill in SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY
```

Run the migration against your Supabase project (SQL editor, or
`supabase db push`):

```bash
supabase/migrations/0001_init.sql
```

> **Supabase status: provisioned.** A dedicated project (`seo-geo-aeo-engine`,
> ref `bdapoclfqymoifdxyeiq`, free tier) is live and the migration above is
> already applied — `prospects`, `audits`, and `audit_findings` exist with
> RLS enabled. `.env` on the dev machine has `SUPABASE_URL` filled in;
> `SUPABASE_SERVICE_ROLE_KEY` still needs to be pasted in manually from
> [Project Settings → API](https://supabase.com/dashboard/project/bdapoclfqymoifdxyeiq/settings/api)
> before `--save`, `compare`, or `prospects` will work — that key is a
> secret and is deliberately never handled by tooling on your behalf.

## Usage

```bash
# One-off single-page audit, prints markdown to stdout
seo-geo-aeo audit https://example.com --profile geo

# React/Vue/SPA site — render through headless Chromium first
seo-geo-aeo audit https://example.com --profile geo --render

# Crawl up to 20 same-origin pages and aggregate scores
seo-geo-aeo audit https://example.com --profile geo --pages 20

# Also enables the Wikipedia/Wikidata check
seo-geo-aeo audit https://example.com --profile geo --brand-name "Example Co"

# Markdown + PDF output
seo-geo-aeo audit https://example.com --profile geo -o report.md --pdf report.pdf

# Site returning a hang/timeout on a WAF-protected domain? Override the UA
# and/or the render wait strategy before assuming the site itself is down
seo-geo-aeo audit https://example.com --profile geo --render \
  --user-agent "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36" \
  --wait-until load

# Save it and track the domain as an outreach prospect (needs Supabase — see above)
seo-geo-aeo audit https://example.com --profile geo --save --prospect

# Compare the two most recent saved audits for a domain
seo-geo-aeo compare example.com

# List prospects
seo-geo-aeo prospects --status lead
```

## Architecture

```
src/seo_geo_aeo/
├── core/
│   ├── fetcher.py         # SSRF-guarded, robots.txt-aware plain HTTP fetcher
│   ├── render_fetcher.py  # Headless-Chromium fetcher, same SSRF guard per sub-request
│   ├── crawler.py         # Same-origin BFS site crawl, page-budgeted
│   ├── parser.py          # HTML -> structured page data + schema.org extraction
│   ├── scoring.py         # Weighted composite scorer, cross-page aggregation
│   └── orchestrator.py    # Wires fetch -> parse -> all six modules -> composite score
├── modules/
│   ├── geo_citability.py       # AI Citability
│   ├── brand_authority.py      # Brand Authority (Wikipedia/Wikidata + sameAs)
│   ├── eeat.py                 # Content E-E-A-T
│   ├── geo_crawlers.py         # Technical GEO (per-crawler robots.txt matrix)
│   ├── geo_schema.py           # Schema
│   ├── platform_optimization.py # Platform Optimization (5 platforms)
│   └── seo_technical.py        # On-page/technical SEO (--profile seo)
├── reporting/
│   ├── markdown_report.py
│   └── pdf_report.py
└── storage/
    └── supabase_client.py      # audits / audit_findings / prospects tables
```

## Honest limitations

- **`brand_authority` and `platform_optimization` score only what's
  programmatically verifiable** — Wikipedia/Wikidata existence (real API
  calls), and reachability of profiles a site self-declares via schema.org
  `sameAs` links. Third-party mention *volume*, *sentiment*, and follower/
  subscriber counts require a live web-search pass; every finding that
  depends on one says so explicitly rather than guessing.
- **Some sites block or hang on the default bot User-Agent without
  returning a clean error.** `SafeFetcher`'s default UA
  (`SEOGeoAeoEngine/1.0`) self-identifies as a bot, which is correct/ethical
  behavior for respecting robots.txt — but WAFs like Wordfence or Sucuri
  frequently respond to *unrecognized* bot signatures by silently hanging
  the connection rather than returning 403. Confirmed in practice: a live
  WordPress site timed out identically on plain HTTP and on `--render`
  under the default UA, while a standard browser UA against the exact same
  URL returned 200 in under 10 seconds both ways. **If a fetch times out on
  a site you can verify is up in a normal browser, try `--user-agent`
  before concluding anything about the site or the engine.**
- **`--render`'s default wait strategy is `load`, not `networkidle`, on
  purpose.** Ad-heavy or analytics-heavy pages often never go fully
  network-idle (polling, retries, chat widgets), which makes `networkidle`
  time out on pages that actually loaded fine — confirmed on the same
  WordPress site above, which has ~10 third-party ad/tracking embeds.
  `load` is the more reliable default; use `--wait-until networkidle` only
  for SPAs you know fetch data asynchronously after the load event, since
  `load` can fire before that data arrives.
- **Bot-protection services (Cloudflare, etc.) can block `--render` the
  same way they'd block a real AI crawler** — this is a different failure
  mode from the WAF/UA issue above, and neither `--user-agent` nor
  `--wait-until` fixes it. If a rendered fetch comes back near-empty on a
  site you *know* has content, check whether the domain's bot-fight-mode is
  fingerprinting headless Chromium and stalling it before the app mounts.
  That's a genuine, reportable finding in its own right (a site can be
  simultaneously well-optimized on-page and invisible to AI crawlers
  because of its own WAF configuration). Confirmed in practice: auditing a
  live SPA product with `--render` showed the DOM permanently stuck on a
  loading spinner with a Cloudflare challenge script injected — the
  headless browser never got past it, under any wait strategy or UA tried.
- **`--render` is slow.** A browser launch plus JS execution costs seconds
  per page, not milliseconds — don't run `--pages 50 --render` against a
  large SPA site without expecting it to take a while.
- **No search-ranking, backlink-authority, or IndexNow signal anywhere.**
  These need paid third-party APIs (Ahrefs/Semrush-class data) that aren't
  wired in; every dimension that would include them documents the gap in
  its `raw["unmeasured"]` field.
