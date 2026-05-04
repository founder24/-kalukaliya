# Performance Roadmap — Task #361

> **Scope:** Layer RAG-result caching, embedding caching, a fast-mode
> 1b vs 3b model A/B, and real-world p99 instrumentation onto the
> #360 baseline. Goal: cut blended chat TTFB p50 to ~400–600 ms
> (cache-hit turns ~50–100 ms), raise effective daily RAG capacity
> to ~12–15k turns/day at the same Workers-AI quota, drop
> fast-mode cost-per-turn ~30–50% if the 1b A/B promotes, tighten
> p99 to ~2.0–2.5 s.
>
> **Companion docs:**
> - `infra/per-cloud-feature-delegation.md` — v3 spec.
> - `infra/provider-priority-map.md` — per-feature provider table.
> - `infra/credit-burn-runbook.md` — flag mechanics (extended in §F
>   for the new cache flags).
> - `infra/capacity-roadmap-363.md` — capacity tier (the cache-shard
>   client below is forward-compat with #363 §3.3).
>
> **Provider removals tracked in #347. v3 spec locked in #359. v3
> dispatch implementation tracked in #360. Capacity tier in #363.**

**Status:** locked spec — 2026-05-04
**Owner:** founder@syrabit.ai

---

## §1 — RAG-result cache

### §1.1 — Key shape and TTL

```
key:    rag:syllabus:<curriculum_version>:query_hash:<sha256>
value:  msgpack({rag_chunks: [...], llm_answer: "..."})
TTL:    86400  # 24 h outer bound
```

`<curriculum_version>` is read from the **single Redis key
`curriculum:version`** (string, monotonic — e.g. `"2026.05"`,
`"2026.06.b"`). Embedding the version in the prefix means a version
bump silently invalidates all prior entries; no scan/delete needed
for correctness, old entries age out via TTL.

`<sha256>` is the digest of the **content-normalized query string**:
lowercased, whitespace-collapsed (`re.sub(r"\s+", " ", q.strip())`),
NFKC-normalized.

### §1.2 — Stable-query gate (populator-side)

The cache is populated **only** when the query is stable:

- No per-user attachments (`request.attachments == []`).
- No ephemeral hints (no `request.hints` set).
- No personalization tokens injected into the prompt
  (no `user_profile.weak_subjects` materially shaping retrieval —
  the bias was a soft re-rank, not a hard filter).
- The active syllabus version equals the version embedded in the
  current RAG chain config.

A query that fails any gate is served live and **not** populated —
this prevents user-specific answers from leaking across sessions.

### §1.3 — Cache-invalidation policy

On a verified syllabus deploy, a one-shot job runs:
1. **Bump** `curriculum:version` (atomic `INCR`-style write or a
   monotonic version string `SET`).
2. **Optional background reclaim** — `SCAN` + `DEL` against the
   previous prefix to reclaim Redis memory faster than the 24 h TTL
   (cosmetic; correctness already holds via prefix change).
3. **Emit** a `cache_invalidated` metric to App Insights so the
   post-deploy hit-rate dip is **expected**, not alarming.

Per-user attachments and ephemeral hints are **never cached**
(enforced by §1.2). A manual flush command for incident response:

```
python3 scripts/perf/flush_rag_cache.py --prefix=rag:syllabus:2026.05:
```

### §1.4 — Cache-shard-aware client (forward-compat with #363 §3.3)

Provision the RAG-cache and embed-cache Redis clients as a
**dedicated cache-shard client**, separate from the
rate-limiter/hot-flag client used by #360 Meters A/B. On the
single-shard Upstash database used at #360 baseline, **both clients
point at the same database** — no behavior change today.

```python
# artifacts/syrabit-backend/clients/redis_clients.py
RATE_LIMITER_REDIS = upstash_url(env="UPSTASH_REDIS_REST_URL")     # Meters A/B, hot-flags
CACHE_REDIS        = upstash_url(env="UPSTASH_REDIS_CACHE_URL"     # RAG + embed cache
                                  default=env("UPSTASH_REDIS_REST_URL"))
```

When #363 §3.3 splits Redis into a multi-shard topology, **only the
cache client is re-pointed** to the dedicated cache shard; the rest
of the codebase is untouched. The dedicated cache shard is the
right place for cache traffic because of strong access locality
(same exam questions repeat across users) — hashing destroys the
cache-hit rate.

### §1.5 — Hit-rate SLO

| Metric | SLO |
|---|---|
| `cache_hit_rate_rag_1h` (rolling) | warning if < 10% **after** §6 promotion to live-serve; sunset after 30 days < 10% |
| `cache_fill_latency_ms_p95` | < 20 ms |
| `cache_serve_latency_ms_p95` (cache-hit turns) | < 100 ms |

---

## §2 — Embedding cache for repeated questions

### §2.1 — Key shape and TTL

```
key:    embed:question:<sha256>
value:  msgpack(vector_1024_float32)   # ~4 KB per entry
TTL:    aligned with curriculum version (24 h outer bound; same
        prefix-bump trick: keys live under embed:question:v<N>:<sha>
        once #363 wants the same "silent invalidation" property)
```

`<sha256>` uses the **same content-normalized hash** as §1.1 so
same-chapter / same-topic repeats hit (lowercased, whitespace-
collapsed, NFKC-normalized).

### §2.2 — Per-turn flow

```
embed_vector = embed_cache.get(hash)
if embed_vector is None:
    embed_vector = await workers_ai.embed("@cf/baai/bge-m3", q)
    fire_and_forget(embed_cache.set(hash, embed_vector, ttl=86400))
# proceed with Pinecone query using embed_vector
```

The cache `set` is fire-and-forget — never block the live turn on a
Redis write.

### §2.3 — Hit-rate SLO

| Metric | SLO |
|---|---|
| `cache_hit_rate_embed_1h` (rolling) | target ≥ 25% (embed cache hit rate is naturally higher than RAG-result because vectors don't depend on syllabus version polish) |
| `embed_cache_serve_latency_ms_p95` | < 5 ms |

---

## §3 — Fast-mode model A/B (`@cf/meta/llama-3.2-1b-instruct` vs `3b-instruct`)

### §3.1 — Routing

A configurable env var `FAST_MODE_AB_1B_TRAFFIC_PCT` (default `0`,
ramped to `10` → `25` → `50` over the experiment window) controls
the fraction of `fast_mode` turns that route to `1b-instruct`.
Routing decision is **hash-stable per session_id** so the same user
sees consistent latency / quality within a session (no flapping
within a turn pair).

```python
def pick_fast_mode_arm(session_id: str) -> str:
    pct = int(os.environ.get("FAST_MODE_AB_1B_TRAFFIC_PCT", "0"))
    bucket = int(hashlib.sha256(session_id.encode()).hexdigest(), 16) % 100
    return "1b" if bucket < pct else "3b"
```

### §3.2 — Per-arm telemetry

Emit to App Insights, dimension = `fast_mode_arm` ∈ {`1b`, `3b`}:

| Metric | Notes |
|---|---|
| `fast_mode_ttfb_ms` | first-token TTFB |
| `fast_mode_full_response_ms` | full response |
| `fast_mode_cost_usd_per_turn` | from CF AI Gateway `cf-aig-cost` header |
| `fast_mode_user_rating_per_turn` | 1–5 thumbs/star from chat UI |
| `fast_mode_followup_within_60s` | bool — did user send another message in same session within 60 s of assistant reply |

### §3.3 — Promotion gate (concrete, not values-debate)

Promote 1b → fast-mode primary **only when all three hold over a
minimum sample of ≥10,000 turns per arm and ≥7 days of traffic
(whichever takes longer)**:

| Quality gate | Statistical rule | Emitted metric |
|---|---|---|
| **User-rating delta ≥ 0** | 95% CI on `(mean_rating_1b − mean_rating_3b)` is **not** below zero (i.e., 1b is statistically not-worse) | `quality_gate_user_rating_delta` |
| **Engagement delta ≥ 0** | Same statistical-not-worse rule on **follow-up-turn rate** (within 60 s, same session) | `quality_gate_engagement_delta` |
| **Cost-per-turn delta < 0** | 1b must actually be cheaper, not just non-worse | `quality_gate_cost_delta` |

The promotion decision is recorded in the runbook (see §F.5 below)
with sample size, window, and confidence intervals at the time of
the call.

**Reject rule.** If either quality metric is **worse** at 95% confidence,
1b is rejected and the experiment closes — **no "let's run it
longer"**. Workers-AI quota for the experiment is finite.

The statistical computation is implemented in
`scripts/perf/quality_gate_calculator.py` (Welch's t-test for the
mean-rating delta; two-proportion z-test for the engagement delta).

### §3.4 — Time-on-page is explicitly NOT used

Mobile and web mix it differently; rating + follow-up are the
canonical proxies.

---

## §4 — Real-world p99 instrumentation

### §4.1 — Metrics to emit

Extend the existing App Insights / CloudWatch SLO emission from
#360 Step 5 to record per-turn TTFB and end-to-end RAG latency at
**p50 / p95 / p99**:

| Metric | Source |
|---|---|
| `chat_ttfb_ms_{p50,p95,p99}` | FastAPI middleware (existing) |
| `chat_e2e_rag_ms_{p50,p95,p99}` | dispatcher span sum |
| `embed_hotpath_ms_{p50,p95,p99}` | bge-m3 client wrapper |
| `pinecone_query_ms_{p50,p95,p99}` | Pinecone client wrapper |
| `mongo_user_profile_load_ms_{p50,p95,p99}` | Mongo client wrapper |
| `mongo_history_load_ms_{p50,p95,p99}` | Mongo client wrapper |
| `output_moderation_ms_{p50,p95,p99}` | moderation dispatcher |

### §4.2 — Outlier session shapes (targeted fixes)

The p99 tail is dominated by three shapes — ship targeted fixes:

| Outlier shape | Fix |
|---|---|
| **Very long histories** | History-budget cap: load at most last `MAX_HISTORY_TURNS=12` turns; older context comes via the rolling `user_profile.summary` (#360 Step 1's off-critical-path summarizer) |
| **Very large RAG chunks** | Chunk re-ordering / re-ranking: drop chunks > `MAX_CHUNK_TOKENS=512` to a re-rank tier; prefer high-score short chunks first |
| **Stuck-LLM tails** | Per-turn timeout `LLM_TURN_TIMEOUT_S=15` triggers a hot-failover to the secondary chain (Azure GPT-4.1-mini); records a `stuck_llm_failover` metric per occurrence |

All three are configurable via env vars listed in §F so they can be
tuned without a deploy.

### §4.3 — Dashboards

Build (or extend existing) dashboards in App Insights:
- **TTFB-by-percentile** time-series (p50/p95/p99 lines).
- **Cache hit-rate** time-series (RAG + embed; warning band).
- **A/B quality gate** panel (per-arm delta CIs).
- **Outlier-fix counters** (history-truncate, chunk-drop,
  stuck-llm-failover).

---

## §5 — Smoke matrix

Each row independently runnable. Budget **2–3 days** of test-author
work to bring the matrix green from a cold start; reuse #360 harness
where possible.

| # | Scenario | Pass criteria |
|---|---|---|
| 1 | Cache hit (post-promotion) | answer returned in < 100 ms; **0 LLM calls**; **0 Pinecone queries** |
| 2 | Cache miss | matches baseline behavior — full pipeline, populator writes cache after success |
| 3 | Cache shadow-write (during 7-day window) | cache populated; live-serve disabled; `cache_hit_rate` metric emitted |
| 4 | Curriculum-version bump | hit rate drops within 5 min; `cache_invalidated` metric fires; no incorrect cache hits afterward |
| 5 | Embed cache hit | embed served in < 5 ms; bge-m3 not called |
| 6 | Embed cache miss | bge-m3 called; cache populated fire-and-forget |
| 7 | A/B routing split | with `FAST_MODE_AB_1B_TRAFFIC_PCT=25`, observed 1b traffic is 25% ± 2% over 1k turns |
| 8 | A/B per-session stability | same `session_id` always lands on same arm |
| 9 | Quality-gate calculator (synthetic) | rejects when 1b worse at 95% CI; promotes when not-worse + cheaper |
| 10 | p99 metric availability | App Insights query for `chat_ttfb_ms_p99` returns ≥ 7 days of data |
| 11 | History truncation | turn 13 of a 50-turn session loads only last 12 + summary; latency unchanged vs baseline |
| 12 | Oversized chunk drop | RAG result with a >512-token chunk routes to re-rank; not injected raw |
| 13 | Stuck-LLM failover | force a 20 s LLM stall; assert failover to secondary chain at 15 s; `stuck_llm_failover` metric increments |
| 14 | Kill-switch | `redis-cli SET cache:rag_enabled 0` propagates within 5 ms; next turn bypasses cache |
| 15 | Auto-disable on staleness | force `cache_invalidated` event with no hit-rate drop; `cache:rag_enabled` auto-flips to 0 within 5 min; alert fires |

---

## §6 — ROI gate + kill-switch

The whole #361 ROI thesis hinges on the RAG/embed cache hitting
**15–35%** in production. **Don't ship-and-pray.** Stage the
rollout:

### §6.1 — 7-day shadow-write window (mandatory first phase)

Run the cache in **shadow-write mode** for the first 7 days post-
deploy:
- Populate the cache.
- Emit `cache_hit_rate` metric per hour.
- **Do NOT serve cache-hit responses to users** — still hit Pinecone
  + LLM, then **compare the live answer to what the cache would
  have returned**.
- This proves the hit rate **without** risking serving stale
  answers.

### §6.2 — Promotion gate to live-serve mode

Promote to live cache-serving **only when all three hold**:

| Gate | Threshold |
|---|---|
| Measured hit rate over last 48 h | ≥ 15% |
| Shadow-served answer matches live answer | ≥ 95% on cache-hit candidates (string-equality after whitespace normalization, OR cosine ≥ 0.95 if answer formatting drifts) |
| Cache-fill latency p95 | ≤ 20 ms (Redis isn't the bottleneck) |

### §6.3 — Kill-switch

```
key:           cache:rag_enabled
type:          Redis hot-flag
read path:     dispatcher reads on every turn (sub-ms)
default:       0 (off, until §6.2 promotion gate clears)
flip command:  redis-cli SET cache:rag_enabled 0
```

On-call can disable cache-serving in **< 1 s** if cache starts
serving wrong answers.

### §6.4 — Auto-disable on staleness signals

If `cache_invalidated` metric fires (from §1.3 invalidation) but
the hit rate **doesn't drop within 5 min**, that means the
version-bump didn't propagate — **auto-flip `cache:rag_enabled=0`**
and alert. False stale-cache reads are a quality regression worse
than the latency win.

Implementation note: a small App Insights alert rule + Logic App
flips the Redis flag (no human in the loop for this specific
failure mode).

### §6.5 — Sunset rule

If after **30 days** of live-serve mode the hit rate is **< 10%**,
the cache is **removed** (not "tuned forever") — the Workers-AI
quota saved doesn't justify the operational complexity. Record
the decision in the runbook either way (§F.5 below).

---

## §A — Updates to `provider-priority-map.md` (perf tier)

No new feature keys required — caching is transparent to the
provider chains. The two new operational concerns surface only as
**hot-flag rows** in `infra/credit-burn-runbook.md` (see §F below)
and as the cache-client config in §1.4.

The post-#363 namespace-shard topology in
`infra/capacity-roadmap-363.md` §A is **forward-compat** with the
cache client introduced here (§1.4) — when sharded, only the
`CACHE_REDIS` URL changes.

---

## §B — Updates to `per-cloud-feature-delegation.md` (perf tier)

Add to §16 (Latency budget & hot-path rules) — **new Rule 13**:

> 13. **Two-stage cache lookup before pipeline.** On every turn:
>     (a) compute the content-normalized query hash; (b) read
>     `cache:rag_enabled` and the RAG-result cache by key; (c) on
>     hit, serve the cached `(rag_chunks, llm_answer)` and skip the
>     entire pipeline (Pinecone query + LLM call); on miss, proceed
>     to the standard concurrent-read path (Rule 2). The embed
>     cache is consulted inside the embed step (between user-message
>     hash and `bge-m3` call). Both caches are populated only by
>     stable queries (no per-user attachments, no ephemeral hints,
>     syllabus version matches). Both lookups are sub-5 ms; their
>     latency cost on miss is negligible compared to the pipeline
>     they short-circuit on hit.

This rule is appended in this commit.

---

## §F — Updates to `credit-burn-runbook.md` (perf tier)

### §F.1 — New hot-flag rows (per-flag operations table)

| flag_name | read_path | write_path | default_value | who_can_flip | propagation_latency_target | rollback_command |
|---|---|---|---|---|---|---|
| `cache:rag_enabled` *(post-#361)* | Redis hot-flag, read every turn | on-call (manual) + App Insights staleness rule (auto-disable) | `0` (off until §6.2 promotion gate clears) | on-call + automation | < 5 ms | `redis-cli DEL cache:rag_enabled` (then `SET 1` to re-enable after fix) |
| `cache:embed_enabled` *(post-#361)* | Redis hot-flag, read every turn | on-call | `1` (lower-risk than RAG cache; only short-circuits embed step) | on-call | < 5 ms | `redis-cli SET cache:embed_enabled 0` |
| `curriculum:version` *(post-#361)* | Redis string, read at process start + on every cache lookup | release engineer (atomic write during syllabus deploy) | `"2026.05"` (current) | release engineer | < 5 ms | `redis-cli SET curriculum:version <previous>` (rolls back to prior cache cohort) |
| `FAST_MODE_AB_1B_TRAFFIC_PCT` *(post-#361)* | env var read at process start (overridable via Redis `chat:fast_mode_ab_pct`) | A/B owner | `0` (ramps `0 → 10 → 25 → 50` over experiment window) | A/B owner | next deploy or sub-ms via Redis | `redis-cli SET chat:fast_mode_ab_pct 0` |
| `MAX_HISTORY_TURNS` *(post-#361)* | env var read at process start (overridable via Redis) | on-call | `12` | on-call | next deploy or sub-ms via Redis | `redis-cli SET chat:max_history_turns 12` |
| `MAX_CHUNK_TOKENS` *(post-#361)* | env var read at process start (overridable via Redis) | on-call | `512` | on-call | next deploy or sub-ms via Redis | `redis-cli SET rag:max_chunk_tokens 512` |
| `LLM_TURN_TIMEOUT_S` *(post-#361)* | env var read at process start (overridable via Redis) | on-call | `15` | on-call | next deploy or sub-ms via Redis | `redis-cli SET chat:llm_turn_timeout_s 15` |

### §F.2 — Cache-shard-aware client config

Two named clients on the runbook:

| client | env var | role | post-#363 destination |
|---|---|---|---|
| `RATE_LIMITER_REDIS` | `UPSTASH_REDIS_REST_URL` | Meters A/B counters, hot-flags from #360 | rate-limiter shard |
| `CACHE_REDIS` | `UPSTASH_REDIS_CACHE_URL` (defaults to rate-limiter URL pre-#363) | RAG + embed cache from #361 | dedicated cache shard (#363 §3.3) |

### §F.3 — A/B promotion-decision log (new table)

Operators record each promotion decision here:

| decision_date | arm_promoted | sample_n_per_arm | window_days | rating_delta_95ci | engagement_delta_95ci | cost_delta_per_turn_usd | decision_owner | notes |
|---|---|---|---|---|---|---|---|---|
| _(populated at first promotion call)_ | | | | | | | | |

### §F.4 — Rate-vs-DAU table for `VALIDATION_SAMPLE_RATE` (already in runbook; restated for cross-link)

| DAU | turns/day | sample_rate | validated turns/day |
|---|---:|---:|---:|
| 100k (post-#360) | 10000 | 0.10 | 1000 |
| 250k | 25000 | 0.04 | 1000 |
| 500k (post-#363) | 50000 | 0.02 | 1000 |
| 1M (post-#363 ceiling) | 100000 | 0.01 | 1000 |

Floor: never below `0.005`.

### §F.5 — Cache-sunset decision log (new table)

If the §6.5 sunset rule fires, record here:

| decision_date | hit_rate_30d_avg | reason | action | decision_owner |
|---|---:|---|---|---|
| _(populated only if cache is sunset)_ | | | | |

---

## §C — Owners + decision parameters

| Decision parameter | Locked value | Owner | Locked-on date |
|---|---|---|---|
| `MAX_HISTORY_TURNS` | 12 | founder@syrabit.ai | 2026-05-04 |
| `MAX_CHUNK_TOKENS` | 512 | founder@syrabit.ai | 2026-05-04 |
| `LLM_TURN_TIMEOUT_S` | 15 | founder@syrabit.ai | 2026-05-04 |
| Cache TTL outer bound | 86400 s (24 h) | founder@syrabit.ai | 2026-05-04 |
| Shadow-write window | 7 days | founder@syrabit.ai | 2026-05-04 |
| A/B minimum sample | 10000 turns/arm AND 7 days | founder@syrabit.ai | 2026-05-04 |
| A/B initial traffic ramp | `0 → 10 → 25 → 50` | founder@syrabit.ai | 2026-05-04 |
| Cache hit-rate sunset threshold | < 10% over 30 days post-promotion | founder@syrabit.ai | 2026-05-04 |
| Cache hit-rate promotion threshold | ≥ 15% over 48 h shadow window | founder@syrabit.ai | 2026-05-04 |
| Shadow-vs-live answer match threshold | ≥ 95% (string-equal post-normalize OR cosine ≥ 0.95) | founder@syrabit.ai | 2026-05-04 |

---

## §D — Out of scope

- Model retraining, fine-tuning, or new model providers (covered by
  #363 capacity follow-on).
- Long-term memory recall depth (covered by #362 features
  follow-on).
- Building the App Insights dashboards themselves (operator-side;
  metric emission is the deliverable).

---

## §E — Cross-references

- Helper scripts:
  - `scripts/perf/quality_gate_calculator.py` (§3.3 statistical
    not-worse check; Welch's t-test + two-proportion z-test).
  - `scripts/perf/flush_rag_cache.py` (§1.3 manual flush for
    incident response).
- Originating tasks: #359 (spec), #360 (implementation), #347
  (provider removals), #363 (capacity, forward-compat hooks).
