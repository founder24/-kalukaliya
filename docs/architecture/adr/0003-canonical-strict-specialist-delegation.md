# ADR-0003: Canonical strict specialist-delegation map

- **Status:** Accepted (Task #559)
- **Date:** 2026-05-07
- **Owner:** founder@syrabit.ai
- **Supersedes:** the percentage-cost-share framing in the previous `infra/four-cloud-delegation.md` (Task #489) and every ad-hoc per-feature routing decision documented inline across `replit.md` between 2026-05-03 and 2026-05-07.
- **Tracked under:** `infra/v4-locked-architecture.md` §0, §3, §17 + the umbrella CI guard `artifacts/syrabit-backend/scripts/ci/check_canonical_delegation.py`.

---

## Context

Between V4 lock-in (Task #489) and the perpetual `$100/mo` cap (Task #549), Syrabit accumulated **eight** overlapping rules about which provider owns which feature:

- the four-cloud cost-share table (40 / 30 / 20 / 10),
- the V4 §4 chat dispatch chain,
- the §15 §5 content-formatter rule,
- the §15 §6 dispatcher,
- the §16 dead-provider purge,
- the Task #549 founder-locks (`$100` cap, voice paywall, degradation ladder),
- the Task #551 AWS expansion (Lambda manifest + Glacier),
- the Task #554 Azure-OpenAI retirement.

Each rule was correct on its own. Together they produced two real-world problems:

1. **Routing drift.** A reasonable engineer reading `infra/four-cloud-delegation.md` could not tell from the matrix alone whether Vertex was the chat head (Task #549 says yes), the chat fallback (V4 §4 said no), the content-formatter primary (V4 §15 §5 says yes), or all three on different days (which is in fact the case once `_select_chat_primary()` flips on credit runway).
2. **Guard sprawl.** `scripts/check_dead_providers.py` had grown five different bans across five tasks; `scripts/check_budget_ceiling.py` had grown a parallel set of bans; the four-cloud-drift workflow had a third. A new "feature X is now sole-owned by provider Y" decision required edits to three CI guards plus two docs, and there was no single place to look up "what is feature X's canonical owner today".

## Decision

Adopt a **strict specialist-delegation** model:

> Every production feature has exactly **one canonical primary provider** and **at most one named, strict fallback**. The map of features → (primary, fallback) is the routing contract. Cost-share percentages are an *outcome* of that map, not an enforceable target.

The map lives in `infra/four-cloud-delegation.md` §A; it supersedes the percentage matrix that file used to carry.

The single CI guard `artifacts/syrabit-backend/scripts/ci/check_canonical_delegation.py` enforces it. Older guards (`scripts/check_dead_providers.py`) become **shims** that import the umbrella's `main` so historical commands and tests keep working.

## Why "strict specialist" and not "weighted pool"

- **Honesty over availability.** The V4 §12 "no silent fallbacks" rule is incompatible with multi-provider weighted pools that silently advance. A weighted pool that draws Provider A 90 % of the time and Provider B 10 % of the time looks like load-balancing but is actually a hidden silent-fallback when A is degraded — the rotation just routes around the failure invisibly.
- **One throat to choke.** When a chat turn fails, the on-call engineer asks "who was supposed to serve this?" The answer in the new map is a single name, not "whichever Workers-AI variant the weighted draw picked at this nanosecond". Debugging time drops.
- **Founder-lock compatibility.** The `$100/mo` ceiling (Task #549) demands deterministic credit drain. Specialist delegation drains the highest-credit pool first by design (`vertex` → drains GCP startup credits before flipping to the free Workers-AI tier); a weighted pool would spread spend across providers and burn the cap faster.
- **Trade-off accepted.** A specialist owner that is degraded for the entire window between primary fail + named fallback fail produces a hard 503 / 402 / 503 instead of a slow but successful rotation through a third option. This is intentional. Alerting + the credit-runway flip cover the cases that matter; the cases that don't get a documented loud failure.

## What the umbrella enforces today

Bank A (carried verbatim from Task #297 → #554, behaviour-preserving):

- bare-token bans on `cerebras|cohere|voyage_ai|cartesia|groq|openrouter|quge5|azure_openai|AzureOpenAI|gpt-4.1-nano|AZURE_OPENAI_*`,
- vendor-SDK import bans on `stripe`, `resend`, the deleted bedrock-proxy, and four legacy LLM SDKs (`openai`, `anthropic`, `xai`, `grok`),
- direct-Gemini env-var read ban (must go through `config.py`),
- direct `vertex_format.format_with_vertex` import ban (must go through `content_formatter.format_content`),
- aca_jobs/* manifest completeness check (Task #551).

Bank B (new in #559):

- `PROVIDER_PRIORITY["english_rag_chat"]` ≡ `{vertex, workers_ai_llama32_3b}` (set equality; head order is runtime-dynamic),
- `PROVIDER_PRIORITY["assamese_rag_chat"]` ≡ `{sarvam, workers_ai_indic}`,
- `cost_caps._select_chat_primary` + `CHAT_PRIMARY_OVERRIDE` knob both present,
- `routes/voice.py` `/tts` + `/stt` + `/voice/voice` all sit behind `Depends(require_paid_plan)`.

Bank C (parent-task-gated — `TODO_<n>_PATTERN` regexes are wired into `_check_canonical_bank()` and become hard failures the moment the parent task ships):

- **Task #556 — SES sole tier-1 transactional email — ACTIVATED.** `TODO_556_PATTERN` is live and bans `sendgrid|SendGridAPIClient|SENDGRID_API_KEY|resend|RESEND_API_KEY` plus the `EMAIL_PROVIDER` / `EMAIL_FALLBACK` provider-flag env knobs across backend / frontend / IaC / lockfiles / Workers code.
- **Task #557 — Self-hosted VAPID web-push — ACTIVATED.** `TODO_557_PATTERN` is live and bans `firebase_admin|FCM_SERVER_KEY|FIREBASE_SERVICE_ACCOUNT`.
- **Task #558 — Observability narrowing to GCP Cloud Trace single exporter — ACTIVATED.** `TODO_558_PATTERN` is live and bans multi-exporter `OTEL_TRACES_EXPORTER=…,…`, positive `traces_sample_rate`, `enable_tracing=True`, `sentry_sdk.start_transaction`, `@sentry_sdk.trace`. The single allowed exporter literal is `googlecloud` (the upstream `opentelemetry-exporter-gcp-trace` package's documented exporter ID).

All three Bank-C patterns currently produce hard failures on `main` if reintroduced; there is no commented-out / staged code path. The `TODO_<n>_PATTERN` naming is retained for grep continuity with the cutover runbook and the parent task threads.

## Removed providers — per-row rollback risk + reversibility

Each row below names a provider this map *retired* in the run-up to
Task #559, the rollback risk of un-retiring it, and a rough
engineer-day estimate of how long a re-introduction would take if
the canonical primary collapses for an extended window. Estimates
assume one engineer working full-time, no parallel work, and that the
upstream account / billing relationship still exists.

- **AssemblyAI (STT)** — retired by Task #552 §G. Rollback risk:
  **low**. Re-introduction cost: **~1 engineer-day** (recreate the
  thin `providers/assemblyai.py` adapter from the deleted file in git
  history, restore the `ASSEMBLYAI_API_KEY` env knob, re-add the
  provider to `PROVIDER_PRIORITY['stt']`). The Deepgram Nova-3 +
  Google Chirp_2 pair has covered every failure mode AssemblyAI
  previously absorbed; we keep the SDK in `requirements.txt` history
  only.
- **Deepgram Aura-2 (TTS)** — retired by Task #552 §G. Rollback risk:
  **low**. Re-introduction cost: **~1 engineer-day** (restore the
  `_tts_deepgram` dispatch branch + the `synthesize()` method on
  `providers/deepgram.py`). The Deepgram STT half is unchanged so
  the account + key are still live; only the TTS code path is gone.
- **Azure OpenAI (chat / embed / Whisper / text-embedding-3-large)** —
  retired by Task #554. Rollback risk: **medium** — Azure App Service
  managed identities and Azure OpenAI quota are non-trivial to
  re-provision once the resource group is torn down. Re-introduction
  cost: **~5 engineer-days** (re-create the resource + redeploy the
  `azure_openai` provider module from git history + re-wire the
  `AZURE_OPENAI_*` secrets in Key Vault + run the dispatch chain
  shape tests). Mitigated because Vertex Gemini 2.5 Flash and Workers
  AI Llama-3.2-3B together cover both the Azure OpenAI chat surface
  AND the long-context surface the old Whisper rollouts used.
- **Cerebras / Cohere / Voyage-AI** — retired by Task #491. Rollback
  risk: **low** for any single one, **medium** if the Pinecone rerank
  also fails simultaneously. Re-introduction cost: **~2 engineer-days
  per provider** (restore the `providers/<name>.py` adapter, key
  plumbing, and rotate them into `PROVIDER_PRIORITY['embedding']` /
  `['rerank']` per V4 §3 §B). The cache-only degraded mode on Workers
  AI custom embed is the real fallback today; these adapters only
  matter if Pinecone rerank goes down for more than the cache TTL.
- **SendGrid / Resend (transactional email)** — retired by Task #556.
  Rollback risk: **low**. Re-introduction cost: **~3 engineer-days**
  (restore the SendGrid HTTP adapter, re-add the SES → SendGrid
  fallback shim, re-wire the bulk-email worker). The 410-stub on the
  `workers/email-worker/` historical surface stays in the repo so an
  un-retirement is grep-able.
- **Firebase Cloud Messaging / `firebase_admin` (web-push)** — retired
  by Task #557. Rollback risk: **medium** — the FCM tombstone migration
  is irreversible for any device that already rotated to VAPID
  (`tombstoned → purged` is one-way once `purged`). Re-introduction
  cost: **~4 engineer-days** (restore the `firebase_admin` SDK adapter,
  re-add `FCM_SERVER_KEY` / `FIREBASE_SERVICE_ACCOUNT` to Key Vault,
  prompt every active subscription to re-grant). Mitigated because
  the W3C `PushSubscription` shape we now enforce is browser-native
  and not Firebase-specific.
- **Sentry tracing addon (Performance / `traces_sample_rate=0.1`)** —
  retired by Task #558. Rollback risk: **low**. Re-introduction cost:
  **~1 engineer-day** (flip `traces_sample_rate` back to a positive
  number, re-add `enable_tracing=True`, re-introduce the `start_transaction`
  call sites — Sentry SDK init shape is unchanged). GCP Cloud Trace
  carries the trace surface in the meantime.

If a re-introduction is ever requested, the ADR-decision log below
(see "Decision log") gets a new row pointing at the relevant PR; the
canonical map row in `infra/four-cloud-delegation.md` §A flips back
to the multi-provider shape; the umbrella TODO patterns get re-armed.

## Consequences

**Positive.**

- A single grep (`rg "feature-name" infra/four-cloud-delegation.md`) now answers "who owns this?".
- A single command (`python artifacts/syrabit-backend/scripts/ci/check_canonical_delegation.py`) covers every routing-contract violation we've ever shipped a guard for.
- New canonical decisions are one new row in §A + one (optional) new check in the umbrella + one decision-log entry. No more guard sprawl.
- The deploy workflow gains a hard pre-deploy gate (`canonical_delegation_gate`) that runs the umbrella before the existing budget-ceiling gate, so a broken routing contract cannot ship even if the budget gate is green.

**Negative.**

- Strict-fallback exhaustion produces hard failures (chat → 503, voice → 402) instead of silent rotation through a third option. Mitigated by the credit-runway flip, the operator override env-vars, and the cache-only embed degraded mode.
- Adding a new sub-task (#557, #558) requires *both* the underlying code change AND uncommenting the TODO bans in the umbrella in the same PR — otherwise the canonical map drifts ahead of the enforcement. The umbrella's TODO comments name the parent task explicitly so this is hard to miss in code review.

**Neutral.**

- Cost-share percentages move to `replit.md` and the credit-runway memo. They remain useful as a quarterly-review headline but are no longer the source of truth for "which provider runs feature X".

## Acceptance gate

```bash
# 1. Umbrella guard passes.
python artifacts/syrabit-backend/scripts/ci/check_canonical_delegation.py
# expected: "Canonical-delegation guard OK — scanned <N> files."

# 2. Legacy shim still passes (behaviour-preserving).
python artifacts/syrabit-backend/scripts/check_dead_providers.py
# expected: same output as above.

# 3. Per-feature canonical map present + complete.
rg -n '^\| \*\*' infra/four-cloud-delegation.md | wc -l
# expected: ≥ 18 rows in §A.

# 4. Cutover runbook present + 10 steps.
rg -c '^\| [0-9]+ \|' artifacts/syrabit/docs/infra/canonical-delegation-cutover.md
# expected: 10
```

## Alternatives considered

### Observability errors-only sink — GlitchTip self-hosted on Hetzner (rejected)

Captured here per Task #558's "document the rejected option" requirement.

We considered standing up a Hetzner CX11 VM (~$5/mo) running the
official `glitchtip/glitchtip` Docker Compose stack (PostgreSQL +
Redis), fronted by a Cloudflare Tunnel so the Hetzner box never
exposes a public port. Backups would be a nightly Postgres dump
shipped to AWS S3 via a small `cron` script. The DSN would land in
Azure Key Vault as `GLITCHTIP_DSN` and replicate to AWS + Cloudflare.
The Sentry SDK is wire-compatible with GlitchTip, so no client code
would have changed.

**Rejected because:**

1. **Cost ceiling pressure.** $5/mo cash + the implicit ops time to
   maintain a Compose stack pushes against the perpetual `$100/mo`
   cap (Task #549). Sentry Developer free is `$0/mo` cash with the
   same errors-only signal up to 5k events / month. A Sentry
   inbound-data-filter alert at 4k/mo gives the runway warning.
2. **DR runbook overhead.** Self-hosting buys a backup-restore drill,
   a TLS-tunnel rotation drill, and a "GlitchTip 1.x → 2.x upgrade"
   responsibility we do not have head-count for. The "one throat to
   choke" principle from §"Why strict specialist" applies to
   observability too: with Sentry-free the throat is the vendor.
3. **Reversibility is cheap.** If 5k events / month becomes binding
   we can re-stand the GlitchTip VM and swap a single env var
   (`SENTRY_DSN` → `GLITCHTIP_DSN`); the SDK init shape in
   `observability/sentry_setup.py` is identical for both back-ends.

The decision is reviewed at the same quarterly cadence as the credit
runway memo (Task #550) — next review **2026-08-07**, sooner if
monthly events cross 4k.

## Decision log

- **2026-05-07 (Task #559)** — ADR accepted. Umbrella + shim + per-feature map + cutover runbook + V4 §17 lock all merged in the same PR. SES / web-push / observability rows TODO-gated (deferred to Tasks #557 + #558).
- **2026-05-07 (Task #558)** — Observability narrowing landed: errors-only Sentry Developer free + OTEL → GCP Cloud Trace as sole exporter. `TODO_558_PATTERN` activated in the umbrella (bans multi-exporter `OTEL_TRACES_EXPORTER=…,…`, positive `traces_sample_rate`, `enable_tracing=True`, `sentry_sdk.start_transaction`, `@sentry_sdk.trace`). GlitchTip self-hosted captured above as the rejected alternative.
