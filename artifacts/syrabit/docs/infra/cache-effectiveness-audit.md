# Cache Effectiveness Audit (Task #571 — 2026-05-07)

End-to-end inventory of every cache layer in the Syrabit stack with the
cost-leak gaps closed by Task #571. Read this first before raising any
TTL, adding a new cache surface, or changing the prompt-normalizer.

## TL;DR

- We now have **eight** cache layers (browser → CF edge → CF KV →
  AI-input-cache in-process LRU → AI-input-cache Redis → ai_cache LLM
  response cache → kv_cache pre-baked answers → rag_cache) and prior
  to Task #571 only the first three were observable.
- Three cost-leak gaps were sealed: MCQ / flashcard / definition
  generators were dispatching live LLM calls on every admin re-run;
  prompt normalization was missing so `"What is X?"` and `"define X"`
  fragmented the keyspace; and the only counter we had was a single
  global `ai_cache.get_stats()` blob with no content-type split or
  miss-reason attribution.
- The new admin-only `GET /api/health/cache` endpoint surfaces
  per-content-type hit-ratio, `unique_keys_24h` cardinality, and a
  miss-reason ranking. The nightly `cache-effectiveness` Lambda ships
  the same numbers to the `Syrabit/Cache` CloudWatch namespace, where
  two alarms ride on the `(ContentType=Total)` dimension:
  `cache-ai-hitratio-low` (HitRatio < 0.30 for 1 day) +
  `cache-cardinality-spike` (UniqueKeys24h > 3× the 7-day MA).
- **Founder locks are untouched.** No changes to the $100/mo cap, the
  5-second `/api/me/quota` TTL, the `TOKEN_BUDGETS` ceilings, or the
  English / Assamese chat dispatch chain. The live chat hot path
  remains explicitly excluded from `ai_input_cache` per the K.2 gotcha.

## Layer map

| # | Layer                                      | Storage                          | TTL              | Keyed by                                  | Observability owner             |
| - | ------------------------------------------ | -------------------------------- | ---------------- | ----------------------------------------- | ------------------------------- |
| 1 | Browser HTTP cache                         | UA-managed                       | per `Cache-Control` | URL + `Vary`                            | n/a (UA-side)                   |
| 2 | Cloudflare edge cache                      | CF POPs                          | `monitored-urls.json` per-route TTL | URL + `Vary`              | CF Analytics + Task #571 advisory |
| 3 | Cloudflare KV (`ai_response_cache`)        | CF KV namespace                  | 30 days          | `ai_response_cache:v1:<model>:<sha256>`   | `/api/health/cache`             |
| 4 | AI-input-cache (in-process LRU)            | `OrderedDict[str,str]` x 2048    | pod lifetime     | same as layer 3                           | `/api/health/cache`             |
| 5 | AI-input-cache (Redis)                     | Upstash Redis                    | 30 days          | same as layer 3                           | `/api/health/cache`             |
| 6 | `ai_cache` legacy LLM-response cache       | Redis                            | 24 h             | `(model, hash(messages))`                 | `/admin/diagnostics` (legacy)   |
| 7 | `kv_cache` pre-baked answers               | CF KV (`PREBAKED_ANSWERS`)       | indefinite       | normalized question text                  | none (admin-CMS-driven)         |
| 8 | `rag_cache` retrieval results              | Redis                            | 1 h              | `(query, subject, top_k)`                 | `/admin/diagnostics`            |

Layers 3-5 are a single logical AI-input cache fanned across three
storage tiers (the canonical KV namespace, a pod-local LRU, and a
shared Redis). The Task #571 panel and CloudWatch namespace report the
**combined** view because that is the cost-relevant metric — what we
care about is whether dispatching the prompt actually hit the LLM.

## Gaps closed by Task #571

### 1. MCQ / flashcard / definition generators were uncached

`artifacts/syrabit-backend/routes/admin_pipeline.py:_pipeline_generate_mcqs` and
`_pipeline_generate_flashcards`, plus
`artifacts/syrabit-backend/vertex_services.py:extract_key_concepts`
(the canonical "definition" content type, since it produces the
`key_terms[].definition` rows that back the topics corpus) all
dispatched a live LLM call on every invocation. Admin re-runs against
the same chapter content paid the LLM bill on every retry.

