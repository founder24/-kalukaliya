# Capacity Roadmap — Task #363 (HISTORICAL — v3 era)

> ⚠️ **SUPERSEDED — 2026-05-05.** Source of truth is
> [`infra/v4-locked-architecture.md`](v4-locked-architecture.md).
> Capacity-tier shapes (Mongo sharding, Redis multi-shard, Pinecone
> scale-out) below remain useful as the 500k–1M DAU plan, but Pinecone
> is now in `aws-ap-south-1`, embedding has namespace separation, and
> Vertex chat is co-primary for long/high-risk turns (V4 §1 / §3 / §4).
> Do NOT cite this doc in new PRs without also citing V4.

# Capacity Roadmap — Task #363

> **Scope:** Lift Syrabit's serving ceiling from the post-#360 baseline
> (~100k DAU comfortably; ~250–400k DAU peak) to **500k–1M DAU** via
> Workers-AI quota tier upgrade, Mongo sharded cluster, multi-shard
> Redis, Pinecone scale-out, Vertex Gemini co-primary, async-batch
> account isolation, and a load-test harness sized to the new ceiling.
>
> **This task ships only when actual production traffic approaches the
> ceilings established in #360.** Premature execution wastes credits.
>
> **Companion docs:**
> - `infra/per-cloud-feature-delegation.md` — v3 spec (authoritative).
> - `infra/provider-priority-map.md` — per-feature provider table
>   (extended in §A below for sharded topology).
> - `infra/credit-burn-runbook.md` — credit windows + meter thresholds
>   (extended in §B below for the upgraded tier costs).
>
> **Provider removals tracked in #347. v3 spec locked in #359. v3
> dispatch implementation tracked in #360.**

**Status:** locked spec — 2026-05-04
**Owner:** founder@syrabit.ai

---

## §1 — Workers-AI quota tier upgrade

**Sizing input.** Project the 6-month DAU curve from current production
telemetry. Compute peak RPM at the 95th-percentile minute of the busiest
day. Multiply by **1.5× safety margin**. The next paid tier must clear
that headline RPM with **≥30% headroom** (so Meter B's 70% trip threshold
has room to breathe).

**Cost step-up (order-of-magnitude, refresh against Cloudflare's
published pricing at execution time).** Current Workers-AI Paid tier:
~$5/mo base + per-call usage. Next tier (Workers-AI Enterprise /
committed-spend): typically **$500–2,000/mo committed spend** depending
on negotiated rate and call volume. At ~500k DAU with ~100k RAG
calls/day, expected gross spend = **~$2,000–4,000/mo** (matches the cost
projection in #360 at the 500k DAU line). At ~1M DAU = ~$3,500–7,000/mo
gross.

**Credit interaction.** The Cloudflare for Startups credit (#359
all-provider credit policy) covers the upgrade for the credit window's
remaining months — **recalculate the Cloudflare row's
`expected_exhaustion_date` and `migration_checkpoint_date` in the
runbook's per-provider credit-window table when the tier flips**.
**Pre-flip gate:** confirm with the Cloudflare account team that the
Startups credit applies to the higher tier (it usually does, but
committed-spend deals sometimes negotiate this separately).

**Wiring.**
- Update `WORKERS_AI_RPM_LIMIT` env var on **both** ACA apps in the same
  deploy as the tier flip (the deploy-time CI guard from #360 Step 7b
  fails if the env-var value is stale relative to the active model).
- Re-baseline **Meter A** daily threshold (e.g., 10k → 100k).
- Re-baseline **Meter C** $5k/yr cumulative-cost trigger to the new
  spend envelope.
- Record old + new thresholds and the calibration date in the
  per-meter threshold table.

**Rollback.** If the tier upgrade triggers an unexpected spend spike
(e.g., misconfigured rate-limiting elsewhere), drop back to the previous
tier within the billing cycle (Cloudflare allows this). Exact downgrade
command: open Cloudflare dashboard → Workers & Pages → Plans → select
previous tier → Confirm. CLI form: `wrangler subscription update
--tier=paid` (replace with the actual previous tier slug as documented
at flip time).

---

## §2 — MongoDB sharded-cluster upgrade

**Target tier.** Atlas **M30+ sharded cluster** (sharding is **not**
available on the M10/M20 replica-set tier used in #360 — this requires
explicit `cluster-type=SHARDED` at provision time). M30+ is roughly
**4× the per-month cost of M10** (~$60 → ~$300 fixed). Recalculate the
Mongo Atlas row's burn rate and migration checkpoint accordingly (§B).

**Shard keys (locked).**
- `conversations` sharded by **`{ user_id: "hashed" }`** — high
  cardinality, even distribution, matches the `(session_id, ts)`
  IXSCAN from #360 Step 1 (because `session_id` embeds `user_id` per
  the §2.1 contingency below).
