# Environment Variables — V4 Locked

> **Authoritative against:** [`infra/v4-locked-architecture.md`](infra/v4-locked-architecture.md).
> **Source of truth for values:** Azure Key Vault `syrabit-prod-kv`
> (V4 §6). AWS Secrets Manager + Cloudflare Secrets are read-only
> replicas synced daily by Terraform-CI with SHA-256 hash validation.
> See `docs/SECRET_ROTATION.md` for rotation procedure.

This doc lists every environment variable read by Syrabit's runtime
components (FastAPI backend, Cloudflare embed worker, edge proxy, and
deploy workflows). It is grouped by surface and matches the V4 spec.

---

## Backend — `artifacts/syrabit-backend/` (FastAPI on Azure ACA `eastus2`)

### Required (boot will fail without these)

| Var | Source | Purpose |
|---|---|---|
| `MONGO_URL` | AKV `MONGO_URI_ATLAS` | Mongo Atlas `ap-south-1` peered URI. SoT for conversations, profiles, chunk metadata. |
| `JWT_SECRET` | AKV | User session JWT signing. |
| `ADMIN_JWT_SECRET` | AKV | Admin session JWT (separate from user JWT — never share). |
| `AZURE_OPENAI_API_KEY` | AKV | Azure OpenAI `gpt-4.1-nano` in `eastus2` — **SOLE primary** for English chat (V4 §4, user-locked 2026-05-06 via B3). |
| `AZURE_OPENAI_ENDPOINT` | AKV | Azure OpenAI base URL. |
| `AZURE_OPENAI_DEPLOYMENT` | AKV | Deployment name (default `gpt-4.1-nano`). |
| `AZURE_OPENAI_MODEL` | AKV | Legacy alias of `AZURE_OPENAI_DEPLOYMENT`. |
| `AZURE_OPENAI_MODEL_OVERRIDE` | optional env | Operator override for the resolved deployment (V4 §4 A3). Set to `gpt-4.1-mini` for the staged quality upgrade — single env flip, no secret rotation. Emits an INFO log at backend startup when active. |
| `RAZORPAY_KEY_SECRET` | AKV | Razorpay INR-only payment gateway. |
| `WORKERS_EMBED_SECRET` | AKV | Shared secret for the Cloudflare embed worker (`embed.syrabit.ai`). Worker validates this on every `/embed` POST. |
| `WORKERS_EMBED_URL` | static | `https://embed.syrabit.ai` (production) / `https://embed-staging.syrabit.ai` (staging). |
| `EMBED_PROVIDER_PRIMARY` | static | Locked to `workers_ai_custom`. Switching to anything else disables the V4 embed path. |
| `EMAIL_PROVIDER` | static | Locked to `sendgrid` (V4 §0). |
| `EMAIL_FALLBACK` | static | Locked to `ses` (AWS SES, activated when SendGrid burn threshold exceeded). |
| `SENDGRID_API_KEY` | AKV | SendGrid Pro 100k via Azure Marketplace. |
| `AWS_ACCESS_KEY_ID` | AKV (mirror) | AWS SES + SQS (re-embed queue) + S3 + CloudWatch. |
| `AWS_SECRET_ACCESS_KEY` | AKV (mirror) | Pair with above. |
| `AWS_REGION` | static | `ap-south-1` (Mongo + Pinecone region; SES is `us-east-1` separately). |
| `PINECONE_API_KEY` | AKV | Pinecone `aws-ap-south-1` index. |
| `PINECONE_INDEX` | static | Production index name. |
| `PINECONE_NAMESPACE_PRIMARY` | static | `cached_gemma_today` (Gemma-300M 1024-dim). |
| `PINECONE_NAMESPACE_FALLBACK` | static | `fallback_vertex_pending_reembed` (Vertex multilingual; queued for re-embed by SQS Lambda). **Never read on the live chat path.** |
| `RAG_EMBEDDING_PROVIDER` | runtime flag | `cf_gemma` (default) or `fallback_vertex` (set by health-check controller on CF embed-worker outage). |
| `MONGO_USER_WRITES` | optional env (default `1`) | V4 §13 / ADR-0001 Phase 2 rollback switch. `1`/unset = best-effort Mongo mirror enabled for every PG `users` write; `0` = disable all mirrors (PG-only, single env flip, no deploy). Counter snapshot: `db_dualwrite.get_dualwrite_counters()` → `users.{success,fail,skipped_disabled,skipped_no_db}`. |
| `MONGO_CONVERSATION_WRITES` | optional env (default `1`) | V4 §13 / ADR-0001 Phase 2 rollback switch for the `conversations` collection (independent of `MONGO_USER_WRITES`). `1`/unset = best-effort Mongo mirror on every PG `conversations` upsert/update/delete; `0` = disable. Counter keys: `conversations.{success,fail,skipped_disabled,skipped_no_db}`. |
| `MONGO_EDU_NOTE_WRITES` | optional env (default `1`) | V4 §13 / ADR-0001 Phase 2 rollback switch for the `edu_notes` collection (greenfield Mongo target; independent of the other `MONGO_*_WRITES` flags). `1`/unset = best-effort Mongo mirror on every `edu_notes` write in `routes/edu_study.py` (create / patch / delete / AI-autogen / claim bulk-reassign); `0` = disable. Counter keys: `edu_notes.{success,fail,skipped_disabled,skipped_no_db}`. |

