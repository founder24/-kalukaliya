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

Bank C (TODO-gated; documented in the canonical map but the regex stays commented out until the parent task merges):

- **Task #557** — SES sole tier-1 transactional email + self-hosted VAPID web-push (bans `sendgrid|SendGridAPIClient|SENDGRID_API_KEY|resend|RESEND_API_KEY|firebase_admin|FCM_SERVER_KEY|FIREBASE_SERVICE_ACCOUNT`).
- **Task #558** — observability narrowing to GCP Cloud Trace single exporter (bans Sentry tracing literals + multiple OTEL exporters).

The TODO regexes live in `TODO_557_PATTERN` / `TODO_558_PATTERN` in the umbrella; flipping them on is one comment-uncomment + one PR.

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

## Decision log

- **2026-05-07 (Task #559)** — ADR accepted. Umbrella + shim + per-feature map + cutover runbook + V4 §17 lock all merged in the same PR. SES / web-push / observability rows TODO-gated (deferred to Tasks #557 + #558).
