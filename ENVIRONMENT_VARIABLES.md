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
| `AWS_ACCESS_KEY_ID` | AKV (mirror) | AWS SES + SQS (re-embed queue) + S3 + CloudWatch. |
| `AWS_SECRET_ACCESS_KEY` | AKV (mirror) | Pair with above. |
| `AWS_REGION` | static | `ap-south-1` (Mongo + Pinecone region). |
| `SES_REGION` | static | `us-east-1` (SES sole transactional path — Task #556). Flip to `ap-south-1` for the quarterly DR drill via `scripts/ses_burn_smoke.sh`. |
| `EMAIL_FROM` | static | `Syrabit.ai <noreply@syrabit.ai>` — sender for both the SES transactional helper and the Cloudflare bulk Worker. |
| `BULK_EMAIL_WORKER_URL` | optional | Cloudflare Email Workers endpoint for bulk/digest fan-out (separate from SES; absence → digests skip with reason `no_worker_url`). |
| `BULK_EMAIL_WORKER_AUTH_KEY` | optional | HMAC bearer for backend → bulk Worker auth. |
| `PINECONE_API_KEY` | AKV | Pinecone `aws-ap-south-1` index. |
| `PINECONE_INDEX` | static | Production index name. |
| `PINECONE_NAMESPACE_PRIMARY` | static | `cached_gemma_today` (Gemma-300M 1024-dim). |
| `PINECONE_NAMESPACE_FALLBACK` | static | `fallback_vertex_pending_reembed` (Vertex multilingual; queued for re-embed by SQS Lambda). **Never read on the live chat path.** |
| `RAG_EMBEDDING_PROVIDER` | runtime flag | `cf_gemma` (default) or `fallback_vertex` (set by health-check controller on CF embed-worker outage). |
| `MONGO_USER_WRITES` | optional env (default `1`) | V4 §13 / ADR-0001 Phase 2 rollback switch. `1`/unset = best-effort Mongo mirror enabled for every PG `users` write; `0` = disable all mirrors (PG-only, single env flip, no deploy). Counter snapshot: `db_dualwrite.get_dualwrite_counters()` → `users.{success,fail,skipped_disabled,skipped_no_db}`. |
| `MONGO_CONVERSATION_WRITES` | optional env (default `1`) | V4 §13 / ADR-0001 Phase 2 rollback switch for the `conversations` collection (independent of `MONGO_USER_WRITES`). `1`/unset = best-effort Mongo mirror on every PG `conversations` upsert/update/delete; `0` = disable. Counter keys: `conversations.{success,fail,skipped_disabled,skipped_no_db}`. |
| `MONGO_EDU_NOTE_WRITES` | optional env (default `1`) | V4 §13 / ADR-0001 Phase 2 rollback switch for the `edu_notes` collection (greenfield Mongo target; independent of the other `MONGO_*_WRITES` flags). `1`/unset = best-effort Mongo mirror on every `edu_notes` write in `routes/edu_study.py` (create / patch / delete / AI-autogen / claim bulk-reassign); `0` = disable. Counter keys: `edu_notes.{success,fail,skipped_disabled,skipped_no_db}`. |
| `MONGO_EDU_FLASHCARD_WRITES` | optional env (default `1`) | V4 §13 / ADR-0001 Phase 2 rollback switch for the `edu_flashcards` collection (greenfield Mongo target; FK child of `edu_notes`; independent of the other `MONGO_*_WRITES` flags). `1`/unset = best-effort Mongo mirror on every `edu_flashcards` write in `routes/edu_study.py` (build bulk-insert_many / review SM-2 replace_one upsert / claim bulk-reassign); `0` = disable. Counter keys: `edu_flashcards.{success,fail,skipped_disabled,skipped_no_db}`. |
| `MONGO_EDU_STUDY_SETTING_WRITES` | optional env (default `1`) | V4 §13 / ADR-0001 Phase 2 rollback switch for the `edu_study_settings` collection (greenfield Mongo target; composite PK `(actor_kind, actor)` — no surrogate id column; singular form follows the edu_notes / edu_flashcards convention; independent of the other `MONGO_*_WRITES` flags). `1`/unset = best-effort Mongo mirror on every `edu_study_settings` write in `routes/edu_study.py` (8 PG sites collapsed into 5 mirror calls: streak update / strict-mode set / guardian PIN set / claim user-side upsert / claim anon-side delete); `0` = disable. Counter keys: `edu_study_settings.{success,fail,skipped_disabled,skipped_no_db}`. |
| `MONGO_ACTIVITY_LOG_WRITES` | optional env (default `1`) | V4 §13 / ADR-0001 Phase 2 rollback switch for the `activity_log` collection (first soft-join target — the Mongo collection is *already* populated by the existing 3rd-tier fallback in `db_ops.supa_insert_activity_log` whenever both PG and the Supabase legacy tier raise; Phase 2 adds a mirror on the PG-success branches of `supa_insert_activity_log` + `supa_clear_activity_log` so Mongo sees *every* audit write, not only the PG-failure ones; default name — no `_FLAG_NAME_OVERRIDES` entry because the default singularisation already yields the correct name; independent of the other `MONGO_*_WRITES` flags). `1`/unset = best-effort Mongo mirror on every PG-success activity-log insert + on the bulk-clear purge (2 sites in db_ops.py covering all 8 route-level callers in admin_settings / admin_logs / admin_auth_users); `0` = disable the mirror but **leaves the existing PG-failure fallback path intact** — flipping this flag off does NOT break the failure-mode audit-trail safety net. Counter keys: `activity_log.{success,fail,skipped_disabled,skipped_no_db}`. |
| `MONGO_NOTIFICATION_WRITES` | optional env (default `1`) | V4 §13 / ADR-0001 Phase 2 rollback switch for the `notifications` collection (second soft-join target — the Mongo collection is *already* populated by the existing 3rd-tier fallback inside `db_ops.supa_insert_notification` and `supa_delete_notification` whenever both PG and the Supabase legacy tier raise; Phase 2 adds a mirror on the **PG-success** branches of both helpers so Mongo sees *every* admin notification write + per-id delete, not only the PG-failure ones; **trailing 's' singularised** by the default `rstrip('S')` rule — no `_FLAG_NAME_OVERRIDES` entry needed; independent of the other `MONGO_*_WRITES` flags). `1`/unset = best-effort Mongo mirror on every PG-success notification insert + per-id delete (2 sites in db_ops.py covering every route-level caller — admin notification CRUD plus push-notification dispatch helpers in `deps.py` / `cloudflare_client.py` all funnel through these centralised helpers); `0` = disable the mirror but **leaves the existing PG-failure fallback path intact** — flipping this flag off does NOT break the failure-mode safety net. Counter keys: `notifications.{success,fail,skipped_disabled,skipped_no_db}`. |

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

### Removed (Task #347 / #556 — never re-add without a V5 spec change)

Historical AI / payment / transport vars — all retired:
`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `XAI_API_KEY`,
`BEDROCK_PROXY_AUTH_TOKEN`, `STRIPE_SECRET_KEY`,
`STRIPE_WEBHOOK_SECRET`, `RAILWAY_TOKEN`, `QUGE5_*`, `GROQ_API_KEY`,
`CEREBRAS_API_KEY`, `FIREWORKS_API_KEY`, `OPENROUTER_API_KEY`.

Historical email transport vars (Task #556 — SES is the sole
transactional path; the legacy provider-flag knobs are retired and
must NOT be re-introduced): the previous `EMAIL_PROVIDER` flag, the
previous `EMAIL_FALLBACK` flag, the previous SendGrid API key, and
the previous Resend API key. The umbrella CI guard
(`artifacts/syrabit-backend/scripts/ci/check_canonical_delegation.py`)
fails the build if any of these names re-appears in code, env
templates, lockfiles, IaC, deploy workflows, the Cloudflare Workers
under `workers/`, or this document.

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
