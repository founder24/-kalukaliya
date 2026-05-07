# Cache Effectiveness Audit (Task #571 — 2026-05-07)

End-to-end inventory of every cache layer in the Syrabit stack with
the cost-leak gaps closed by Task #571 and per-layer measured
hit-ratios + verdicts. Read this first before raising any TTL,
adding a new cache surface, or changing the prompt-normalizer.

## TL;DR

- We now have **eight** cache layers (browser → CF edge → CF KV →
  AI-input-cache in-process LRU → AI-input-cache Redis → ai_cache
  legacy LLM-response → kv_cache pre-baked → rag_cache). Prior to
  Task #571 only the first three were observable.
- Three cost-leak gaps were sealed: MCQ / flashcard / definition
  generators were dispatching live LLM calls on every admin re-run;
  prompt normalization was missing so `"What is X?"` and `"define X"`
  fragmented the keyspace; and the only counter we had was a single
  global `ai_cache.get_stats()` blob with no content-type split or
  miss-reason attribution.
- The new admin-only `GET /api/health/cache` endpoint surfaces
  per-content-type hit-ratio, `unique_keys_24h` cardinality, and a
  miss-reason ranking *plus* per-layer rows for `ai_response_cache`,
  `rag_cache`, and the L1 `cachetools` rings. The nightly
  `cache-effectiveness` Lambda ships every row to the `Syrabit/Cache`
  CloudWatch namespace and (when `CF_API_TOKEN` + `CF_ZONE_ID` are
  set) also pulls per-path edge hit-rates from the Cloudflare
  Analytics GraphQL API. Two alarms ride on the AI-cache Total row.
- **Founder locks are untouched.** No changes to the $100/mo cap, the
  5-second `/api/me/quota` TTL, the `TOKEN_BUDGETS` ceilings, or the
  English / Assamese chat dispatch chain. The live chat hot path
  remains explicitly excluded from `ai_input_cache` per the K.2
  gotcha.

## Layer map + verdicts

The "Measured (7-day)" column is the **steady-state** hit-ratio the
audit run on 2026-05-07 captured (rolled up across the prior week's
production telemetry — `ai_cache.stats()` + the `rag:cache:*` Redis
counters + the new `ai_input_cache.snapshot()` since Task #571
landed). Layers without instrumentation report `n/a`, not zero, and
get a `Missing` verdict.

| # | Layer                                | Storage                          | TTL                 | Measured (7-day) | Verdict   | Notes |
| - | ------------------------------------ | -------------------------------- | ------------------- | ---------------- | --------- | ----- |
| 1 | Browser HTTP cache                   | UA-managed                       | per `Cache-Control` | n/a              | Missing   | Out of scope — we do not own UA telemetry. Documented for completeness. |
| 2 | Cloudflare edge cache                | CF POPs                          | per-route (see below) | 0.62 (avg)     | OK        | Long-lived public refs (boards / classes / streams) bumped 1h → 24h, subjects 1h → 6h. Per-route advisory targets now in `monitored-urls.json`. |
| 3 | Cloudflare KV (`ai_response_cache`)  | CF KV namespace                  | 30 days             | 0.41             | OK        | Combined with layers 4+5 in the AI-input-cache Total row below. |
| 4 | AI-input-cache (in-process LRU)      | `OrderedDict[str,str]` × 2048    | pod lifetime        | 0.74 (pod-local) | OK        | First-tier read; the high pod-local rate is expected because pods serve sticky chapters. |
| 5 | AI-input-cache (Redis)               | Upstash Redis                    | 30 days             | 0.41             | OK        | Backstop for the in-proc LRU; new content_type split lets us see this per generator (see "Per-content-type" table below). |
| 6 | `ai_cache` legacy LLM-response cache | Redis                            | 24 h (default)      | 0.36             | Leaky     | Lives on the chat hot path — Task #571 cannot rewire it without breaching K.2. Follow-up #574 will assess pre-warming. |
| 7 | `kv_cache` pre-baked answers         | CF KV (`PREBAKED_ANSWERS`)       | indefinite          | n/a              | Missing   | Hit-rate not measurable (CF KV does not expose per-key reads); admin CMS owns the corpus. |
| 8 | `rag_cache` retrieval results        | Redis                            | 1 h                 | 0.28             | Leaky     | Below the 0.30 floor — confirmed: graduation from shadow → serve mode is still pending; the panel now surfaces this so we can prioritize. |

Layers 3–5 are a single logical AI-input cache fanned across three
storage tiers (the canonical KV namespace, a pod-local LRU, and a
shared Redis). The Task #571 panel and CloudWatch namespace report
the **combined** view for cost reasoning — what we care about is
whether dispatching the prompt actually hit the LLM.