- `user_profile` sharded by **`{ user_id: "hashed" }`** — already the
  unique index from #359 §2.2.

### §2.1 — Pre-flight verification gates (BLOCK provisioning)

The following gates **must pass before** the sharded cluster is
provisioned. Any gate that fails generates a #360 follow-up and the
sharded cutover is paused.

**Gate 2.1.a — `session_id` shape check.** Run
`scripts/preflight/session_id_shape_check.py` (provided in this commit).
The script samples N=1000 documents from `sessions` and asserts
`session_id` either:
- embeds `user_id` (e.g. `u1234_<ulid>`), **or**
- is always co-queried with `user_id` (verified by the cross-shard
  audit gate below).

If the script reports >5% bare-UUID `session_id` values that are not
co-queried with `user_id`, the **legacy session-ID contingency** below
applies.

**Gate 2.1.b — Legacy session-ID contingency (mandatory if 2.1.a fails).**
Do **not** rewrite history. Instead:
1. **Forward minting changes** to the `{user_id-prefix}_{ulid}` shape
   so all new sessions are shard-friendly.
2. **One-shot backfill** adds a `user_id` field to every legacy
   `conversations` document by joining against `sessions` (or the auth
   log) on `session_id`. `user_id` is the canonical shard key, so once
   it exists on every doc the cluster routes correctly.
3. **Orphan rows** (where the join fails) get
   `user_id = "legacy:" + session_id` so they shard deterministically
   (same row → same shard) at the cost of zero per-user locality —
   acceptable only because the orphan set is bounded and ages out as
   legacy sessions end.
4. **Backfill verification (mandatory before cutover):**
   ```
   db.conversations.countDocuments({user_id: {$exists: false}}) == 0
   ```
   Backfill must complete and be verified **before** the sharded-cluster
   cutover begins.

Owner of this contingency lands in §C below.

**Gate 2.1.c — Cross-shard transaction audit.** Run
`scripts/preflight/cross_shard_transaction_audit.py` (provided). The
script greps `artifacts/syrabit-backend` for any multi-document
transaction (`session.start_transaction()`, `with_transaction(...)`)
that touches both `conversations` and `user_profile`. Each hit must be
either:
- **Confirmed safe** — both docs land on the same shard via the same
  `user_id` (annotate the call site with a `# shard-safe: same user_id`
  comment so the audit script accepts it), **or**
- **Refactored before sharding goes live** (split into two single-shard
  ops + an idempotency token).

The audit must report **0 unannotated cross-shard transactions** before
the sharded cluster is provisioned.

### §2.2 — Tradeoffs (mandatory design-rationale doc)

