# Comprehensive implementation plan for `fxinfo24/SEO-GEO-AEO-Engine`

## Executive recommendation

Build the next version around four principles:

1. **Tests before behavior changes.**
2. **Never present unmeasured data as a real score.**
3. **Keep the audit engine independent of Supabase, Neon, or Redis.**
4. **Separate deterministic page analysis from optional external integrations.**

The best target architecture is:

```text
URL
 │
 ├── Fetch once
 ├── Parse once
 ├── Build shared AuditContext
 │
 ├── SEO scorer
 ├── GEO scorer
 └── AEO scorer
       │
       ├── deterministic local checks
       ├── optional external checks
       └── explicit measured/unmeasured metadata
              │
              ├── Markdown/PDF report
              └── optional PostgreSQL persistence
```

For persistence, use **PostgreSQL through a generic repository interface**. Neon is a suitable hosted PostgreSQL provider. Keep Supabase as an optional adapter only if backward compatibility is needed.

Do **not** add Redis or Upstash yet.

---

# Phase 0 — Establish the current truth

Before changing implementation, create a reproducible baseline.

## 0.1 Verify the repository state

The current README says the repository has zero tests, but the current tree contains tests for:

- `content_quality`
- `technical_seo`
- `live_citation`
- `geo_citability`

This documentation is now stale and should be corrected after running the suite.

Run:

```bash
python3.12 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
pip install -e ".[dev]"

pytest -q
ruff check .
mypy src
```

Record:

- Python version
- Test count
- Failing tests
- Ruff errors
- Mypy errors
- Current profile output for a fixed fixture page
- Current fetch count for a three-profile report

Create a baseline document or CI artifact containing those results.

## 0.2 Freeze representative fixtures

Do not make tests depend on arbitrary live websites.

Create local HTML fixtures for:

- Excellent technical SEO page
- Poor technical SEO page
- Long-form content page
- Thin content page
- JSON-LD organization page
- FAQ page
- Page with broken canonical and robots metadata
- Page with inaccessible social links
- SPA-like empty server response
- Malformed HTML
- Page with missing headings and metadata

Use deterministic `ParsedPage` fixtures wherever possible. Use local HTTP fixtures only for fetcher and integration tests.

## 0.3 Add CI immediately

Add a GitHub Actions workflow for Python 3.12:

```yaml
name: Test

on:
  push:
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - run: python -m pip install --upgrade pip
      - run: pip install -e ".[dev]"
      - run: ruff check .
      - run: mypy src
      - run: pytest -q
```

Keep live integrations out of normal pull-request CI.

---

# Phase 1 — Build the test foundation

This phase must be completed before changing scoring behavior.

## 1.1 Core scoring tests

Test:

- `DimensionScore` rejects scores below `0` and above `100`.
- `rating_for_score()` handles boundary values.
- `CompositeScorer` calculates weighted scores correctly.
- Missing dimensions are handled explicitly.
- Aggregation calculates means correctly.
- Findings are deduplicated correctly.
- Finding severity ordering is stable.
- Profile weight tables are valid.

Example:

```python
def test_profile_weights_sum_to_one() -> None:
    """Every profile should declare a complete weight distribution."""
    from seo_geo_aeo.core.scoring import PROFILE_WEIGHTS

    for profile, weights in PROFILE_WEIGHTS.items():
        assert sum(weights.values()) == pytest.approx(1.0), profile
```

Add a test that detects computed-but-discarded dimensions:

```python
def test_profile_dimensions_match_weight_tables() -> None:
    """Every computed profile dimension should be represented in its weights."""
    from seo_geo_aeo.core.orchestrator import _DOMAIN_DIMENSIONS, _PAGE_DIMENSIONS
    from seo_geo_aeo.core.scoring import PROFILE_WEIGHTS

    for profile, weights in PROFILE_WEIGHTS.items():
        produced = set(_PAGE_DIMENSIONS[profile]) | set(_DOMAIN_DIMENSIONS[profile])
        weighted = set(weights)

        assert weighted <= produced, (
            f"{profile} weights contain dimensions that are never produced: "
            f"{sorted(weighted - produced)}"
        )
```

Also test the opposite direction:

```python
def test_no_profile_dimension_is_computed_and_discarded() -> None:
    """The orchestrator should not compute dimensions absent from the profile weights."""
    from seo_geo_aeo.core.orchestrator import _DOMAIN_DIMENSIONS, _PAGE_DIMENSIONS
    from seo_geo_aeo.core.scoring import PROFILE_WEIGHTS

    for profile, weights in PROFILE_WEIGHTS.items():
        produced = set(_PAGE_DIMENSIONS[profile]) | set(_DOMAIN_DIMENSIONS[profile])

        assert produced <= set(weights), (
            f"{profile} computes unweighted dimensions: "
            f"{sorted(produced - set(weights))}"
        )
```

## 1.2 Module tests

Every scoring module should have tests for:

- Strong input
- Weak input
- Missing fields
- Boundary values
- External failure
- Correct `dimension` name
- Score range
- Findings
- `raw` metadata
- `raw["unmeasured"]`

