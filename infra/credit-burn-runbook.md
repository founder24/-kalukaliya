# Credit-Burn Runbook — v3 Canonical

> **Provider removals (OpenAI, Anthropic, Bedrock, Stripe, Quge5,
> Resend, Grok, Railway, DigitalOcean) are tracked in Task #347.**
>
> Companion docs:
> - `infra/per-cloud-feature-delegation.md` — full v3 spec (authoritative).
> - `infra/provider-priority-map.md` — per-feature provider table.
> - `infra/capacity-roadmap-363.md` — revised burn rates / meter
>   thresholds / new flag rows post-capacity-tier upgrade. See Task
>   #363 §B for the row deltas; the rows here remain authoritative
>   until the §1–§4 cutovers complete.
> - `infra/perf-roadmap-361.md` — perf-tier flags (`cache:rag_enabled`,
>   `cache:embed_enabled`, `curriculum:version`, `FAST_MODE_AB_*`,
>   `MAX_HISTORY_TURNS`, `MAX_CHUNK_TOKENS`, `LLM_TURN_TIMEOUT_S`)
>   and the A/B promotion-decision + cache-sunset decision logs.
>   See Task #361 §F.
> - `infra/features-roadmap-362.md` — features-tier flags
>   (`recall_intent:tier1_phrases`, `recall_intent:tier2_tokens`,
>   `session:fallback:{id}`, `session:fallback:disabled`,
>   `session:ttfb:{id}`, `moderation:rephrase_hints`,
>   `moderation:hard_floors_test_mode`) plus the moderation-mode
>   threshold matrix and non-negotiable safety floors. See Task #362 §F.

**Status:** locked v3 — 2026-05-04
**On-call channel:** `#syrabit-oncall` (Slack)
**Owner:** founder@syrabit.ai

---

## §1 — Credit-burn trigger and meter shape (spec only)

The credit-burn meter is built in #360. This runbook only specifies the
shape the meter must conform to:

- **Hot-path counter:** Redis hash `chat:meter:rag_calls` keyed by UTC
  date (`YYYY-MM-DD`), incremented on every RAG call. TTL = 48 h.
- **Durable backstop:** AWS Lambda + DynamoDB table
  `syrabit-credit-burn-meter` syncs Redis → Dynamo every 60 s for
  cross-restart and cross-region durability.
- **Cost telemetry:** Cloudflare AI Gateway response headers
  (`cf-aig-cost`) sum into a 365-day rolling-window counter for Rule C.
- **RPM telemetry:** Sliding-window counter (1-minute window) per
  Workers-AI model, used by Rule B.

---

## §2 — On-call procedure

1. **Alert fires** in `#syrabit-oncall` (Slack) — Logic Apps or
   CloudWatch SNS → Slack webhook.
2. **On-call acknowledges** within 5 min during business hours; within
   15 min outside.
3. **Triage** using the per-meter and per-flag tables below.
4. **Flip flag** via the runbook command (table in §6).
5. **Pin flag** if the failover should persist beyond auto-clear:
   `redis-cli SET chat:fallback:pin 1`.
6. **Document** the incident in `#syrabit-oncall` thread; add to
   weekly review.

---

## §3 — Per-provider credit-window table

> One row per provider in the all-provider credit policy. Update
> `last_reviewed_date` monthly. **Post-#363 burn-rate revisions are
> tracked side-by-side in the `monthly_burn_post_363_usd` and
> `expected_exhaustion_post_363` columns** — those values become the
> authoritative numbers when the §1–§4 cutovers in
> `infra/capacity-roadmap-363.md` complete; until then, the original
> `monthly_burn_usd` and `expected_exhaustion_date` columns are
> authoritative.

