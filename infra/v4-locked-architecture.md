# V4 Locked Architecture — Final Multi-Cloud Configuration

> **Status: LOCKED — 2026-05-05**
> **Owner:** founder@syrabit.ai
> **Supersedes:** v3 (`per-cloud-feature-delegation.md`, `provider-priority-map.md`, `credit-burn-runbook.md`).
> The v3 docs remain on disk for diff/blame history but every section in
> them is overridden by this file. **If anything in v3 disagrees with V4,
> V4 wins.** Any new PR touching infra MUST cite this doc.

---

## §0 — Four-cloud delegation map (canonical, locked)

| Provider | Core role | Main workloads | Cost-share |
|---|---|---|---|
| **Cloudflare** | Edge front-end + AI dispatch + edge caching + WAF | Pages-SSR (`syrabit.ai`, `chat.syrabit.ai`); Workers-AI **EmbeddingGemma-300M** (mean-pooled to 1024-dim to match Pinecone) → `/embed`; Workers-AI Indic translation (IndicTrans2); R2 (chapter PDFs, audio, exports, backups); KV (chapter index, syllabus map, flags, allowlists); Cache-Reserve (long-TTL assets); Vectorize (edge RAG cache); D1 (SEO meta, audit logs, syllabus-map read-before-Mongo); AI Gateway (BYOK to Gemini + Azure OpenAI); WAF + RateLimiter DO. | **40 %** |
| **Azure** | HTTP backend + auth + AI safety + primary email | Azure Container Apps `syrabit-backend` in `eastus2` (Python FastAPI hot path) + `rust-core` (async batch); **Llama-Guard-2 self-hosted** as moderation-primary on the same ACA compute; **Azure AI Content Safety** as moderation-secondary; **SendGrid (Pro 100k via Azure Marketplace)** as primary transactional email (`EMAIL_PROVIDER=sendgrid`); orchestrates Pinecone, MongoDB Atlas, Deepgram, ElevenLabs, Sarvam-AI, Azure Translator. **Azure Key Vault is the source of truth for all secrets.** | **30 %** |
| **AWS** | Event backbone + durable data/backups + fallback email | Lambda / Step-Functions / SQS / EventBridge / CloudWatch (batch embed ops, Pinecone index maintenance, shard rebalancing, `lambda-otel`, `lambda-workers`, **Vertex re-embed queue worker**); Atlas-peered VPC connectivity (Mongo Atlas in `ap-south-1`); S3 (dumps, temp exports; final backups sync to R2); **SES fallback email** (`EMAIL_FALLBACK=ses`, activated when SendGrid burn-threshold exceeded). | **20 %** |
| **GCP / Vertex** | Gen-AI validation + safety + observability | **Vertex Gemini 2.5 Flash** = default content-validation model + **co-primary English chat** for **long/high-risk turns** (token-length + risk-score router); short/low-risk turns → **Qwen3-0.6B** on Workers AI. **Gemini RAI** = batch/async-only for `exam_model_paper` review (never blocks live chat). Web Risk API for malicious-URL checks. Cloud Trace for OTEL spans. **Vertex multilingual embedding** = embed-failover only, writes to a separate Pinecone namespace with a re-embed queue (see §3). | **10 %** |

✅ **Cost-share sum: 40 + 30 + 20 + 10 = 100 %** (single integers, no ranges).

---

## §1 — Specialists (locked)

| Provider | Role | Region / hosting | Notes |
|---|---|---|---|
| **Pinecone** | RAG index + rerank (Pinecone Rerank v0) | **`aws-ap-south-1`** (moved from us-east-1; co-located with Mongo Atlas) | <50 ms RAG hop budget within India region. |
| **MongoDB Atlas** | Session + profile + chunk-metadata store | `ap-south-1` AWS-peered VPC | DPDP-compliant data residency. **Stores only chunk metadata + Pinecone IDs — NEVER recomputes embeddings from chunk text.** |
| **Deepgram / ElevenLabs / Sarvam-AI** | STT / TTS / regional speech | Provider-default | Assamese / Indic voice RAG. No re-host on Azure / AWS Speech Services. |
| **Azure Translator** | Indic→English fallback translation | `eastus2` | Quota-limited fallback only (after IndicTrans2 + Workers-AI translation). |
| **AWS SES** | Low-cost fallback email | `us-east-1` | Activated when SendGrid burn-threshold exceeded. DNS ownership = `syrabit.ai` on Cloudflare DNS (SPF/DKIM/DMARC published for both providers). |

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