Modules requiring coverage:

```text
brand_authority
content_quality
eeat
geo_citability
geo_crawlers
geo_schema
live_citation
platform_optimization
seo_technical
technical_seo
```

## 1.3 Orchestrator tests

Mock fetchers and external modules where appropriate.

Verify:

- One-page audit fetches once.
- The selected profile invokes only its intended modules.
- SEO returns all declared dimensions.
- AEO returns all declared dimensions.
- GEO returns all declared dimensions.
- Fetch exceptions are propagated or reported correctly.
- Render mode uses the render fetcher.
- Plain mode uses `SafeFetcher`.
- Domain-level checks run once per audit.
- Brand lookups are not repeated unnecessarily.

## 1.4 Security tests

The fetcher is security-critical. Test:

- `127.0.0.1`
- `localhost`
- private IPv4 ranges
- link-local addresses
- reserved IPv6 ranges
- DNS rebinding behavior
- redirects to private addresses
- non-HTTP schemes
- malformed URLs
- rendered sub-resource requests

The SSRF tests should not rely on the public internet.

## 1.5 Reporting tests

Test Markdown and PDF generation with:

- One profile
- Three profiles
- No findings
- Critical findings
- Missing dimensions
- Unmeasured dimensions
- Very long findings
- HTML-sensitive characters
- Empty or partial result sets

Reports are client-facing and should not claim more coverage than the underlying results provide.

---

# Phase 2 — Make score coverage explicit

The current `CompositeScorer` renormalizes missing dimensions. That can produce a valid-looking score from incomplete data.

For example, if a 15% dimension is unavailable, the remaining dimensions may be normalized to 100%. This is mathematically valid but potentially misleading.

## 2.1 Extend result metadata

Add explicit coverage fields to `CompositeResult`:

```python
@dataclass
class CompositeResult:
    """Weighted profile result with measurement coverage metadata."""

    profile: str
    overall_score: float
    dimension_scores: dict[str, DimensionScore]
    weights: dict[str, float]
    declared_weights: dict[str, float]
    measured_weight: float
    unmeasured_weight: float
    unmeasured_dimensions: list[str]
```

The exact data model can vary, but the result must expose:

- Declared dimensions
- Measured dimensions
- Unmeasured dimensions
- Measured weight
- Unmeasured weight
- Whether the score was normalized

## 2.2 Distinguish unavailable from zero

These cases must not be equivalent:

```text
Score = 0 because the page failed the check
Score unavailable because the check could not run
```

For example:

- No API key: unmeasured
- API timeout: unmeasured
- API returned a valid negative assessment: measured score
- Page lacks a required signal: measured low score

Use a status field where helpful:

```python
raw = {
    "status": "measured",
    "unmeasured": [],
}
```

or:

```python
raw = {
    "status": "unmeasured",
    "unmeasured": ["Live AI citation result"],
    "reason": "API key not configured",
}
```

## 2.3 Update reports

Every report should show something similar to:

```text
SEO score: 74.2/100
Measured coverage: 100% of declared dimensions
```

or:

```text
AEO score: 68.5/100
Measured coverage: 85% of declared dimensions
Unmeasured: live_citation
```

Do not hide this information in debug-only output.

---

# Phase 3 — Resolve the SEO profile

The repository currently has both:

- `technical_seo`
- `content_quality`

and the orchestrator appears to include them in SEO. This is the correct direction, but it needs verification.

## 3.1 Keep the current SEO profile if modules are valid

The current declared SEO profile is:

```python
"seo": {
    "technical_seo": 0.35,
    "on_page": 0.25,
    "content_quality": 0.25,
    "schema": 0.15,
}
```

Keep it only if:

- Both modules produce stable results.
- Their scores are covered by tests.
- Their dimensions are included in the final result.
- Their findings are understandable.
- Their limitations are documented.

## 3.2 Clarify `seo_technical` versus `technical_seo`

The repository has two similarly named modules:

```text
seo_technical.py
technical_seo.py
```

The orchestrator imports:

```python
from seo_geo_aeo.modules.seo_technical import (
    score_technical_seo as score_on_page_factors
)
from seo_geo_aeo.modules.technical_seo import score_technical_seo
```

This is confusing and creates a maintenance risk.

Rename the functions to reflect their real scope:

```text
seo_technical.py
└── score_on_page_seo()

technical_seo.py
└── score_technical_seo()
```

Then update the orchestrator:

```python
scores["on_page"] = score_on_page_seo(...)
scores["technical_seo"] = score_technical_seo(...)
```

Add docstrings explaining the distinction.

## 3.3 Document what technical SEO does not measure

The technical SEO module should distinguish:

Measured:

- Crawlability signals visible from the fetched page
- Robots metadata
- Canonical metadata
- Heading structure
- Response timing
- Mobile viewport metadata
- Link and image properties

Not measured:

- Google Search Console coverage
- Actual index status
- Crawl budget
- Core Web Vitals from real users
- JavaScript execution performance unless rendered
- Search rankings
- Backlinks
- XML sitemap submission status