| provider | program_name | credit_balance_usd | monthly_burn_usd | monthly_burn_post_363_usd | expected_exhaustion_date | expected_exhaustion_post_363 | migration_checkpoint_date | migration_owner | paid_billing_handoff_status | documented_fallback | last_reviewed_date |
|---|---|---:|---:|---:|---|---|---|---|---|---|---|
| Azure | Microsoft for Startups | 5000 | 200 | 200 | 2028-09-04 | 2028-09-04 | 2028-08-05 | founder@syrabit.ai | not_started | Pay-as-you-go on existing Azure account | 2026-05-04 |
| AWS | AWS Activate | 1000 | 80 | 80 | 2027-05-04 | 2027-05-04 | 2027-04-04 | founder@syrabit.ai | not_started | Pay-as-you-go on existing AWS account | 2026-05-04 |
| Cloudflare | Cloudflare for Startups | 5000 | 200 | **2000** *(Workers-AI Enterprise tier)* | 2028-09-04 | **2026-08-04** | **2026-07-05** *(post-#363)* | founder@syrabit.ai | not_started | Workers Paid + Pinecone Standard direct billing | 2026-05-04 |
| MongoDB Atlas | Atlas for Startups | 500 | 60 | **250** *(M30+ sharded, ~4× M10)* | 2027-02-04 | **2026-07-04** | **2026-06-04** *(post-#363)* | founder@syrabit.ai | not_started | M30+ direct billing | 2026-05-04 |
| Pinecone | Pinecone for Startups | 5000 | 50 | **150** *(scale-out + separate batch index)* | 2031-05-04 | **2028-10-04** | **2028-09-04** *(post-#363)* | founder@syrabit.ai | not_started | Standard tier direct billing per index | 2026-05-04 |
| Upstash | Upstash for Startups | 1000 | 20 | **80** *(cluster or N-fanout)* | 2030-05-04 | **2027-04-04** | **2027-03-05** *(post-#363)* | founder@syrabit.ai | not_started | Pro tier direct billing per shard | 2026-05-04 |
| Google Cloud | Google for Startups | 2000 | 120 | 120 | 2027-09-04 | 2027-09-04 | 2027-08-05 | founder@syrabit.ai | not_started | Pay-as-you-go on existing GCP project | 2026-05-04 |
| Cohere | Cohere for Startups | 1000 | 50 | 50 | 2028-01-04 | 2028-01-04 | 2027-12-05 | founder@syrabit.ai | not_started | Pay-as-you-go on Cohere direct API | 2026-05-04 |
| Razorpay | Razorpay startup program | 0 | 0 | 0 | n/a (perpetual live mode) | n/a | n/a | founder@syrabit.ai | live | n/a — already on direct billing | 2026-05-04 |
| SendGrid | (via Azure Marketplace) | (drawn from Azure pool) | (in Azure burn) | (in Azure burn) | tied to Azure | tied to Azure | 2028-08-05 | founder@syrabit.ai | not_started | Essentials Free 100/day fallback tier | 2026-05-04 |

---

## §4 — Per-meter threshold table

| meter_id | metric | warning_threshold | trip_threshold | warning_post_363 | trip_post_363 | auto_clear_threshold | propagation_path | last_calibrated_date | tunable_via_env_var |
|---|---|---|---|---|---|---|---|---|---|
| A | RAG API calls / UTC day | 8000 | 10000 | **80000** | **100000** | next UTC day rollover (00:00 UTC) | redis_hot_flag | 2026-05-04 | `METER_A_DAILY_LIMIT` |
| B | Workers-AI RAG RPM (rolling 1-min window) | 60% of model RPM | 70% of model RPM | 60% of model RPM | 70% of model RPM | < 50% for 5 consecutive minutes | redis_hot_flag | 2026-05-04 | `METER_B_TRIP_PCT`, `METER_B_CLEAR_PCT`, `METER_B_SUSTAIN_MIN`, `METER_B_WINDOW_S` |
| C | Cumulative Workers-AI RAG cost / 365-day rolling | 60% of $5000 | 70% of $5000 | **60% of $24000** | **70% of $24000** | n/a (notify-only) | notify_only | 2026-05-04 | `METER_C_BUDGET_USD`, `METER_C_ALERT_PCT` |

> **Post-#363 columns become authoritative when the §1 Workers-AI tier
> upgrade ships.** Until then, the pre-#363 columns are the on-call
> reference. Both sets are kept side-by-side here so on-call doesn't
> have to cross-read `infra/capacity-roadmap-363.md` mid-incident.

**Active Workers-AI model RPM (refresh whenever active model changes):**

| model | published RPM |
|---|---|
| `@cf/mistral/mistral-7b-instruct-v0.3` | 300 |
| `@cf/openai/gpt-oss-20b` | 300 |
| `@cf/meta-llama/Llama-3.2-3B-Instruct` | 300 |

> When the active model changes, refresh this table and recompute Meter
> B's absolute trip / clear values from `trip_pct × published_RPM`.

**Tunable note for Meter B:** Defaults are 70% trip / 50% auto-clear /
5-min sustain / 1-min sliding window. If traffic patterns change —
short bursty spikes are normal but sustained load stays low — relax to
e.g. 75% trip / 45% auto-clear; if you want more headroom safety,
tighten to e.g. 65% trip / 55% auto-clear. Any change must be backed by
a short load-test that validates the new thresholds against the active
model's published RPM and must be recorded below with the date, the
operator, and the reason. Do **not** convert Meter B into a notify-only
or long-window cost-style rule — those roles belong to Meters A/C
respectively.

**Meter B calibration log:**

| date | operator | new trip_pct | new clear_pct | new sustain_min | reason |
|---|---|---|---|---|---|
| 2026-05-04 | founder@syrabit.ai | 70 | 50 | 5 | Initial v3 lock |

---

## §5 — Fallback rules (canonical text)

### Rule A — daily-call ceiling (auto-flip)

> *"When the count of RAG API calls in a single UTC day exceeds 10,000,
> flip `CHAT_FALLBACK=1`. Secondary exam-Q&A traffic shifts to Azure
> GPT-4.1-mini until the next UTC day rollover (00:00 UTC), unless
> on-call has pinned the flag."*

### Rule B — RPM-headroom guard (auto-flip)

> *"When Workers-AI RAG-call rate reaches 70% of the published platform
> RPM limit for the currently-active Workers-AI model (measured over a
> rolling 1-minute window), flip `CHAT_FALLBACK=1`. Auto-clear when
> usage drops below 50% of the RPM limit for 5 consecutive minutes,
> unless on-call has pinned the flag."*

### Rule C — cumulative cost alert (notify-only)

> *"When cumulative Workers-AI RAG cost exceeds 70% of $5k over a
> 365-day rolling window, post a high-priority alert to the on-call
> channel."* This rule **does not** flip any flag on its own; on-call
> decides whether to flip `CHAT_FALLBACK=1` manually.

### Rule interaction

Rules A, B, and C operate independently and may fire at different times.
If either auto-flip rule (A or B) is active, `CHAT_FALLBACK=1` stays set
even after the other clears, until both clear or on-call resets.

### Flag mechanism (sub-ms propagation)

`CHAT_FALLBACK` and `CHAT_FALLBACK_PIN` are **Redis hot-flags**
(`chat:fallback`, `chat:fallback:pin`) read by the dispatcher on every
turn. The Azure Container Apps env vars are the durable cold-start
default only; flip-propagation must not depend on an ACA revision
rollout (which is ~30–60 s and would let the limited model keep taking
traffic during a flip).

---

## §6 — Per-flag operations table

| flag_name | read_path | write_path | default_value | who_can_flip | propagation_latency_target | rollback_command |
|---|---|---|---|---|---|---|
| `chat:fallback` | Redis hot-flag, read every turn | Meter A/B auto-flip + on-call manual | `0` | meter automation + on-call | < 5 ms | `redis-cli DEL chat:fallback` |
| `chat:fallback:pin` | Redis hot-flag, read every turn | on-call only | `0` | on-call | < 5 ms | `redis-cli DEL chat:fallback:pin` |
| `email:fallback` | Redis hot-flag, read on email send | on-call manual + SendGrid bounce monitor | `0` | on-call + automation | < 5 ms | `redis-cli DEL email:fallback` |
| `VALIDATION_SAMPLE_RATE` | env var read at process start (overridable via Redis `validation:sample_rate`) | on-call | `0.10` | on-call | next deploy or sub-ms via Redis override | `redis-cli DEL validation:sample_rate` |
| `CHAT_FALLBACK` (env var, durable) | ACA env var (cold-start default only) | ACA revision rollout | `0` | infra owner | ~30–60 s (cold-start only) | redeploy ACA revision with `CHAT_FALLBACK=0` |
| `mongo:primary` *(post-#363)* | Redis hot-flag, read on Mongo client init | on-call | `"sharded"` (post-cutover) / `"legacy"` (pre-cutover) | on-call | < 5 ms (with reconnect) | `redis-cli SET mongo:primary "legacy"` |
| `chat:routing_mode` *(post-#363)* | Redis hot-flag, read every turn | on-call | `"quality_weighted"` | on-call | < 5 ms | `redis-cli SET chat:routing_mode "workers_only"` |
| `pinecone:n_namespaces` *(post-#363)* | env var read at process start (overridable via Redis) | on-call | `1` (pre-cutover) → `N` (post-cutover) | on-call | next deploy or sub-ms via Redis | `redis-cli SET pinecone:n_namespaces 1` |
| `redis:shard_count` *(post-#363)* | env var read at process start | on-call | `1` (pre-cutover) → `N` (post-cutover) | infra owner | ACA revision rollout | redeploy ACA with previous `REDIS_SHARD_COUNT` |
| `cache:rag_enabled` *(post-#361)* | Redis hot-flag, read every turn | on-call (manual) + App Insights staleness rule (auto-disable) | `0` (off until #361 §6.2 promotion gate clears) | on-call + automation | < 5 ms | `redis-cli DEL cache:rag_enabled` (then `SET 1` to re-enable after fix) |
| `cache:embed_enabled` *(post-#361)* | Redis hot-flag, read every turn | on-call | `1` | on-call | < 5 ms | `redis-cli SET cache:embed_enabled 0` |
| `curriculum:version` *(post-#361)* | Redis string, read at process start + on every cache lookup | release engineer (atomic write during syllabus deploy) | `"2026.05"` | release engineer | < 5 ms | `redis-cli SET curriculum:version <previous>` |
| `FAST_MODE_AB_1B_TRAFFIC_PCT` *(post-#361)* | env var read at process start (overridable via Redis `chat:fast_mode_ab_pct`) | A/B owner | `0` (ramps `0 → 10 → 25 → 50`) | A/B owner | next deploy or sub-ms via Redis | `redis-cli SET chat:fast_mode_ab_pct 0` |
| `MAX_HISTORY_TURNS` *(post-#361)* | env var read at process start (overridable via Redis) | on-call | `12` | on-call | next deploy or sub-ms via Redis | `redis-cli SET chat:max_history_turns 12` |
| `MAX_CHUNK_TOKENS` *(post-#361)* | env var read at process start (overridable via Redis) | on-call | `512` | on-call | next deploy or sub-ms via Redis | `redis-cli SET rag:max_chunk_tokens 512` |
| `LLM_TURN_TIMEOUT_S` *(post-#361)* | env var read at process start (overridable via Redis) | on-call | `15` | on-call | next deploy or sub-ms via Redis | `redis-cli SET chat:llm_turn_timeout_s 15` |
| `recall_intent:tier1_phrases` *(post-#362)* | Redis JSON list, read at process start + on every turn | on-call (manual edit via `redis-cli SET`) | seed list per #362 §1.2 | on-call | < 5 ms | `redis-cli SET recall_intent:tier1_phrases '<previous-json>'` |
| `recall_intent:tier2_tokens` *(post-#362)* | Redis JSON list, read at process start + on every turn | on-call | seed list per #362 §1.2 | on-call | < 5 ms | `redis-cli SET recall_intent:tier2_tokens '<previous-json>'` |
| `session:fallback:{id}` *(post-#362)* | Redis string, read every turn (per-session, before global `chat:fallback`) | dispatcher (auto on K=3 consecutive turns > 2.4s TTFB) | unset (= use global chain) | dispatcher / on-call | < 5 ms | `redis-cli DEL session:fallback:{id}` |
| `session:fallback:disabled` *(post-#362)* | Redis string, read every turn | anti-thundering-herd job (auto on > 5%/5min trip rate) | `0` | background job / on-call | < 5 ms | `redis-cli DEL session:fallback:disabled` |
| `session:ttfb:{id}` *(post-#362)* | Redis hash, read+write every turn (per-session) | dispatcher | unset on session start | dispatcher | < 5 ms | `redis-cli DEL session:ttfb:{id}` |
| `moderation:rephrase_hints` *(post-#362)* | Redis JSON object, read at process start (refreshed every 5 min) | on-call | seeded per #362 §4.4 | on-call | up to 5 min (acceptable — non-safety-critical) | `redis-cli SET moderation:rephrase_hints '<previous-json>'` |
| `moderation:hard_floors_test_mode` *(post-#362)* | env var read at process start | infra owner | unset (= disabled) | infra owner | next deploy | redeploy without the env var |
| `user_profile.moderation_mode` *(post-#362)* | Mongo document, read every turn from `user_profile` (cached on FastAPI request scope) | user (UI setting) / admin override | `"default"` | user / admin | next turn (no cache TTL) | revert via UI; admin override via Mongo update + audit-log entry |

---

## §7 — Moderation fail-closed / fail-open rule

- **Live chat path (`moderation_default`):** **fail-open** if both
  Llama Guard and Azure AI Content Safety error in the same turn — the
  alternative (blocking the response) is worse UX than letting an
  un-vetted reply through given Llama Guard's 99%+ availability.
  Failures are logged; a sustained fail-open rate > 0.5% over 5 min
  triggers an alert in `#syrabit-oncall`.
- **`exam_model_paper` flow (`moderation_exam_paper`):** **fail-closed**
  — if any moderation tier errors, the content is held for human review.
  This flow is async-batch so the latency cost is acceptable.

---

## §8 — Latency Rule notes (per-rule operational record)

### Rule 1 — region anchor

**Decision:** Pin **Mongo + ACA → Pinecone's anchor (`aws-us-west-2`)**.
Pinecone re-embed of the entire `syrabit-rag` corpus is the more
expensive side to relocate.

### Rule 8 — ACA `minReplicas: 1` in prod

- **(i) Recurring cost.** ~$25/mo per ACA app (Python FastAPI + Rust
  core) at the smallest paid SKU; ~$50/mo combined. Includes the
  always-on memory + vCPU reservation that eliminates cold-starts.
- **(ii) Credit bridge.** $200 Azure free-account signup credit funds
  days 1–30; Microsoft for Startups Azure credits ($5k pool) carry from
  day 31 onward.
- **(iii) Day-25 credit-bridge checkpoint.** Owner verifies the
  Microsoft for Startups application is approved and Azure credits are
  live before the signup credit lapses on day 30. Calendar reminder set
  for day 25.
- **(iv) Three fallback exit options if startup credits are NOT live by
  day 25:**
  1. **Commit to ACA pay-as-you-go** — accept $50/mo cash burn and
     keep both apps at `minReplicas: 1`.
  2. **Drop `rust-core` to `minReplicas: 0`** — halves overage to
     $25/mo; async batch tolerates cold-start.
  3. **Drop both to `minReplicas: 0`** — eliminates overage; accept
     the cold-start TTFB hit on the live chat path until credits land.

  Implementation of the checkpoint and all three exit options lives in
  #360 Step 8.

### Rule 10 — pooled HTTP clients

| client | max_connections | max_keepalive_connections | keepalive_expiry_s | notes |
|---|---:|---:|---:|---|
| Mongo | 50 | 50 | 300 | `maxPoolSize ≥ 50` |
| Pinecone | 32 | 16 | 60 | HTTP/2 |
| Cohere | 16 | 8 | 60 | async-batch only |
| AI Gateway (Cloudflare) | 64 | 32 | 60 | HTTP/2 |
| Azure OpenAI direct | 32 | 16 | 60 | |
| Vertex direct (rollback) | 16 | 8 | 60 | |
| Razorpay | 8 | 4 | 60 | |
| SendGrid | 16 | 8 | 60 | |

All clients are process-singleton, attached to FastAPI lifespan.

### Rule 12 — Vertex validation sampling rate

`VALIDATION_SAMPLE_RATE = 0.10` (10% of completed turns). Configurable
via env var or Redis `validation:sample_rate` (sub-ms override path).

---

## §9 — Escalation path

1. **Auto-flip fires** → `#syrabit-oncall` Slack.
2. **On-call ack** within 5 min (business hours) / 15 min (out-of-hours).
3. **If unable to triage in 30 min:** page founder via PagerDuty
   (founder@syrabit.ai phone number — also stored in Azure Key Vault
   `oncall-contact`).
4. **If incident persists > 1 h:** post status update to
   `status.syrabit.ai`.
5. **Post-incident:** write a 1-page postmortem in
   `docs/postmortems/YYYY-MM-DD-<slug>.md` within 48 h.

---

## §10 — Rollback procedures

### Rollback `CHAT_FALLBACK` flip

```
# Verify current state
redis-cli GET chat:fallback
redis-cli GET chat:fallback:pin

# If pinned, unpin first
redis-cli DEL chat:fallback:pin

# Clear the flag
redis-cli DEL chat:fallback
```

### Rollback `EMAIL_FALLBACK` flip

```
redis-cli DEL email:fallback
```

### Rollback ACA revision

```
az containerapp revision list \
  --name syrabit-fastapi --resource-group syrabit-prod -o table

az containerapp revision activate \
  --name syrabit-fastapi --resource-group syrabit-prod \
  --revision <previous-revision-name>
```

### Rollback validation sampling rate

```
# Live override (sub-ms)
redis-cli SET validation:sample_rate 0.10

# Or revert env var via ACA revision rollback (above)
```

---

## §11 — Locked decisions (cross-reference)

- **Razorpay scope = INR-only** (locked, see #347).
- **Fast-mode primary = `@cf/meta-llama/Llama-3.2-3B-Instruct`** (locked,
  see #347).
- **Rust core = separate Azure Container App** (confirmed, see #347 +
  cloud-delegation map).
- **SendGrid = Pro 100k via Azure Marketplace + Essentials Free
  fallback** (day-0, see §3 of `infra/per-cloud-feature-delegation.md`).
  Mandatory migration to a paid Pro tier funded outside the credit pool
  before the Microsoft for Startups Azure credit window lapses;
  rollback-to-Essentials-Free path documented in §6 above.
