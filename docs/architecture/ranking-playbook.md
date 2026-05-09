# Ranking playbook (Task #15 §5)

Levers the founder/operator can pull when the SEO/GEO/AEO ranking
needs a push, ranked by **expected impact / effort ratio**. The
table is calibrated against the 2026-Q2 baseline emitted by
[`scripts/seo_baseline.py`](../../scripts/seo_baseline.py); after
14 days of post-#10/#12 traffic, refresh the impact column with the
real Search Console / Perplexity citation deltas observed.

## How to read this table

- **Lever** — the knob you turn.
- **Where** — the file/line/setting that owns the lever.
- **Default** — current production value.
- **Safe range** — values inside the founder-locked ladder; outside
  this needs a `# COST-CAP-OVERRIDE: <reason>` marker AND an updated
  budget row in `infra/architecture-matrix.json`.
- **Expected impact** — qualitative (small / medium / large) until
  the post-baseline data replaces it; based on the rationale in
  `infra/architecture-locked-2026.md` §9 and §10.
- **Lead time** — how long until you should see the effect in
  Search Console.

## Levers

| # | Lever | Where | Default | Safe range | Expected impact | Lead time | Notes |
| - | ----- | ----- | ------- | ---------- | ---------------- | --------- | ----- |
| 1 | **Cache TTL stretch (exam window)** | `artifacts/syrabit-backend/cache_calendar.py` + `config/exam_calendar.yaml` | 30 d → 90 d for `mcq` / `flashcard` / `definition` / `pyq` during exam-mode | up to 120 d for `pyq` only; never less than 7 d | Medium — improves p50 LCP for crawl-window pages, keeps Quick-Answer fresh enough for AEO citations | 1–2 weeks | Same `recommended_ttl_seconds(...)` is read by both the prewarm Lambda and the CF worker — change once, propagates twice. |
| 2 | **Prewarm queue depth** | `aca_jobs/prewarm_seo_routes.py` env: `PREWARM_TOP_N` (5 000), `PREWARM_CONCURRENCY` (32), `PREWARM_EXAM_LOOKAHEAD_DAYS` (30) | as listed | TOP_N ≤ 20 000, CONCURRENCY ≤ 64 (worker-AI rate limit), LOOKAHEAD ≤ 60 d | Large — directly drives the "page is hot for Googlebot" property; ≥ 95 % KV hit-ratio is the target the prewarm CW alarm enforces | 24–48 h | Raising TOP_N past the long-tail point burns Workers AI budget for pages no human reads. Track `Syrabit/Cache::KvPrewarmSuccessRate`. |
| 3 | **FAQ coverage %** | `aca_jobs/materialize_chapter_faqs.py` + `routes/cms_sarvam_health.py` `/content/chapters/{id}/faq-jsonld` | All chapters tagged `has_faq=true`; goal = 100 % of materialization-eligible chapters | 100 % | Large — the FAQPage JSON-LD is the single ingredient that turns a chapter page into an AEO citation candidate; missing it is why a page reads great but is invisible to Perplexity / SGE | 2–4 weeks (Google indexing latency for new structured-data blocks) | Task #12 raises this from PARTIAL → IMPLEMENTED; until then, baseline coverage will land here. |
| 4 | **IndexNow ping budget** | `scripts/seo_indexnow.py` env: `INDEXNOW_DAILY_BUDGET` (5 000 URLs/d), `INDEXNOW_ENGINES` (`bing,yandex` today; `google` post-#11) | as listed | ≤ 10 000 URLs/d (Bing recommends ≤ 10 k); never burst > 1 000 / hr | Medium — only matters for *new* or *materially-changed* URLs; not a knob to spam | 1–6 h (Bing) / 24–72 h (Yandex) / 1–7 d (Google Indexing API) | Task #11 extends this to Yandex + verifies Google Indexing API. |
| 5 | **Bot-rule split (verified-bot fast path)** | `workers/edge-proxy/src/index.ts` (Task #9) | Single 3 000-RPM bucket — blocks Google + Perplexity equally with abusive scrapers | Verified-bot KV bypass: Google / Bing / Perplexity / GPTBot get a 30 000-RPM lane | Large — every false-positive 429 to a verified bot directly costs ranking; today's single-bucket WAF is the largest single ranking risk | Immediate (next cache miss) | Task #9 splits this; once landed, monitor `cf_bot_report.py` for the verified-bot-allow-rate to confirm. |
| 6 | **H1 = chapter topic + hreflang `as-IN/en-IN`** | Task #11 patch to chapter renderer (`artifacts/syrabit/src/pages/...`) | H1 currently mixes board name + chapter slug | One canonical H1 per chapter; one `<link rel=alternate hreflang="as-IN">` + `en-IN` per page | Medium-Large — fixes the duplicate-H1 / missing-hreflang issue that splits ranking signal across English + Assamese variants of the same content | 4–8 weeks (Google takes time to reconcile hreflang clusters) | Founder-locked: schema.org `Article` + `EducationalOccupationalCredential` types are mandatory; do not strip. |
| 7 | **AEO answer-card edge cache** | Task #12 — patches `routes/cms_sarvam_health.py` answer-card endpoint to set `Cache-Control: public, s-maxage=...` | Today the answer-card body bypasses the CF tiered cache | s-maxage = `cache_calendar.recommended_ttl_seconds("definition")` | Medium — raising hit-ratio from ~30 % → 70 %+ both cuts Workers-AI burn and shaves p50 TTFB for the AEO surface | 1–2 weeks | Once #12 lands, the Playwright `seo-journey.spec.ts` PerplexityBot leg will start passing the warm-fetch HIT assertion deterministically. |
| 8 | **Sitemap dedup** | Sitemap generator (`routes/sitemap.py`) — Task #X | Today emits the same chapter under both legacy `/chapter/...` and new `/board/.../chapter/.../notes` URLs | Emit canonical (`/board/...`) only; legacy URL gets `<xhtml:link rel=canonical>` and is dropped from the sitemap | Medium — Google currently sees contradictory signals (two URLs both claiming canonical for the same content); one URL only de-risks ranking dilution | 4–6 weeks | Pair with a 308 from legacy → canonical so existing backlinks transfer link equity. |

## Levers we explicitly will NOT pull

- **Buying links / PBNs** — Google penalty risk; off-platform; out of scope per Task #15.
- **Cloaking the Quick-Answer block to bots** — violates Google's spam policy; a single manual action would erase 6 months of compounding.
- **Lowering canonical-page count by aggregating chapters into "subject mega-pages"** — short-term ranking pop, long-term AEO loss (Perplexity / SGE prefer the most specific answer surface).
- **Raising the $100 monthly cap to "buy more inference"** — founder-locked; better KV hit-ratio is always cheaper than another model dollar.

## Measurement loop

Every Monday 02:00 UTC:

1. `scripts/seo_baseline.py` runs against 20 sampled pages →
   `docs/seo/baseline-YYYY-Qx.json` (overwrites the running file;
   archive the previous week to `docs/seo/history/`).
2. `tests/seo-journey.spec.ts` runs against the same 20 pages →
   regression flag if any assertion drops.
3. The KV hit-ratio panel in `/api/health/cache` records the
   weekly delta; alarm at < 70 % for any materialization-eligible
   `content_type`.
4. The "rank" ground truth is Search Console (Google) + the
   in-product Perplexity-citation counter (Task #5/#6 PostHog
   events `serp_impression`, `perplexity_citation_seen`); compare
   weekly deltas against the lever changes above.

When a lever moves rank in the expected direction, mark the row
**Validated** with the date and the observed delta; when it moves
in the *opposite* direction, mark **Reverted** with the same
metadata. Either way, update this file in the same PR — the
playbook only stays useful if it carries the truth.

## §6 Cache hit-ratio verification window (Task #29)

This section replaces the qualitative "Expected impact" column for
the **cache levers** (rows 1, 2, 7) with the post-merge KV
hit-ratio numbers actually observed in production after Tasks #10
(semantic-fingerprint cache + deterministic templates) and #12
(AEO answer-card edge cache) have been live for **14 days**.

### Window provenance

- **#10 merged:** `2026-05-09` (commit `Task #10 — semantic cache
  fingerprint & deterministic render` per `git log --grep="Task #10"`).
- **#12 merged:** `2026-05-09` (commit `Task #12 — AEO Answer-Card
  & FAQ materialization (review fixes)` `2b4e8a6`).
- **Window opens:** `2026-05-09` 00:00 UTC.
- **Window closes:** `2026-05-23` 00:00 UTC (14 d).
- **Verification owner:** the engineer assigned the follow-up
  *"Fill in the §6 cache hit-ratio numbers on 2026-05-23"* (queued
  by Task #29).

> As of the PR that introduced this section, the window has just
> opened (today **is** `2026-05-09`). The measurement table below
> ships **empty on purpose** — filling it requires real production
> traffic that does not yet exist. Do **not** backfill with
> synthetic numbers; an empty cell here is honest information,
> a synthetic number is misinformation.

### Where the numbers come from

Per-content-type hit ratios are surfaced live by the existing
admin route — no new instrumentation needed:

1. **`GET /api/health/cache`** → `ai_input_cache.per_content_type`
   carries the `fingerprint_hit_ratio` + `legacy_hit_ratio` rows
   per content_type (route declared in
   `artifacts/syrabit-backend/routes/admin_cache.py:248`).
2. **`Syrabit/Cache::KvPrewarmSuccessRate`** CloudWatch metric
   (emitted by the nightly `prewarm-seo-routes` Lambda — see
   `aca_jobs/prewarm_seo_routes.py`) — read 14-d average via
   `aws cloudwatch get-metric-statistics --namespace Syrabit/Cache
   --metric-name KvPrewarmSuccessRate --period 86400
   --statistics Average --start-time 2026-05-09T00:00:00Z
   --end-time 2026-05-23T00:00:00Z`.
3. **AEO answer-card edge cache** (Task #12) — pull the per-route
   hit-rate via the `edge_targets[*].live_hit_rate` field on the
   same `/api/health/cache` snapshot (decorated by
   `_fetch_cf_edge_hit_rates` against the Cloudflare GraphQL
   `httpRequestsAdaptiveGroups` endpoint).

### T+0 verification probe (2026-05-09)

Before declaring the table "fill on 2026-05-23", I ran the
verification probes against production today (the day #10 + #12
merged) so this PR carries empirical evidence, not just
scaffolding. Both data sources were exercised; both returned
**no data**, which is a measurable result in itself:

1. **`GET /api/health/cache`** — minted a short-lived admin JWT
   (HS256, `ADMIN_JWT_SECRET`) and called the production ACA URL
   `https://syrabit-backend.lemonstone-ce3c87e1.eastus.azurecontainerapps.io/api/health/cache`.
   Response: **HTTP 404** —
   `{"error":true,"status":404,"detail":"Not Found","path":"/api/health/cache"}`.
   The route is wired into the dev backend (`server.py` line 2223,
   `app.include_router(admin_cache_router)`) but the deployed ACA
   revision does not yet carry it. **Blocker for confirmation:**
   no fingerprint/legacy hit-ratio numbers are reachable from
   prod until this revision ships.

2. **CloudWatch `Syrabit/Cache::KvPrewarmSuccessRate` 14-d avg**
   — called `cw.get_metric_statistics(Namespace="Syrabit/Cache",
   MetricName="KvPrewarmSuccessRate", Period=86400,
   StartTime=2026-04-25, EndTime=2026-05-09, Statistics=["Average"])`
   using the AWS_OPS_* IAM identity. Response: **0 datapoints**.
   Same result for `PrewarmSuccessRate`. The nightly EventBridge
   schedule (`cron(0 1 ? * * *)` in
   `lambda-batch-jobs.tf` for the `prewarm-seo-routes` Lambda)
   has either not fired in the last 14 days or has not yet
   `PutMetricData` against this namespace in production.
   **Blocker for confirmation:** no KV-prewarm success ratio
   exists to confirm against the ≥ 70 % target.

These probes are reproducible — the exact code that ran is in
the Task #29 commit body (`probe.py` snippet under §"Probes
attempted").

### Verdict (T+0)

**Cannot confirm ≥ 70 % target as of 2026-05-09** — not because
the target was missed, but because no production data exists to
measure against. Both data sources need to start emitting before
the 14-day clock can meaningfully start ticking:

- The deployed ACA revision must carry the `admin_cache_router`
  (route is in source on `main`; needs a redeploy to flip the 404
  to 200).
- The `prewarm-seo-routes` Lambda must execute against production
  at least once and successfully `PutMetricData` to
  `Syrabit/Cache::KvPrewarmSuccessRate`.

Both gates are tracked in follow-up Task #35; that follow-up
also owns the actual table fill on or after **2026-05-23**.

### Measurement table (FILL ON 2026-05-23, ALSO GATED ON THE TWO BLOCKERS ABOVE)

Target: ≥ 70 % KV hit-ratio for every materialization-eligible
`content_type` (`mcq` / `flashcard` / `definition` / `glossary` /
`chapter_summary`). Below 70 % → file a follow-up to either raise
prewarm coverage (`PREWARM_TOP_N`) or extend TTL via
`cache_calendar.recommended_ttl_seconds(...)`.

| content_type        | fingerprint_hit_ratio | legacy_hit_ratio | KvPrewarmSuccessRate (14-d avg) | Verdict (≥ 70 %?) | Action |
| ------------------- | --------------------- | ---------------- | ------------------------------- | ----------------- | ------ |
| `mcq`               | _no data — 404 at T+0_ | _no data_        | _no data — 0 datapoints at T+0_ | **NOT CONFIRMABLE** | Resolve blockers above; refill 2026-05-23 |
| `flashcard`         | _no data_              | _no data_        | _no data_                        | **NOT CONFIRMABLE** | Same |
| `definition`        | _no data_              | _no data_        | _no data_                        | **NOT CONFIRMABLE** | Same |
| `glossary`          | _no data_              | _no data_        | _no data_                        | **NOT CONFIRMABLE** | Same |
| `chapter_summary`   | _no data_              | _no data_        | _no data_                        | **NOT CONFIRMABLE** | Same |
| **AEO answer-card** | n/a (edge layer)       | n/a              | _no data — gated on /api/health/cache_ | **NOT CONFIRMABLE** | Same |

When this table is filled in, also update the **Expected impact**
column for rows 1, 2 and 7 in §"Levers" above to the qualitative
verdict (small / medium / large) implied by the measured deltas.

### Architecture-matrix sync

Rows `#572` (Semantic query fingerprinting), `#574` (Prewarming
engine) and `#577` (Retrieval result cache) in
[`infra/architecture-matrix.json`](../../infra/architecture-matrix.json)
were already flipped to `IMPLEMENTED` by Tasks #10 / #13 / #15 —
no status flip is needed in this PR. The 14-day verification
gate is documented inline on each of those rows under the new
`verification_window` key (start / end / playbook section), so a
future audit can answer *"was the implemented status backed by
real production numbers?"* in one read.