Fix: each generator now wraps its prompt with `_aic_get` /
`_aic_set` (mirroring `pipeline.stage3_polish`'s pattern) keyed by
`(template_version, prompt)`. The per-generator template-version
constants (`mcq_pipeline_v1`, `flashcard_pipeline_v1`,
`extract_key_concepts_v1`) are folded into the cache key so a
template bump invalidates the entire content-type's cache cleanly,
and the `template_version_bump` miss-reason fires automatically.

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

The normalizer only fires when a caller passes `normalize_text=True`
to `ai_input_cache.get_response` / `set_response`, so it is opt-in
per K.2. Generator wirings (#1) deliberately leave it off because
generator prompts are already templated.

### 3. Miss-reason attribution

`ai_input_cache._classify_miss` tags every miss with one of:
`normalization_mismatch`, `template_version_bump`, `ttl_expiry`,
`uncached_content_type`, `cold`. Cheap — two in-process lookups
against the recently-set ring + the per-content-type
last-template-version table. The miss-reason breakdown is the single
fastest signal when the hit-ratio drops: it tells you whether to
look at the normalizer, the template registry, the TTL, or
something genuinely new.

### 4. Per-content-type observability

Prior state: `ai_cache.get_stats()` returned a single `{hits, misses}`
blob with no content-type split. Operators could see the cache was
"40 % effective" but had no way to ask *for what*.

`ai_input_cache.snapshot()` now returns per-content-type rows
(`mcq` / `flashcard` / `definition` / `formatter` / `translate` /
`ocr` / `stage3_polish` / `unknown`) with hits / misses / sets /
hit-ratio / `unique_keys_24h` cardinality / miss_reasons. Surfaced
via the new admin-only `GET /api/health/cache` and shipped to
CloudWatch by `lambda_batch.cache_effectiveness`.

### 5. CloudWatch alarms

Two new alarms in `infra/aws/lambda-batch-jobs.tf` (Syrabit/Cache
namespace, both target `(ContentType=Total)`, both page
`ops_alerts`):

- `cache-ai-hitratio-low` — `HitRatio < 0.30` for 1 daily datapoint.
- `cache-cardinality-spike` — `UniqueKeys24h > 3 ×` the trailing
  7-day moving average. Uses CW metric math.

The hit-ratio floor is intentionally low (30 %) because the cold
cache after a deploy will spike misses — anything above 30 % means
the cache is doing real work.

### 6. Edge-cache advisory targets

Each cacheable entry in `workers/edge-proxy/monitored-urls.json` got
an advisory `cache_hit_ratio_target` field. This is **advisory only**
— we do not have backend visibility into per-route CF cache stats,
so the field documents what we expect each route to achieve so that
when CF Analytics shows a drop, the runbook in
`/admin/observability` knows whether to escalate. TTLs were patched
where the audit found a mismatch (long-lived public reference data
got bumped to 24 h; per-user routes capped at 15 min).

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
requires a new task and a fresh threat-model pass per the K.2 gotcha.

## How to read the panel

`/admin/observability` → "Cache hit-ratio" panel:

- **HitRatio < 30 %** for any content-type for >1 day → check
  `miss_reasons` for that row. `template_version_bump` means a recent
  deploy flipped a template — the cache will refill within ~24 h
  unless the template is broken. `normalization_mismatch` spike means
  a new prompt phrasing is in the wild that the normalizer does not
  collapse — add a synonym rule + test.
- **UniqueKeys24h** > 3× the 7-day moving average for a single
  content-type → that generator is leaking unstable inputs into the
  prompt (timestamp, uuid, request id). Inspect the prompt template;
  the `cache-cardinality-spike` CW alarm fires for the Total row but
  the panel localizes to the offending content-type.
- **uncached_content_type** > 0 → a caller invoked `get_response` /
  `set_response` without a `content_type` kwarg. Trace the caller
  and either tag it or remove the cache wiring.

## Out of scope (deferred to follow-ups)

- Cache pre-warming on deploy (proposed in #574).
- Stretch TTLs beyond 30 days (proposed in #575).
- A free-user "cache-only" driver that refuses to dispatch the LLM
  on a miss (proposed in #581).

These are explicitly **not** part of Task #571 because each one would
require its own threat-model pass and would touch the live chat hot
path, which K.2 forbids without a fresh task.