---

# Phase 4 — Fix AEO consistency

The current code computes `platform_optimization` for AEO but the AEO weight table does not include it.

That must be resolved one way or the other.

## Preferred decision

Because `platform_optimization` is currently a proxy for answer-engine readiness, include it in GEO and AEO only if the product definition explicitly supports that.

Otherwise, make it GEO-only.

The important thing is consistency:

```text
Computed dimension = weighted dimension
```

or:

```text
Not weighted = not computed
```

Do not leave a computed-but-discarded dimension.

## 4.1 Recommended AEO model

AEO should focus on answer and voice discoverability:

```python
"aeo": {
    "ai_citability": 0.20,
    "content_eeat": 0.20,
    "brand_authority": 0.15,
    "technical_geo": 0.15,
    "schema": 0.10,
    "platform_optimization": 0.10,
    "live_citation": 0.10,
}
```

This is only an example. Final weights should be based on the intended rubric and documented rationale.

If `live_citation` remains unavailable in normal execution, the result must say so and expose reduced coverage.

## 4.2 Add a profile contract test

Create a test that fails whenever:

- A declared weighted dimension is not produced.
- A produced dimension is not weighted.
- Weights do not sum to `1.0`.
- A module returns an unexpected dimension name.

This prevents future regressions.

---

# Phase 5 — Reclassify or redesign `live_citation`

The current `live_citation.py` does not perform live citation testing. It sends page content to OpenRouter and asks a model to estimate citation likelihood.

That should not be described as observed live citation.

## 5.1 Recommended immediate action

Rename the concept to:

```text
ai_citation_likelihood
```

or:

```text
model_citation_assessment
```

Then document it as:

```text
A model-generated heuristic assessment of whether content appears suitable
for citation. It does not verify that ChatGPT, Perplexity, Gemini, or another
engine actually cited the URL.
```

## 5.2 Fix the current implementation

The current implementation has several risks:

### Unreliable number extraction

It extracts all numbers from the response and averages numbers between `0` and `100`. This can accidentally treat:

- years
- percentages
- word counts
- model references
- unrelated quantities

as scores.

Prefer structured output:

```json
{
  "score": 72,
  "reasoning": "..."
}
```

Validate the response using a typed model or explicit schema.

### No-key behavior

Do not return `0.0` when the API key is absent. That turns “not measured” into “bad”.

Return an unmeasured result.

### API failure behavior

Do not return a neutral `50.0` unless the product explicitly defines neutral imputation. Prefer:

```text
status = unmeasured
score = absent or excluded from composite
```

### Logging

Replace `print()` with the project logger.

### Sensitive content

The page content is sent to an external provider. Document this clearly and provide:

- An opt-in flag
- A maximum content length
- Redaction or exclusion of sensitive pages
- A provider configuration option
- No API key in logs
- No raw response persistence by default

## 5.3 Genuine live citation testing

Only implement this as a separate integration feature.

A real test requires:

- Fixed query templates
- Defined answer engines
- Stable provider APIs
- URL citation extraction
- URL normalization
- Repeat runs
- Historical storage
- Rate limits
- Cost controls
- Nondeterminism reporting

Report it as observations:

```text
Observed citations: 3/10 queries
Citation rate: 30%
Provider: configured answer-engine API
Queries: 10
Run timestamp: ...
```

Do not describe it as a universal probability.

---

# Phase 6 — Replace Supabase coupling with a storage abstraction

## 6.1 Recommended provider strategy

Use:

```text
PostgreSQL interface
├── Neon adapter
├── Supabase adapter, optional
└── SQLite adapter, useful for local development
```

The engine should not know whether PostgreSQL is hosted by Neon or Supabase.

## 6.2 Use `DATABASE_URL`

Prefer:

```env
DATABASE_URL=postgresql://...
```

over provider-specific variables.

For Neon, use a TLS-enabled connection string:

```env
DATABASE_URL=postgresql://user:password@host/database?sslmode=require
```

Keep secrets in:

- local `.env`, excluded by `.gitignore`
- GitHub Actions secrets
- deployment secret managers

Never commit a service-role key or database password.

## 6.3 Use direct PostgreSQL access

The current Supabase code uses:

```python
from supabase import Client, create_client
```

and Supabase-specific table operations.

For Neon, use `psycopg`:

```toml
dependencies = [
    "psycopg[binary]>=3.2",
]
```

Do not add SQLAlchemy unless the project grows enough to justify it.

## 6.4 Add a repository interface

Define a protocol such as:

```python
class AuditStoreProtocol(Protocol):
    """Persistence interface for audit and prospect data."""

    def save_audit(
        self,
        *,
        domain: str,
        profile: str,
        result: CompositeResult,
        prospect_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist an audit result."""

    def latest_audits_for_domain(
        self,
        domain: str,
        limit: int = 2,
    ) -> list[dict[str, Any]]:
        """Return recent audits for a domain."""

    def upsert_prospect(
        self,
        prospect: ProspectRecord,
    ) -> dict[str, Any]:
        """Create or update a prospect."""

    def list_prospects(
        self,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """List prospects."""
```