✅ **Trade-off explicitly accepted:** availability-OK during CF outage; correctness is "good-enough-for-now / re-embed-later"; **zero index-mix corruption** because the two embedding spaces never share a namespace.

---

## §4 — Per-turn dispatch order (chat hot path, locked)

```
Cloudflare Worker (edge)
  ├─ token-length + risk-score router
  │
  ├─ short/low-risk turn  ──▶  Workers-AI Qwen3-0.6B (edge)
  │
  └─ long/high-risk turn  ──▶  Vertex Gemini 2.5 Flash (eastus2 via AI Gateway BYOK)
                                  ↓ on 429/exhaust
                                Azure OpenAI gpt-4.1-mini (eastus2)
                                  ↓ on 5xx
                                Workers-AI Mistral-7B / Llama-3.2-3B (edge)

Assamese Indic path
  ├─ Sarvam Indic chat  (primary, weight 10000)
  └─ Workers-AI IndicTrans2  (fallback, weight 0; reachable only via exclusion-redraw)
```

- **Llama-Guard-2** runs as a pre-filter on the Azure ACA compute (moderation-primary).
- **Azure AI Content Safety** runs in parallel as moderation-secondary.
- **Vertex Gemini RAI** is batch/async-only for `content_type=exam_model_paper` — never per-turn synchronous.

---

## §5 — Vectorless RAG layer (new in V4, complementary to vector RAG)

Three-tier retrieval router in `artifacts/syrabit-backend/rag.py`:

1. **Tree-walk router** — if the query parses as a syllabus path (`AHSEC class 11 → Physics → Chapter 5`) or PYQ ref (`AHSEC 2023 Q4(b)`), answer directly from D1 / KV. Zero embed cost. Target: 20–30 % of all chat turns.
2. **BM25 keyword pass** — Mongo `$text` index on `chunks.text_en` and `chunks.text_as`. Fires in parallel with the vector call. Best for exact-term, formula, and verbatim-script queries (especially Assamese morphology where Gemma-300M drifts).
3. **Vector pass (existing)** — Gemma-300M → Pinecone → Pinecone Rerank v0.

Results from (1)+(2)+(3) are fused with **Reciprocal Rank Fusion (RRF)** before the rerank step. Telemetry: `rag.router.tier_hit{tier=tree|bm25|vector}` counter in Sentry/Cloud Trace; success criterion is `≥25 %` of chat turns served without an embed call **with no MRR@10 regression** on the existing eval set.

---

## §6 — Secrets topology (three-store sync, controlled)

- **Source of truth:** **Azure Key Vault** (`syrabit-prod-kv`). Rotated first.
  - Owns: `MONGO_URI_ATLAS`, `GCP_WEB_RISK_API_KEY`, `AWS_SES_*`, `CF_WORKER_AI_*`, `JWT_SECRET`, `ADMIN_JWT_SECRET`, `RAZORPAY_KEY_SECRET`, `WORKERS_EMBED_SECRET`, `AZURE_OPENAI_API_KEY`.
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

---

## §9 — Latency budget (locked, India-anchored)

| Hop | Budget | Notes |
|---|---|---|
| CF Edge → Azure ACA `eastus2` | ~120 ms | Cross-Atlantic cost accepted; Vertex co-primary keeps long turns close to Azure. |
| Azure ACA → Mongo Atlas `ap-south-1` | <10 ms | After Atlas-peered VPC. |
| Azure ACA → Pinecone `aws-ap-south-1` | <50 ms | Resolves the v3 latency conflict (Pinecone was us-east-1). |
| Workers-AI inference (Qwen3-0.6B) | <200 ms | Edge-local. |
| Vertex Gemini 2.5 Flash (long turn) | <800 ms | Acceptable for high-risk / long-context turns. |
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

## §13 — Lock conditions met

1. ✅ Embedding model mismatch → namespace separation + re-embed queue.
2. ✅ Cost-shares sum to 100 % (40 + 30 + 20 + 10) with single integers.
3. ✅ Secrets-sync mechanism defined (Terraform-CI job + hash test).
4. ✅ Sentry tracing + header propagation outlined.
5. ✅ RTO relaxed to 4 h with quarterly drill.
6. ✅ Azure declared as explicit SPOF.
7. ✅ Pinecone moved to `aws-ap-south-1`; latency conflict resolved.
8. ✅ Vectorless RAG layer added as complementary tier (§5).

**This V4 plan is locked as "approved with conditions met". No further infra renegotiation without a V5 doc.**