### Embedding-failover topology (V4 §3)

When the embed-worker health check fails:
1. Controller sets `RAG_EMBEDDING_PROVIDER=fallback_vertex`.
2. New chunks are embedded by Vertex AI multilingual embedding and written to `PINECONE_NAMESPACE_FALLBACK`.
3. Each fallback write is enqueued in AWS SQS `syrabit-reembed-queue`.
4. When CF returns, controller flips back to `cf_gemma`; Lambda drains the queue and re-embeds those chunks into `PINECONE_NAMESPACE_PRIMARY`.

### Optional / feature-gated

| Var | Purpose |
|---|---|
| `GEMINI_API_KEY` | Vertex Gemini 2.5 Flash. **NOT in chat hot path** (V4 §4 user-locked 2026-05-06 via B3 — chat is Azure-SOLE-primary). Used for: long-form `content` pool fallback, safety/validation, and Gemini RAI batch-only review of `exam_model_paper`. |
| `GOOGLE_APPLICATION_CREDENTIALS_JSON` | SA-gated GCP services (Cloud Scheduler, Tasks, Web Security Scanner, Discovery Engine). |
| `SARVAM_API_KEY` | Sarvam Indic chat primary. |
| `DEEPGRAM_API_KEY` | STT. |
| `ELEVENLABS_API_KEY` | TTS. |
| `VOYAGE_API_KEY` | `memory_brain` collection embeddings (separate from chunks; voyage-3.5, 1024-dim, Atlas `$vectorSearch`). |
| `CARTESIA_API_KEY` | Voice TTS (alternate). |
| `UPSTASH_REDIS_REST_URL`, `UPSTASH_REDIS_REST_TOKEN` | Hot counter for credit-burn meter (V4 §10) and translation cache. |
| `GITHUB_TOKEN` | Used by ops scripts only (not by FastAPI runtime). |

### Removed (Task #347 — never re-add without a V5 spec change)

`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `XAI_API_KEY`, `BEDROCK_PROXY_AUTH_TOKEN`, `RESEND_API_KEY`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `RAILWAY_TOKEN`, `QUGE5_*`, `GROQ_API_KEY`, `CEREBRAS_API_KEY`, `FIREWORKS_API_KEY`, `OPENROUTER_API_KEY`.

---

## Cloudflare embed worker — `artifacts/syrabit/workers/embed-worker/`

| Var | Where | Purpose |
|---|---|---|
| `EMBED_SHARED_SECRET` | `wrangler secret put` (per env) | Must equal backend's `WORKERS_EMBED_SECRET`. Separate values for `production` and `staging` envs. |
| `[ai] binding` | `wrangler.toml` per `[env.X.ai]` | Cloudflare Workers AI binding; used to call EmbeddingGemma-300M and Qwen3-0.6B. |

---

## Cloudflare edge proxy — `workers/edge-proxy/`

| Var | Purpose |
|---|---|
| `BACKEND_URL` | `https://syrabit-backend.lemonstone-ce3c87e1.eastus.azurecontainerapps.io` (V4 live). Was DO `blr1` pre-cutover. |
| `ORIGIN_SHARED_SECRET` | Header injected by worker; backend rejects requests without it. Sourced from CF Secrets (replica of AKV). |
| `SENTRY_TRACE_FORWARD` | Locked to `true` — V4 §7 requires `traceparent` / `baggage` propagation. |

---

## Deploy workflow — `.github/workflows/azure-container-apps-deploy.yml`

GitHub Actions secrets (replicas of AKV via Terraform-CI sync):

| Secret | Purpose |
|---|---|
| `AZURE_CREDENTIALS` | Service principal JSON for `az login`. |
| `AZURE_RESOURCE_GROUP` | Target ACA RG. |
| `AZURE_CONTAINER_APP` | `syrabit-backend`. |
| `ACR_NAME` | Azure Container Registry. |
| `GHCR_TOKEN` | For pulling pre-built images. |

The workflow's single ARM PATCH **must** include `properties.configuration.ingress.traffic = [{latestRevision: true, weight: 100}]` and `targetPort: 8000`. Removing either strands traffic on the helloworld fallback revision (see replit.md "Gotchas").

---

## Frontend — `artifacts/syrabit/`

Build-time `VITE_*` vars only (no secrets):

| Var | Purpose |
|---|---|
| `VITE_API_BASE_URL` | `/api` (proxied through CF edge worker). |
| `VITE_RAZORPAY_KEY_ID` | Public Razorpay key (paired with `RAZORPAY_KEY_SECRET` server-side). |
| `VITE_SENTRY_DSN` | Browser-side Sentry; same project as backend so traces stitch end-to-end (V4 §7). |
| `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` | Public Supabase anon credentials for OAuth bootstrapping. |

---

## Validation

The Terraform-CI sync job runs daily and on AKV rotation hook; it
fails the pipeline if any of the variables marked `AKV` above have a
mismatched SHA-256 across AKV / AWS Secrets Manager / CF Secrets.
See `docs/SECRET_ROTATION.md` §3 for the procedure.