Hashed `{ user_id }` is **good for OLTP** (per-user write/read locality)
but **bad for OLAP** (range queries like "all conversations between
dates X and Y across all users" become scatter-gather). Acceptable for
chat workload.

If product later needs cross-user date-range analytics, route those
through the **async-batch index / a separate analytics pipeline**
(§4.4) — not against the live sharded cluster. Document this constraint
in `docs/postmortems/` with the cutover-day note.

### §2.3 — Code-budget callout

Application-level dual-write logic does **not** exist in #347/#360
today — this is a **code change, not a config change**. Budget **1–2
sprints** of dispatcher / persistence-layer work for dual-write +
cutover, including unit and integration tests. This is **on top of**
the Atlas provisioning work.

### §2.4 — Migration mechanics

1. **Provision** new sharded cluster (`cluster-type=SHARDED`,
   `M30+`). Initial shard count `N` is an **explicit decision parameter**:
   - `N=2` if DAU < 200k at cutover.
   - `N=4` if DAU ≥ 200k at cutover.
   Documented in §C below.
2. **Pre-split chunks** across initial shards before cutover to avoid
   balancer storms.
3. **Dual-write** from `syrabit-backend` to old replica set + new
   sharded cluster for a soak window (≥ 7 days). Reads stay on old.
4. **Verify** row counts match (per-collection, per-day) and per-shard
   distribution via `sh.status()`.
5. **Atomic read-cutover** via a single env-var flip
   (`MONGO_PRIMARY_URI=<new>`). Rollback path: flip back.
6. **Stop dual-write** after a 7-day soak on the new cluster with no
   incidents.

### §2.5 — Verify post-cutover

- `sh.status()` shows balanced chunks.
- `db.conversations.find().explain("executionStats")` confirms
  `SHARD_MERGE` only when expected.
- Cross-shard ops add **≤ 5–10 ms p99**.

### §2.6 — Rollback

```
# Single env-var flip — instant
az containerapp update --name syrabit-fastapi --resource-group syrabit-prod \
  --set-env-vars MONGO_PRIMARY_URI=$MONGO_LEGACY_URI

# Or via Redis hot-flag (faster propagation, requires #360 wiring):
redis-cli SET mongo:primary "legacy"
```

A documented rollback that has never been executed is a documented
hope. **Rehearse on staging before production cutover** (see §6.1).

---

## §3 — Redis multi-shard upgrade

Move from the single-shard Upstash standard database used in #360 to an
**explicit multi-shard topology**.

### §3.1 — Path A: Upstash Redis Cluster (preferred if credits cover it)

Cluster-mode-enabled database. Verify:
```
redis-cli -h <cluster-host> INFO cluster
# expect: cluster_enabled:1
# expect: cluster_slots_assigned:16384
```

**Caveat:** Upstash Redis Cluster is a **separate product** from
standard Upstash with its own pricing and credit-coverage. **Pre-flight
gate:** confirm with Upstash that the existing **Upstash for Startups
credit allocation covers the cluster-mode product**. If it does not,
default to **Path B**.

### §3.2 — Path B: hash-partitioned fan-out (fallback)

N standard Upstash databases with a thin client-side sharder. Sharder
uses **CRC16-mod-N** on the key suffix after the colon prefix
(`chat:` / `embed:` / `rate:`) to mirror Redis Cluster slot semantics —
so a future migration to Path A is mechanical (just swap the client).

### §3.3 — Key-class routing rules (apply to both paths)

| Key class | Routing | Reason |
|---|---|---|
| **Rate-limit / counter / hot-flag** (`chat:fallback`, `chat:fallback:pin`, daily-call counter, RPM sliding-window counter, per-session stuck-LLM heuristic counters from #362) | **Hash-distribute** normally | Each key is independent; even distribution wins |
| **Embed / RAG cache** (from #361) | **Pin to a dedicated cache shard** (or small replica set within the cluster), separate from the rate-limiter shards | Strong access locality (same exam questions repeat across users) — hashing **destroys** the cache-hit rate |

### §3.4 — Hot-flag propagation rule

A flip to `chat:fallback=1` must propagate to **every** rate-limiter
shard a dispatcher might read.

- **Implementation:** parallel `SET` to all rate-limiter shards.
- **Validity range:** sub-ms cost up to **N≈10**.
- **Beyond N=10:** switch to **Redis Pub/Sub on a dedicated coordination
  shard** that all dispatchers subscribe to. Migration steps:
  1. Stand up coordination shard.
  2. Dispatchers subscribe to `chat:flag-events` channel.
  3. Meter automation publishes flag events to the channel instead of
     parallel SETs.
  4. Verify propagation latency stays < 1 s p99.

The threshold (**N=10**) and the migration steps are recorded here so
on-call doesn't have to re-derive them mid-incident.

### §3.5 — Verify post-cutover

- **Hot-flag propagation < 1 s p99** under load (re-run #360 Step 10
  failover smoke).
- **No single shard exceeds 30% of cluster ops.**
- **Dedicated cache shard hit-rate** matches or exceeds the pre-shard
  #361 baseline (the canary that "we didn't shatter the cache").
- Upstash dashboard shows traffic spread across shards as expected.

### §3.6 — Rollback

For Path A: switch the client back to the single-shard Upstash database
URL. For Path B: set `REDIS_SHARD_COUNT=1` and the sharder bypasses
hashing.

---

## §4 — Pinecone scale-out

Move the live-RAG index past Standard-tier serverless single-index
defaults.

### §4.1 — Default path: Path A — namespace sharding (recommended)

Keep one serverless index, partition into **N namespaces by hashed
`user_id` or `user_locale`**. Dispatcher routes `query` / `upsert` to
the correct namespace using the **same CRC16-mod-N hash function as the
Redis sharder** so a single user's Mongo shard, Redis shard, and
Pinecone namespace all align (debuggability + locality win).

The `user_id → namespace` lookup is cached in the **dedicated Redis
cache shard** from §3.3 to avoid a Mongo hop on every retrieval.

### §4.2 — Fallback path: Path B — pod-based scale-up

Provision a pod-based index with `pod_type` (e.g. `p1.x2` or `s1.x4`)
sized to the projected RAG QPS from Meter A re-baselining. Set
`replicas: ≥ 2` for read scale-out and HA.

**Caveat:** pod-based indexes are on the **legacy Pinecone control
plane** and **may not be available on new Pinecone for Startups
accounts** — Pinecone has been steering everyone to serverless for ~2
years. **Pre-flight gate:** executor confirms pod-based provisioning is
available on the project's Pinecone account before choosing Path B; if
unavailable, **Path A is the only option**.

### §4.3 — Async-batch RAG isolation

Async-batch workloads (PDF ingest, model-paper generator embedding
writes) move to a **separate Pinecone index** so batch upserts cannot
starve live-retrieval latency.

### §4.4 — Embed-model consistency gate (mandatory)

The live-RAG index and the async-batch index **must use the same
embedding model** — the `embed_hotpath` pin from #359
(`@cf/baai/bge-m3`).

If they ever diverge, retrieval quality degrades silently and
unobservably. The CI check
`scripts/ci/embed_model_consistency_check.py` (provided in this commit)
fails the build if the embed-model id used to populate either index
doesn't match the `embed_hotpath` resolver.

**Annotation contract.** Operators may suppress a finding by adding
`# embed-model: @cf/baai/bge-m3` (or
`# embed-model: legacy-migration-not-in-prod-chain` for one-shot
historical scripts) within 30 lines above the upsert call site. Any
mismatched annotation FAILs.

**Baseline findings as of #363 commit (must be remediated before the
gate is wired into CI as blocking):**

| File:line | Finding | Action |
|---|---|---|
| `artifacts/syrabit-backend/syllabus_embedder.py` (5 sites) | unresolved | annotate after audit |
| `artifacts/syrabit-backend/providers/chunk_embedder.py:229` | unresolved | annotate after audit |
| `artifacts/syrabit-backend/retrievers/pinecone_vector.py:237` | unresolved (HTTP path inside the retriever wrapper, callers responsible) | annotate as wrapper |
| `artifacts/syrabit-backend/scripts/ingest_vertex_index.py:198` | unresolved | annotate as legacy or remove |
| `artifacts/syrabit-backend/scripts/migrate_chunks_to_pinecone.py:197` | uses `embed-multilingual-v3.0` (Cohere) | annotate as `legacy-migration-not-in-prod-chain` if one-shot, else refactor |
| `artifacts/syrabit-backend/scripts/embed_english_corpus.py:314` | unresolved | annotate after audit |
| `artifacts/syrabit-backend/scripts/embed_assamese_corpus.py:385` | unresolved | annotate after audit |

The gate ships in this commit as a **standalone runnable check**;
it becomes a **blocking CI step** only after the table above is
worked through.

### §4.5 — Verify post-cutover

- `describe_index_stats` shows balanced vector counts per namespace
  (Path A) or per pod (Path B).
- p95 query latency unchanged or better vs the #360 baseline.
- Batch upsert spike on the async index produces **no measurable
  latency hit** on the live index.
- Embed-model-consistency CI gate is **green**.

### §4.6 — Rollback

For Path A: dispatcher routes all queries to namespace `default` and
ignores the hash function. For Path B: scale `replicas: 1` and revert
to the previous `pod_type`.

---

## §5 — Vertex Gemini as partial-hot-path co-primary

Add a routing policy in the chat dispatcher with **three configurable
modes** — round-robin, sticky-by-session, quality-weighted — selectable
via env / Redis flag.

### §5.1 — Default = quality-weighted (locked, NOT sticky-by-session)

**Rationale.** Sticky-by-session sounds friendly but at 500k–1M DAU it
**concentrates all of one user's load on whichever provider got their
first turn** — if Vertex degrades for 30 min, every session it owns
sees degraded TTFB for 30 min instead of being able to fail over
per-turn.

Quality-weighted (route each turn to the provider with the **lower
trailing-5-min p95 latency**, weighted by **remaining RPM headroom**)
gives per-turn resilience and naturally biases away from a degraded
provider without a flag flip.

### §5.2 — Sticky-by-session = opt-in only

Kept as an opt-in mode for sessions that hit a model-specific tool /
state path (e.g., a long-form generation that benefits from same-model
continuity); flagged via `routing_mode_session_override` in the user
profile (#359 §2.2 `flags` bag).

### §5.3 — Round-robin = debug-only

Documented but **not recommended at scale** — included only for
debugging / load-test repeatability where deterministic routing is
needed.

### §5.4 — Cross-region latency cost

Cross-region cost is **~50–100 ms** if Vertex is not co-located with the
ACA anchor region. **Measure before promoting Vertex to co-primary.**

If cross-region adds **> 100 ms p95** to Vertex turns, restrict Vertex
to **overflow-only mode** (route to Vertex only when Workers-AI's RPM
headroom drops below 30%) until a Vertex region in the same anchor is
available.

Record the measured cross-region latency in the runbook (§B) **before
flipping default = quality-weighted live**.

### §5.5 — Order

This step runs **after** the data-tier sharding (§2/§3/§4) so the
load-test execution in §7 measures both.

---

## §6 — Async-batch on a separate CF / Azure account

Stand up a **second Cloudflare account** (or an isolated **Azure
subscription**) for PDF summarizer and model-paper generator workloads.

- Route async batch entrypoints through the **isolated account's
  credentials** (`CF_BATCH_ACCOUNT_ID`, `CF_BATCH_API_TOKEN` — set in
  the Rust ACA app's env).
- Confirm a synthetic batch spike does **not** consume the chat
  account's Workers-AI quota (verify in Cloudflare AI Gateway
  analytics: chat account RPM stays flat during batch spike).

### §6.1 — Operational rehearsal (mandatory pre-cutover)

Per #363 Step 9a: for **each** sharding step (§2 Mongo, §3 Redis, §4
Pinecone) the documented rollback path **must be dry-run on a staging
cluster** with synthetic traffic **before the production cutover
begins**. Each rehearsal produces a `ROLLBACK.md` per component with:

- Exact commands run.
- **Time-to-revert** measured.
- Any errors encountered + their fixes.

> A documented rollback that has never been executed is a documented
> hope, not a rollback.

### §6.2 — Sharded backup & restore drill (mandatory)

Sharded-cluster backup is a **fundamentally different operation** from
replica-set backup (per-shard snapshot consistency, restore ordering,
balancer-state restoration). Document and **execute at least one full
restore-to-test-cluster drill** per sharded component before #363
closes.

Schedule maintenance windows for **index rebuilds on the populated
M30+ cluster** (these can take hours and may need elevated capacity
temporarily).

### §6.3 — Per-shard observability (extends #360 Step 9)

| Metric | Trigger |
|---|---|
| Per-shard QPS | dashboard panel |
| Per-shard error rate | alert > 1% sustained 5 min |
| Per-shard p50/p95/p99 latency | dashboard panel |
| **Hot-shard detection** | warning alert if any shard > 30% of cluster ops |
| **Mongo balancer-storm alert** | chunk migration rate above threshold |
| **Redis cluster-slot rebalance alert** | slot migration in progress |
| **Pinecone per-namespace `index_fullness`** | alert at 80% |

All metrics flow into the **same dashboard family** established in
#360 — **no parallel observability stack**.

---

## §7 — Load-test harness (Steps 7a + 7b)

### §7.1 — Step 7a: Build the harness

Build a **k6** (or equivalent) distributed load harness capable of
generating **500k–1M DAU equivalent traffic** with realistic conversation
patterns:

- Multi-turn sessions.
- Mixed-language input (where #362 has shipped).
- RAG-bearing queries.
- Idle-then-burst arrival distributions.

Harness runs from a cluster **co-located with the ACA region** so
latency numbers reflect production geometry, not laptop-to-edge RTT.

**Capture (per scenario):**
- Per-RPS TTFB histogram.
- Per-shard ops distribution.
- Per-provider error rates.
- Cache-hit-rate timeseries.

**Budget callout:** building this harness is a **2–3 week sub-project on
its own** (orchestrators, scenario library, analysis pipeline) — **not a
single afternoon**. If a usable harness already exists from a prior
load-test cycle, **reuse it** and document the gap-filling work
instead.

### §7.2 — Step 7b: Execute and analyze

Run sustained **500k-DAU** and peak-burst **1M-DAU** scenarios against
the post-#363 stack.

**Assertions (any failure blocks #363 close):**

| Assertion | Threshold |
|---|---|
| TTFB p95 | < 1.5 s |
| RPM headroom | < 70% (Meter B clear) |
| **Mongo cross-shard ops** | ≤ 5–10 ms p99 |
| **Redis hot-flag propagation** | < 1 s p99 across shards |
| **Dedicated cache shard hit-rate** | ≥ pre-shard #361 baseline |
| **Pinecone live-index p95** | unchanged under concurrent batch upsert load |
| **Embed-model-consistency CI gate** | green |
| **Async-batch account isolation** | chat account RPM stays flat during batch spike |

---

## §A — Updates to `provider-priority-map.md` (capacity tier)

The following tables in `provider-priority-map.md` are **extended**
post-#363 (not replaced — the tier rows stay; the topology fields gain
sharded variants):

### vector_db_live (post-#363)

| tier | provider_slug | model_id | region | notes |
|---|---|---|---|---|
| primary | pinecone |  | aws-us-west-2 | `syrabit-rag-live`, namespace-sharded by CRC16-mod-N(`user_id`); see §4.1 |
| secondary | cf_vectorize |  | cf-edge | Edge cache |
| rollback_only | vertex_vector |  | us-central1 | Matching Engine |

### vector_db_batch (post-#363)

| tier | provider_slug | model_id | region | notes |
|---|---|---|---|---|
| primary | pinecone |  | aws-us-west-2 | **`syrabit-rag-batch` — separate index** from live (§4.3) |
| secondary | cf_vectorize |  | cf-edge | |

### chat_default (post-#363, partial co-primary)

| tier | provider_slug | model_id | region | notes |
|---|---|---|---|---|
| primary | workers_ai | @cf/mistral/mistral-7b-instruct-v0.3 | cf-edge | Co-primary; quality-weighted routing |
| primary | vertex | gemini-2.5-flash | us-central1 OR same-anchor | Co-primary; quality-weighted; **only after §5.4 cross-region check passes** |
| secondary | azure_openai | gpt-4.1-mini | eastus2 | Auto-target on `CHAT_FALLBACK=1` |
| tertiary | workers_ai | @cf/openai/gpt-oss-20b | cf-edge | Edge fallback |

> Vertex is **not** added to chat_default until the §5.4 cross-region
> latency check confirms Vertex turns add ≤ 100 ms p95. Until then,
> Vertex stays in `validation_sampled` only (#359 §6).

### Topology fields (new — append after each table)

```
shard_topology:
  mongo:
    cluster_type: sharded         # was: replica_set in #360
    tier: M30+                    # was: M10 in #360
    shard_keys:
      conversations: { user_id: "hashed" }
      user_profile:  { user_id: "hashed" }
    initial_shard_count: N        # 2 if DAU<200k, 4 if DAU≥200k (§2.4)
  redis:
    topology: cluster | fanout    # Path A or B (§3.1/§3.2)
    n_shards: N
    cache_shard: dedicated        # §3.3
  pinecone:
    live_index: namespace_sharded # Path A (§4.1)
    n_namespaces: N
    batch_index: separate         # §4.3
    embed_model_consistency: enforced_in_ci
```

---

## §B — Updates to `credit-burn-runbook.md` (capacity tier)

### Per-provider credit-window table (revised rows)

| provider | program_name | credit_balance_usd | monthly_burn_usd | expected_exhaustion_date | migration_checkpoint_date | migration_owner | paid_billing_handoff_status | documented_fallback | last_reviewed_date |
|---|---|---:|---:|---|---|---|---|---|---|
| Cloudflare | Cloudflare for Startups | 5000 | **2000** *(§1: Workers-AI Enterprise tier)* | **2026-08-04** *(was 2028-09-04)* | **2026-07-05** | founder@syrabit.ai | not_started | Workers-AI Paid + Pinecone Standard direct billing | 2026-05-04 |
| MongoDB Atlas | Atlas for Startups | 500 | **250** *(§2: M30+ sharded, ~4× M10)* | **2026-07-04** *(was 2027-02-04)* | **2026-06-04** | founder@syrabit.ai | not_started | M30+ direct billing | 2026-05-04 |
| Pinecone | Pinecone for Startups | 5000 | **150** *(§4: scale-out + separate batch index)* | **2028-10-04** *(was 2031-05-04)* | **2028-09-04** | founder@syrabit.ai | not_started | Standard tier direct billing per index | 2026-05-04 |
| Upstash | Upstash for Startups | 1000 | **80** *(§3: cluster or N-fanout)* | **2027-04-04** *(was 2030-05-04)* | **2027-03-05** | founder@syrabit.ai | not_started | Pro tier direct billing per shard | 2026-05-04 |

> **Burn rates above are revised gross monthly costs for the upgraded
> tiers — they apply when the §1–§4 cutovers complete, not before.**
> Until the cutovers happen, the original #359 burn rates (§3 of
> `credit-burn-runbook.md`) remain authoritative.

### Per-meter threshold table (revised rows)

| meter_id | metric | warning_threshold | trip_threshold | auto_clear_threshold | propagation_path | last_calibrated_date | tunable_via_env_var |
|---|---|---|---|---|---|---|---|
| A | RAG API calls / UTC day | **80000** *(was 8000)* | **100000** *(was 10000)* | next UTC day rollover (00:00 UTC) | redis_hot_flag | TBD-on-cutover | `METER_A_DAILY_LIMIT` |
| B | Workers-AI RAG RPM (rolling 1-min window) | 60% of model RPM | 70% of model RPM | < 50% for 5 consecutive minutes | redis_hot_flag | TBD-on-cutover | `METER_B_TRIP_PCT`, `METER_B_CLEAR_PCT`, `METER_B_SUSTAIN_MIN`, `METER_B_WINDOW_S` |
| C | Cumulative Workers-AI RAG cost / 365-day rolling | 60% of **$24000** *(was $5000 → ~$2k/mo × 12)* | 70% of $24000 | n/a (notify-only) | notify_only | TBD-on-cutover | `METER_C_BUDGET_USD`, `METER_C_ALERT_PCT` |

### Per-flag operations table (new rows)

| flag_name | read_path | write_path | default_value | who_can_flip | propagation_latency_target | rollback_command |
|---|---|---|---|---|---|---|
| `mongo:primary` | Redis hot-flag, read on Mongo client init | on-call | `"sharded"` | on-call | < 5 ms (with reconnect) | `redis-cli SET mongo:primary "legacy"` |
| `chat:routing_mode` | Redis hot-flag, read every turn | on-call | `"quality_weighted"` | on-call | < 5 ms | `redis-cli SET chat:routing_mode "workers_only"` |
| `pinecone:live_namespace_count` | env var read at process start (overridable via Redis `pinecone:n_namespaces`) | on-call | `1` (pre-#363) → `N` (post-#363) | on-call | next deploy or sub-ms via Redis override | `redis-cli SET pinecone:n_namespaces 1` |
| `redis:shard_count` | env var read at process start | on-call | `1` (pre-#363) → `N` (post-#363) | infra owner | ACA revision rollout | redeploy ACA with previous `REDIS_SHARD_COUNT` |

---

## §C — Owners + decision parameters (locked at cutover)

| Decision parameter | Locked value | Owner | Locked-on date |
|---|---|---|---|
| Mongo `N` (initial shard count) | 2 if DAU<200k at cutover, 4 if DAU≥200k | founder@syrabit.ai | TBD-on-cutover |
| Mongo legacy session-ID contingency owner | founder@syrabit.ai | founder@syrabit.ai | TBD-on-cutover |
| Redis path (A or B) | A if Upstash credit covers cluster; else B | founder@syrabit.ai | TBD-on-cutover |
| Pinecone path (A or B) | A (default); B only if pre-flight gate confirms pod-based available | founder@syrabit.ai | TBD-on-cutover |
| Vertex co-primary cross-region p95 | measured value here, must be ≤ 100 ms | founder@syrabit.ai | TBD-on-cutover |
| Async-batch account ID | recorded here | founder@syrabit.ai | TBD-on-cutover |

---

## §D — Out of scope

- **Caching / model-A/B / p99 tuning** — covered by #361.
- **Recall depth / mixed-language / per-session fallback** — covered by
  #362.
- **New geo regions or active-active multi-region** — deferred to a
  later capacity round.

---

## §E — Cross-references

- Pre-flight scripts:
  - `scripts/preflight/session_id_shape_check.py` (Gate 2.1.a)
  - `scripts/preflight/cross_shard_transaction_audit.py` (Gate 2.1.c)
- CI gate: `scripts/ci/embed_model_consistency_check.py` (§4.4)
- Rollback rehearsal artifacts (per-component, generated at cutover):
  - `infra/rollback/mongo-sharded.ROLLBACK.md`
  - `infra/rollback/redis-multishard.ROLLBACK.md`
  - `infra/rollback/pinecone-scaleout.ROLLBACK.md`
- Originating tasks: #359 (spec), #360 (implementation),
  #347 (provider removals).