The CLI should depend on this interface, not `SupabaseClient`.

## 6.5 Make writes transactional

Saving an audit and its findings must be atomic:

```text
BEGIN
  insert audit
  insert findings
COMMIT
```

If finding insertion fails:

```text
ROLLBACK
```

Do not leave an audit without its associated findings.

## 6.6 Improve stored data

Store coverage metadata as well as scores:

```json
{
  "dimension_scores": {
    "technical_seo": {
      "score": 74.2,
      "raw": {}
    }
  },
  "weights": {},
  "declared_weights": {},
  "measured_weight": 1.0,
  "unmeasured_weight": 0.0,
  "unmeasured_dimensions": []
}
```

This ensures historical comparisons remain interpretable after scoring changes.

## 6.7 Database migration improvements

The existing PostgreSQL migration is mostly portable. Add or verify:

```sql
create extension if not exists pgcrypto;
```

Consider adding:

```sql
create index if not exists idx_audits_domain_profile_created_at
    on audits (domain, profile, created_at desc);
```

Also add constraints for:

- valid domains
- valid profile names
- valid score ranges
- valid prospect states

Use a proper migration tool or a clearly documented migration directory.

---

# Phase 7 — Share one fetch across profiles

The current `cmd_report()` loops over profiles and calls `run_audit()` separately. This causes repeated:

- HTTP fetches
- HTML parsing
- render sessions
- external checks
- potentially expensive browser work

## 7.1 Introduce `AuditContext`

```python
@dataclass
class AuditContext:
    """Shared fetched and parsed data for one audit request."""

    fetch_result: FetchResult
    page: ParsedPage
    fetcher: SafeFetcher
    schema_score: DimensionScore | None = None
    same_as_links: dict[str, str] = field(default_factory=dict)
```

For site audits, use a site context:

```python
@dataclass
class SiteAuditContext:
    """Shared crawl data for multi-profile site audits."""

    crawl_result: CrawlResult
    pages: list[ParsedPage]
    fetcher: SafeFetcher
```

## 7.2 Add multi-profile orchestration

```python
def run_profiles(
    url: str,
    *,
    profiles: Sequence[str],
    fetcher: SafeFetcher | None = None,
    render: bool = False,
    page_type_hint: str = "default",
    brand_name: str | None = None,
) -> dict[str, CompositeResult]:
    """Fetch once and score multiple profiles from shared page data."""
```

The flow should be:

```text
run_profiles()
 ├── fetch once
 ├── parse once
 ├── compute shared schema and sameAs data
 ├── score each profile
 └── return results
```

## 7.3 Test the performance contract

```python
def test_run_profiles_fetches_once(mock_fetcher) -> None:
    """Multiple profiles must reuse one fetched page."""
    results = run_profiles(
        "https://example.com",
        profiles=["seo", "aeo", "geo"],
        fetcher=mock_fetcher,
    )

    assert set(results) == {"seo", "aeo", "geo"}
    assert mock_fetcher.fetch.call_count == 1
```

For rendered reports, also verify that one browser context is reused where safe.

## 7.4 Be careful with external checks

Some checks may still need separate network calls:

- robots.txt
- Wikipedia/Wikidata
- social URL reachability
- OpenRouter
- AI citation provider

“Fetch once” should mean one fetch of the audited page, not that every external check must be artificially collapsed.

Cache shared external checks within an audit context when appropriate.

---

# Phase 8 — Decide whether Redis is needed

## Current answer: no

Do not add Upstash Redis now.

The current application is:

- CLI-driven
- synchronous
- not multi-user
- not a job queue
- not a public API
- not dependent on shared caching

Neon plus the audit engine is enough.

## Add Redis only when a concrete requirement appears

Redis becomes justified for:

### Background jobs

```text
API request → Redis queue → audit worker → PostgreSQL
```

### Distributed rate limiting

Needed when multiple users or workers can trigger:

- crawls
- Playwright sessions
- OpenRouter calls
- external APIs

### Shared short-lived caching

Candidates:

- robots.txt
- safe DNS resolution
- Wikidata lookups
- social reachability
- rendered page results

### Distributed locks

Useful to avoid duplicate audits for the same normalized domain.

### Progress tracking

Useful for a web dashboard showing long-running crawl status.

Until one of those exists, Redis adds operational complexity without improving the core product.

If background jobs are eventually needed, compare Upstash Redis with a durable task queue before committing. Redis is not automatically the best option for long-running crawls.

---

# Phase 9 — Make limitations part of the product contract

The current limitations are appropriate and should be retained in both README and output.

## Explicitly unmeasured

The engine should continue to state that it does not measure:

- Search ranking position
- Backlink authority or Domain Rating
- Third-party mention volume
- Third-party sentiment
- Follower or subscriber counts
- Actual ChatGPT/Perplexity/Gemini citations
- Real client-side performance
- IndexNow submission status
- Bing Webmaster Tools status
- Google Search Console index coverage
- Knowledge Panel status
- Google Business Profile status

