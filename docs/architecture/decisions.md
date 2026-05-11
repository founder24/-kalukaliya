# Architecture Decisions

Full, verbatim record of every architecture decision previously inlined under `## Architecture decisions` in `replit.md`. The README now keeps only a short index pointing back here.

## Supabase-only auth cutover — split into prep + destructive PRs (Task #47, 2026-05-09)

**Decision: split Task #47 into two PRs at user direction.** The original task spec rolled the full Supabase-sole-auth cutover into one ticket: delete the four legacy email/password endpoints in `routes/auth.py`, replace `_supa_client.auth.get_user(token)` with a JWKS-local verifier across `auth_deps.py`, rotate the cookie name to `syrabit_session_v2`, drop the frontend signup form, and execute it all during a weeknight 23:00–01:00 IST maintenance window. That diff is ~500 lines across 4+ files on the highest-risk surface in the codebase (a wrong move locks every active user out). Per founder guidance on 2026-05-09 the work was split:

1. **Prep PR (this entry — non-destructive, safe to merge any time).** Lands the JWKS local verifier with cache, the synthetic canary, the Mongo↔Supabase reconciliation script, the cutover/rollback runbook, and the lock §13 + matrix annotations. **Nothing in the request hot path calls the new verifier yet** — `routes/auth.py:supabase_session` still HTTPs `_supa_client.auth.get_user(token)` and still mints `JWT_SECRET`-signed `syrabit_session` cookies via `create_access_token`. The lock §13 row stays PARTIAL.
2. **Destructive PR (next task — lands during the maintenance window).** Replaces `auth.get_user(token)` with `supabase_jwks.verify_supabase_jwt(token)` in `routes/auth.py:supabase_session` and every authed dep in `auth_deps.py`; rotates the cookie name to `syrabit_session_v2`; deletes the four legacy email/password endpoints; removes the frontend signup form. Gated behind `SUPABASE_ONLY_AUTH=1` with a 48h dual-cookie grace so a single env-var flip rolls back without a redeploy.

**Why JWKS local verify (vs `auth.get_user` round-trip).** The current production path makes one outbound HTTPS call to Supabase per authed request:

| Lever | `auth.get_user(token)` (today) | `verify_supabase_jwt(token)` (post-cutover) |
|---|---|---|
| Per-request latency | ~30–80ms | ~0.5ms RSA verify |
| Supabase auth-API outage blast radius | every authed request 401s | invisible until a key actually rotates (5min stale-grace) |
| Free-tier request budget | counts every authed call | zero (verify is local) |
| Crypto guarantee | identical (Supabase signs the JWT either way) | identical |

**Cache windows (founder-locked at the defaults).** 1h fresh + 5min stale-on-error. Inside the fresh window, no network. Inside the stale window, refresh is attempted but a failure serves the existing cache (and emits `Syrabit/Auth::SupabaseJwksStale=1`). Outside both windows, `SupabaseJWKSError` raised → 401. The 1h fresh window is the upper bound on how long a revoked Supabase signing key keeps validating tokens after rotation; Supabase rotates signing keys on the order of years, so 1h is well inside the cadence and matches the `google.auth.jwt` default.

**Reconciliation contract (`scripts/verify_supabase_mirror.py`).** Run pre-cutover. Pulls every active Mongo user with an email, intersects against `supabase.auth.admin.list_users`. Two failure buckets:
- **HARD BLOCK** — `auth_provider != 'google'` users with no Supabase row. Cutover cannot proceed; run `scripts/sync_users_to_supabase.py` first.
- **SOFT WARN** — `auth_provider == 'google'` users with no Supabase row yet. Pre-cutover this is fine (Supabase auto-creates them on first OAuth sign-in). Post-cutover they will be silently locked out until they sign in again, which is acceptable given they were already going through Google.

**Canary (`aca_jobs/supabase_auth_canary.py`).** Mints a token via a dedicated `SUPABASE_CANARY_EMAIL/PASSWORD` user, verifies it through `supabase_jwks.verify_supabase_jwt`, emits `Syrabit/Auth::SupabaseAuthCanary` (1=pass, 0=fail). EventBridge `rate(5 minutes)` wiring is deferred to the destructive PR (alongside the alarm rule) so this PR has zero TF churn. The cutover runbook treats 3 consecutive red passes as a hard go/no-go.

**`JWT_SECRET` survives.** Retained ONLY for short-lived service-to-service tokens (e.g. the edge-proxy → backend OriginGate handshake, internal admin signing). Never for user sessions after cutover. `ADMIN_JWT_SECRET` and the Cloudflare-Access-gated admin path are explicitly out of scope for Task #47.

**Rollback drill.** Documented in `docs/runbooks/task-47-supabase-auth-cutover.md` §"Rollback drill". Single env-var flip (`SUPABASE_ONLY_AUTH=0` via `az containerapp update`), rolling restart, ~60s back to the legacy `JWT_SECRET` cookie path. The 48h dual-cookie grace means users holding `syrabit_session_v2` re-login once on rollback (same UX as the cutover).

## OCR scratch storage — keep on R2, retire Azure Blob row (Task #46, 2026-05-09)

**Decision: Path A.** The 2026 lock §4.2 row "Azure Blob — temporary OCR/media" had been PARTIAL since the lock was written, on the assumption that OCR scratch bytes would land in an Azure Blob container co-located with ACA. Audit during Task #46 found two facts that change the answer:

1. **OCR has no persisted scratch tier today.** Both OCR endpoints — `POST /api/ai/ocr-image` (`artifacts/syrabit-backend/routes/ai_chat.py:566-654`, single-image chat composer) and `POST /api/admin/pyq/agentic-process` (`artifacts/syrabit-backend/routes/pyq.py:582`, PYQ agentic pipeline) — read the upload into a bounded in-memory buffer (`_OCR_MAX_BYTES=8MB`), magic-byte sniff, and pass the bytes straight to Vertex Vision (`vertex_services.ocr_image` / `vertex_services.analyze_image`). The extracted text goes into Mongo. No `r2_upload`, no `azure_blob_storage`, no temp-file write.
2. **No Azure Blob client is wired in the backend.** `rg "azure.*blob|BlobServiceClient"` over `artifacts/syrabit-backend/` returns zero hits. Standing up an Azure Blob OCR-scratch tier would mean: new SDK dependency, new Key Vault secret + managed-identity binding, new lifecycle rule, new feature flag, new test surface — all to persist bytes that the current pipeline doesn't even keep across a single request.

**R2 vs Azure Blob (the comparison the task asked for).**

| Lever | R2 (chosen) | Azure Blob (rejected) |
|---|---|---|
| $/GB-month | $0.015 (R2 standard) | $0.0184 (Hot LRS, centralindia) |
| Egress | $0 (R2 free egress) | $0.087/GB cross-region, ~$0.0 inside same region |
| Already wired | Yes — `r2_storage.py` carries chapter PDFs, audio, admin uploads | No — would need new module + KV secret + MI binding |
| Latency from ACA (eastus2) | ~30-60ms via CF edge | ~5ms (same region) — but irrelevant: OCR is bound by Vertex Vision (~800-2000ms), so storage RTT is in the noise |
| DPDP residency (IN-region) | Cloudflare PoPs in BOM/MAA serve IN traffic; bytes only transit R2 if persisted (currently never) | centralindia region — cleaner on paper, but moot when nothing is persisted |
| Lifecycle / retention | R2 lifecycle rules already in place on `syrabit-media` | Would need new rule (7-day delete) |
| Blast radius if leaked | Same SA role as warm media (`r2_storage.r2_*`) | New surface, new RBAC, new audit trail |

