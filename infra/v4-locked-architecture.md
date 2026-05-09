# V4 Locked Architecture — Final Multi-Cloud Configuration

> **Status: LOCKED — 2026-05-05** (last spec-clarity pass: 2026-05-07 — see §17 canonical strict specialist delegation; previous pass 2026-05-06 A1–A9)
> **Owner:** founder@syrabit.ai
> **Supersedes:** v3 (`per-cloud-feature-delegation.md`, `provider-priority-map.md`, `credit-burn-runbook.md`).
> The v3 docs remain on disk for diff/blame history but every section in
> them is overridden by this file. **If anything in v3 disagrees with V4,
> V4 wins.** Any new PR touching infra MUST cite this doc.

---

## §0 — Four-cloud delegation map (canonical, locked)

| Provider | Core role | Main workloads | Cost-share |
|---|---|---|---|
| **Cloudflare** | Edge front-end + AI dispatch + edge caching + WAF | Pages-SSR (`syrabit.ai`, `chat.syrabit.ai`); Workers-AI **EmbeddingGemma-300M** (mean-pooled to 1024-dim to match Pinecone) → `/embed`; Workers-AI Indic translation (IndicTrans2); R2 (chapter PDFs, audio, exports, backups); KV (chapter index, syllabus map, flags, allowlists); Cache-Reserve (long-TTL assets); Vectorize (edge RAG cache); D1 (SEO meta, audit logs, syllabus-map read-before-Mongo); AI Gateway (BYOK to Gemini + Azure OpenAI + Cerebras); WAF + RateLimiter DO. | **40 %** |
| **Azure** | HTTP backend + auth + AI safety | Azure Container Apps `syrabit-backend` in `eastus2` (Python FastAPI hot path) + `rust-core` (async batch); **Llama-Guard-2 self-hosted** as moderation-primary on the same ACA compute; **Azure AI Content Safety** as moderation-secondary; orchestrates Pinecone, MongoDB Atlas, Deepgram, ElevenLabs, Sarvam-AI, Azure Translator. **Azure Key Vault is the source of truth for all secrets.** *(Task #556 retired the previous Azure-Marketplace transactional email vendor — SES is now the sole transactional path; see §1 row "AWS SES" below.)* | **30 %** |
| **AWS** | Event backbone + durable data/backups + transactional email | Lambda / Step-Functions / SQS / EventBridge / CloudWatch (batch embed ops, Pinecone index maintenance, shard rebalancing, `lambda-otel`, `lambda-workers`, **Vertex re-embed queue worker**); Atlas-peered VPC connectivity (Mongo Atlas in `ap-south-1`); S3 (dumps, temp exports; final backups sync to R2); **SES sole transactional email** (`SES_REGION=us-east-1` primary / `ap-south-1` regional flip — Task #556, no fallback, no break-glass per V4 §12). | **20 %** |
| **GCP / Vertex** | Gen-AI validation + safety + observability | **Vertex Gemini 2.5 Flash** = default content-validation model + long-form `content` pool fallback (sits behind Workers-AI Mistral-7B / Llama-3.2-3B). **NOT in the chat hot path** (founder choice 2026-05-06, see §4). **Gemini RAI** = batch/async-only for `exam_model_paper` review (never blocks live chat). Web Risk API for malicious-URL checks. Cloud Trace for OTEL spans. **Vertex multilingual embedding** = embed-failover only, writes to a separate Pinecone namespace with a re-embed queue (see §3). | **10 %** |

✅ **Cost-share sum: 40 + 30 + 20 + 10 = 100 %** (single integers, no ranges).

> **Task #559 amendment (2026-05-07):** the cost-share table above is now an **informational outcome** of the per-feature canonical map in `infra/four-cloud-delegation.md` §A, **not** a routing contract. The current snapshot (post-Task #549 `$100/mo` lock + post-Task #554 Azure-OpenAI retirement) is **40 % CF / 30 % GCP / 15 % Az / 10 % AWS / 5 % other** — see §17 below for the canonical strict specialist-delegation map and the umbrella CI guard that enforces it.

---

## §1 — Specialists (locked)

| Provider | Role | Region / hosting | Notes |
|---|---|---|---|
| **Pinecone** | RAG index + rerank (Pinecone Rerank v0) | **`aws-ap-south-1`** (moved from us-east-1; co-located with Mongo Atlas) | <50 ms RAG hop budget within India region. |
| **MongoDB Atlas** | Session + profile + chunk-metadata store | `ap-south-1` AWS-peered VPC | DPDP-compliant data residency. **Stores only chunk metadata + Pinecone IDs — NEVER recomputes embeddings from chunk text.** |
| **Deepgram / ElevenLabs / Sarvam-AI** | STT / TTS / regional speech | Provider-default | Assamese / Indic voice RAG. No re-host on Azure / AWS Speech Services. |
| **Azure Translator** | Indic→English fallback translation | `eastus2` | Quota-limited fallback only (after IndicTrans2 + Workers-AI translation). |
| **AWS SES** | Sole transactional email path (Task #556 — previous Azure-Marketplace vendor retired, no fallback, no break-glass per V4 §12) | `us-east-1` primary / `ap-south-1` regional flip via `SES_REGION` | DNS ownership = `syrabit.ai` on Cloudflare DNS (SPF / DKIM / DMARC published for both SES regions). Bulk / digest fan-out goes through the separate Cloudflare Email Workers `bulk-email` Worker via `bulk_email.send_bulk` — not a transactional fallback, different surface. |
| **Cohere** *(A1, decided 2026-05-06)* | Embed-failover (V4-allowed, BYOK via CF AI Gateway slug `cohere/v1`) | Provider-default | Listed in `embed*` provider chains in `config.py` after `workers_ai_custom`. Rerank role retired in favour of Pinecone Rerank v0. **Not on the chat hot-path.** |
| **Llama-Guard-2 (self-hosted)** *(A7)* | Chat-moderation primary | Azure Container Apps `syrabit-backend` (`eastus2`), CPU pod, **min 1 / max 4** replicas, scales on `concurrent_requests > 8`. **Fail-open** for transient 5xx (logged + alert), **fail-closed** on >5 s timeout (turn rejected with retry-able 503). | Azure AI Content Safety runs in parallel as moderation-secondary (§4). |
| **Cerebras** *(A2, decided 2026-05-06)* | Chat fallback (V4-allowed, **CF-Gateway-only** path) | BYOK via CF AI Gateway slug `cerebras` | Re-instated by Task #420 to give the on-pod cache-hit-ratio counter a Cerebras row. Direct (non-gateway) calls remain blocked by `scripts/check_dead_providers.py`. **Never primary; never used for content-gen.** |

---

## §2 — Cloudflare embed worker = primary embedding path

- **Worker:** `artifacts/syrabit/workers/embed-worker/` (deployed at `embed.syrabit.ai`).
- **Model stack:** EmbeddingGemma-300M + Qwen3-0.6B → mean-pool hidden states → **1024-dim** vector matching the existing Pinecone index dimension.
- **Backend client:** `artifacts/syrabit-backend/providers/workers_embed.py`, primary when `EMBED_PROVIDER_PRIMARY=workers_ai_custom`.
- **Auth:** shared secret in `WORKERS_EMBED_SECRET` (Azure KV → ACA env via secretRef).
- **Health:** `GET /admin/health/embed-stack` (combined embed + rerank + memory-brain).

---

## §3 — Embed-failover topology (no silent index corruption)

When `embed.syrabit.ai` is down or returning 5xx:

1. **In-flight chat turns** serve Vectorize cache hits only — no new embeds attempted on the live path.
2. **New chunks** that need embedding are routed to `RAG_EMBEDDING_PROVIDER=fallback_vertex` → **Vertex AI multilingual embedding** → written to a **separate Pinecone namespace** `fallback_vertex_pending_reembed` (NOT the primary `cached_gemma_today` namespace).
3. **Re-embed queue:** every chunk written to the fallback namespace is enqueued in **AWS SQS `syrabit-reembed-queue`**. An AWS Lambda worker drains the queue when Cloudflare returns, re-embeds with Gemma-300M, writes to the primary namespace, and deletes the fallback-namespace entry.
4. **Config flags:**
   - `RAG_EMBEDDING_PROVIDER=cf_gemma` (default)
   - `RAG_EMBEDDING_PROVIDER=fallback_vertex` (set by health-check controller on CF outage)
   - `PINECONE_NAMESPACE_PRIMARY=cached_gemma_today`
   - `PINECONE_NAMESPACE_FALLBACK=fallback_vertex_pending_reembed`

**A4 — Failover trigger (decided 2026-05-06):**
- **Probe:** `embed.syrabit.ai/health` polled every **30 s** by an Azure ACA controller container (`embed_failover_controller.py`).
- **Trip rule:** ≥3 of last 5 probes return non-200 OR p95 latency over a 60-s rolling window > 2 000 ms → flag flips to `fallback_vertex` and emits a Sentry alert + `#syrabit-oncall` Slack note.
- **Reset rule:** ≥10 consecutive successful probes AND p95 < 500 ms over 60 s → flag flips back to `cf_gemma`. SQS drain begins immediately.
- **Manual override:** `RAG_EMBEDDING_PROVIDER_FORCE={cf_gemma|fallback_vertex|auto}` env var. `auto` (default) honours the probe; the two pinned values disable the controller and pin the route.
- **Owner:** on-call rotation; controller alerts page rather than auto-flip if the override is non-`auto`.

✅ **Trade-off explicitly accepted:** availability-OK during CF outage; correctness is "good-enough-for-now / re-embed-later"; **zero index-mix corruption** because the two embedding spaces never share a namespace.

---

## §4 — Per-turn dispatch order (chat hot path, locked — Task #554 amendment 2026-05-07)

```
English chat (3-position chain, credit-runway-aware — Task #2 2026 blueprint)
  Vertex Gemini 2.5 Flash  (drains GCP startup credits)        ← default head
    ↓ on 5xx / 429 / exhaust
  Vertex Flash-Lite        (mid-tier GCP cost cushion, shares Vertex RPM bucket)
    ↓ on 5xx / 429 / exhaust
  Workers-AI Llama-3.2-3B  (Cloudflare free tier)              ← terminal tail

  When projected GCP credit runway ≤ 90 days, the chain FLIPS:
  Workers-AI Llama-3.2-3B  →  Vertex Flash-Lite  →  Vertex Gemini 2.5 Flash
  (no silent removal — V4 §12; selector lives in
   `artifacts/syrabit-backend/cost_caps.py::_select_chat_primary`,
   60 s monotonic cache so the hot path never thrashes env / redis.)

Assamese chat (3-position chain — Task #2 2026 blueprint)
  Sarvam-M                 (Indic specialist primary)
    ↓ on 5xx / 429 / exhaust
  Vertex Gemini 2.5 Flash  (with Assamese system-prompt prefix — named LLM fallback)
    ↓ on 5xx / 429 / exhaust
  retrieval_only           (deterministic — returns top RAG snippet verbatim;
                            never crosses into a wrong-language model — V4 §12)

Voice TTS (3-position chain — Task #2 2026 blueprint)
  ElevenLabs               (canonical English-TTS primary; $5/mo Starter
                            budgeted into the $100 ceiling)
    ↓ on 5xx / 429 / exhaust
  Deepgram Aura-2          (named English-TTS fallback)
    ↓ on 5xx / 429 / exhaust
  Workers-AI               (weight-0 last-resort tail)
```

**Task #554 retirement (2026-05-07):** Azure OpenAI (chat / embed / Whisper / text-embedding-3-large) is REMOVED from the dispatch chain. `providers/azure_openai.py`, `gpt-4.1-nano`, the legacy A3 SKU table, the third-tier `Workers-AI Mistral-7B` and `Workers-AI generic gpt-oss-20b` legs are all retired from `PROVIDER_PRIORITY["english_rag_chat"]`. The surviving Azure surfaces are **Azure Speech** (TTS / STT) and **Azure Translator**, both wired through `providers/azure_speech.py` on `AZURE_SPEECH_*` / `AZURE_TRANSLATOR_*` keys. CI guard `scripts/check_dead_providers.py` bans `azure_openai|AzureOpenAI|AZURE_OPENAI_*|gpt-4.1-nano` bare-token across the tree.

**Operator override:** `CHAT_PRIMARY_OVERRIDE=vertex|workers_ai_llama32_3b` pins the head; unrecognised values are logged and ignored (no silent fallback). Runway signal sources: `CHAT_CREDIT_RUNWAY_DAYS` (cron-published) or `GCP_CREDITS_REMAINING_USD` + MeterD month-to-date burn; absence of both keeps the default chain.

- **Llama-Guard-2** runs as a pre-filter on the Azure ACA compute (moderation-primary). **Fail-open on transient 5xx, fail-closed on >5 s timeout** (see §1 row).
- **Azure AI Content Safety** runs in parallel as moderation-secondary (uses Azure Cognitive Services key — distinct from the retired Azure OpenAI tenant).
- **Vertex Gemini RAI** is batch/async-only for `content_type=exam_model_paper` — never per-turn synchronous.

**Assamese Indic path:**

```
  ├─ Sarvam Indic chat  (primary, weight 10000)
  └─ Workers-AI IndicTrans2  (fallback, weight 0; reachable only via exclusion-redraw)
```

---

## §5 — Vectorless RAG layer (new in V4, complementary to vector RAG)

Three-tier retrieval router in `artifacts/syrabit-backend/rag.py`:

1. **Tree-walk router** — if the query parses as a syllabus path (`AHSEC class 11 → Physics → Chapter 5`) or PYQ ref (`AHSEC 2023 Q4(b)`), answer directly from D1 / KV. Zero embed cost. Target: 20–30 % of all chat turns.
2. **BM25 keyword pass** — Mongo `$text` index on `chunks.text_en` and `chunks.text_as`. Fires in parallel with the vector call. Best for exact-term, formula, and verbatim-script queries (especially Assamese morphology where Gemma-300M drifts).
3. **Vector pass (existing)** — Gemma-300M → Pinecone → Pinecone Rerank v0.

Results from (1)+(2)+(3) are fused with **Reciprocal Rank Fusion (RRF)** before the rerank step. Telemetry: `rag.router.tier_hit{tier=tree|bm25|vector}` counter in Sentry/Cloud Trace; success criterion is `≥25 %` of chat turns served without an embed call **with no MRR@10 regression** on the eval set below.

**A6 — Eval set pinning (decided 2026-05-06):**
- **Path:** `artifacts/syrabit-backend/evals/rag_router_v4.jsonl`
- **Frozen at commit:** to be set on first publication of the file (placeholder `EVAL_SHA_PLACEHOLDER` — gate B2 cannot pass until this is replaced with the real SHA).
- **Refresh policy:** quarterly, with the previous JSONL retained alongside as `_q{N-1}.jsonl` for delta diff.

---

## §6 — Secrets topology (three-store sync, controlled)

- **Source of truth:** **Azure Key Vault** (`syrabit-prod-kv`). Rotated first.
  - Owns: `MONGO_URI_ATLAS`, `GCP_WEB_RISK_API_KEY`, `AWS_SES_*`, `CF_WORKER_AI_*`, `JWT_SECRET`, `ADMIN_JWT_SECRET`, `RAZORPAY_KEY_SECRET`, `WORKERS_EMBED_SECRET`, `GOOGLE_APPLICATION_CREDENTIALS_JSON`, `AZURE_SPEECH_KEY`, `AZURE_TRANSLATOR_KEY`. *(Task #554 — `AZURE_OPENAI_API_KEY` retired alongside the Azure OpenAI tenant; chat now auths via the GCP service-account JSON.)*
- **Read-only replicas:** AWS Secrets Manager + Cloudflare Secrets (Worker bindings).
- **Sync mechanism:** Terraform + GitHub Actions job (`.github/workflows/secrets-sync.yml`) runs daily and on Azure KV rotation hook:
  1. Pull from Azure KV.
  2. Push to AWS Secrets Manager (region `ap-south-1`) with same key names.
  3. Push to Cloudflare via `wrangler secret put` for each Worker.
  4. **Hash-validation step:** SHA-256 each secret value across all three stores; fail the job if any pair mismatches.
- **Rotation cadence:**
  - MongoDB URI — quarterly drill.
  - AI / email API keys — per-incident or per fabric-auth-policy expiry.
- **CI gate:** drift test runs on every PR that touches `infra/` or `.github/workflows/`.

---

## §7 — Observability: Sentry as end-to-end trace owner

- **Sentry Performance** (not error-only) is enabled for all requests touching `chat.syrabit.ai` and `api.syrabit.ai`.
- **Header propagation rule:** Cloudflare Workers emit `sentry-trace`, `traceparent`, and `baggage` on every outbound request. Azure Container Apps and AWS Lambda are configured to **read and re-emit** `traceparent` into downstream calls (Vertex AI, Pinecone, Mongo Atlas, R2/S3).
- **Result:** single trace ID flows CF Worker → Azure ACA → Lambda → Pinecone / Mongo / Vertex → back. Sentry is the true correlator, not just an error sink.
- **Backstop:** OpenTelemetry to GCP Cloud Trace remains for raw span retention beyond Sentry's 30-day window.

---

## §8 — Disaster recovery (RTO / RPO)

- **RTO = 4 hours** (relaxed from 1 h after audit — realistic for multi-cloud full restore).
- **RPO = 15 minutes** (Mongo Atlas continuous backup; Pinecone weekly snapshot to R2; D1 daily export to R2).
- **Quarterly restore drill (mandatory):**
  1. Simulate Cloudflare Workers outage → verify embed-failover to Vertex + re-embed queue drain.
  2. Simulate Azure `eastus2` regional outage → manual re-deploy to `westus3` from Bicep, restore secrets from KV geo-replica.
  3. Restore Mongo Atlas + Pinecone index from backups within 4 h SLA.
- **Azure SPOF — explicitly accepted:** "Azure `eastus2` Container Apps is a hard SPOF; full API outage during regional Azure incident until manual `westus3` re-deploy and restore-pipeline completion. No false impression of mitigation."

**A8 — DR runbook reference (decided 2026-05-06):**
- **Primary runbook:** `artifacts/syrabit/docs/infra/aca-cutover.md` (covers `eastus2 → westus3` Bicep re-deploy + KV geo-replica restore — the longest leg of the 4 h RTO).
- **Embed-failover runbook:** `artifacts/syrabit/docs/infra/aws-landing-zone.md` §"SQS re-embed queue drain" + this doc §3.
- **Drill log:** `docs/ops/dr-drills/` (one Markdown file per quarterly drill, dated `YYYY-Qn-drill.md`).
- **If any runbook above is missing or stale at drill time, RTO is downgraded to "best-effort, not contractual" until the runbook is re-published.** Honesty over false-mitigation.

---

## §9 — Latency budget (locked, India-anchored)

| Hop | Budget | Notes |
|---|---|---|
| CF Edge → Azure ACA `eastus2` | ~120 ms | Cross-Atlantic cost accepted; Azure is SOLE chat primary so this hop is on the critical path for every chat turn. |
| Azure ACA → Mongo Atlas `ap-south-1` | <10 ms | After Atlas-peered VPC. |
| Azure ACA → Pinecone `aws-ap-south-1` | <50 ms | Resolves the v3 latency conflict (Pinecone was us-east-1). |
| Workers-AI inference (Mistral-7B / Llama-3.2-3B fallback) | <300 ms | Edge-local; only on Azure-exhaust path. |
| Vertex Gemini 2.5 Flash (content pool fallback only) | <800 ms | Off the chat critical path; reached only by long-form `content` overflow. |
| **Total p95 chat turn** | **<2.5 s** | Budget for the full chat hot path including moderation. |

---

## §10 — Three independent fallback rules

- **Rule A — RPM exhaustion:** sliding-window 1-min counter per Workers-AI model exceeds quota → auto-flip `CHAT_FALLBACK=1` (Azure OpenAI takes over).
- **Rule B — Hot-path 5xx:** ≥3 consecutive 5xx from primary in 30 s → auto-flip `CHAT_FALLBACK=1`.
- **Rule C — Credit-burn:** 365-day rolling cost counter (from `cf-aig-cost` headers) crosses 80 % of provider credit pool → notify-only Slack alert in `#syrabit-oncall`. Manual decision to flip provider weights.

---

## §11 — Storage roles (clarified, no future "who was embedding what?")

- **Cloudflare D1** — SEO meta, audit logs, syllabus map (read-before-Mongo). **Retained.**
- **MongoDB Atlas** — `conversations`, `user_profile`, `chunks` (metadata + Pinecone ID only), `chat_memory_brain`. **Source of truth for user data.**
- **Pinecone** — actual embeddings (primary namespace `cached_gemma_today` + fallback namespace `fallback_vertex_pending_reembed`).
- **Cloudflare Vectorize** — edge RAG cache only; never primary store.
- **Cloudflare R2** — chapter PDFs, audio, exports, final backups.
- **AWS S3** — temp dumps, intermediate exports; nightly sync of finals → R2.

✅ **Embeddings are NEVER recomputed from Mongo content.** Mongo holds only the metadata + Pinecone ID pointer.

---

## §12 — Risks introduced by V4 (controlled, not hidden)

| Risk | Control |
|---|---|
| Three-store secrets topology (AKV + AWS SM + CF Secrets) | Terraform-CI sync + daily job + hash-validation test + documented rotation cadence (§6). |
| Embedding heterogeneity (Gemma-300M vs Vertex multilingual) | Namespace separation + cache-only during outage + re-embed queue (§3). |
| Azure `eastus2` SPOF | Explicit acceptance + quarterly drill + Bicep `westus3` re-deploy procedure (§8). |
| Sentry header drop in any hop | CI test that asserts `traceparent` round-trips end-to-end on the canary chat turn (§7). |

---

## §13 — Data migration plan: Postgres → Mongo Atlas (NEW, A5)

**Status as of 2026-05-06: NOT STARTED.** Until this completes, V4 is *aspirational* on the user-data SoT axis. The audit-found drift (`deps.py:135` "Replit PostgreSQL (asyncpg pool) — primary relational store"; `db_ops.py` full of asyncpg; `routes/edu_study.py:20` "Authenticated users → PostgreSQL") is real and lives below.

### Target end-state
- `deps.py` does not import `asyncpg`.
- `DATABASE_URL` is not in the `_ALWAYS_NEEDED` env list.
- Every authenticated route reads/writes user data from Mongo Atlas (`ap-south-1`).
- The pre-existing Mongo `conversations` / `user_profile` / `chat_memory_brain` collections are joined by the migrated tables (`users`, `sessions`, `edu_study_*`, etc.).

### Phases (each gated on the previous)

| # | Phase | Done-when | Rollback |
|---|---|---|---|
| 1 | **ADR** | `docs/architecture/adr/0001-pg-to-mongo.md` published with collection-mapping table for every PG table touched in `db_ops.py` + `routes/edu_study.py`. | n/a (doc-only) |
| 2 | **Dual-write** | Every PG write in `db_ops.py` is mirrored into the corresponding Mongo collection inside the same request. PG remains read-of-record. New `metric: db.dualwrite.{success,fail}` shipped to Sentry. **`users` collection: SHIPPED 2026-05-06** (helper `db_dualwrite.py`; counters via `get_dualwrite_counters()`; carve-out for the 8 transactional `routes/admin_monetization.py` sites — see ADR-0001 decision log). **`conversations` collection: SHIPPED 2026-05-06** (`mirror_conversation_write()` wired into upsert/update/delete; rollback `MONGO_CONVERSATION_WRITES=0`). **`edu_notes` collection: SHIPPED 2026-05-06** (`mirror_edu_notes_write()` wired into all 5 sites in `routes/edu_study.py`: create_note, patch_note, delete_note, autogen INSERT, claim_anon_data bulk update_many; rollback `MONGO_EDU_NOTE_WRITES=0`; greenfield collection so PATCH uses `replace_one(upsert=True)`). **`edu_flashcards` collection: SHIPPED 2026-05-06** (`mirror_edu_flashcards_write()` wired into all 5 sites in `routes/edu_study.py`: 3 INSERT branches in build_flashcards bulk-mirrored as one `insert_many(ordered=False)` after PG block exits to amortise ≤2.4 k-card fan-out, review_flashcard SM-2 UPDATE → `replace_one(upsert=True)` with `{id,actor_kind,actor}` filter, claim_anon_data bulk update_many gated on `cards_count > 0`; rollback `MONGO_EDU_FLASHCARD_WRITES=0`; greenfield Mongo target). **`edu_study_settings` collection: SHIPPED 2026-05-06** (`mirror_edu_study_settings_write()` wired into all 8 PG write sites in `routes/edu_study.py`, collapsed into 5 mirror calls: streak block's 3 mutually-exclusive INSERT/UPDATE branches → 1 post-block `update_one(upsert=True)` with `{actor_kind,actor}` composite key + `$setOnInsert` PG defaults; `set_study_settings` strict-mode → `$set` with `req.strict_mode is None` → no-op semantics replicated; `guardian_pin_set` PIN → `$set` upsert; claim flow's 3 txn writes → 1 user-side upsert (INSERT/UPDATE branches share a unified mirror plan captured in-txn) + 1 anon-side `delete_one`, both fired post-commit; rollback `MONGO_EDU_STUDY_SETTING_WRITES=0` (singular form); greenfield Mongo target with composite PK so no surrogate id column). **`activity_log` collection: SHIPPED 2026-05-06** (first soft-join target — Mongo collection already populated by `db_ops.supa_insert_activity_log`'s existing 3rd-tier fallback; Phase 2 adds `mirror_activity_log_write()` on the PG-success branches of `supa_insert_activity_log` + `supa_clear_activity_log` so Mongo now sees *every* audit write, not only PG-failure ones — prerequisite for Phase-3 per-day row-count read-shadow; only 2 db_ops sites instrumented because all 8 route-level callers funnel through these centralised helpers; default flag name `MONGO_ACTIVITY_LOG_WRITES=0` for rollback — no `_FLAG_NAME_OVERRIDES` entry needed because the default singularisation already yields the correct name). **`notifications` collection: SHIPPED 2026-05-06** (second soft-join target — Mongo collection already populated by `db_ops.supa_insert_notification` / `supa_delete_notification` 3rd-tier fallback; Phase 2 adds `mirror_notifications_write()` on the PG-success branches of both helpers so Mongo sees every admin notification write + per-id delete, not only PG-failure ones; only 2 db_ops sites instrumented because every route-level caller (admin notification CRUD + push-dispatch helpers) funnels through these centralised helpers; default flag `MONGO_NOTIFICATION_WRITES=0` — trailing 's' singularised by default rule, no override entry). Other 3 collections NOT STARTED. | Disable mirror via `MONGO_USER_WRITES=0` env flag — single env flip, zero deploy. |
| 3 | **Read-shadow** | Every authenticated read also runs the Mongo equivalent in parallel and diffs the result. Diff > 0.1 % on any 24 h window blocks Phase 4. Sentry counter `db.shadow.{match,diff}`. | Disable shadow read via `MONGO_USER_READ_SHADOW=0`. |
| 4 | **Cutover** | Read-of-record flips to Mongo via `USER_DATA_PRIMARY=mongo` env flag. PG continues as backup-write only. | Flip env back to `USER_DATA_PRIMARY=pg`. (This is the last reversible point.) |
| 5 | **Rip-out** | After 14 days clean on Mongo primary: delete asyncpg from `deps.py`, drop `DATABASE_URL` from `_ALWAYS_NEEDED`, remove `db_ops.py` PG branches, drop the helium PG instance. | Restore from PG nightly backup (only viable for ≤24 h post-rip-out). |

### Hard blocker: Supabase / Google OAuth
- `/api/auth/supabase-session` is the live Google OAuth handler (calls Supabase Auth which is itself backed by the helium Postgres). **B5 (Supabase removal) cannot start until Phase 4 of this plan is complete**, because killing Supabase before Mongo is the read-of-record locks out every Google-OAuth user.
- Replacement endpoint to be designed in Phase 1 ADR: native Mongo-backed `users` collection with verified-email index + a thin OAuth verifier (validate Google ID-token signature directly, no Supabase round-trip).

### Acceptance script (binary)
```bash
cd artifacts/syrabit-backend && python -c "
import deps, importlib
assert 'asyncpg' not in [m.__name__ for m in deps.__dict__.values() if hasattr(m, '__name__')], 'asyncpg still imported in deps.py'
import os
assert 'DATABASE_URL' not in open('server.py').read().split('_ALWAYS_NEEDED')[1].split(']')[0], 'DATABASE_URL still in _ALWAYS_NEEDED'
print('V4 §13 acceptance: PASS')
"
```

---

## §14 — Lock conditions met (was §13, renumbered for §13 above)

1. ✅ Embedding model mismatch → namespace separation + re-embed queue.
2. ✅ Cost-shares sum to 100 % (40 + 30 + 20 + 10) with single integers.
3. ✅ Secrets-sync mechanism defined (Terraform-CI job + hash test).
4. ✅ Sentry tracing + header propagation outlined.
5. ✅ RTO relaxed to 4 h with quarterly drill.
6. ✅ Azure declared as explicit SPOF.
7. ✅ Pinecone moved to `aws-ap-south-1`; latency conflict resolved.
8. ✅ Vectorless RAG layer added as complementary tier (§5).
9. ✅ *(2026-05-06, A1–A9)* Cohere status declared (§1); Cerebras CF-Gateway-only path declared (§1, §4); Azure OpenAI SKU table (§4); embed-failover trigger spec (§3); eval-set pinning (§5); Llama-Guard-2 hosting + fail-mode (§1, §4); DR-runbook references (§8); Workers-AI fallback ordering clarified (§4); §13 data-migration plan added with hard Supabase blocker noted.

**This V4 plan is locked as "approved with conditions met". No further infra renegotiation without a V5 doc.**

---

## §15 — Amendment: Vertex scope-down to content-format only (Task #490, 2026-05-06)

**Trigger:** the four-cloud delegation lock-in (#489) declared Vertex chat / multilingual-embed / Vector Search out of scope. This amendment makes the code match.

### Changes vs V4 §1–§4

1. **Vertex chat / vision / translate — REMOVED.** `vertex_chat.py`, `_call_vertex_chat`, `_stream_vertex_gemini`, and the SA-OAuth chat helper inside `llm.py` are deleted. Vertex no longer participates in `english_chat`, `assamese_chat`, `long_context`, `casual_chat`, `vision`, `translate`, or `safety` pools. The §4 dispatch chain is now Azure `gpt-4.1-nano` → Workers-AI Mistral-7B → Workers-AI Llama-3.2-3B → generic Workers-AI (no Vertex leg).
2. **Vertex multilingual embed — REMOVED.** `providers/vertex_embed.py` is deleted. `embed_doc` / `embed_query` pools no longer include Vertex.
3. **Vertex Vector Search retriever — REMOVED.** `retrievers/vertex.py` and the `VertexVectorSearchRetriever` adapter are deleted. The retriever factory now exposes only `vectorize`, `mongodb_vector`, and `pinecone`. The admin retriever-toggle endpoint refuses `active="vertex"` with HTTP 400.
4. **Embed failover → Option D (cache-only degraded mode).** The §3 second-Pinecone-namespace fallback (`fallback_vertex_pending_reembed`) is **abandoned**. When primary Workers-AI embed is unavailable the system serves cached vectors when present and enqueues a deferred-embed job to AWS SQS (`sqs-reembed.tf` consumer at `services/backend/sqs_consumers/reembed.py`). Fresh content with no cached vector is **not** embedded by Vertex — it is queued for re-embed once primary recovers. There is **no second Pinecone namespace** and **no silent fallback** (V4 §12 "fail loud" rule).
5. **Only remaining Vertex surface — `content_format`.** Vertex Gemini 2.5 Flash is kept as a **NotebookLM-style content formatter** via `vertex_format.format_with_vertex(text, *, style, lang)`. A new pool `content_format=["vertex"]` with weight `10000` is added to `config.PROVIDER_PRIORITY` / `POOL_WEIGHTS`. Wiring the formatter into routes is **out of scope** for #490 and tracked separately as #494.
6. **Config trim.** `config.py` drops every legacy `("<pool>", "vertex")` entry from `POOL_WEIGHTS` and removes `"vertex"` from every `PROVIDER_PRIORITY` list except `content_format`. The legacy `_LEGACY_EMBED_WEIGHTS` Vertex row is removed.
7. **Acceptance gate.** `rg "_call_vertex_chat|_stream_vertex_gemini|VertexVectorSearchRetriever|VERTEX_INDEX_ID|VERTEX_DEPLOYED_INDEX_ID|fallback_vertex_pending_reembed|RAG_EMBEDDING_PROVIDER=fallback_vertex"` returns zero hits in `artifacts/syrabit-backend/` outside this §15 changelog. Contract tests `tests/test_vertex_format_contract.py` and `tests/test_embed_failover_degraded_mode.py` lock the new shape.

### Out of scope (tracked separately)

- **#494** — wire `vertex_format.format_with_vertex` into the actual notes / RAG / SEO render paths (today the formatter exists but is invoked only by `polish_notes_with_vertex`).
- **#489** — Cloud Run / GCP-leftover module deletes (already shipped).
- Cohere / Cerebras provider removal (separate amendment).
- `routes/admin_vertex.py` admin diagnostics surface (kept for ops visibility into the surviving formatter quota).

---

## §15 — Amendment: Sarvam scope-down to Assamese chat LLM only (Task #492, 2026-05-06)

**Trigger:** Sibling to #490 (Vertex scope-down) and #491 (Cohere/Cerebras removal). Sarvam was historically wired into translate, TTS, transliterate, STT, and a polish helper. Audit shows none of those are on the live hot path post-V4 (Workers-AI IndicTrans2 owns translate; ElevenLabs/Deepgram own TTS; Google Chirp_2 + Workers-AI Whisper own STT; Vertex owns polish). The chat-LLM surface (`assamese_rag_chat` → `sarvam-m`) is the only Sarvam call still earning its keep.

### Changes vs V4 §1 / §4

1. **Sarvam HTTP surfaces — REMOVED.** `routes/cms_sarvam_health.py` no longer exposes `POST /sarvam/translate`, `POST /sarvam/tts`, `POST /sarvam/transliterate`, or `GET /sarvam/status` as live endpoints — they now return **HTTP 410 GONE** with a JSON body citing this amendment so external integrators see a loud failure. `_normalise_lang`, `_sarvam_cache_key`, and `_sarvam_tts_direct_fallback` are deleted; `_SARVAM_LANG_CODES` is renamed to `_SUPPORTED_TRANSLATE_LANGS`.
2. **Sarvam clients collapsed to a single surface.** `deps.sarvam_client`, `deps.sarvam_translate_client`, `deps.sarvam_client_direct`, **and** `deps.sarvam_llm_client_direct` (and all of their lifespan `aclose` blocks in `server.py`) are deleted. `config.SARVAM_TRANSLATE_KEY` is removed. The **only** surviving Sarvam client is `deps.sarvam_llm_client` (chat LLM, `Accept: text/event-stream`) used by `_pick_sarvam_client` / `_call_sarvam_llm` / `_stream_sarvam` in `llm.py` for `assamese_rag_chat` dispatch. The historical CF-Gateway-bypass twin was retired per the Task #492 acceptance gate; CF Gateway outages now propagate so the dispatcher advances to the Workers-AI IndicTrans2 leg instead of silently bypassing the gateway.
3. **Polish helper — RENAMED.** `_polish_notes_with_sarvam` in `routes/admin_pipeline.py` (already a thin wrapper over `polish_notes_with_vertex`) is renamed to `_polish_notes_with_vertex_safely` to remove the misleading Sarvam name. Both call sites (notes generate + reflow) updated.
4. **Translate helper — RENAMED + tightened.** `_translate_text_sarvam` is renamed to `_translate_text_chunked` and its in-line Sarvam HTTP fallback (lines 813–831) is deleted; on dispatch failure it now raises `503` directly instead of silently revivng Sarvam translate. `providers/chunk_embedder.py` Assamese backfill loop swaps the direct Sarvam HTTP loop for `call_translate_with_dispatch` (Workers-AI IndicTrans2 primary).
5. **STT — Sarvam Saaras REMOVED.** `routes/edu_study.py /edu/stt` drops the Sarvam Saaras leg; the chain is now Google Chirp_2 (Indic) → Workers-AI Whisper. `/edu/voice/status` probes the surviving providers (ElevenLabs/Deepgram for TTS, Google STT/Workers-AI for STT) instead of `sarvam_client`.
6. **Indic provider toggle — locked.** `lang_sanitizer._VALID_INDIC_PROVIDERS = ("sarvam",)` documented as the V4 §15 lock — no Vertex, no other Indic chat provider is admissible.
7. **Telemetry rename — coordinated.** `failing_leg="sarvam_vertex_chain"` (assamese-unavailable counter) is renamed to `failing_leg="sarvam_workers_indic_chain"`. The label name was a frozen telemetry id; this amendment migrates it together with the admin health panel (`AdminHealth.jsx` legLabels), the test pin (`AdminHealth.assameseRecent.test.jsx`), and the outage test suite (`tests/test_assamese_recent_outages.py`) in lock-step. The chain it names is unchanged: `sarvam-m → Workers-AI IndicTrans2`.
8. **Acceptance gate.** `rg "sarvam_client|sarvam_translate_client|sarvam_client_direct|sarvam_llm_client_direct|SARVAM_TRANSLATE_KEY|_translate_text_sarvam|_polish_notes_with_sarvam|sarvam_vertex_chain" artifacts/syrabit-backend/{routes,providers,deps.py,config.py,server.py,llm.py}` returns **zero** production hits (only changelog comments remain); `cd artifacts/syrabit-backend && python -c "import server"` succeeds.

### Out of scope (tracked separately)

- **#491** — Cohere/Cerebras provider removal.
- **#494** — wire `vertex_format` into render paths (no overlap with Sarvam).
- Sarvam admin UI removal (frontend `/admin/sarvam-health` panel kept as a stub showing the surviving chat client until #495).

---

## §15 — Amendment: content_format dispatcher with Workers-AI Llama-3.3-70b fallback (Task #494, 2026-05-06)

**Trigger:** §15 §5 (Task #490) kept Vertex Gemini 2.5 Flash alive solely as the `content_format` formatter but pinned the pool to a single provider (`["vertex"]`). That left every notes-publish, reflow, and Assamese bulk-translate caller exposed to a Vertex-only single point of failure — and gave operators no audit trail showing which formatter actually shaped a given document. #494 ships the V4-mandated dual-leg dispatcher and the missing `formatted_by` Mongo audit field, completing the §15 §5 deferral.

### Changes vs V4 §15 §5

1. **Dispatcher introduced.** New module `artifacts/syrabit-backend/content_formatter.py` exposes `format_content(text, *, style, lang, max_tokens) -> {text, formatted_by, duration_ms, trace_id}`. Style / lang are typed enums (`Literal["notebook_lm","study_notes","flashcard"]`, `Literal["en","as"]`). Unknown values raise `ValueError` rather than degrading to passthrough.
2. **Routing chain.** Vertex Gemini 2.5 Flash is the **primary**; on outage the dispatcher advances to Workers-AI Llama-3.3-70b (`@cf/meta/llama-3.3-70b-instruct-fp8-fast`) called via `providers.cloudflare_ai.chat`. On dual outage the dispatcher returns the original text with `formatted_by="passthrough"` — fail-loud is preserved upstream because the audit field surfaces the degradation in the admin health panel and the Mongo doc; the dispatcher itself never raises (callers were already passing through Vertex outages with the prior single-provider helper, so a raise here would be a regression in availability).
3. **Assamese purity gate.** When `lang="as"`, `lang_sanitizer.measure_leakage` is run on the polished output and on a rejection (`ratio > get_threshold()` or no Assamese script at all) the dispatcher discards the polish and returns the original text under `formatted_by="passthrough"`. This blocks the Vertex / Llama failure mode where the model silently translates Assamese back to English under a polish prompt.
4. **Pool / weights updated.** `config.PROVIDER_PRIORITY["content_format"] = ["vertex","workers_ai_llama33_70b"]`; `POOL_WEIGHTS["content_format"] = {"vertex": 10000, "workers_ai_llama33_70b": 100}`. The 100:10000 weight is intentional — the weighted draw still lands on Vertex >99% of the time, but Llama-3.3-70b is enrolled in the SmartKeyPool credit / 429 accounting path so cost and throttle telemetry survive the fallback. The dispatcher's own primary→fallback advance is what actually triggers the Llama leg, not pool draw probability. `PROVIDER_CREDITS["workers_ai_llama33_70b"]` is set to the standard CF AI Gateway BYOK credit baseline.
5. **All callers wired.** `llm.polish_notes_with_vertex` now delegates to `format_content` (signature preserved for back-compat; returns string only). New `llm.polish_notes_with_format` returns the full dict so route handlers can persist `formatted_by`. `routes/admin_pipeline.py` notes-publish (L325) and reflow (L1247) write `formatted_by` to the chapter Mongo doc; the Assamese bulk-translate loop (L826/L867/L926) writes per-field `*_formatted_by` keys via `_format_translation_safely`. `routes/admin_advanced.py` swaps to `polish_notes_with_format` and exposes the formatter in the admin readout.
6. **Mongo audit field — `formatted_by`.** Every chapter / translation document polished after #494 ships carries the field. Legacy documents without it are treated as "vertex" by the admin health panel for backwards-compatibility (the pre-#494 single-provider behaviour) — there is no backfill job because the field is purely audit / observability.
7. **Admin health panel.** `routes/admin_health.py` exposes a new `content_formatter` panel sourced from `content_formatter.get_recent_breakdown()` (in-process ring of the last 256 invocations). The panel reports per-formatter counts (`vertex` / `workers_ai_llama33_70b` / `passthrough`) and the rolling p50/p95 of `duration_ms`. Operators see Vertex outages immediately because the `workers_ai_llama33_70b` and `passthrough` counts spike.
8. **CI guard.** `scripts/check_dead_providers.py` now bans direct `from vertex_format import format_with_vertex` and `vertex_format.format_with_vertex(` outside of `content_formatter.py`, `vertex_format.py` itself, and `tests/test_vertex_format_contract.py`. Any future caller that bypasses the dispatcher fails CI with a Task #494 violation message.
9. **Acceptance gate.** `cd artifacts/syrabit-backend && python -c "import server"` succeeds; `python -m pytest tests/test_content_formatter_dispatch.py tests/test_provider_priority_locked.py tests/test_vertex_format_contract.py` is green; `rg "vertex_format\.format_with_vertex\(|from vertex_format import .*format_with_vertex" artifacts/syrabit-backend/` returns hits only inside `content_formatter.py`, `vertex_format.py`, and the contract test.

### Out of scope (tracked separately)

- Backfill of `formatted_by` on pre-#494 chapters (treated as `"vertex"` by the admin readout — no historical data loss).
- Replacing Llama-3.3-70b with a future Workers-AI formatter (e.g. gpt-oss-120b) — requires its own purity-gate validation pass against Assamese content.
- The `routes/admin_vertex.py` diagnostics surface (kept intact; reads Vertex quota directly, not via the dispatcher).

---

## §16 — Amendment: Cerebras / Cohere / Voyage-AI provider removal (Task #491, 2026-05-07)

**Trigger:** sibling to #490 (Vertex scope-down) and #494 (content_format dispatcher). Cohere, Voyage-AI, and Cerebras carry no production traffic post-V4 (workers_ai_custom owns embed; Pinecone owns rerank; Azure `gpt-4.1-nano` + Workers-AI Mistral/Llama own chat). The dispatch chains, BYOK secret-audit lifecycle, admin readouts, and credit ledger are now collapsed to the surviving providers.

### Changes vs V4 §1, §3, §4, §15

1. **Cerebras dispatch — REMOVED.** `_call_cerebras` / `_stream_cerebras` deleted from `llm.py` and `emergentintegrations/llm/chat.py`. `cerebras` removed from `_PROVIDER_429`, `_PROVIDER_DEFAULT_MODELS`, `_PROVIDER_CANONICAL`, `_SLM_PROVIDER_MAX_INPUT_CHARS`, and every `PROVIDER_PRIORITY` chain. `CEREBRAS_API_KEY` removed from `.env.example` and from the BYOK primary/legacy maps in `server.py`.
2. **Cohere + Voyage-AI embed/rerank — REMOVED.** `providers/cohere.py` and `providers/voyage_ai.py` deleted. The mongodb_atlas vector-search branch in `rag.py` now uses `providers.workers_embed.embed_query` (workers_ai_custom). `vertex_services.embed_text` is single-source workers_ai_custom — no Cohere or Bedrock-Cohere primary. `COHERE_API_KEY` and `VOYAGE_API_KEY` deleted from `_BYOK_PRIMARY` / `_SUPABASE_MANAGED_LEGACY`.
3. **Bedrock-Cohere — REMOVED.** `providers/aws_native.py` drops `bedrock_cohere` from `FEATURE_KEYS`, `_FEATURE_REGIONS`, `_COST_EXPLORER_SERVICE_MAP`, plus `bedrock_embed` / `bedrock_rerank` / `BEDROCK_COHERE_MODELS`.
4. **syllabus_embedder — single-source contract.** `embed_chapter()` and `classify()` both call `vertex_services.embed_text` (workers_ai_custom). The previous `classify` Pinecone-Inference-first query path created cross-embedding-space contamination against vertex-indexed vectors and is gone.
5. **Admin surfaces.** `routes/admin_credits.py` `_CREDIT_REFERENCE`, `_PROGRAMME_NAMES`, smoke-test docstring, and endpoint description drop the Cohere row. `routes/admin_aws_native.py`, `routes/admin_embed_stack_health.py`, `routes/admin_advanced.py`, `routes/admin_vertex.py`, `routes/admin_routing_config.py`, and `chat_speedup_metrics.py` rephrased to neutral "legacy embed/SLM provider" wording. `CREDITS.md` updated to the Workers-AI custom embed primary.
6. **CI guard.** `scripts/check_dead_providers.py` `BANNED_LITERAL` extended to include `cerebras|cohere|voyage_ai` (alongside the pre-existing `cartesia|groq|openrouter|quge5`). The wider token set listed in the original task spec (`baseten|bedrock|gemini|xai|openai_direct`) is **not** enforced because those literals still appear as legitimate operator/brand strings (Bedrock IAM ARNs, Gemini model aliases, xAI crawler UAs); re-enabling is tracked under follow-up #524.
7. **Acceptance gate.** `rg "cerebras|CEREBRAS_API_KEY|CEREBRAS_RPM|providers\.cohere|bedrock_cohere|COHERE_API_KEY|voyage_ai|VOYAGE_" --type py` returns zero hits outside `scripts/check_dead_providers.py` + this changelog. `python -c "import server"` succeeds.

### Out of scope (tracked separately)

- **#524** — wider banned-token sweep across docs / frontend / admin panels so the CI guard can ban `bedrock|gemini|xai|baseten|openai_direct` as bare tokens without breaking legitimate brand copy.
- **#525** — pytest contract pinning that index-time and query-time syllabus embeddings share the same provider/model.

---

## §17 — Amendment: Canonical strict specialist-delegation map (Task #559, 2026-05-07)

**Trigger:** Eight overlapping routing rules had accumulated across V4 §0 / §3 / §4 / §15 / §16 + the founder-locks (#549) + the AWS expansion (#551) + the Azure-OpenAI retirement (#554). Each rule was correct on its own; together they made "who owns feature X today" require reading three guards and four docs. #559 collapses the lot into a **single per-feature canonical map** + a **single umbrella CI guard**.

### Decision

Adopt **strict specialist delegation**: every production feature has **exactly one** canonical primary provider and **at most one** named, strict fallback. The cost-share table in §0 is now an *informational outcome* of that map, not a routing contract.

- **Map (source of truth):** [`infra/four-cloud-delegation.md`](./four-cloud-delegation.md) §A — rewritten as a per-feature table superseding the old percentage matrix.
- **ADR:** [`docs/architecture/adr/0003-canonical-strict-specialist-delegation.md`](../docs/architecture/adr/0003-canonical-strict-specialist-delegation.md).
- **Cutover protocol (10 steps):** [`artifacts/syrabit/docs/infra/canonical-delegation-cutover.md`](../artifacts/syrabit/docs/infra/canonical-delegation-cutover.md).

### Enforcement

A single umbrella CI guard at `artifacts/syrabit-backend/scripts/ci/check_canonical_delegation.py` runs two banks of checks on every PR:

1. **DEAD-PROVIDER BANK** — carried verbatim from Tasks #297 / #347 / #491 / #494 / #554 (bare-token bans on retired providers + vendored-SDK import bans + direct `vertex_format.format_with_vertex` ban + aca_jobs/* manifest completeness). Behaviour-preserving: any PR that previously passed `scripts/check_dead_providers.py` still passes the umbrella.
2. **CANONICAL-DELEGATION BANK** — new in #559:
   - `_check_chat_chains` — `PROVIDER_PRIORITY["english_rag_chat"]` ≡ `{vertex, vertex_flash_lite, workers_ai_llama32_3b}` and `PROVIDER_PRIORITY["assamese_rag_chat"]` ≡ `{sarvam, vertex_assamese, retrieval_only}` (set-equality; English head order is runtime-dynamic via `_select_chat_primary`). Updated by Task #2 (2026-05-09).
   - `_check_chat_primary_selector` — `cost_caps._select_chat_primary` + `CHAT_PRIMARY_OVERRIDE` knob both present.
   - `_check_voice_paywall` — `routes/voice.py` `/tts` + `/stt` + `/voice/voice` all sit behind `Depends(require_paid_plan)` (mirrors `scripts/check_budget_ceiling.py`).
   - **TODO-gated** (banned only after the parent task merges): `TODO_557_PATTERN` (SES sole tier-1 + self-hosted VAPID web-push, ban flips on with Task #557) and `TODO_558_PATTERN` (observability narrowing to GCP Cloud Trace single exporter, ban flips on with Task #558). The regex literals live in the umbrella; uncomment the matching `_scan_pattern_global` call to activate.

The legacy `artifacts/syrabit-backend/scripts/check_dead_providers.py` is now a **thin shim** that imports `main` from the umbrella, so existing pre-deploy gates, pytest fixtures, and ops cron entries keep working without touching their command lines.

### Deploy-workflow wiring

`.github/workflows/azure-container-apps-deploy.yml` gains a new `canonical_delegation_gate` job that runs the umbrella **before** the existing `budget_ceiling_gate`. The `deploy` job depends on both. A routing-contract drift now fails the build before the budget gate even gets a chance to mask it.

### Relationship to V4 §3 (embed failover) and §4 (chat dispatch)

- §3 (cache-only degraded mode + AWS SQS reembed queue) is unchanged. The umbrella adds no new check here — V4 §15 §4 already names the strict fallback (cache-only) and the dead-provider bank already bans every embedder that is not Workers-AI custom.
- §4 (chat dispatch) is **superseded** by the dynamic `_select_chat_primary`-driven 2-chain documented in §17 above and in the canonical map. The four old fallback legs (Mistral-7B / Llama-3.2-3B / generic Workers-AI / Vertex) have been collapsed into a single named strict fallback. Strict-chain exhaustion now surfaces 503 instead of silent advance to a third option (V4 §12 in code).

### Acceptance gate

```bash
# 1. Umbrella guard passes.
python artifacts/syrabit-backend/scripts/ci/check_canonical_delegation.py
# expected: "Canonical-delegation guard OK — scanned <N> files."

# 2. Legacy shim still passes (behaviour-preserving).
python artifacts/syrabit-backend/scripts/check_dead_providers.py
# expected: same output as above.

# 3. Per-feature canonical map present.
rg -c '^\| \*\*' infra/four-cloud-delegation.md
# expected: ≥ 18 rows in §A.
```

### Out of scope (tracked separately)

- ~~**Task #557 — SES sole tier-1**~~ — **shipped (Task #556, 2026-05-07).** Already enforced — see `infra/four-cloud-delegation.md` §A "Transactional email" row. The umbrella's `TODO_556_PATTERN` is **active** and bans the legacy vendor SDK names + API-key env vars + provider-flag env knobs across backend + frontend + workers + IaC + lockfiles + the canonical docs. The `TODO_557_PATTERN` flip-on still owns the self-hosted VAPID web-push migration only (the FCM → VAPID code change).
- **Task #558** — flip the `TODO_558_PATTERN` ban on (observability narrowing). One comment-uncomment + the Sentry → GCP Cloud Trace exporter migration.
