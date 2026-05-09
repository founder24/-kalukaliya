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