**Action taken (Path A — keep R2).** No runtime code change is required (the storage surface is already wired; OCR just doesn't persist today). The lock §4.2 row is renamed from "Azure Blob — temporary OCR/media" to **"OCR scratch + warm media (Cloudflare R2 — Azure Blob retired)"** and flipped from PARTIAL → **IMPLEMENTED** with concrete `source_paths = [r2_storage.py, routes/ai_chat.py, routes/pyq.py]`. R2 is now the **named canonical surface** for OCR scratch — any future "persist OCR bytes" feature MUST land on R2 (not Azure Blob); Azure Blob is retired for this row. The matrix row mirrors the lock. `scripts/check_architecture_lock.py` stays green (29 sections / 107 rows verified). The Azure landing-zone runbook §10 "intentionally not here" gains a row pointing OCR scratch at R2.

**When this decision should be revisited.** If a future feature has to persist OCR bytes (e.g., a "let me re-run OCR with a different model" UX, or a DPDP-mandated audit trail of every uploaded PYQ scan), open a fresh task that picks between R2 and Azure Blob with the *then*-current latency and cost numbers, and update this entry as a follow-up. Founder locks ($100/mo cap, V4 §12 no silent fallbacks) bind any such re-introduction.

**Doc-closure follow-up — Task #48 (2026-05-09).** Task #48 was the auto-proposed mirror of #46 covering the two doc surfaces #46 didn't touch: (1) `replit.md` §4.2 Azure summary no longer lists "Blob (OCR scratch)" — replaced with explicit R2-canonical wording matching this entry; (2) `docs/architecture/audit-2026-05-09-baseline-vs-current.md` row for "Azure Blob — temporary OCR/media" flipped from PARTIAL ("note still accurate") to RETIRED with a #46 attribution. No runtime / matrix / lock semantic change — both files now agree with the IMPLEMENTED row above. `python scripts/check_architecture_lock.py` stays green at 29 sections / 107 rows.

## Voice canonical specialists (Task #552 §G, 2026-05-07; reversed by §G-R, 2026-05-09)

**Voice canonical specialists (Task #552 §G, 2026-05-07; reversed by §G-R, 2026-05-09):** AssemblyAI fully retired (`providers/assemblyai.py` deleted; `ASSEMBLYAI_API_KEY` / `ASSEMBLYAI_STT_MODEL` env knobs removed; `_ASSEMBLYAI_KEY` symbol dropped from `config.py`; `assemblyai` removed from `PROVIDER_PRIORITY['stt']`, `POOL_WEIGHTS['stt']`, `PROVIDER_CREDITS`, `_CF_PROVIDER_SLUGS`, `llm._PROVIDER_DEFAULT_MODELS`, `llm._PROVIDER_CANONICAL`, the admin credit / probe / panel registries, and the `/admin/syra/stt` fallback). **Task #552 §G-R (2026-05-09 reversal):** the original §G removal of Deepgram Aura-2 TTS is **un-done** — Aura-2 is now the canonical English-TTS primary and ElevenLabs is demoted to the named fallback. Trigger: ElevenLabs free-plan API gate would have required a $5/mo Starter upgrade to ship a single TTS byte; Deepgram already had the same `$500` startup-credit balance and Aura-2 covers the same use case at lower per-character cost. `providers/deepgram.py` re-exposes `synthesize()` (Aura-2, English-only, 2000-char per-request hard limit, default voice `aura-2-thalia-en` — override via `DEEPGRAM_TTS_MODEL`); `routes/voice.py` keeps the `_tts_deepgram` dispatch branch alongside `_tts_elevenlabs`. (**Task #2 2026 blueprint amendment:** ElevenLabs was subsequently restored as the English-TTS PRIMARY and Deepgram Aura-2 demoted to the named fallback after the $5/mo Starter upgrade was budgeted into the $100 ceiling — see the "Voice canonical (Task #2 2026 blueprint)" entry in `replit.md`. Live `PROVIDER_PRIORITY['tts']` is now `[elevenlabs, deepgram, workers_ai]`, with workers_ai as the locked free-tier tail per `test_tts_stt_voice_have_workers_ai_tail`.) Canonical voice map (post-§G-R): **Deepgram Aura-2 = sole English TTS primary** + **ElevenLabs eleven_multilingual_v2 = named English-TTS fallback**; **Google Cloud TTS Neural2 = sole Indic TTS** (hi / bn / as — Aura-2 is English-only so the existing Indic-first wiring in `routes/voice.py` is unchanged); **Deepgram Nova-3 = sole English STT**; **Google Cloud STT Chirp_2 = sole Indic STT** (wired in `routes/voice.py:_transcribe_with_indic_first` so an Indic prompt ALWAYS hits Chirp_2 before the English-pool round-robin — V4 §12, no silent downgrade). Workers AI Whisper / Aura is the absolute last-resort tail. Admin Syra orb (`/admin/syra/tts` / `/admin/syra/stt`) follows the same map. The `/api/voice/health` endpoint reports Deepgram (STT + TTS) + ElevenLabs + Workers AI + Google Chirp_2 / Neural2. CI ban (umbrella `check_canonical_delegation.py`) is now narrowed to `assemblyai|AssemblyAI|ASSEMBLYAI_API_KEY|ASSEMBLYAI_STT_MODEL` (the previous Aura-2 / `_tts_deepgram` / `deepgram.synthesize(` bans were removed by §G-R). The `@cf/deepgram/aura-2-*` Workers-AI model IDs in `providers/cloudflare_ai.py` remain allowlisted as CF-hosted Aura.

## Sarvam Assamese-chat facade (Task #553, 2026-05-07)

**Sarvam Assamese-chat facade (Task #553, 2026-05-07):** `providers/sarvam.py` exposes a typed async `chat()` entry point over the existing `deps.sarvam_llm_client` HTTP/2 pool, returning a `ChatResponse` dataclass and raising `SarvamUnavailable` (5xx / timeout / transport / no-client) or `SarvamRateLimited(reason=...)` (upstream 429 *or* per-user-monthly-cap exhaustion). Per-user cap defaults to `SARVAM_PER_USER_MONTHLY_CAP=30` (Redis-keyed `sarvam:user:{id}:{YYYYMM}`, ~32-day TTL); set to `0` to disable and leave the edge worker's `CHAT_CAP_MONTHLY=30` as sole enforcer. Anonymous callers (`user_id=None`) skip the local check — the edge keys on `anon-id` and we don't see it. Every call records the canonical `llm._record_llm_call("sarvam","sarvam-m",..., feature_key="sarvam_chat")` audit row plus a process-local 1h `(ts, success)` ring used by `success_rate_snapshot()`. Admin tile lives at `GET /api/admin/health/sarvam` (`routes/admin_sarvam_health.py`, registered in `server.py:2355`) and is rendered by `SarvamHealthCard.jsx` inside the AdminHealth "Sarvam Purity" tab — surfaces `ok / err / success_rate / per_user_monthly_cap` and an `alert=true` flag when success-rate < **95 %** over the trailing hour with ≥ **20 samples** (per-replica sensitivity, drives the Sentry <95%/1h alert). `SARVAM_API_KEY` ships through ACA via the new `sarvam-api-key` Bicep secret (Azure KV `SARVAM-API-KEY` → AWS SM `syrabit/prod/sarvam-api-key` + Cloudflare Secrets binding `SARVAM_API_KEY`, mirrored read-only per the existing secrets policy). Tests: `tests/providers/test_sarvam.py` (12 hermetic cases) + `tests/integration/test_sarvam_smoke.py` (gated on `SARVAM_INTEGRATION=1`). **Out of scope:** PROVIDER_PRIORITY wiring (already locked in `[sarvam, workers_ai_indic]`), Hindi/Bengali, Azure-OpenAI removal (already done by Task #554), migrating the live in-place `llm._call_sarvam_llm` dispatcher to the facade.

## Cost split (post-Task #559 snapshot, 2026-05-07)

**Cost split (post-Task #559 snapshot, 2026-05-07):** **40 % Cloudflare / 30 % GCP / 15 % Azure / 10 % AWS / 5 % other (Pinecone, Mongo, ElevenLabs, Deepgram).** This is the *outcome* of the per-feature canonical map (see "Canonical specialist delegation" below) plus the founder-locked `$100/mo` ceiling — it is **not** an enforceable routing target. The previous 40/30/20/10 cost-share table is superseded.

## Canonical specialist delegation (Task #559, 2026-05-07)

**Canonical specialist delegation (Task #559, 2026-05-07):** Every production feature has exactly **one** canonical primary provider and **at most one** named, strict fallback. Source of truth: [`infra/four-cloud-delegation.md`](infra/four-cloud-delegation.md) §A (per-feature map), [`infra/v4-locked-architecture.md`](infra/v4-locked-architecture.md) §17 (V4 lock), and [`docs/architecture/adr/0003-canonical-strict-specialist-delegation.md`](docs/architecture/adr/0003-canonical-strict-specialist-delegation.md) (ADR). Enforced by the umbrella CI guard `artifacts/syrabit-backend/scripts/ci/check_canonical_delegation.py` (the legacy `scripts/check_dead_providers.py` is now a shim) and wired into the deploy workflow as the `canonical_delegation_gate` job, which runs **before** `budget_ceiling_gate`. Currently enforced rows: English chat dynamic 2-chain (`vertex` / `workers_ai_llama32_3b` selected by `cost_caps._select_chat_primary()`), Assamese strict `[sarvam, workers_ai_indic]`, Azure-OpenAI bare-token ban, voice paywall on `/tts` `/stt` `/voice/voice`. SES sole tier-1 / self-hosted web-push (Task #557) and observability narrowing (Task #558) are documented but **TODO-gated** until those parents merge — the `TODO_557_PATTERN` / `TODO_558_PATTERN` regexes in the umbrella flip on then. Adoption protocol: 10-step runbook at [`artifacts/syrabit/docs/infra/canonical-delegation-cutover.md`](artifacts/syrabit/docs/infra/canonical-delegation-cutover.md).

## Embedding strategy

**Embedding strategy:** Primary is Gemma-300M + Qwen3-0.6B on Cloudflare Workers AI (1024-dim) to Pinecone. On primary outage, system enters cache-only degraded mode, queuing fresh content for replay.

## GCP credit-runway publisher (Task #565, 2026-05-08)

**GCP credit-runway publisher (Task #565, 2026-05-08):** Daily `chat-credit-runway` Lambda (`artifacts/syrabit/services/backend/lambda_batch/chat_credit_runway.py`, EventBridge cron `cron(30 3 * * ? *)`, registered in `infra/aws/lambda/manifest.json` `auxiliary_jobs` + `artifacts/syrabit/infra/aws/lambda-batch-jobs.tf`) queries the GCP Billing BigQuery export, computes `remaining_credits = GCP_TOTAL_CREDITS_USD − cumulative_cost` and `runway_days = remaining / (cost_30d / 30)`, and writes the integer to Upstash Redis at `chat:credit_runway_days` (TTL 48 h). `cost_caps._projected_chat_runway_days()` resolution order is now: 1) `CHAT_CREDIT_RUNWAY_DAYS` env (operator manual override) → 2) Redis key (cron-published) → 3) `GCP_CREDITS_REMAINING_USD` env + MeterD MTD burn (legacy fallback). The Task #554 selector's existing 60 s in-process cache picks up the new Redis value on the next refresh — no ACA redeploy required (the Lambda lives in AWS, not Azure). Two CW alarms ride on `Syrabit/Cost::ChatCreditRunwayDays` (`Source=lambda` dimension): `chat-credit-runway-stale` (`treat_missing_data=breaching`, period 86 400 s) pages on-call when the metric is missing for >24 h, and `chat-credit-runway-low` fires when projected runway drops below 60 days for 2 consecutive daily publishes (early warning past the 90 d flip threshold). The Lambda also `sentry_sdk.capture_message`s on every compute / publish failure (V4 §12 — fail loud). A second, **independent** hourly probe (`freshness_handler` in the same module, wired as the `chat-credit-runway-freshness` Lambda on `rate(1 hour)`) reads the Redis key directly and `sentry_sdk.capture_message`s when the value is missing or older than `RUNWAY_FRESHNESS_THRESHOLD_S` (default 24 h) — independence matters: if the publisher Lambda fails to even invoke, this probe is the only Sentry-side detector. `google-cloud-bigquery==3.27.0` is pinned in `artifacts/syrabit-backend/requirements.txt` so the shared sqs_consumers Lambda image bundles the BQ client. Tests: `tests/test_chat_credit_runway.py` (16 cases — selector resolution order + Redis bytes/str/None/garbage paths + pure `compute_runway_days` branches + handler missing-config Sentry capture + freshness probe missing/stale/fresh).

## Chat dispatch (Task #554, 2026-05-07; canonical row reaffirmed by Task #559)

**Chat dispatch (Task #554, 2026-05-07; reaffirmed by Task #559; updated by Task #2 — 2026 blueprint, 2026-05-09):** English chat is a **strict 3-position chain** — **Vertex Gemini 2.5 Flash** (drains GCP startup credits) → **Vertex Flash-Lite** (mid-tier GCP cost cushion, shares the Vertex RPM bucket) → **Workers-AI Llama-3.2-3B** (Cloudflare free-tier tail). When projected GCP credit runway falls to ≤ 90 days the chain FLIPS to `workers_ai_llama32_3b → vertex_flash_lite → vertex` so the cheapest leg leads while Vertex stays paid-fallback (V4 §12 — no silent removal). Selector is `cost_caps._select_chat_primary()` with a 60 s monotonic cache. Operator override: `CHAT_PRIMARY_OVERRIDE=vertex|workers_ai_llama32_3b` pins the head; unsupported values are logged and ignored. **Assamese chat is the strict 3-position chain `[sarvam, vertex_assamese, retrieval_only]`** — Sarvam-M (primary), Vertex Gemini 2.5 Flash with an Assamese-system-prompt prefix (named LLM fallback), and a deterministic `retrieval_only` tail that returns the top RAG snippet verbatim when both LLM legs are exhausted instead of crossing into a wrong-language model (V4 §12, no silent downgrade). **Voice TTS chain becomes `[elevenlabs, deepgram, workers_ai]`** — ElevenLabs restored as the canonical English-TTS primary (richest voice library; the $5/mo Starter is now budgeted into the $100 ceiling), Deepgram Aura-2 as the named fallback, Workers-AI weight-0 last-resort tail. The `assamese_rag_chat` weights row pins `sarvam=10000, vertex_assamese=10000, retrieval_only=0` so `select_provider` walks the chain deterministically. Locked tests: `tests/test_provider_priority_locked.py`, `tests/test_assamese_chat_failover.py`, `tests/test_chat_credit_runway.py`, `tests/test_voice_paid_gate.py`. CI guards: `scripts/ci/check_canonical_delegation.py` (chain-shape + Azure-OpenAI ban + voice paywall), `scripts/check_budget_ceiling.py` (cap ≤ $100, chat head ∈ {`workers_ai*`, `vertex`, `vertex_flash_lite`}, voice paywall).

### Assamese-aware regional cache (Task #2, 2026)

The edge proxy stamps an `X-Cache-Region` header on every backend-bound request based on the Cloudflare geo-IP lookup: `ne-india` for `country=IN AND regionCode ∈ {AS, ML, TR, MN, MZ, NL, AR}` (Assam + the surrounding NE-India states), `global` everywhere else. The header is folded into:

- `ai_input_cache._key()` — region is part of the SHA-256 + key prefix so an Assamese cohort never shares an entry with the global cohort.
- `kv_cache.get/get_local/set` — region is bumped into per-region `{hits, misses, sets, hit_ratio}` counters surfaced via `kv_cache.snapshot()['per_region']`.
- `cf_tiered_cache.record_region_event()` — the worker tags hits/misses on the way back through the proxy and `per_region_snapshot()` rolls them up.

The admin cache panel (`GET /api/health/cache`) now returns a top-level `per_region` tile rolling all three layers so the operator can see hit-ratio side-by-side for `global` vs `ne-india`.

### Admin Ops Console (Task #2, 2026)

`GET /api/admin/ops/console` (admin-only, `routes/admin_ops_console.py`) returns three tiles in a single round-trip so the new `AdminOpsConsole.jsx` panel — wired into `AdminPage.jsx` as the `ops` system-group section — can render without fan-out:

1. **SLA ledger** — rolling **24h *and* 7d** success rate, **p50 / p95 latency**, locked latency target (chat 2000ms / Assamese 2500ms / voice 3000ms / formatter 5000ms), and **breach count** per canonical-specialist chain (`english_rag_chat`, `assamese_rag_chat`, `tts`, `stt`, `content_format`). Sourced from the `_LLM_PROVIDER_METRICS` ring already populated by every `_record_llm_call` site (Redis-backed sorted set when available, in-process ring otherwise).
2. **Outage map** — circuit-breaker state per provider (`open` / `closed`, consecutive failures, last-failure timestamp + error class). Sourced from `llm._BREAKER_STATE`.
3. **Toggles viewer** — read-only listing of the founder-locked operator env knobs (`CHAT_PRIMARY_OVERRIDE`, `EMBED_DEGRADED_MODE`, `RAG_EMBEDDING_PROVIDER_FORCE`, `EMBED_PROVIDER_PRIMARY`, `MONTHLY_TOTAL_USD_CAP`, `CHAT_CREDIT_RUNWAY_DAYS`, `GCP_CREDITS_REMAINING_USD`) + the four `cost_caps` thresholds (`_DEFAULT_MONTHLY_TOTAL_USD_CAP`, `DEGRADATION_PCT_PAUSE_BATCH/VOICE_OFF/FREE_503`).

The Ops Console is a viewer; mutating writes still flow through the existing `/api/admin/settings` panel. AdminOpsConsole.jsx polls every 30 s; missing layers degrade to `available: false` rows rather than raising. Azure OpenAI (chat / embed / Whisper / text-embedding-3-large) is **fully retired**; **Task #552 §G-R (2026-05-09)** also retired Azure Speech (TTS / STT) and Azure Translator — `providers/azure_speech.py` is gone, voice chains run ElevenLabs → Deepgram → Workers-AI, and translate runs IndicTrans2 → Workers-AI. The dead-provider literal ban (`azure_openai|AzureOpenAI|AZURE_OPENAI_*|gpt-4.1-nano`) plus the chain-shape + voice-paywall checks are now enforced by the **umbrella CI guard** `artifacts/syrabit-backend/scripts/ci/check_canonical_delegation.py` (Task #559); `scripts/check_dead_providers.py` is a shim that re-exports the umbrella's `main`. Locked tests: `tests/test_provider_priority_locked.py` (chain shape + credit-flip + override + negative), `tests/test_assamese_routing_chain_e2e.py`, `tests/test_voice_paid_gate.py`.

## Content formatter (§15 §6, Task #494)

**Content formatter (§15 §6, Task #494):** All notebook/study/exam polish flows route through `content_formatter.format_content` — Vertex Gemini 2.5 Flash primary → Workers-AI Llama-3.3-70b fallback → passthrough on dual outage / Assamese purity-gate rejection. Every polished Mongo doc carries a `formatted_by` audit field; admin health panel reports per-formatter rolling counts.

## Vectorless RAG

**Vectorless RAG:** A three-tier router performs tree-walk on D1 syllabus, then BM25 on Mongo, then a vector pass. Results are fused with RRF before Pinecone rerank.

## Secrets management

**Secrets management:** Azure Key Vault is the source of truth, with AWS Secrets Manager and Cloudflare Secrets as read-only replicas.

## Observability (Task #558, 2026-05-07)

**Observability (Task #558, 2026-05-07):** **Errors-only Sentry Developer free tier** + **OTEL → GCP Cloud Trace as the SOLE tracing destination**. Sentry Performance / tracing is fully removed (the prior `traces_sample_rate=0.1` + Sentry-Performance addon have been retired); the SDK is initialized in `artifacts/syrabit-backend/observability/sentry_setup.py` with the traces sampler clamped to zero and a `before_send` filter that drops third-party script errors, AbortError/CancelledError from cancelled fetches, expected 4xx tagged events, ResizeObserver loop notifications, and stacks that live entirely in vendored library code. Tracing in `tracing.py` ships exactly one exporter (`opentelemetry-exporter-gcp-trace`); the Azure App Insights + Axiom dual-export from Task #333 is gone. Sampling is head-based **10 %** for normal traffic and **100 %** for spans tagged `error=true` or `slo_breach=true`. `/api/health/otel` exposes last-export timestamp + last-export error + ingestion-lag for the admin Observability card; the weekly digest of top-10 errors + trace-export health goes to the founder via SES (transport hand-off lands with Task #557). Bicep ships `OTEL_TRACES_EXPORTER=googlecloud` (single value, no comma) plus the new `sentry-dsn` Key Vault secret. Enforced by the umbrella CI guard (Task #558 row): bans `OTEL_TRACES_EXPORTER=<value>,` (multi-exporter), `traces_sample_rate=<positive>` (literal `=0` is allowed), `enable_tracing=True`, `sentry_sdk.start_transaction`, `@sentry_sdk.trace`. Rejected option (GlitchTip self-hosted on Hetzner): documented in ADR-0003 — adds a $5/mo VM + DR runbook for no incremental signal over Sentry-free's 5k-error/mo allowance.

## PG to Mongo Migration

**PG to Mongo Migration:** Phase 2 complete for key user data; Phase 3 (read-shadow) and 4 (cutover) are pending.

## Provider chain (Task #491, 2026-05-07; updated by Tasks #554 + #559)

**Provider chain (Task #491, 2026-05-07; updated by Tasks #554 + #559):** Cerebras, Cohere, and Voyage-AI fully retired. Embedding stack is single-source `workers_ai_custom` (Gemma-300M + Qwen3-0.6B, 1024-dim); on primary outage the system enters cache-only degraded mode (V4 §3) — there is no Azure-OpenAI embed fallback any more (Task #554 retired Azure OpenAI completely). Rerank is Pinecone-only. CI guard is now the umbrella `artifacts/syrabit-backend/scripts/ci/check_canonical_delegation.py` (Task #559); `scripts/check_dead_providers.py` is a thin shim. Banned literals: `cerebras|cohere|voyage_ai|cartesia|groq|openrouter|quge5|azure_openai|AzureOpenAI|AZURE_OPENAI_*|gpt-4.1-nano`.

## Free-tier cost minimization (Task #581, 2026-05-07)

**Free-tier cost minimization (Task #581, 2026-05-07):** Drives the heavy free-user cohort to ₹0.50–3 / mo by stacking ten levers: **L1** free users are hard-routed off Vertex (always Workers-AI Mistral-7B / Llama-3.2-3B regardless of `_select_chat_primary()` head); **L4** free chat dispatch is a **4-step turn ladder** — turns 1-10 cheap (Mistral-7B, full chat output), 11-20 tight (Mistral-7B clamped to `FREE_TIER_TIGHT_OUTPUT_TOKENS=400`), 21-30 retrieval-only (no LLM; caller MUST attempt `retrieval_first.try_resolve` before paywalling), 31+ paywall; **L5** retrieval-first ladder (`retrieval_first.try_resolve`) checks ai_input_cache → rag_cache → Mongo materialized stores (`mcqs`, `definitions`, `pyqs`, `flashcards`) at ≥0.85 confidence before any LLM call; **L6** Assamese gate (`assamese_dispatch`) does translation-cache + cached-explanation-translate + a `needs_reasoning(text)` heuristic before the locked Sarvam dispatch; **L7** per-content-type free-tier output sub-caps (`FREE_TIER_OUTPUT_CAPS`: definition 200 / explanation 400 / mcq_explanation 200 / pyq_answer 500); **L9** OCR daily cap split (`OCR_DAILY_CAP_USER_FREE=3` / `_PAID=100`), long-context >8k input paid-only (`LONG_CONTEXT_FREE_MAX_INPUT_TOKENS=8000`), and a once-per-UTC-day **voice preview** for free users via `require_paid_plan_or_voice_preview` — TWO independent buckets per kind: 1 STT/day (any duration up to the existing 25 MB cap) AND 1 short TTS/day (~30 s, ≤ `FREE_VOICE_PREVIEW_TTS_CHAR_LIMIT=600` chars). Bucket kind is derived from the request URL (`/tts` → `tts`, `/stt` and `/voice/voice` → `stt`); the second call to the same bucket → 402 with `X-Paywall-Voice-Kind` header; **L10** four-step free-tier-first MeterD ladder (`DEGRADATION_PCT_FREE_TIGHTEN_1..4 = 0.40 / 0.50 / 0.55 / 0.58`) ALL strictly below the legacy `DEGRADATION_PCT_PAUSE_BATCH=0.60` so free load sheds before any paid feature touches; **L8** observability via `free_tier_dispatch.snapshot()` (rolling 24 h, 1 h Redis buckets per content-type / lang) surfaced through admin-only `GET /api/health/free-tier-dispatch` and a >5 % `paid_escalation_pct` alarm target. CI guard `scripts/check_budget_ceiling.py` enforces the 4-step ladder ordering + the `< PAUSE_BATCH` invariant + accepts EITHER `Depends(require_paid_plan)` or `Depends(require_paid_plan_or_voice_preview)` on the three voice routes. **Locks unchanged:** $100 monthly cap, paid `TOKEN_BUDGETS`, chain shape, K.2 chat exclusion (live `routes/ai_chat.py` is NOT cached), voice paywall semantics, Sarvam = Assamese head. **Live integration (round-2):** `routes/ai_chat.py` non-streaming dispatch now passes `monthly_spend_fraction()` into `_select_chat_model`, branches explicitly on `tier in {"retrieval_only","paywall"}` (with `retrieval_first.try_resolve` short-circuit before the 402), enforces the L9 long-context paywall (>8k input tokens) BEFORE any LLM call, runs L5 `retrieval_first.try_resolve` BEFORE the cheap/tight free-tier LLM call (paid plans bypass), invokes the L6 Assamese pre-gate (translation cache + cached-explanation lookup) BEFORE Sarvam dispatch, applies the L6 `assamese_dispatch.needs_reasoning` classifier to clamp Sarvam output for simple Assamese turns, and emits per-tier `free_tier_dispatch.record()` counters on every branch. The streaming dispatch (~ai_chat.py L≈2890) still uses the legacy path — that wiring is the only piece deferred to the follow-up. Tests: `tests/test_free_tier_581.py` (10 cases) + rewritten `test_select_chat_model_free_four_step_turn_ladder` in `tests/test_cost_caps.py`.

## Perpetual $100/month budget (Task #549, 2026-05-07)

**Perpetual $100/month budget (Task #549, 2026-05-07):** Default `MONTHLY_TOTAL_USD_CAP` lowered from $500 → **$100** in both `cost_caps._DEFAULT_MONTHLY_TOTAL_USD_CAP` and `credit_burn_meter.MeterDConfig.cap_usd`. `PROVIDER_PRIORITY["english_rag_chat"]` now starts with `workers_ai_llama32_3b` (Cloudflare free tier) and `cost_caps._select_chat_primary()` is the runway-aware head selector. `CHAT_PRIMARY_OVERRIDE` is reserved for the vertex re-enable work (sub-task #555/#556) — until that lands the helper logs and ignores any non-workers override (V4 §12 no-silent-fallbacks) so a misconfig surfaces loudly instead of routing through a non-existent dispatch branch. `routes/voice.py` (`/tts`, `/stt`, `/voice/voice`) requires the new `auth_deps.require_paid_plan` dep (returns 402 for free users; admin/staff/educator bypass). The three-stage degradation ladder constants live in `cost_caps.DEGRADATION_PCT_PAUSE_BATCH/VOICE_OFF/FREE_503` (60 / 80 / 95 %); MeterD still LOCKS chat:cheaponly at 100 %. CI guard `artifacts/syrabit-backend/scripts/check_budget_ceiling.py` fails the build when either default is raised above $100 without a `# COST-CAP-OVERRIDE: <reason>` marker. Sarvam stays the Assamese-chat primary unchanged. Deep Azure/SES/web-push/observability removals are split into sub-tasks #553–#558.

## Cost minimization (Task #513, 2026-05-07)

**Cost minimization (Task #513, 2026-05-07):** Browser-heavy traffic is now capped at the edge (30 chat turns/month + 3/day per anon-id, enforced in `workers/edge-proxy/src/index.ts` via the `RATE_LIMIT` KV namespace). Backend dispatch clamps every LLM call against the locked `TOKEN_BUDGETS` table in `artifacts/syrabit-backend/cost_caps.py` (chat 3000/800, content 4000/2000, formatter 4500/2500, translate 2000/2000, OCR 1500/800, STT 2000/500); a budget bump requires a `# COST-CAP-OVERRIDE: <reason>` comment + Sentry-annotated changelog. Tier-routing in `_select_chat_model` keeps free-user turns 1-2 on Workers-AI Mistral-7B and clamps free-user turns >15 to a 600-token output ceiling (paid plans bypass). ACA right-sized to 0.25 vCPU / 0.5 GiB × min 2 / max 30 replicas at 30 concurrent requests/pod (~75 % idle-baseline saving). Rule D (`MeterD`, default $500/month) flips `chat:cheaponly=1` in Redis when tripped — `_select_chat_model` reads the flag on every dispatch.

## Assamese content backfill

**Assamese content backfill:** A resumable driver translates English content fields into Assamese using Workers-AI IndicTrans2 primary → Vertex/Gemini polish. Runs nightly at 03:00 UTC as the EventBridge-scheduled `syrabit-as-translation-backfill` Lambda (`artifacts/syrabit/infra/aws/lambda-batch-jobs.tf`, manifest `infra/aws/lambda/manifest.json`); handler `lambda_batch/as_translation_backfill.py` emits per-collection `Translated`/`Failed`/`Remaining` plus Job-only `RemainingTotal`/`FailedTotal`/`TranslatedTotal`/`ProcessedTotal` rollups to the `Syrabit/BatchJobs` CloudWatch namespace (the rollups carry no `Collection` dimension so the alarm metric identity actually resolves). Two ops_alerts alarms watch those rollups — `as-translation-backfill-stuck` (RemainingTotal > 0 for 3 consecutive daily passes) and `as-translation-backfill-failed` (FailedTotal > 50/day) — so a stuck pass pages on-call instead of silently letting the `/as/...` SSR corpus fall behind English.

## Cost & runway model (Task #550, 2026-05-07)

**Cost & runway model (Task #550, 2026-05-07):** Phased credit-runway memo at [`artifacts/syrabit/docs/infra/credit-runway-cost-model.md`](artifacts/syrabit/docs/infra/credit-runway-cost-model.md). Headline **credit-on infra value** (cash + credit drawdown) per phase: **P1 (1k–5k DAU) = $120–$320 / mo**, **P2 (5k–10k DAU) = $300–$800 / mo** (cash side held at the $100 cap), **P3 (10k–50k DAU) = $1,200–$3,500 / mo** (requires `# COST-CAP-OVERRIDE` cap raise), **P4 (50k–100k DAU) = $4k–$12k / mo** (credits exhausted, revenue-positive at ~$97k gross). Coexists with Task #549 founder-locks: the $100 cap, the workers_ai chat head, the voice paywall, and the 60/80/95 % degradation ladder are **superior** to anything in the memo — if a memo number ever requires breaching one of those locks, the lock wins and the memo must be re-derived. Quarterly review cadence; next review **2026-08-07** OR sooner whenever any credit pool changes by ≥ 20 %.

---

# Extended Gotchas

Long, multi-paragraph operational gotchas previously inlined in `replit.md`. Short pointers in `replit.md` link back here.

## K.2 deterministic cache scope (chat-adjacent)

**K.2 deterministic cache scope (chat-adjacent):** The deterministic-input AI cache (`ai_input_cache.py`) is wired into formatter / translate / OCR paths AND into `pipeline.stage3_polish`, plus (Task #571) the MCQ generator (`routes/admin_pipeline.py:_pipeline_generate_mcqs`, template `mcq_pipeline_v1`), the flashcard generator (`routes/admin_pipeline.py:_pipeline_generate_flashcards`, template `flashcard_pipeline_v1`), and the definition generator (`vertex_services.extract_key_concepts`, template `extract_key_concepts_v1`). All three are keyed by `(content_type, template_version, exact prompt text, model, max_tokens)`, never serve cached completions across users for streaming or temperature>0 calls, and emit per-content-type counters + miss-reasons through `ai_input_cache.snapshot()` (surfaced via admin-only `/api/health/cache`). This was accepted in the round-7 review as "chat-adjacent but safe"; do NOT extend `is_deterministic(...)` to live `routes/ai_chat.py` dispatch — the live chat hot path is excluded by policy and any change there requires a new task and a fresh threat-model pass.

## Cache-effectiveness observability (Task #571)

**Cache-effectiveness observability (Task #571, 2026-05-07):** Admin-only `GET /api/health/cache` returns the `ai_input_cache.snapshot()` per-content-type rows (hits / misses / sets / hit_ratio / unique_keys_24h / miss_reasons). Nightly `cache-effectiveness` Lambda (03:15 UTC, declared in `artifacts/syrabit/infra/aws/lambda-batch-jobs.tf` `locals.batch_jobs`) ships the same numbers to the `Syrabit/Cache` CloudWatch namespace; two alarms ride the `(ContentType=Total)` dimension — `cache-ai-hitratio-low` (HitRatio < 0.30 for 1 day) + `cache-cardinality-spike` (UniqueKeys24h > 3× the trailing 7-day MA, computed via metric math). Prompt-canonicalization for opt-in callers lives in `prompt_normalizer.py` (NFKC + lowercase + punct/whitespace strip + curated synonym map; every map entry is pinned in `tests/test_prompt_normalizer.py`). Edge-cache advisory targets per route are now stored as `edge_cache.cache_hit_ratio_target` in `workers/edge-proxy/monitored-urls.json`. Audit report: `artifacts/syrabit/docs/infra/cache-effectiveness-audit.md`. Founder locks (the $100 cap, the 5s `/api/me/quota` TTL, the `TOKEN_BUDGETS` ceilings, the chat dispatch chain) are explicitly **not** touched by Task #571.

## Cache calendar — Knob (Task #575)

**Knob (Task #575, 2026-05-07):** `artifacts/syrabit-backend/config/exam_calendar.yaml` declares the AHSEC + SEBA exam / results windows. `cache_calendar.current_season()` classifies "today" as `exam` / `results` / `normal` and `cache_calendar.ai_cache_ttl_for(content_type)` stretches the `ai_input_cache` TTL from **30 days → 90 days** for the four exam-relevant deterministic content types (`mcq`, `flashcard`, `definition`, `pyq`) while in exam / results mode. Formatter / translate / OCR keep the 30-day default in every season — they're admin-edit driven and the longer TTL would mask a freshly polished body for too long. `ai_input_cache.set_response(..., ttl=None)` (the default) reads the calendar; an explicit `ttl=` always wins.

## Cache calendar — Edge wiring

**Edge wiring:** `GET /api/health/season` (public, `Cache-Control: public, max-age=60`, registered in `workers/edge-proxy/monitored-urls.json` + exported as `SEASON_HEALTH_PATH`) exposes the current season, multiplier, active window, and next transition. The Cloudflare worker uses a three-tier cache: a region-scoped `SeasonCacheDO` Durable Object (single instance via `idFromName("global")`) owns the authoritative snapshot and enforces the 60 s shared-refresh contract so the FastAPI origin sees one call per minute per region regardless of isolate count; per-isolate `cached` shadows the DO RPC; the DO's upstream fetch carries `cf: { cacheTtl: 60, cacheEverything: true }` as belt-and-braces against cross-POP cold-wake stampedes. Cold start (and DO unbound in local dev) serves FALLBACK ("normal") immediately and refreshes via `ctx.waitUntil` so the request path never blocks. Per-route stretched TTLs live in `monitored-urls.json` as `edge_cache.exam_ttl_seconds`; routes that don't declare it keep their normal `ttl_seconds` regardless of season. Currently opted in: `/api/pyq/` (1h → **24h**), `/api/content/library-bundle` (30m → **4h**), `/api/content/chapter-by-slug/` (1h → **6h**), `/api/content/topic/` (1h → **6h**).

## Cache calendar — PYQ wiring scope

**PYQ wiring scope (explicit):** The deterministic AI input cache (`ai_input_cache`) is wired into the MCQ, flashcard, and definition generators today (Task #571). The PYQ generation pipeline (`routes/pyq.py:admin_pyq_agentic_process`) calls Gemini Vision OCR DIRECTLY and does NOT go through `ai_input_cache`, so the 30d→90d TTL stretch is *ready* (the calendar's `EXAM_STRETCH_CONTENT_TYPES` includes `"pyq"`) but a no-op on the AI-cache side until the generator is refactored to write through `ai_input_cache.set_response(content_type="pyq", ...)`. The follow-up that does that wiring is tracked as task #582. The edge-cache side of the PYQ benefit (the `/api/pyq/` `exam_ttl_seconds` stretch declared in `monitored-urls.json`) is live today regardless.

## Cache calendar — Adding a window

**Adding a window:** edit `config/exam_calendar.yaml` and add an entry with `name`, `kind` (`exam`|`results`), `start` and `end` (YYYY-MM-DD inclusive). Concurrent AHSEC + SEBA passes MUST be merged into a single combined window — overlaps are rejected by the loader and by the CI guard (`scripts/ci/check_exam_calendar.py`, wired into the deploy workflow as the `exam_calendar_gate` job). The CI guard also enforces a **365-day forward horizon**: the latest window's `end` must be ≥ 1 year from today so the edge worker keeps applying stretched TTLs through the next pass without a manual refresh.

## Cache calendar — Admin Observability banner

**Admin Observability banner:** `CacheHitRatioPanel` polls `/api/health/season` alongside `/api/health/cache` and renders an amber banner during exam / results mode showing the current season, TTL multiplier, active window name, and next transition date.

## Cache calendar — Founder locks unchanged by this knob

**Founder locks unchanged by this knob:** `/api/me/quota` 5 s edge cache TTL, `/api/ai/chat` edge bypass (live chat hot-path NEVER cached — K.2 gotcha still applies), `$100/mo` monthly USD cap (`cost_caps._DEFAULT_MONTHLY_TOTAL_USD_CAP` + `MeterDConfig.cap_usd`), and `TOKEN_BUDGETS` ceilings. The exam-mode TTL stretch only applies to the deterministic input cache + the four opted-in public content routes; nothing in the chat dispatch chain or the cost-cap ladder is touched.


## Task #27 — Cohere `embed-multilingual-v3` via AWS Bedrock (2026-05-09)

**Partial reversal of Task #491.** Task #491 retired Cohere outright after a
single English-only A/B against the Workers-AI custom embed worker. The
A/B never measured Indic recall — Cohere had no Assamese training data
weighting at the time of #491 — and the broad ban swept up the
multilingual-v3 model that AWS later released on Bedrock with first-class
Indic coverage. Task #27 narrows #491 to a language-gated re-introduction:
Cohere stays banned as a SaaS SDK / `COHERE_API_KEY` route, but
`cohere.embed-multilingual-v3` is allowed **exclusively** via AWS Bedrock
(`bedrock-runtime:InvokeModel`, reuses the existing per-feature OIDC role —
no new credentials, no new vendor relationship).

**Routing.** `llm.call_embed_with_dispatch` resolves the embed provider by
detected language tag:

  * `lang ∈ {as, as-IN, indic, hi, bn, ...}` → `cohere_multilingual_v3_bedrock`
    (1024-dim, must match `pinecone_dim` founder-lock).
  * everything else → `workers_ai_custom` (English / unknown / mixed).

A successful Bedrock call charges `BEDROCK_COHERE_EMBED_USD_PER_1K_TOKENS
= 0.0001` (`us-east-1` on-demand price sampled 2026-05-09) into both the
global Rule-D bucket AND a dedicated Indic sub-bucket via
`MeterD.record_usd_indic_bedrock`. When the sub-bucket crosses
`INDIC_EMBED_MONTHLY_USD_SUBCAP=$5/mo`, `embed:indic:paused=1` is set
(TTL = next UTC month start) and `is_indic_embed_paused()` flips True;
the dispatcher then routes Indic queries to Workers-AI for the rest of
the calendar month. The same dollars only count once toward the global
$100 cap, so the sub-cap does not artificially inflate Rule-D.

**Failure handling (V4 §12 no silent fallbacks).** Bedrock failures
(`AccessDeniedException`, `ThrottlingException`, dim-mismatch) DO NOT
silently degrade — they record a Sentry breadcrumb (`degraded_to_workers_ai`)
+ flip the `embed_degraded_controller` probe + per-call route to Workers-AI
for the single request only; the next call retries Bedrock. Operator
kill-switches: `EMBED_INDIC_PROVIDER=workers_ai_custom` (per-deployment)
or `RAG_EMBEDDING_PROVIDER_FORCE=workers_ai_custom` (per-call).

**Cache + Pinecone isolation.** `embed_cache.py` folds `embed_provider`
into the Redis key (`emb:cohere_multilingual_v3_bedrock:<sha256>` vs
`emb:workers_ai_custom:<sha256>`) so paraphrased / bilingual variants
never cross-contaminate. `chunk_embedder.py` stamps every Pinecone vector
with a top-level `embed_provider` metadata field for the same reason —
Pinecone filtering can isolate by provider when comparing recall.

**Admin surface.** `/admin/health/embed-stack` returns a 4-leg payload
(`embed`, `bedrock_indic`, `rerank`, `memory`). The Indic leg is
**excluded** from the top-level `ok` aggregate because a paused or
kill-switched Indic route degrades to Workers-AI by design and must not
turn the entire health pill red on a healthy English primary. The
dedicated detail card surfaces region, model id, monthly spend vs sub-cap,
the `EMBED_INDIC_PROVIDER` switch, and a paused-state amber notice.

**CI guards.** `scripts/ci/check_canonical_delegation.py` removed the
bare `cohere` token from `BANNED_LITERAL` (it would otherwise fire on
the Bedrock model id and the provider module name) and added scoped
patterns: `^\s*import\s+cohere\b`, `^\s*from\s+cohere\b`,
`\bCOHERE_API_KEY\b` — the SaaS-route surface remains banned.
`infra/architecture-matrix.json` removed `cohere` from
`retired_providers` and added two new §5.1 rows (`embed.default` +
`embed.indic`); the architecture-lock guard's strict patterns are
import/env-var scoped so the runtime references in
`providers/cohere_bedrock_embed.py` don't trigger.

**Founder locks (unchanged).** `MONTHLY_TOTAL_USD_CAP=$100`, V4 §12
no-silent-fallbacks, `pinecone_dim=1024`, `sarvam_assamese_head=true`
(Sarvam remains the sole Assamese chat head — Task #27 only touches
the embed surface). Sub-cap is INSIDE the global cap; raising either
requires the standard `# COST-CAP-OVERRIDE: <reason>` discipline,
enforced by `scripts/check_budget_ceiling.py`.

## Smart-router topic-score threshold (Task #39, 2026-05-09)

`chat_router.route()` decides RAG vs. web by comparing the centroid
similarity from `chat_router.probe_topic_score(...)` against a single
configurable threshold (`_DEFAULT_TOPIC_THRESHOLD = 0.55`, override via
`CHAT_ROUTER_TOPIC_THRESHOLD` env, hard-clamped to `(0, 1)` with a
warn-and-default on out-of-range values). The probe itself is
language-correct: English uses `wai_chapter_index.classify` (Workers-AI
`@cf/baai/bge-small-en-v1.5` per-subject centroid index, returning the
real `similarity` field in `[0, 1]`); Assamese uses
`_probe_assamese_via_bedrock_cohere` (Cohere `embed-multilingual-v3`
via AWS Bedrock per Task #27 → Pinecone `namespace="as"` top-1 cosine).

Why 0.55: it's the legacy `rag_router` gate — preserved verbatim so
existing traffic doesn't shift on the day this lands. The number is
intentionally a starting point, not a tuned value. Iteration 1 of
Task #37 used a stage1 high/low confidence proxy mapped to {0.8, 0.4}
which made any threshold movement coarser than ±0.4. Task #39 wires
the **real numeric similarity** through `_build_route_trace` and
surfaces it on the dev-mode QA badge in `MessageBubble.jsx` as
`score=<float> th=<float>` so the actual distribution is observable.

Re-tune procedure (run after collecting ≥7 days of dev/staging
traffic):

1. Pull the per-turn `topic_score` values from the
   `[NON-STREAM][ROUTER]` / `[STREAM][ROUTER]` log lines emitted by
   `routes/ai_chat.py` (already include `score=<f>`).
2. For each turn, label the user-perceived correct branch (RAG vs.
   web) — easiest via human review of a 100-turn sample.
3. Pick the threshold `t` that maximises (precision_rag *
   recall_rag) on the labelled set. The legacy 0.55 is roughly the
   point where bge-small-en-v1.5 within-chapter cosines start to
   dominate cross-chapter ones.
4. Set `CHAT_ROUTER_TOPIC_THRESHOLD=<t>` in the ACA app env (no
   redeploy needed — `_topic_threshold()` reads on every call).
   Roll back by removing the env var.

Founder locks unaffected: the threshold only governs RAG-vs-web
selection — it cannot disable the casual short-circuit, change the
language-correct embed pool selection (English → Workers-AI custom;
Assamese → Cohere-Bedrock per Task #27), bypass the $100/mo cap, or
escape the no-silent-fallbacks rule (a probe failure surfaces as web,
not as a silent rag-empty 503).

## Cost caps & cloud budget mirror (Tasks #4, #549)

`MONTHLY_TOTAL_USD_CAP = $100` is enforced in three coordinated places that must move together:

1. **In-app:** `cost_caps._DEFAULT_MONTHLY_TOTAL_USD_CAP` and `credit_burn_meter.MeterDConfig.cap_usd` defaults must remain ≤ $100 unless the changed line carries a `# COST-CAP-OVERRIDE: <reason>` marker. The 60 / 80 / 95 % degradation thresholds must stay strictly increasing inside (0.0, 1.0). `cost_caps.TOKEN_BUDGETS` per-provider ceilings carry the same override discipline; bumping any value also requires a Sentry-annotated changelog entry. `tests/test_cost_caps.py` walks the source file and fails CI when either signal is missing.
2. **Edge:** `workers/edge-proxy/src/index.ts` `CHAT_CAP_MONTHLY = 30` and `CHAT_CAP_DAILY = 3` per-user caps. Same `# COST-CAP-OVERRIDE` rule.
3. **Cloud mirror (Task #4):** `aws_budgets_budget.monthly_cost` in `artifacts/syrabit/infra/aws/account-billing.tf` mirrors the same $100/mo ceiling and the same 60 / 80 / 95 % thresholds, fanning out to the `syrabit-ops-alerts` SNS topic so an AWS-side breach lands in the same Slack channel as the in-app `cost_caps` ladder. Raising the AWS budget above $100 needs the same override marker. Ops contact for the budget alerts is `local.lz_ops_email` in the same file.

CI guard: `scripts/check_budget_ceiling.py`. Any drift between the three surfaces is treated as a cap raise and must carry the override discipline.

## Prewarm engine (Task #13)

`aca_jobs/prewarm_seo_routes.py` runs nightly at 01:00 UTC via Lambda (Terraform: `prewarm-seo-routes` in `lambda-batch-jobs.tf`).

- **Selection:** `top_n` chapters by 7-day `db.page_views` traffic UNION every chapter under a subject whose exam window starts within `PREWARM_EXAM_LOOKAHEAD_DAYS` (default 30).
- **Per-chapter work:** warms an extra FAQ JSON-LD leg (`GET /content/chapters/{id}/faq-jsonld`, KV-cached at edge with 1h server-side cache) so the schema.org FAQPage block is hot for crawlers, then walks all 7 SEO `PAGE_TYPES`.
- **Request shape:** GETs each URL through Cloudflare with `X-Prewarm-Recommended-TTL` (advertises the `cache_calendar` TTL) + `X-Prewarm-Auth` (== `BACKEND_ORIGIN_SECRET`, gates the worker's `getPrewarmOverrideTtl` / `withOverriddenTtl` override path) so the worker fills its tiered cache AND the materialization-eligible page-types (`mcqs`, `flashcards`, `definitions`, `summary`, `pyqs`) produce a body that fills KV (`aic:fp:*`) + Mongo `ai_input_cache`.
- **Persistence:** per-board summary written to `db.seo_prewarm_runs`, consumed by admin tile `/api/admin/seo/prewarm-coverage` (surfaces split `kv_attempted/warmed/failed/success_rate` alongside the combined counts).
- **Metrics + alarms:** emits both `Syrabit/Cache::PrewarmSuccessRate` (combined) and `Syrabit/Cache::KvPrewarmSuccessRate` (KV-eligible only) per pass; CloudWatch alarms `cache-prewarm-success-rate-low` and `cache-kv-prewarm-success-rate-low` each fire at <0.90 so a degraded materialization path is not masked by healthy edge-only legs.
- **Knobs:** `PREWARM_TOP_N=5000`, `PREWARM_CONCURRENCY=32`, `PREWARM_HTTP_TIMEOUT_S=10`, `PREWARM_EXAM_LOOKAHEAD_DAYS=30`, `PUBLIC_BASE_URL=https://syrabit.ai`.
- **Auth:** Lambda env carries `PREWARM_AUTH_TOKEN_SECRET_ARN` (mirrors `origin/shared-secret` SM entry == worker `BACKEND_ORIGIN_SECRET`); `lambda_batch/_db.bootstrap_env` hydrates it into `PREWARM_AUTH_TOKEN` at cold-start.
- **Target:** ≥95 % KV hit-ratio during exam windows for materialization-eligible content types.

## Backend test gates (Tasks #85, #86)

Two pytest gates in `artifacts/syrabit-backend/pyproject.toml` enforce async hygiene + canonical chat-chain shape.

**Task #85 — leaked-coroutine warnings are CI errors.** `[tool.pytest.ini_options].filterwarnings` promotes the `coroutine ... was never awaited` `RuntimeWarning` to a hard test failure. If your test starts failing with `RuntimeWarning: coroutine 'X' was never awaited`, the production code path or the test fixture forgot to `await` an async/`AsyncMock` call — fix the missing `await` (or stub the call as `AsyncMock(return_value=...)` and ensure the caller awaits it) rather than suppressing the warning. The filter is intentionally narrow to that one message so unrelated `RuntimeWarning`s (third-party resource warnings, etc.) keep their default informational behaviour. Sweep with `pytest -W error::RuntimeWarning` before promoting any new warning class.

**Task #86 — use `asyncio.run()` not `asyncio.get_event_loop().run_until_complete()`.** Python 3.10+ deprecated `get_event_loop()` for callers without a running loop, and the deprecation warning interacts badly with the Task #85 gate. New tests that need to drive an async helper from a sync test body must use `asyncio.run(coro)` directly. The wider canonical pattern is `@pytest.mark.asyncio async def test_…(): await …` (asyncio mode is `auto` in `pyproject.toml`); use the bare `asyncio.run` form only when the surrounding test must remain sync (e.g. fixtures or helpers that pre-date the asyncio plugin).

**Task #86 — chat-provider chains in tests are canonical, not historical.** `_PAID_PROVIDER_RPM_WINDOWS` only tracks `vertex` and `sarvam` (the only paid chat primaries). Tests that seed RPM windows or assert chain membership must reference the canonical chains from architecture lock §5.1 (`vertex → vertex_flash_lite → workers_ai_llama32_3b` for English, `sarvam → vertex_assamese → retrieval_only` for Assamese) — `azure_openai` / `workers_ai_indic` (chat) / `workers_ai_mistral_7b` (chat) are retired from the chat chains and seeding their windows raises `KeyError`. `workers_ai_indic` and `workers_ai_mistral_7b` DO remain legitimate members of the `assamese_content` / `content` pools respectively, so the `test_no_retired_providers_present` retired-set must NOT include them — the chat-chain shape tests already enforce their absence from chat. `scripts/ci/check_canonical_delegation.py` enforces these chains in CI.
