# Canonical Specialist-Delegation Map (locked)

> **Status: LOCKED — 2026-05-07** (Task #559 — supersedes the four-cloud cost-share matrix shipped under Task #489 and amended by #513 / #549 / #551.)
> **Owner:** founder@syrabit.ai
> **Source of truth:** [`infra/v4-locked-architecture.md`](./v4-locked-architecture.md) §0, §3, §4, §17 + the Task #549 founder-locks (`$100/mo` cap, voice paywall, 60/80/95 % degradation ladder).
> **CI guard:** [`artifacts/syrabit-backend/scripts/ci/check_canonical_delegation.py`](../artifacts/syrabit-backend/scripts/ci/check_canonical_delegation.py) (the legacy `scripts/check_dead_providers.py` is now a shim over this guard).
> **Pre-deploy gate:** `.github/workflows/azure-container-apps-deploy.yml` job `canonical_delegation_gate` (added in Task #559) runs the umbrella before the existing `budget_ceiling_gate`.

This document is the **single per-feature canonical map** of which provider owns which production responsibility, and which provider (if any) is the strict fallback. It supersedes the percentage-cost-share matrix that lived here before — the cost split is documented in `replit.md` and audited via the credit-runway memo, but it is **not** the routing contract. The routing contract is the per-feature table in §A below.

The new map is "**strict specialist delegation**": every production feature has a sole owner and at most one named fallback. Silent multi-provider rotation pools are forbidden by V4 §12 ("no silent fallbacks") and now by the umbrella guard above.

---

## §A — Per-feature canonical map (locked)

| Feature | Primary (sole owner) | Strict fallback (single, named) | Enforcement |
|---|---|---|---|
| **English chat dispatch** | **Vertex Gemini 2.5 Flash** (drains GCP startup credits) — head selected at runtime by `cost_caps._select_chat_primary()` (60 s monotonic cache) | **Workers-AI Llama-3.2-3B** (Cloudflare free tier). The chain FLIPS so Llama is the head when projected GCP credit runway falls to ≤ 90 days. Operator override: `CHAT_PRIMARY_OVERRIDE=vertex|workers_ai_llama32_3b` (unsupported values are logged + ignored — V4 §12). | Umbrella guard `_check_chat_chains` + `_check_chat_primary_selector`; runtime test `tests/test_provider_priority_locked.py`. |
| **Assamese chat dispatch** | **Sarvam Indic chat** (`sarvam-m`, weight 10000) | **Cloudflare Workers-AI IndicTrans2** (en-indic neural MT, weight 0 — reachable only via exclusion redraw). Strict-chain exhaustion surfaces 503; **no** silent downgrade to generic `workers_ai`. | Umbrella guard `_check_chat_chains`; `tests/test_assamese_routing_chain_e2e.py`. |
| **Content formatter (notebook / study / exam polish)** | **Vertex Gemini 2.5 Flash** via `content_formatter.format_content` | **Workers-AI Llama-3.3-70b** → passthrough on dual outage / Assamese purity-gate rejection. Audit field `formatted_by` written to every Mongo doc. | Umbrella guard `DIRECT_VERTEX_FORMAT_IMPORT` (forbids bypassing the dispatcher); `tests/test_content_formatter_dispatch.py`. |
| **Embedding (English / unknown lang — primary)** | **Cloudflare Workers AI custom embed worker** (`embed.syrabit.ai`, Gemma-300M + Qwen3-0.6B mean-pooled to 1024-dim, matches Pinecone) | **Cache-only degraded mode** (Option D) — fresh content with no cached vector enqueues to **AWS SQS `syrabit-reembed-queue`**; Lambda drains on recovery. **No live third-party embedder.** Vertex multilingual embedding is *retired* (Task #490). | V4 §15 §4; `tests/test_embed_failover_degraded_mode.py`. |
| **Embedding (Assamese / Indic — language-gated)** | **AWS Bedrock `cohere.embed-multilingual-v3`** (1024-dim, `BEDROCK_EMBED_REGION=us-east-1`, reuses existing AWS IAM via the `bedrock-runtime:InvokeModel` permission on the per-feature OIDC role; **no Cohere SDK, no `COHERE_API_KEY`** — `scripts/ci/check_canonical_delegation.py` enforces) | **Workers AI custom embed worker** (same row above) on IAM denial / model-access not granted / throttle / dim mismatch / `EMBED_INDIC_PROVIDER` kill-switch / `RAG_EMBEDDING_PROVIDER_FORCE=workers_ai_custom` / MeterD Indic sub-cap tripped. The fallback is per-call (the route flips back to Bedrock the moment the gate clears) — there is no cross-region failover for the Bedrock leg. | Task #27 (partial reversal of #491); `cost_caps.INDIC_EMBED_MONTHLY_USD_SUBCAP=$5/mo` inside the global $100 cap; `providers/cohere_bedrock_embed.py` + `llm.call_embed_with_dispatch`; `tests/test_cohere_bedrock_embed.py`. |
| **Rerank** | **Pinecone Rerank v0** | None (single-source by V4 §15). | `tests/test_provider_dispatch.py`. |
| **Vector store** | **Pinecone** (`aws-ap-south-1`, 1024-dim) | MongoDB Atlas (`mongodb_atlas`, weight 0 — disaster-only). | Pinecone-dimension lock (replit.md gotcha). |
| **TTS / STT / Indic speech** | **ElevenLabs** (TTS primary), **Deepgram** (STT primary), **Sarvam** (Assamese chat LLM only) | TTS: Deepgram Aura-2 → Workers AI; STT: Workers-AI Whisper; Indic TTS: Google Neural2; Indic STT: Google Chirp_2 → Workers-AI Whisper. Azure Speech retired by Task #552 §G-R. **All three voice routes (`/tts`, `/stt`, `/voice/voice`) sit behind `Depends(require_paid_plan)`** (Task #549). | Umbrella guard `_check_voice_paywall`; `tests/test_voice_paid_gate.py`. |
| **Indic→English translation fallback** | **Workers-AI IndicTrans2** | Generic Workers AI last-resort only. Azure Translator was retired by Task #552 §G-R alongside Azure Speech (the entire Azure AI surface was wound down). | `tests/test_translate_fallback_chain.py`. |
| **Vision / OCR** | **Workers AI** (single source) | None — Vertex vision retired by Task #554. | (Task #554 amendment.) |
| **HTTP API origin (FastAPI hot path)** | **Azure Container Apps** `syrabit-backend` (`eastus2`) | DR cutover to `westus3` via Bicep runbook (no parallel hot region — V4 §8 explicit SPOF). | Bicep template drift (replit.md gotcha). |
| **Edge worker (WAF, OriginGate, edge cache)** | **Cloudflare Workers** (`syrabitworker`, `syrabitfrontend` Pages) | None — sole carrier of `X-Origin-Auth: $BACKEND_ORIGIN_SECRET`; backend rejects everything else with 403. | OriginGate lock-step rotation (replit.md gotcha). |
| **Async event backbone (SQS / Lambda / EventBridge / Step Functions)** | **AWS** (`ap-south-1`) | None (sole owner). | `infra/aws/lambda/manifest.json` + umbrella `_check_aca_jobs_manifest`. |
| **Object storage (warm)** | **Cloudflare R2** (chapter PDFs, audio, exports, **final backups**) | None. | V4 §11. |
| **Object storage (cold compliance, 7-yr DPDP retention)** | **AWS S3 Glacier Deep Archive** (3 buckets: receipts / content snapshots / CW logs) | None — cold tier only; warm reads stay on R2. | `infra/aws/glacier-archive.tf` + `glacier-restore-runbook.md`. |
| **User-data store (sessions, profiles, conversations, chunk metadata)** | **MongoDB Atlas** (`ap-south-1`, AWS-peered VPC) | Replit Postgres (legacy — being ripped out per V4 §13 Phases 2 → 5; ADR-0001). | ADR-0001 + Phase-2 dual-write counters. |
| **Edge RAG cache** | **Cloudflare Vectorize** | None (cache-only; never primary). | V4 §11. |
| **DNS apex (`syrabit.ai`, `api.syrabit.ai`, `embed.syrabit.ai`)** | **Cloudflare DNS** | None. | V4 §0. |
| **Secrets source-of-truth** | **Azure Key Vault** (`syrabit-prod-kv`) | AWS Secrets Manager + Cloudflare Secrets are **read-only** replicas, hash-validated nightly. | V4 §6. |
| **Observability (tracing)** | **Sentry Performance** (primary) | **GCP Cloud Trace** (long-retention backstop). Header propagation `sentry-trace` + `traceparent` + `baggage` end-to-end. *(Task #558 will narrow this to GCP Cloud Trace single exporter — see §C.)* | V4 §7. |
| **Cost / credit-burn telemetry → Meter A/B/C/D** | **GCP Cloud Billing** + **Cloudflare AIG cost headers** + **AWS Cost Explorer** | Meter D LOCKS `chat:cheaponly=1` at the perpetual `$100/mo` cap (Task #549). Notify-only Slack alert at 80 %; **no auto-flip**. | `scripts/check_budget_ceiling.py`. |
| **Transactional email** | **AWS SES** sole tier-1 (verified senders in `us-east-1` primary / `ap-south-1` regional flip via `SES_REGION`; DKIM/SPF/DMARC published on Cloudflare DNS). No fallback, no break-glass — Task #556 retired the previous Azure Marketplace vendor and the dual-provider env knobs (V4 §12 no silent fallbacks). Bulk / digest fan-out goes through the separate Cloudflare Email Workers `bulk-email` Worker via `bulk_email.send_bulk` (not a transactional fallback — different surface). | Umbrella `TODO_556_PATTERN` enforced — bare-token bans the legacy vendor SDKs / API-key env vars / provider-flag knobs across backend + frontend + workers + IaC + lockfiles + this document. |
| **Web-push notifications** | **Self-hosted VAPID push** (`pywebpush` + `py-vapid`, ACA env `WEB_PUSH_VAPID_PRIVATE_KEY` from Azure KV `WEB-PUSH-VAPID-PRIVATE-KEY` + `WEB_PUSH_CONTACT` mailto for the RFC-8292 `sub` claim; W3C `PushSubscription` shape enforced at `/push/subscribe`). The matching VAPID public key is *derived* from the private PEM at request time so there is no second secret to keep in sync. | None — Firebase Cloud Messaging fully retired (Task #557, 2026-05-07); `firebase_admin` / `FCM_SERVER_KEY` / `FIREBASE_SERVICE_ACCOUNT` banned by the umbrella `TODO_557_PATTERN`. The 30-day FCM → VAPID rollout (`pending → tombstoned → purged`) is owned by `scripts/migrate_fcm_to_vapid.py`; admin status at `GET /api/admin/push/migration-status`. | Umbrella `TODO_557_PATTERN` (active); `tests/push/*` (30 cases). |

---

## §B — Strict-fallback rule (V4 §12 in code)

Every row in §A obeys the same rule: **at most one named fallback per feature, declared in code, exercised by tests, never silent.** Concretely:

1. **No multi-provider rotation pools.** The `POOL_WEIGHTS` table in `config.py` retains weights for legacy reasons (round-robin draws inside a feature pool that genuinely has interchangeable providers — e.g. Workers-AI Mistral-7B / Llama-3.2-3B inside `content`), but **no production feature** above ever silently advances past its named fallback. When the strict chain exhausts, the request fails loud with the documented HTTP status (chat → 503; voice → 402 for free users; embed → cache-only degraded mode).
2. **Operator overrides are explicit.** `CHAT_PRIMARY_OVERRIDE`, `RAG_EMBEDDING_PROVIDER_FORCE`, `EMBED_DEGRADED_MODE` — every override is an env-var named in the row above, surfaced in `replit.md`, and ignored when the value is unrecognised (rather than silently degrading). Email has **no operator override knob** any more — the legacy provider-flag env vars were retired with Task #556 because SES is the only supported transactional path.
3. **Azure OpenAI is fully retired** (Task #554). The umbrella bans `azure_openai|AzureOpenAI|AZURE_OPENAI_*|gpt-4.1-nano` bare-token across the tree. **Task #552 §G-R (2026-05-09)** retired the remaining Azure Speech + Translator surfaces too: `providers/azure_speech.py` and `services/backend/azure_ai/{speech,translator}.py` are deleted, the `AZURE_SPEECH_*` / `AZURE_TRANSLATOR_*` env vars are no longer read, and voice/translate chains now run on ElevenLabs / Deepgram / IndicTrans2 / Workers-AI exclusively.
4. **Azure ACA is the sole HTTP origin.** No ECS / App Runner / Cloud Run for `syrabit-backend`; DR is the `eastus2 → westus3` Bicep cutover runbook (V4 §8 explicit SPOF).
5. **GCP hosts nothing.** Vertex Gemini 2.5 Flash, Cloud Trace, Web Risk, Discovery Engine, Cloud Billing — all consumed via API. No Cloud Run, no Cloud Tasks, no Cloud Scheduler, no Compute, no GCS-as-website (Task #489 — still locked).

---

## §C — TODO-gated rows (documented now, banned later)

These rows are part of the canonical map but the umbrella guard does **not** ban them yet because the work to migrate the codebase has not landed on `main`. The guard carries explicit `TODO Task #558` markers so the bans flip on the moment that task merges — preventing a documented-but-unenforced row from quietly drifting back in. (Tasks #556 and #557 have shipped; their bans are now active.)

| Row | Pending task | What flips on |
|---|---|---|
| ~~Email (SES sole tier-1)~~ | **Shipped — Task #556 (2026-05-07)** | Already enforced — see §A "Transactional email" row above. The umbrella's `TODO_556_PATTERN` is **active** and bans the legacy vendor SDK names, API-key env vars, helper names, and the previously documented provider-flag env knobs across backend + frontend + workers + IaC + lockfiles + this canonical doc. |
| ~~Web-push (self-hosted VAPID)~~ | **Shipped — Task #557 (2026-05-07)** | Already enforced — see §A "Web-push notifications" row above. The umbrella's `TODO_557_PATTERN` is **active** and bans `firebase_admin|FCM_SERVER_KEY|FIREBASE_SERVICE_ACCOUNT` across the tree. Required env: `WEB_PUSH_VAPID_PRIVATE_KEY` (KV → Bicep secretRef) + `WEB_PUSH_CONTACT` (RFC-8292 `sub` mailto). The public key is derived from the private PEM at request time — no second secret. |
| Observability narrowing | **Task #558** | Bans Sentry tracing literals + multiple OTEL exporters; only `OTEL_TRACES_EXPORTER=googlecloud` survives. Cloud Trace becomes the sole tracing sink. |

The umbrella's `TODO_557_PATTERN` / `TODO_558_PATTERN` constants carry the exact regex that will be enabled — search `scripts/ci/check_canonical_delegation.py` for `# TODO Task #557` / `# TODO Task #558` to flip them on in the same PR that lands the parent task.

---

## §D — Cost-share annotation (informational, not a routing rule)

Replit.md ships the headline cost-share annotation: **40 % Cloudflare / 30 % GCP / 15 % Azure / 10 % AWS / 5 % other (Pinecone, Mongo, ElevenLabs, Deepgram)**. This number is the **outcome** of the strict delegation above plus the founder-locked `$100/mo` ceiling — it is **not** an enforceable routing target. If any row in §A or §C above changes, the cost-share is re-derived from the new map; the cost-share never overrides the map.

---

## §E — Cutover protocol

Adopting the per-feature canonical map without breaking production is a 10-step protocol owned by [`artifacts/syrabit/docs/infra/canonical-delegation-cutover.md`](../artifacts/syrabit/docs/infra/canonical-delegation-cutover.md). The cutover runbook covers:

1. Land the umbrella guard in dry-run mode.
2. Migrate the chat-dispatch unit-tests + `_select_chat_primary` snapshot.
3. Verify static `PROVIDER_PRIORITY` membership for English + Assamese chains.
4. Wire the umbrella into the deploy workflow as a hard gate.
5. Decommission the legacy `check_dead_providers.py` callsite (now a shim).
6. Rotate operator overrides to documented env-var names.
7. Confirm the voice paywall is wired on `/tts`, `/stt`, `/voice/voice`.
8. Confirm `formatted_by` audit field present in all post-#494 docs.
9. Tag the canonical-map version into the V4 changelog (§17).
10. Snapshot the cost-share post-cutover and refresh `replit.md`.

Each step is a binary acceptance check the operator runs once and ticks off in the runbook.

---

## §F — Decision log

- **2026-05-07 (Task #559)** — Document rewritten as the per-feature canonical map. Old four-cloud cost-share matrix moved to `replit.md` and the runway memo. Umbrella CI guard `scripts/ci/check_canonical_delegation.py` introduced; `scripts/check_dead_providers.py` becomes a shim. SES / web-push / observability rows documented but TODO-gated (deferred to Tasks #557 + #558).
- **2026-05-07 (Task #557 merged)** — Web-push row in §A flipped from Firebase Cloud Messaging to self-hosted VAPID push (`pywebpush` + `py-vapid`). §C row marked shipped; umbrella `TODO_557_PATTERN` is now active and bans `firebase_admin` / `FCM_SERVER_KEY` / `FIREBASE_SERVICE_ACCOUNT`. Required ACA env extended with `WEB_PUSH_VAPID_PRIVATE_KEY` (KV → Bicep secretRef) + `WEB_PUSH_CONTACT`.
- **Earlier history (2026-05-03 → 2026-05-07)** — Task #489 lock-in, #513 cost-minimisation, #549 perpetual `$100/mo` cap, #551 AWS expansion, #554 Azure-OpenAI removal, #555 voice paywall. All superseded by §A above.