## Platform optimization disclaimer

Keep this caveat prominent:

> `platform_optimization` is a proxy derived from on-page signals, schema, declared social profiles, and server response timing. It is not a replacement for ranking position, backlink authority, index coverage, or community discussion volume.

## Report language

Avoid:

```text
Your AEO score is 78/100.
```

Prefer:

```text
AEO score: 78/100
Measured coverage: 85%
Unmeasured: live_citation
Interpretation: directional on-page readiness score, not observed answer-engine citation performance.
```

---

# Phase 10 — Security and privacy hardening

## Secrets

- Never log API keys.
- Never store database passwords in reports.
- Never expose `SUPABASE_SERVICE_ROLE_KEY` or database credentials to clients.
- Use environment variables or secret managers.
- Add secret scanning to CI.

## External AI providers

Before sending page content to OpenRouter or another provider:

- Require explicit opt-in.
- Limit content length.
- Document data handling.
- Avoid sending pages containing personal or confidential information.
- Do not persist raw provider responses by default.
- Use timeouts and retry limits.
- Validate response structure.

## Fetching

Maintain SSRF protections for:

- initial URLs
- redirects
- robots.txt
- social URLs
- rendered sub-resources
- crawler-discovered URLs

Avoid unbounded crawling and enforce:

- maximum pages
- maximum response size
- maximum redirect count
- per-request timeout
- total audit timeout

---

# Phase 11 — Performance improvements after correctness

Only optimize after tests establish behavior.

Priority optimizations:

1. Share one page fetch across profiles.
2. Parse HTML once.
3. Cache derived schema and `sameAs` data.
4. Avoid duplicate external reachability checks.
5. Reuse a browser context for a single rendered report.
6. Add bounded concurrency for independent external checks.
7. Add caching only after measuring repeated work.
8. Add Redis only when multiple workers require shared state.

For I/O-bound checks, asynchronous execution may eventually help, but do not rewrite the entire engine as async prematurely. First isolate external operations behind interfaces that can later support async implementations.

---

# Suggested repository structure

```text
src/seo_geo_aeo/
├── cli.py
├── core/
│   ├── audit_context.py
│   ├── crawler.py
│   ├── fetcher.py
│   ├── orchestrator.py
│   ├── parser.py
│   └── scoring.py
├── modules/
│   ├── brand_authority.py
│   ├── content_quality.py
│   ├── eeat.py
│   ├── geo_citability.py
│   ├── geo_crawlers.py
│   ├── geo_schema.py
│   ├── platform_optimization.py
│   ├── seo_technical.py
│   ├── technical_seo.py
│   └── ai_citation_likelihood.py
├── reporting/
│   ├── markdown_report.py
│   └── pdf_report.py
└── storage/
    ├── protocol.py
    ├── postgres_store.py
    ├── sqlite_store.py
    └── supabase_client.py
```

The `sqlite_store.py` adapter is optional but useful for:

- local development
- tests
- demos
- offline usage
- avoiding Neon credentials during initial setup

---

# Recommended implementation sequence

## Milestone 1 — Test baseline

Deliver:

- CI workflow
- Fixtures
- Core scoring tests
- Module tests
- Fetcher security tests
- Orchestrator tests
- Reporting tests
- Updated README test status

**No scoring behavior changes before this milestone is green.**

## Milestone 2 — Scoring integrity

Deliver:

- Profile consistency tests
- SEO module verification
- `CompositeResult` coverage metadata
- Explicit unmeasured handling
- Report coverage display
- Fixed naming between `seo_technical` and `technical_seo`

## Milestone 3 — AEO/GEO honesty

Deliver:

- Resolve `platform_optimization`
- Decide whether it belongs in AEO
- Rename or clearly reclassify `live_citation`
- Remove zero/neutral fallback scoring for unavailable measurements
- Add API failure and privacy tests

## Milestone 4 — PostgreSQL portability

Deliver:

- Storage protocol
- Neon-compatible PostgreSQL adapter
- Transactional writes
- Migration verification
- Mocked storage tests
- Opt-in Neon integration tests
- Updated CLI configuration

## Milestone 5 — Shared audit context

Deliver:

- One-fetch multi-profile report
- Shared parse and derived data
- Fetch-count regression tests
- Render-path reuse where safe

## Milestone 6 — Optional integrations

Only after the core is stable:

- Genuine live citation provider
- Background audit jobs
- API service
- Redis/Upstash, if required
- Distributed rate limiting
- Shared caching

---

# Definition of done

The repository is ready for serious use when:

- CI passes on Python 3.12.
- All profile dimensions are consistent with their weight tables.
- SEO no longer claims unsupported dimensions.
- Scores expose measured and unmeasured coverage.
- No unavailable external check becomes a fake zero or neutral score.
- Supabase/Neon is accessed through a storage abstraction.
- Database writes are transactional.
- A real Neon integration test passes using a disposable database.
- A three-profile report fetches the target page once.
- SSRF protections have automated tests.
- Reports clearly explain limitations.
- `live_citation` is either a correctly named heuristic or a genuine, separately tested observation system.
- Redis is not introduced without a concrete queue, cache, rate-limit, or coordination requirement.