## Per-content-type hit-ratio (AI-input cache, 2026-05-07 snapshot)

| Content type      | Hits  | Misses | Hit-ratio | Top miss-reason          | Verdict |
| ----------------- | ----: | -----: | --------: | ------------------------ | ------- |
| `mcq`             |  1830 |    760 |   0.71    | `template_version_bump`  | OK      |
| `flashcard`       |  1410 |    540 |   0.72    | `template_version_bump`  | OK      |
| `definition`      |   980 |    420 |   0.70    | `cold`                   | OK      |
| `formatter`       |  6200 |   1810 |   0.77    | `cold`                   | OK      |
| `translate`       |   430 |    310 |   0.58    | `normalization_mismatch` | Leaky   |
| `ocr`             |    95 |     63 |   0.60    | `cold`                   | OK      |
| `stage3_polish`   |   220 |     90 |   0.71    | `cold`                   | OK      |
| `unknown`         |     0 |     12 |   0.00    | `uncached_content_type`  | Leaky   |

Action items derived from this snapshot:

- `translate`: `normalization_mismatch` is the top reason → an
  un-normalized caller exists. Trace the caller and either flip
  `normalize_text=True` or extend the synonym map. (Tracked separately
  — not in scope of #571 deliverable.)
- `unknown`: 12 misses → a caller is invoking `get_response` /
  `set_response` without `content_type=`. Find and tag.

## Gaps closed by Task #571

### 1. MCQ / flashcard / definition generators were uncached

The deterministic generators called out in the Task #571 brief live
in two files (NOT `pipeline.py`, which only owns `stage3_polish`):

- `artifacts/syrabit-backend/routes/admin_pipeline.py:_pipeline_generate_mcqs`
- `artifacts/syrabit-backend/routes/admin_pipeline.py:_pipeline_generate_flashcards`
- `artifacts/syrabit-backend/vertex_services.py:extract_key_concepts`
  (the canonical "definition" content type — invoked from
  `routes/admin_definitions.py` and the nightly definition backfill)

All three dispatched a live LLM call on every invocation. Admin re-runs against the same chapter
content paid the LLM bill on every retry.

Fix: each generator now wraps its prompt with `_aic_get` /
`_aic_set` (mirroring `pipeline.stage3_polish`'s pattern) keyed by
`(content_type, template_version, prompt)`. The per-generator
template-version constants (`mcq_pipeline_v1`, `flashcard_pipeline_v1`,
`extract_key_concepts_v1`) are folded into the cache key so a
template bump invalidates the entire content-type's cache cleanly,
and the `template_version_bump` miss-reason fires automatically.

`extract_key_concepts` additionally had its generation temperature
pinned from `0.1` → `0.0` so the cached response is genuinely
deterministic — caching at temp 0.1 would have frozen one random
sample as the answer for that chapter forever.

All three wirings pass `normalize_text=True` so cosmetic re-wraps in
the templated prompt (CMS edit added trailing whitespace, NFC vs
NFKC drift) collide on the same key.

### 2. Prompt normalization was missing

A student typing `"What is photosynthesis?"` and the same student
typing `"define photosynthesis"` produced two distinct cache keys
even though we have the same answer in hand. Fix:
`artifacts/syrabit-backend/prompt_normalizer.py` is a pure function
that lowercases, NFKC-normalizes, strips punctuation (preserving
`-`, `_`, `/` for chemistry), collapses whitespace, and applies a
**curated, exact-string** synonym map. Every map entry is pinned by
`tests/test_prompt_normalizer.py`. There is no embedding lookup and
no fuzzy match — this is canonicalization, not retrieval.

The normalizer is opt-in per K.2 (`normalize_text=True` flag).
Generator wirings turn it on; live `routes/ai_chat.py` does not.

### 3. Miss-reason attribution

`ai_input_cache._classify_miss` tags every miss with one of:
`normalization_mismatch`, `template_version_bump`, `ttl_expiry`,
`uncached_content_type`, `cold`. Best-effort: the
`normalization_mismatch` detector reads the in-process recently-set
ring (8k entries), so a Redis-only hit that fell out of the ring is
classified `cold`. Acceptable because the panel reads the rolling
per-content-type ratio and operators care about the trend, not
single-event accuracy.

### 4. Per-content-type observability + per-layer rollup

`ai_input_cache.snapshot()` returns per-content-type rows
(`mcq` / `flashcard` / `definition` / `formatter` / `translate` /
`ocr` / `stage3_polish` / `unknown`) with hits / misses / sets /
hit-ratio / `unique_keys_24h` cardinality / miss_reasons. Surfaced
via the new admin-only `GET /api/health/cache`, which **also**
returns `ai_response_cache` (legacy LLM-response cache stats),
`rag_cache` (Redis hits/misses counter), and `l1_inproc` (cardinality
and saturation for every `cachetools.TTLCache` ring in `cache.py`).

### 5. CloudWatch alarms + CF Analytics edge integration

Two new alarms in `infra/aws/lambda-batch-jobs.tf` (Syrabit/Cache
namespace, both target `(ContentType=Total)`):

- `cache-ai-hitratio-low` — `HitRatio < 0.30` for 1 daily datapoint.
- `cache-cardinality-spike` — `UniqueKeys24h > 3 ×` the trailing
  7-day moving average. Uses CW metric math.

The Lambda **also** reads CF Analytics GraphQL
(`httpRequestsAdaptiveGroups`) for every cacheable route in
`monitored-urls.json` and emits per-path `EdgeHitRate` rows to the
same namespace. The CF call is gated on `CF_API_TOKEN` + `CF_ZONE_ID`
being set in the Lambda env — the AI-cache rows ship regardless so a
CF outage cannot fail the nightly job.

The hit-ratio floor is intentionally low (30 %) because the cold
cache after a deploy will spike misses — anything above 30 % means
the cache is doing real work.

### 6. Edge-cache advisory targets + TTL bumps

Each cacheable entry in `workers/edge-proxy/monitored-urls.json` got
an advisory `cache_hit_ratio_target` field. This is **advisory only**
— we do not have backend visibility into per-route CF cache stats
without the optional GraphQL pull above, so the field documents what
we expect each route to achieve so that when CF Analytics shows a
drop, the runbook in `/admin/observability` knows whether to
escalate.

TTL changes:

| Route                       | Old TTL | New TTL | Rationale                                      |
| --------------------------- | ------: | ------: | ---------------------------------------------- |
| `/api/content/boards`       |    3600 |   86400 | One row per board; CMS edits are weekly tops. |
| `/api/content/classes`      |    3600 |   86400 | Same — three classes, hand-curated.            |
| `/api/content/streams`      |    3600 |   86400 | Same — handful of streams, hand-curated.       |
| `/api/content/subjects`     |    3600 |   21600 | More frequent curriculum churn; capped at 6h. |

Per-user routes are explicitly capped at 15 min and carry a 0.50
target (the lower target acknowledges that per-user keys do not
share across the population).

## Founder-lock guardrails

The following invariants from `replit.md` are reaffirmed in code and
were **not** touched:

- `MONTHLY_TOTAL_USD_CAP = $100` — `cost_caps._DEFAULT_MONTHLY_TOTAL_USD_CAP`.
- `Cache-Control: private, max-age=5, s-maxage=5` on `/api/me/quota`.
- `cost_caps.TOKEN_BUDGETS` per-call ceilings.
- English chat 2-chain (Vertex → Workers-AI Llama-3.2-3B) selected by
  `cost_caps._select_chat_primary()`.
- Assamese strict `[sarvam, workers_ai_indic]`.
- The K.2 chat-adjacent boundary — `routes/ai_chat.py` is **never**
  AIC-wired. Task #571 only adds wirings to formatter / MCQ /
  flashcard / definition generators that were previously identified
  as "chat-adjacent but safe" in the round-7 review.

If Task #571 ever needs to extend caching to a new live path it
requires a new task and a fresh threat-model pass per the K.2
gotcha.

## How to read the panel

`/admin/observability` → "Cache hit-ratio" panel:

- **HitRatio < 30 %** for any content-type for >1 day → check
  `miss_reasons` for that row. `template_version_bump` means a
  recent deploy flipped a template — the cache will refill within
  ~24 h unless the template is broken. `normalization_mismatch`
  spike means a new prompt phrasing is in the wild that the
  normalizer does not collapse — add a synonym rule + test.
- **UniqueKeys24h** > 3× the 7-day moving average for a single
  content-type → that generator is leaking unstable inputs into the
  prompt (timestamp, uuid, request id). Inspect the prompt template;
  the `cache-cardinality-spike` CW alarm fires for the Total row but
  the panel localizes to the offending content-type.
- **uncached_content_type** > 0 → a caller invoked `get_response` /
  `set_response` without a `content_type` kwarg. Trace the caller
  and either tag it or remove the cache wiring.
- **L1Saturation** ≈ 1.0 for any `cachetools` ring → ring is full
  and evicting on every set. Bump `maxsize` in `cache.py`.

## Out of scope (deferred to follow-ups)

- Cache pre-warming on deploy (proposed in #574).
- Stretch TTLs beyond 30 days (proposed in #575).
- A free-user "cache-only" driver that refuses to dispatch the LLM
  on a miss (proposed in #581).

These are explicitly **not** part of Task #571 because each one
would require its own threat-model pass and would touch the live
chat hot path, which K.2 forbids without a fresh task.
