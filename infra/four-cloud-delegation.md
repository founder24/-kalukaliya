# Four-Cloud Delegation Matrix (locked)

> **Status: LOCKED — 2026-05-06** (Task #489)
> **Owner:** founder@syrabit.ai
> **Source of truth:** [`infra/v4-locked-architecture.md`](./v4-locked-architecture.md) §0, §1, §3, §6, §7, §10, §11 + the §15 amendment added by sibling tasks #490 (Vertex retirement), #491 (Cerebras / Cohere / Voyage purge), #492 (Sarvam scope-down).
> **Companion runbooks:** [`artifacts/syrabit/docs/infra/azure-landing-zone.md`](../artifacts/syrabit/docs/infra/azure-landing-zone.md), [`artifacts/syrabit/docs/infra/aws-landing-zone.md`](../artifacts/syrabit/docs/infra/aws-landing-zone.md), [`artifacts/syrabit/docs/infra/gcp-landing-zone.md`](../artifacts/syrabit/docs/infra/gcp-landing-zone.md).

This document is the **single canonical map** of which cloud owns which production responsibility. Every PR that touches infra MUST cite this matrix (in addition to V4) and may not introduce a row that violates the "must NOT do" block per provider. The CI drift guard `.github/workflows/four-cloud-delegation-drift.yml` enforces the negative space mechanically.

## §A — Delegation matrix

| Responsibility | Provider (sole owner) | V4 ref | Notes |
|---|---|---|---|
| DNS apex (`syrabit.ai`, `api.syrabit.ai`, `embed.syrabit.ai`) | **Cloudflare** | §0, §11 | Pages + Workers routes; SPF/DKIM/DMARC for `em.syrabit.ai` published here too. |
| Frontend SSR (`syrabit.ai`, `chat.syrabit.ai`) | **Cloudflare Pages** (`syrabitfrontend`) | §0 | Build of `artifacts/syrabit/dist`, prod branch `main`. |
| Edge worker (WAF, OriginGate, AI dispatch, edge cache, KV usage aggregator) | **Cloudflare Workers** (`syrabitworker`) | §0, §6 | Sole carrier of the `X-Origin-Auth: $BACKEND_ORIGIN_SECRET` header — backend rejects everything else with 403 (replit.md gotcha). |
| Embed-worker primary (EmbeddingGemma-300M + Qwen3-0.6B → 1024-dim → Pinecone) | **Cloudflare Workers AI** (`embed.syrabit.ai`) | §2 | Mean-pool, dimension-locked to Pinecone index. |
| **Embed-failover behaviour** | **None — cache-only degraded mode** | §15 (amendment) | Per Option D: on `EMBED_DEGRADED_MODE=true` no third-party embedder is invoked; new chunks enqueue to `syrabit-reembed-queue` (deferred-embed replay) and serve Vectorize cache hits only. **Vertex multilingual embedding is NOT a fallback** (retired by #490). |
| Indic translation primary | **Cloudflare Workers AI IndicTrans2** | §0 | Backed by Sarvam Indic chat for Assamese (§4). |
| Edge AI dispatch / BYOK gateway | **Cloudflare AI Gateway** | §0, §1 | Slugs: `azure-openai` and `workers-ai` only. The `cerebras` / `cohere` / `voyage_ai` slugs are retired by sibling task #491 — drift guard blocks any **new** import of them. |
| R2 buckets (chapter PDFs, audio, exports, **final backups**) | **Cloudflare R2** | §11 | S3 ships **temp dumps**; nightly EventBridge sync moves finals → R2. |
| KV (chapter index, syllabus map, flags, allowlists) | **Cloudflare KV** | §0 | Includes `__kv_usage:CF_EDGE_CACHE` aggregator (Task #454). |
| D1 (SEO meta, audit logs, syllabus-map read-before-Mongo) | **Cloudflare D1** | §11 | Lag watchdog `routes/admin_d1_mirror_lag_alerts.py` (Task #460). |
| Vectorize (edge RAG cache) | **Cloudflare Vectorize** | §11 | Never primary store. |
| HTTP API origin (FastAPI hot path) | **Azure Container Apps** `syrabit-backend` (`eastus2`) | §0, §4 | Explicit accepted SPOF (§8). `westus3` Bicep cutover runbook is the only DR path. |
| Rust async-batch worker | **Azure Container Apps** `rust-core` | §0 | Co-resident with FastAPI tier. |
| Chat-primary LLM | **Azure OpenAI `gpt-4.1-nano`** (`eastus2`) | §4, §4 SKU table | SOLE primary, no Vertex on the chat hot path. Operator override: `AZURE_OPENAI_MODEL_OVERRIDE=gpt-4.1-mini`. |
| Chat fallback chain (post-Azure exhaust) | **Cloudflare Workers AI** Mistral-7B → Llama-3.2-3B → generic | §4 (A9) | Edge-local; never reaches Cerebras / Cohere / Vertex. |
| Moderation primary | **Llama-Guard-2 self-hosted** on Azure ACA | §1, §4 | Fail-open on transient 5xx, fail-closed on >5 s timeout. |
| Moderation secondary | **Azure AI Content Safety** | §4 | Runs in parallel. |
| Content-formatter (long-form English + Assamese, **off the chat hot path**) | **Vertex Gemini** (content pool fallback only — sibling task #494 owns wiring) | §4, §15 | **#489 RETIRES Vertex from the post-cleanup surface.** This row is documentary only — the `aiplatform.googleapis.com` API and `roles/aiplatform.user` IAM binding are intentionally NOT in `artifacts/syrabit/infra/gcp/` (drift guard `four-cloud-delegation-drift.sh` forbids them landing here). Sibling task #494 will introduce both behind its own TF module when (and only when) the formatter pool is re-enabled. Until then: zero Vertex API enablement on the GCP project. |
| Batch RAI review (`exam_model_paper`) | **Vertex Gemini RAI** (async only — sibling task #494 owns wiring) | §1, §4 | Same retire-now-reintroduce-via-#494 rule. Never per-turn synchronous when re-enabled. |
| Primary transactional email | **Azure Marketplace SendGrid** | §0, §10 | `EMAIL_PROVIDER=sendgrid`. |
| Fallback transactional email | **AWS SES** (`us-east-1`) | §0, §10 | `EMAIL_FALLBACK=ses`, activated when SendGrid burn-threshold tripped (§10 Rule C). DKIM/SPF/DMARC published on Cloudflare DNS for both. |
| Secrets source-of-truth | **Azure Key Vault** (`syrabit-prod-kv`) | §6 | AWS Secrets Manager + Cloudflare Secrets are read-only replicas, hash-validated nightly. |
| Async event backbone (SQS + Lambda + EventBridge + Step Functions) | **AWS** (`ap-south-1`) | §0, §3 | All consumers ported off Cloud Tasks (Task #332); Cloud Tasks / Cloud Scheduler client modules deleted by Task #489. |
| Deferred-embed replay queue | **AWS SQS `syrabit-reembed-queue` + Lambda** | §3, §15 | Repurposed from "drain fallback-Vertex namespace" to "deferred-embed replay": on `EMBED_DEGRADED_MODE=true` new chunks enqueue here; on reset, Lambda replays each against `embed.syrabit.ai`, writes to `cached_gemma_today`, deletes message only on confirmed write. CloudWatch alarms on DLQ depth + queue depth + queue age. |
| S3 (temp dumps + intermediate exports) | **AWS S3** | §11 | Nightly EventBridge → S3-to-R2 sync Lambda promotes finals to R2. |
| User-data store (conversations, profiles, chunk metadata, study artifacts) | **MongoDB Atlas** (`ap-south-1`, AWS-peered) | §1, §11, §13 | Pinecone IDs only; never recomputes embeddings from chunk text. |
| Vector store (primary `cached_gemma_today` + drained `fallback_vertex_pending_reembed`) | **Pinecone** (`aws-ap-south-1`) | §1, §3 | Fallback namespace stays on disk for historical reads but is no longer written to (§15). |
| STT / TTS / Indic speech | **Deepgram / ElevenLabs / Sarvam-AI** | §1 | Provider-default regions; no re-host on Azure / AWS Speech Services. Sarvam is **Assamese chat LLM only** post-#492 (no TTS / translate / transliterate / status). |
| Indic→English translation fallback | **Azure Translator** (`eastus2`) | §1 | Quota-bounded; reached only after IndicTrans2 + Workers-AI translation. |
| External-fetch URL safety (educational reader) | **Google Web Risk API** | §0 (this row is the §1 row formerly under "Vertex" GCP block) | Invoked from `edu_reader.fetch_and_extract` against the **post-redirect final URL**, not just the caller-supplied URL (closes the publisher-policy threat in `threat_model.md`). Failures sink to `log_blocked_request` with reason `web_risk_<threats>`. |
| OAuth / GA4 / GSC + Indexing API / Books / Knowledge Graph / Fact Check / NLP | **GCP API-key surfaces** (no service account) | §0 (auxiliary) | All API-key-only; SA never touched. |
| Discovery Engine ingest | **GCP Discovery Engine** (SA-gated) | §0 (auxiliary) | One of the few SA-gated reads we keep; **not** a queue or scheduler. |
| Cost / credit-burn telemetry → Meter A/B/C | **GCP Cloud Billing** + **Cloudflare AIG cost headers** + **AWS Cost Explorer** | §10 Rule C | Notify-only Slack alert in `#syrabit-oncall` at 80 % of credit pool. **No auto-flip.** Post-cleanup pool reflects the dropped Vertex / Cerebras / Cohere / Voyage credit rows. |
| End-to-end tracing | **Sentry Performance** (primary) → **GCP Cloud Trace** (long-retention backstop) | §7, §12 | `sentry-trace` + `traceparent` + `baggage` propagated CF Worker → Azure ACA → AWS Lambda → Pinecone / Mongo / Vertex. CI canary asserts round-trip. |

## §B — What each cloud must NOT do

These rows define the negative space the CI drift guard enforces.

### Cloudflare must NOT

- Host long-running stateful jobs that need >30 s of CPU per request (Workers / Pages SSR are short-lived).
- Hold the secrets source-of-truth (Cloudflare Secrets are read-only mirrors, AKV writes first — §6).
- Run the FastAPI HTTP origin (it stays on Azure ACA).

### Azure must NOT

- Host async event-backbone workloads (SQS-equivalent / EventBridge-equivalent / Step-Functions-equivalent). These belong on AWS (§0, §3).
- Become a third-party embedding provider (the sole embedding path is Cloudflare Workers AI → Pinecone — §2 + §15 cache-only fallback).
- Be the sole region for chat (the `eastus2 → westus3` Bicep cutover runbook is the only DR path; no parallel hot region).

### AWS must NOT

- Host the FastAPI HTTP origin (no ECS service for the API tier, no App Runner, no Elastic Beanstalk for `syrabit-backend`).
- Hold the production DNS apex (Cloudflare keeps the apex and `api.syrabit.ai`).
- Become primary transactional email (SES is the SendGrid fallback only, activated by burn-threshold per §10 Rule C).
- Hold the secrets source-of-truth (AWS Secrets Manager is a read-only mirror — §6).

### GCP / Vertex must NOT

- Host **anything**: no Cloud Run, no Cloud Tasks, no Cloud Scheduler, no Compute, no GKE, no GCS hosting buckets, no Cloud Build, no Cloud Functions for app code (matches `artifacts/syrabit/infra/gcp/README.md`). Hosting / cron / CI / queueing belongs to Azure (compute) + AWS (events) + Cloudflare (edge).
- Sit on the chat hot path. Vertex Gemini is `content`-pool fallback + safety/validation + batch RAI only (§4 founder-locked 2026-05-06).
- Be a Pinecone-writing embedder. Vertex multilingual embedding is retired by #490; the **only** embedder is Cloudflare Workers AI EmbeddingGemma + Qwen3 (§2).
- Be the secrets source-of-truth or hold the `users` collection (§6, §13).

### Cross-cutting "no" list (CI drift guard `four-cloud-delegation-drift.yml`)

A PR fails the gate if any of the following appear in `infra/**/*.tf`, `artifacts/syrabit/infra/**/*.tf`, `artifacts/syrabit-backend/**/*.py`, or `workers/**/*.ts`:

- `google_cloud_run_*`, `google_cloud_tasks_*`, `google_cloud_scheduler_*`, `google_compute_*`, `google_container_cluster`, `google_cloudbuild_*`, `google_cloudfunctions_*`, `google_artifact_registry_*`, `google_storage_bucket` with a `website {…}` block (static-site hosting on GCS forbidden — Pages owns SSR per §0).
- IAM bindings to `roles/run.*`, `roles/cloudtasks.*`, `roles/cloudscheduler.*`, `roles/cloudbuild.*`, `roles/cloudfunctions.*` (re-introduces a deleted attack surface).
- `aws_apprunner_*`, `aws_elastic_beanstalk_*`, or `resource "aws_ecs_service" "syrabit_(backend|api)"` / `"fastapi"` (FastAPI lives on Azure ACA — V4 §0).
- New Python or TS modules importing `cloud_tasks_client` / `cloud_scheduler_client` (deleted by Task #489).
- New imports / SDK references for **Vertex chat** (`vertexai.generative_models` outside the content-formatter module added by #494), **Vertex multilingual embedding** (`text-multilingual-embedding-*`), **Vertex Vector Search** (`vertexai.matching_engine` / `MatchingEngineIndex`), **Cerebras** as a chat-primary in `routes/`/`llm.py`, **Cohere** chat/embed/rerank (allow-listed only for the pre-existing `providers/cohere.py` until #491 deletes it), or **Voyage AI** (allow-listed only for the pre-existing `providers/voyage_ai.py` until #491 deletes it).

## §C — Operator acceptance checklist

Each row of §A has a one-shot proof an operator can run. Together they prove the matrix is enforced, not aspirational.

### C1. OriginGate (Cloudflare → Azure boundary)

```bash
# Direct ACA hit without the worker-injected header MUST 403 on every gated path.
curl -fsS -o /dev/null -w '%{http_code}\n' \
  https://syrabit-backend.lemonstone-ce3c87e1.eastus.azurecontainerapps.io/api/auth/me
# expected: 403  ("Direct origin access denied")

# Same path through the worker MUST NOT 403 (will return 401 absent JWT — that's fine).
curl -fsS -o /dev/null -w '%{http_code}\n' https://api.syrabit.ai/api/auth/me
# expected: 401 (NOT 403)
```

### C2. SES fallback (only on burn-threshold)

```bash
# Force-flip and verify the in-process Tier-2 selects SES (not SendGrid).
EMAIL_PROVIDER=ses python -c "
import email_templates as e
assert e._tier2_provider() == 'ses', 'Tier-2 must select SES when EMAIL_PROVIDER=ses'
print('OK: Tier-2 routes to SES')
"

# CloudWatch alarm coverage on the email-fallback queue:
aws sqs get-queue-attributes --queue-url $(aws sqs get-queue-url \
  --queue-name syrabit-email-fallback --query 'QueueUrl' --output text) \
  --attribute-names ApproximateNumberOfMessages
# expected: 0 in steady state; non-zero after a deliberate burn-threshold trip.
```

### C3. Web Risk on `edu_browser` (across redirects)

```bash
# A known-malicious test URL on the allowlisted domain MUST be blocked
# AFTER redirect resolution, not just at the caller-supplied URL.
curl -fsS -X POST https://api.syrabit.ai/api/edu/reader/fetch \
  -H 'Authorization: Bearer <staff JWT>' \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com/redirect-to-malware"}'
# expected: {"ok": false, "error": "web_risk_blocked", "detail": "MALWARE", ...}
```

### C4. Cloud Trace canary

```bash
# Send a chat turn with a known traceparent; assert the same trace ID
# appears in Cloud Trace within 60 s.
TRACEPARENT="00-$(openssl rand -hex 16)-$(openssl rand -hex 8)-01"
curl -fsS https://api.syrabit.ai/api/chat \
  -H "traceparent: ${TRACEPARENT}" \
  -d '{"message":"canary"}' >/dev/null
sleep 60
gcloud trace traces list --filter="trace_id=${TRACEPARENT:3:32}" --limit=1
# expected: one trace with spans from CF Worker → ACA → Lambda.
```

### C5. Deferred-embed replay (cache-only degraded mode)

```bash
# Trip the flag, write a chunk, verify it lands in SQS not Pinecone.
curl -fsS -X POST https://api.syrabit.ai/admin/embed/degraded-mode \
  -H 'Authorization: Bearer <admin JWT>' -d '{"enabled": true}'
# Submit a chunk that would normally embed:
curl -fsS -X POST https://api.syrabit.ai/admin/embed/test-chunk \
  -H 'Authorization: Bearer <admin JWT>' -d '{"text": "deferred-embed canary"}'
# Assert it's in the SQS queue and NOT in Pinecone:
aws sqs get-queue-attributes --queue-url $(aws sqs get-queue-url \
  --queue-name syrabit-reembed-queue --query QueueUrl --output text) \
  --attribute-names ApproximateNumberOfMessages
# expected: >= 1

# Reset and confirm drain:
curl -fsS -X POST https://api.syrabit.ai/admin/embed/degraded-mode \
  -H 'Authorization: Bearer <admin JWT>' -d '{"enabled": false}'
sleep 30
aws sqs get-queue-attributes --queue-url ... --attribute-names ApproximateNumberOfMessages
# expected: 0 (Lambda drained the queue, wrote to cached_gemma_today).
```

### C6. Drift guard

```bash
# Local invocation of the CI drift guard:
.github/scripts/four_cloud_delegation_drift.sh
# expected: "OK: no four-cloud delegation drift found"
```

## §D — Deferred / explicitly-not-built items

The matrix flags the following as deliberate gaps, not oversights. Each
gets its own follow-up task if/when revisited. The first five rows are
the **explicitly-deferred** items called out by Task #489's acceptance
prompt — they were carved out of #489 to keep the lock-in PR shippable
and each will be a separate follow-up task.

| Item | Status | Why |
|---|---|---|
| **Deferred-embed reembed Lambda** (`sqs_consumers.reembed.handler`) | **LANDED in #489.** | Queue + DLQ + 3 alarms in `artifacts/syrabit/infra/aws/sqs-reembed.tf`; consumer Python in `artifacts/syrabit/services/backend/sqs_consumers/reembed.py` (Workers-AI embed → Pinecone upsert, deletes message only on confirmed write). |
| **OTEL → Cloud Trace exporter wiring** (Bicep) | **LANDED in #489.** | ACA Bicep adds `OTEL_TRACES_EXPORTER=googlecloud,sentry` + `OTEL_EXPORTER_GCP_PROJECT_ID=syrabit-prod` + `OTEL_SERVICE_NAME` (`infra/azure/aca-syrabit-backend.bicep`). GCP IAM `roles/cloudtrace.agent` already bound (`artifacts/syrabit/infra/gcp/iam.tf`). The matching AWS Lambda exporter layer (`infra/aws/lambda-otel.tf`) and the cross-cloud canary workflow stay deferred to a follow-up. |
| **S3 → R2 nightly EventBridge sync** | **LANDED in #489.** | EventBridge Scheduler + Lambda + IAM + Errors alarm in `artifacts/syrabit/infra/aws/s3-to-r2-sync.tf`; Python promoter in `artifacts/syrabit/services/backend/s3_to_r2_sync.py` (verified-write-then-delete). Runs at 02:11 UTC nightly. |
| **SES burn-threshold smoke runbook** | **LANDED in #489.** | Runnable `scripts/ses_burn_smoke.sh` flips ACA `EMAIL_PROVIDER=ses`, fires the diagnostics email, watches the SES `Send` CloudWatch metric, and restores the original provider on exit. Logs to `docs/ops/dr-drills/`. |
| **Cloud Billing → Meter A/B/C runtime hookup** | **LANDED in #489.** | `routes/admin_billing.py` exposes `POST /api/admin/billing/cloud-billing-alert` — the GCP Pub/Sub push subscription posts here with an OIDC bearer (audience pinned to `BILLING_WEBHOOK_AUDIENCE`); the handler decodes the budget envelope and dispatches into `credit_burn_meter_runtime._ALERT_SINK` so SES + Slack reuse the meter alert wiring (V4 §10 Rule C, notify-only — no auto-flip). |
| **AWS Lambda OTEL exporter + cross-cloud trace canary workflow** | **LANDED in #489.** | Both new image-based Lambdas (`reembed_consumer`, `s3_to_r2_sync`) merge `local.otel_env` from `lambda-otel.tf` with per-function `OTEL_SERVICE_NAME`; ADOT collector is already baked into the worker image. Canary at `.github/workflows/cross-cloud-trace-canary.yml` fires every 6 h: generates a `traceparent`, hits the ACA fanout endpoint, then asserts via Sentry `events-trace` that ACA + AWS spans share the trace-id. |
| **Gemini RAI batch reviewer** for `exam_model_paper` | **Deferred** — not implemented despite §1/§4 naming it. | No published SLA against Vertex RAI batch quotas yet. File a separate task before implementing; do NOT slip it into this lock-in. |
| **AWS Bedrock Cohere SKU** (`bedrock_cohere`) | **Retired** by #491. | Was an embed-failover candidate; superseded by cache-only degraded mode (§15). |
| **Vertex Vector Search retriever** | **Retired** by #490. | Pinecone Rerank v0 + RRF fusion in `rag.py` cover the same use case at lower cost. |
| **`westus3` standby ACA** | **Deferred** — Bicep cutover runbook is the only DR path. | Hot-standby would double Azure spend; current `RTO=4h` accepts the cold cutover. |
| **Supabase → Mongo OAuth handler** (V4 §13 hard blocker) | **Separate task.** | Cannot start until Mongo is read-of-record (§13 Phase 4). |

---

> Any PR proposing a row-change must (a) cite the V4 section it amends, (b) update both this matrix and `replit.md`'s "Architecture decisions" pointer, and (c) leave the CI drift guard green. **Drift introduced silently is treated as a regression and reverted.**

---

## §E — Cost minimization for browser-heavy traffic (Task #513, 2026-05-07)

The four-cloud lock holds steady (no new providers; no budget shifts inside the 40/30/20/10 split). The additions below tighten the spend per-call and per-anon without renegotiating the matrix.

### E1. Edge-layer caps (Cloudflare)

- **Chat cap:** `workers/edge-proxy/src/index.ts` short-circuits `/api/ai/chat` and `/api/chat` at the edge — `30 / month + 3 / day` per `x-anon-id` (falls back to `clientIp` when the SPA hasn't issued an anon-id). 429 carries `X-Cap: chat_monthly_30_per_anon` or `X-Cap: chat_daily_3_per_anon` so dashboards can split denials by reason. Counters live in the existing `RATE_LIMIT` KV namespace; keys auto-expire on the natural window.
- **Smoke probe:** `artifacts/syrabit-backend/scripts/smoke_chat_cap.py` (CI-runnable, idempotent) hammers `/api/ai/chat` 35× with a stable anon-id and asserts both caps fire.

### E2. Backend dispatch (Azure ACA)

- **Token budgets:** `artifacts/syrabit-backend/cost_caps.py` owns the locked `TOKEN_BUDGETS` table — chat 3000/800, content 4000/2000, formatter 4500/2500, translate 2000/2000, vision OCR 1500/800, STT 2000/500. Every dispatcher (`llm.py`, `pipeline.py`, `content_formatter.py`, `providers/chunk_embedder.py`, `routes/voice.py`) imports the module; `tests/test_cost_caps.py` walks the source files and fails CI when a dispatcher forgets the wiring or when a budget is bumped without a `# COST-CAP-OVERRIDE: <reason>` comment.
- **Tier-routing:** `cost_caps._select_chat_model(...)` is the single chokepoint for English-chat model selection. Free user turns 1-2 → Workers-AI Mistral-7B; turns 3-15 → Azure `gpt-4.1-nano`; turn >15 → same primary clamped to a 600-token output ceiling. Paid plans bypass to full budget. Assamese always routes to Sarvam (specialist credit pool drains first).
- **Right-sized SKU:** `infra/azure/aca-syrabit-backend.bicep` shrinks each pod to **0.25 vCPU / 0.5 GiB**, raises `maxReplicas` 10 → 30, and tightens `concurrentRequests` 50 → 30. Net peak concurrency 900 (matches the chat-cap headroom); idle baseline drops ~75 %.
- **Credit-drain assertion:** `tests/test_credit_drain_order.py` freezes the per-pool provider order — Sarvam before Workers-AI Indic, Vertex before Workers-AI llama33_70b, `workers_ai_custom` first in `embed`. Retired providers (Cerebras / Cohere / Voyage / Bedrock) must never re-appear in any pool.

### E3. Rule D — global monthly USD cap

- **Meter D** (`credit_burn_meter.MeterD`) tracks calendar-month USD spend. Notify at 80 % of `MONTHLY_TOTAL_USD_CAP` (env, default `$100` per Task #549 — perpetual $100/mo at 10k DAU); LOCK at 100 % by setting `chat:cheaponly=1` in Redis. `_select_chat_model` reads the flag on every dispatch and clamps to Workers-AI Mistral-7B until `chat:cheaponly:pin` is cleared at 00:00 UTC on the 1st of the next month. Alert sink reuses the Meter A/B/C pager wiring (no auto-flip without an alert). The three-stage degradation ladder (`cost_caps.DEGRADATION_PCT_*`) sheds non-essential async batch at 60 %, disables voice for free users + doubles cache TTLs at 80 %, and 503s free-user chat + disables new free signups at 95 %.

### E4. Optimizations (K-series)

- **K.2 — Deterministic-input AI cache** (`artifacts/syrabit-backend/ai_input_cache.py`): in-process LRU + Redis-backed completion cache keyed on `sha256(model | max_tokens | canonical_json(messages))`. Opt-in via `is_deterministic(...)` — never serves a cached response across users for streaming or temperature>0 calls. Wired on the admin chapter pre-gen pipeline and `content_formatter` polish path.
- **K.3 — Embed/formatter batching** (`artifacts/syrabit-backend/ai_batch_queue.py`): generic `AsyncBatcher(flush_size, flush_window_ms, flush_fn)` coalesces concurrent submissions into one upstream call. Cuts Workers-AI request count ~50× during bulk re-embed (`providers/chunk_embedder._BATCH_SIZE = 32` (Task #513 §K.3 — locked at 32, was 48; raising requires Sentry-annotated changelog)).
- **Off in production:** all "nice-to-have" AI experiments (background pre-fetch, speculative completion, multi-model voting, full-history re-embed on sign-in) are gated by `ENABLE_AI_EXPERIMENTS`. Default OFF in production; flip via the admin runtime flags route only — never via Bicep.

### E5. K.1 — Model-optimization eval

Tracked separately. The eval harness (Workers-AI Mistral-7B vs. Azure `gpt-4.1-nano` on the synthetic Syrabit chat traffic mix) is a follow-up item — it depends on the real cap-shaped traffic this task introduces. Current `_select_chat_model` rules are the founder-locked starting point; eval results refine the SESSION_CHEAP_TURN_LIMIT / CONSERVATIVE_OUTPUT_TOKENS thresholds in a future PR.

---

## §F — AWS utilization expansion (Task #551, 2026-05-07)

The four-cloud lock holds (no new providers; no shift inside the 40/30/20/10 split). This addendum expands the AWS row of §A without breaching any "must NOT" rule from §B.

### F1. S3 Glacier Deep Archive — cold compliance (§A row "Object storage (cold compliance)")

- Three buckets land in `infra/aws/glacier-archive.tf`: `syrabit-razorpay-receipts-prod` (90 d → DA), `syrabit-content-snapshots-prod` (180 d → DA), `syrabit-cw-logs-archive-prod` (30 d → DA). All three expire at 7 years (DPDP + IT audit retention).
- Restores go through `POST /admin/archive/restore` (admin-only, audit-logged to `admin_archive_restore_log`); 12 h Standard SLA (~$0.02/GB) or 48 h Bulk (~$0.0025/GB). Procedure: [`glacier-restore-runbook.md`](../artifacts/syrabit/docs/infra/glacier-restore-runbook.md).
- Cost target: ≤ $1 / month at current ~60 GB cold tail. Frees ~$3-5 / mo on Cloudflare R2 by moving the never-read tail off warm storage.

### F2. ACA Jobs → Lambda + EventBridge (§A row "Scheduled batch jobs")

- The three remaining ACA Job in-process loops (`as_translation_backfill`, `embed_backfill`, `comprehend_sampler` — see `artifacts/syrabit-backend/aca_jobs/`) move to AWS Lambda with EventBridge cron triggers (`infra/aws/lambda-batch-jobs.tf`). All three sit inside the Lambda free tier (1 M req/mo + 400 k GB-s) → ~$0/mo cash.
- Migrated-jobs registry: [`infra/aws/lambda/manifest.json`](../infra/aws/lambda/manifest.json). The CI guard `artifacts/syrabit-backend/scripts/check_dead_providers.py` blocks any new ACA Job under `aca_jobs/` that lacks a manifest entry.
- Cutover protocol: 7-day shadow period with daily reconciliation; flip in-process loops OFF via `ACA_JOB_BATCHES_DISABLED=1` only after match-rate ≥ 99 % for 7 consecutive days. Rollback = unset the env var.

### F3. AWS row update (no §B "must NOT" change)

- AWS still must NOT host the FastAPI HTTP origin, hold the DNS apex, or hold the secrets source-of-truth. The Glacier + Lambda additions above are inside the existing §A "Async event backbone" and "S3 (temp dumps)" rows — they expand AWS productively without renegotiating the negative space the drift guard enforces.
- The §A row "Primary transactional email = Azure Marketplace SendGrid" / "Fallback transactional email = AWS SES" remains unchanged in this task. The SES-sole-provider migration (originally section C of #551) was carved out into a dedicated SES task and will update the §A email rows separately.

Tracked separately. The eval harness (Workers-AI Mistral-7B vs. Azure `gpt-4.1-nano` on the synthetic Syrabit chat traffic mix) is a follow-up item — it depends on the real cap-shaped traffic this task introduces. Current `_select_chat_model` rules are the founder-locked starting point; eval results refine the SESSION_CHEAP_TURN_LIMIT / CONSERVATIVE_OUTPUT_TOKENS thresholds in a future PR.