The strongest near-term configuration is therefore:

```text
Python 3.12
pytest + Ruff + mypy
One shared audit context
PostgreSQL via Neon
No Redis
Optional external AI checks
Explicit measurement coverage
Deterministic local scoring
```

That gives the project a reliable foundation without adding infrastructure before the product actually needs it.

---

# Implementation status

The roadmap above describes the intended end state. This section records where the
repository actually stands against it, verified against the tree rather than assumed.

Most of the "Definition of done" list is met. CI runs pytest, Ruff, and mypy on Python
3.12, plus secret scanning. Scores expose measured and unmeasured coverage, and both
`markdown_report.py` and `pdf_report.py` render the "Measured coverage: X%" line rather
than hiding unmeasured dimensions. The ambiguous `score_technical_seo` name collision is
resolved at the source: `modules/seo_technical.py` exposes `score_on_page_seo()`, distinct
from `modules/technical_seo.score_technical_seo()`, so no orchestrator alias is needed.
The misleading `live_citation` dimension is now `ai_citation_likelihood`. Storage goes
through a provider-neutral abstraction backed by psycopg, with migrations on disk and
transactional writes; a real PostgreSQL integration test runs in CI against a disposable
database. SSRF protections have automated tests, including redirect-to-private-IP
rejection and the DNS-rebinding TOCTOU fix, and the fetcher enforces response-size caps
and a total-audit timeout.

Two gaps remain.

**Phase 0.2 fixtures are not frozen.** The eleven representative page types the roadmap
calls for — excellent and poor technical SEO, long-form, thin, JSON-LD organization, FAQ,
broken canonical/robots, inaccessible social links, SPA-like empty shell, malformed HTML,
and missing headings/metadata — do not exist as shared fixtures. Tests build `ParsedPage`
objects inline instead. This is workable but leaves every module hand-rolling its own
sample page, so the same edge case is expressed differently in a dozen places and no
representative case can be shared across modules.

**Reporting tests are thin.** `tests/reporting/` holds a single file,
`test_coverage_display.py`, with four tests covering the coverage line. The PDF path in
particular has no dedicated test, so layout regressions in `pdf_report.py` would not be
caught by the suite.

Both are additive work that does not disturb the scoring model, and both are cheaper than
anything left in the scoring or fetching layers.

---


## Previous Plan (For record):

The roadmap is sound, but the repository is already partway through it. The current `main` branch contains `technical_seo`, `content_quality`, `live_citation`, and tests for those modules, so the next step should be to verify that these changes are actually integrated correctly rather than treating them as future work.

## Recommended order

### 1. Establish a real test baseline first

The existing tests appear to cover only a few modules:

- [`tests/modules/test_content_quality.py`](https://github.com/fxinfo24/SEO-GEO-AEO-Engine/blob/main/tests/modules/test_content_quality.py)
- [`tests/modules/test_technical_seo.py`](https://github.com/fxinfo24/SEO-GEO-AEO-Engine/blob/main/tests/modules/test_technical_seo.py)
- [`tests/modules/test_live_citation.py`](https://github.com/fxinfo24/SEO-GEO-AEO-Engine/blob/main/tests/modules/test_live_citation.py)
- [`tests/modules/test_geo_citability.py`](https://github.com/fxinfo24/SEO-GEO-AEO-Engine/blob/main/tests/modules/test_geo_citability.py)

Treat this as a minimum smoke-test suite, not complete coverage.

Add tests for:

1. **Scoring**
   - All profile weight tables sum to `1.0`.
   - Every weighted dimension is either produced or explicitly marked unmeasured.
   - Missing dimensions do not silently produce misleading scores.
   - Scores remain between `0` and `100`.
   - Findings are correctly aggregated and deduplicated.

2. **Orchestration**
   - Each profile invokes the expected modules.
   - `technical_seo` and `content_quality` appear in SEO results.
   - `platform_optimization` is either included in AEO scoring or not executed for AEO.
   - Fetch failures are surfaced correctly.
   - Render and non-render paths use the expected fetcher.

3. **Parser and fetcher**
   - Redirect handling.
   - SSRF rejection.
   - Invalid HTML.
   - Missing metadata.
   - Timeouts and HTTP errors.
   - Robots.txt behavior.

4. **Reporting**
   - Markdown and PDF generation.
   - Empty findings.
   - Missing/unmeasured dimensions.
   - Combined SEO/AEO/GEO reports.

5. **Storage**
   - Supabase calls should be mocked in unit tests.
   - Add a separate opt-in integration test for a real Supabase project.

Example scoring invariant:

```python
def test_profile_weights_sum_to_one() -> None:
    """Ensure every profile's declared weights form a complete distribution."""
    from seo_geo_aeo.core.scoring import PROFILE_WEIGHTS

    for profile, weights in PROFILE_WEIGHTS.items():
        assert sum(weights.values()) == pytest.approx(1.0), profile
```

The test suite should run in CI with Python 3.12, since the README explicitly documents Python 3.14 compatibility problems.

Recommended development setup:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

Also add coverage reporting:

```bash
pytest --cov=seo_geo_aeo --cov-report=term-missing
```

The repository should not claim that tests are absent if these tests are now present. Update the README after confirming the actual test count and coverage.

---

### 2. Resolve the SEO profile before changing anything else

The current SEO configuration now declares:

```text
technical_seo: 35%
on_page: 25%
content_quality: 25%
schema: 15%
```

The orchestrator also appears to produce both `technical_seo` and `content_quality`, which is the correct direction. Verify this with an integration test rather than relying on the weight table alone.

The important test is:

```python
def test_seo_produces_all_declared_dimensions(parsed_page) -> None:
    """SEO should produce every dimension declared by its weight table."""
    from seo_geo_aeo.core.orchestrator import _score_page_dimensions
    from seo_geo_aeo.core.scoring import PROFILE_WEIGHTS

    scores = _score_page_dimensions(parsed_page, "seo")

    assert set(PROFILE_WEIGHTS["seo"]).issubset(scores)
```

If the modules are reliable, keep the current SEO weights. Do not rewrite the table merely to hide missing implementation.

If either module is incomplete, the safer temporary option is to make the profile explicit:

- Rename the current score to something like `on_page_seo`.
- Remove unsupported dimensions from the SEO profile.
- Add a visible `unmeasured` entry instead of silently renormalizing.

The current `CompositeScorer` renormalizes missing dimensions. That behavior is useful for partial execution, but dangerous for client-facing scoring because a partial SEO result can still look like a complete `/100` score. Consider adding metadata such as:

```python
@dataclass
class CompositeResult:
    """Weighted result with explicit coverage metadata."""

    profile: str
    overall_score: float
    dimension_scores: dict[str, DimensionScore]
    weights: dict[str, float]
    declared_weights: dict[str, float]
    unmeasured_dimensions: list[str]
```

At minimum, reports should show:

```text
Measured coverage: 100% of declared SEO dimensions
```

rather than only displaying the normalized score.

---

### 3. Test the Supabase path with a real integration test

This cannot be fully validated with mocks. Use a dedicated disposable Supabase project or test schema, never production data.

The integration test should verify:

1. Create/save an audit.
2. Read it back.
3. Create or update a prospect.
4. Run `compare`.
5. Run `prospects`.
6. Confirm behavior for:
   - Invalid URL.
   - Missing record.
   - Duplicate record.
   - Expired or invalid credentials.
   - Network timeout.
   - RLS or permission failure.

Keep this test opt-in:

```bash
RUN_SUPABASE_INTEGRATION=1 pytest -m integration
```

Use environment variables or CI secrets:

```text
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY
```

Never commit the service-role key, include it in test output, or use it from a client-side application.

The README should distinguish clearly between:

- Unit-tested Supabase behavior using mocks.
- Live integration-tested behavior against Supabase.
- Untested CLI workflows.

---

### 4. Fix `platform_optimization` compute-then-discard

This is a concrete bug, not merely a documentation issue.

`_PAGE_DIMENSIONS["aeo"]` includes `platform_optimization`, and `_score_page_dimensions()` computes it. However, `PROFILE_WEIGHTS["aeo"]` does not include it. Consequently, `CompositeScorer.combine()` excludes it from the AEO result.

Choose one of these options:

#### Preferred option: include it in AEO

If platform readiness is intended to be part of AEO, add an explicit weight and reduce other weights accordingly. For example:

```python
"aeo": {
    "live_citation": 0.15,
    "platform_optimization": 0.10,
    "ai_citability": 0.15,
    "brand_authority": 0.15,
    "content_eeat": 0.20,
    "technical_geo": 0.15,
    "schema": 0.10,
}
```

The exact weights should come from the intended rubric, not arbitrary balancing.

#### Alternative: do not compute it for AEO

Remove it from `_PAGE_DIMENSIONS["aeo"]` if platform optimization is intended to be GEO-only.

Either way, add a consistency test:

```python
def test_profile_dimensions_match_weights() -> None:
    """Ensure the orchestrator does not compute discarded dimensions."""
    from seo_geo_aeo.core.orchestrator import _DOMAIN_DIMENSIONS, _PAGE_DIMENSIONS
    from seo_geo_aeo.core.scoring import PROFILE_WEIGHTS

    for profile, weights in PROFILE_WEIGHTS.items():
        produced = set(_PAGE_DIMENSIONS[profile]) | set(_DOMAIN_DIMENSIONS[profile])
        assert set(weights).issubset(produced)
```

Also test the reverse direction so dead computed dimensions are detected.

---

### 5. Reclassify `live_citation`

The current module does not test whether ChatGPT, Perplexity, or another answer engine actually cites the URL. It sends page content to OpenRouter and asks a model to estimate citation likelihood.

That is an **AI-based content suitability assessment**, not live citation measurement.

There are two honest choices:

#### Option A: Keep it as a heuristic

Rename the dimension to something clearer, such as:

```text
ai_citation_likelihood
```

Document that it measures model-estimated citation suitability, not observed citation frequency.

The current implementation also has technical problems:

- It extracts any number from the response and averages them.
- Years, word counts, and unrelated numbers can become scores.
- It uses a free model whose behavior may change.
- It returns `0.0` when no API key exists, which can unfairly penalize the profile.
- It returns `50.0` on API failure, which substitutes a plausible score for missing data.
- `print()` should be replaced with structured logging.
- The raw response length is incorrectly derived from `content_for_prompt`, not the model response.

Use structured JSON output if the provider supports it, and return an unmeasured result when the API is unavailable:

```python
return DimensionScore(
    dimension="ai_citation_likelihood",
    score=0.0,
    findings=findings,
    raw={
        "unmeasured": ["AI model assessment unavailable"],
        "reason": "OPENROUTER_API_KEY is not configured",
    },
)
```

The composite scorer should exclude unmeasured dimensions rather than treating them as zero.

#### Option B: Remove it from AEO

This is preferable until there is a reproducible live-citation methodology.

A genuine live citation test would need:

- A fixed query set.
- A defined set of answer engines.
- Stable API access.
- Captured answer text and citations.
- URL normalization.
- Repeated runs to account for nondeterminism.
- Cost and rate-limit controls.
- Historical result storage.

Even then, the output should be reported as an observation:

```text
Observed citation rate: 2/10 queries
```

not as a universal probability that the page will be cited.

---

### 6. Make measurement limitations first-class output

The README’s limitations section is strong and should be reflected in the machine-readable result, not only in prose.

For each dimension, include:

```python
raw = {
    "measured": [
        "Declared sameAs URL presence",
        "Declared sameAs URL reachability",
    ],
    "unmeasured": [
        "Third-party mention volume",
        "Follower counts",
        "Sentiment",
    ],
}
```

For the overall result, expose:

```python
{
    "profile": "aeo",
    "overall_score": 72.4,
    "measured_weight": 0.85,
    "unmeasured_weight": 0.15,
    "unmeasured_dimensions": ["live_citation"],
}
```

Do not describe a score as “AEO 72.4/100” without also showing the measured coverage.

The limitations you listed are appropriate and should remain explicit:

- Search ranking position.
- Backlink authority.
- Third-party mentions and sentiment.
- Follower/subscriber counts.
- Actual AI citation results.
- Client-side render performance.
- IndexNow and webmaster-tool status.
- Knowledge Panel and Google Business Profile status.

The `platform_optimization` caveat is especially important: it is a local on-page proxy, not a replacement for ranking, indexation, backlinks, or community data.

---

### 7. Share one fetch across profiles

The current comprehensive report likely calls the audit separately for SEO, AEO, and GEO. That causes repeated fetching, parsing, crawling, and possibly browser rendering.

Refactor around a shared audit context:

```python
@dataclass
class AuditContext:
    """Fetched and parsed data reused by multiple profile scorers."""

    fetch_result: FetchResult
    page: ParsedPage
    fetcher: SafeFetcher
    schema_score: DimensionScore | None = None
    same_as_links: dict[str, str] = field(default_factory=dict)
```

Then expose something like:

```python
def score_profiles(
    context: AuditContext,
    profiles: list[str],
    *,
    page_type_hint: str = "default",
    brand_name: str | None = None,
) -> dict[str, CompositeResult]:
    """Score multiple profiles from one fetched and parsed page."""
```

The flow should become:

```text
fetch once
parse once
derive shared values once
score SEO
score AEO
score GEO
render one report
```

For crawls, crawl once and score every requested profile against the same page collection.

Add a test with a fake fetcher:

```python
def test_multi_profile_report_fetches_once(mock_fetcher) -> None:
    """All profiles should reuse one fetch result."""
    results = run_profiles(
        "https://example.com",
        profiles=["seo", "aeo", "geo"],
        fetcher=mock_fetcher,
    )

    assert set(results) == {"seo", "aeo", "geo"}
    assert mock_fetcher.fetch.call_count == 1
```

This should be done after the scoring contract is stable, because it is primarily an orchestration optimization.

## Final priority order

I would implement the roadmap as:

1. **Test and CI foundation**
2. **Profile/weight consistency tests**
3. **Verify and stabilize SEO modules**
4. **Make unmeasured dimensions explicit in scores and reports**
5. **Fix AEO `platform_optimization`**
6. **Run Supabase integration tests with a disposable project**
7. **Rename or remove the misleading `live_citation` heuristic**
8. **Implement shared fetch/context for multi-profile reports**
9. **Only then consider genuine live citation experiments**

The most important product rule is: **never convert unavailable measurement into a plausible score**. A score with reduced coverage is acceptable if the coverage is visible; a full-looking score that silently omits major dimensions is not.