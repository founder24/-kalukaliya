# Canonical-Delegation Cutover Runbook (Task #559)

> **Status: LIVE — 2026-05-07.** Operators run this once when adopting the per-feature canonical map (`infra/four-cloud-delegation.md` §A) on a given environment. Each step is a binary acceptance check.
> **ADR:** [`docs/architecture/adr/0003-canonical-strict-specialist-delegation.md`](../../../docs/architecture/adr/0003-canonical-strict-specialist-delegation.md)
> **V4 lock:** `infra/v4-locked-architecture.md` §17.
> **Umbrella guard:** `artifacts/syrabit-backend/scripts/ci/check_canonical_delegation.py`.

The cutover swaps the legacy "weighted-pool plus dead-provider-bans" routing world for the "one canonical primary + one strict fallback per feature, enforced by a single umbrella guard" world. The 10 steps below are the **production sequence** — they verify each per-feature canonical row, in dependency order (specialist health gates first, then chain flips, then dead-provider deletions, then the umbrella + docs lock that captures the final state).

**Per-step monitoring guardrails.** Every step that touches a live dispatch path enforces the same three thresholds for **7 consecutive days** following its completion (the on-call engineer triggers the step's rollback the moment any one trips, and the 7-day counter restarts after the fix lands):

- **Error rate ≤ 2× the trailing-7-day pre-cutover baseline** for the affected route (e.g. for step 2 the watched route is `/api/ai_chat?lang=as`).
- **p95 latency ≤ 1.5× the trailing-7-day pre-cutover baseline** for the same route.
- **Zero new provider names in `artifacts/syrabit-backend/metrics.py`** — `git diff metrics.py` post-step must not introduce a provider tag that does not appear in the canonical map §A. A drift here means the dispatcher silently rotated to a non-canonical provider and the step's "no silent fallback" promise was broken.

The **per-step 7-day window** above is a *step gating* discipline — the next step in the table cannot start until the current step's 7-day window has stayed green end-to-end. The **post-cutover 7-day verification window** at the bottom of this runbook ("7-day post-cutover monitoring window") is the *aggregate* watch that runs once **all 10 steps** have completed; the two windows can overlap when steps are bundled into a single deploy, but the per-step window always governs that step's rollback decision in isolation.

| # | Step | Acceptance check | Rollback |
|---|---|---|---|
| 1 | **Sarvam health gate (canonical Assamese chat primary).** Confirm `GET /api/admin/health/sarvam` returns `ok` with `success_rate ≥ 0.95` over the trailing hour and ≥ 20 samples. The Sentry `<95 %/1h` alarm (Task #553) must be quiet. This step blocks all downstream Assamese routing changes. | `curl -fsS $API/api/admin/health/sarvam \| jq '.ok and .success_rate >= 0.95'` returns `true`; Sentry "Sarvam success-rate <95 %" alert is in `RESOLVED` state. | Hold the cutover; investigate the Sarvam upstream + per-user-monthly-cap exhaustion bucket before proceeding. |
| 2 | **Assamese priority flip to canonical chain.** `PROVIDER_PRIORITY["assamese_rag_chat"]` in `config.py` must equal `[sarvam, workers_ai_indic]` (no Vertex, no Azure-OpenAI, no Workers-AI generic in this list). Watch `/api/ai_chat?lang=as` for 24 h against the per-step thresholds above. | `python -c "from scripts.ci.check_canonical_delegation import _check_chat_chains; assert not _check_chat_chains()"`; `metrics.py` shows only `sarvam` + `workers_ai_indic` provider tags on Assamese turns over the next 24 h. | Restore the previous priority list in `config.py`; redeploy the previous backend revision per "Rollback to git tag / Bicep revision" below. |
| 3 | **English chat dynamic 2-chain verification.** `cost_caps._select_chat_primary()` must return `vertex` by default and flip to `workers_ai_llama32_3b` on ≤ 90 d projected GCP credit runway. The static `PROVIDER_PRIORITY["english_rag_chat"]` must equal `{vertex, workers_ai_llama32_3b}` (set equality). | `cd artifacts/syrabit-backend && pytest tests/test_provider_priority_locked.py -q` is green; `metrics.py` shows only those two provider tags on English turns over the next 24 h. | Restore the previous selector + priority list; redeploy previous backend image. |
| 4 | **Azure OpenAI removal verification.** Grep for any surviving `azure_openai|AzureOpenAI|AZURE_OPENAI_*|gpt-4.1-nano` literal across backend + frontend + workers + IaC; the umbrella's DEAD-PROVIDER bank covers this but a manual sweep catches stale comments + Bicep params. Azure Speech / Translator are unrelated and stay. | `python artifacts/syrabit-backend/scripts/ci/check_canonical_delegation.py` exits 0; `rg -n 'AZURE_OPENAI_' infra .github` returns no active env-var declarations (only retirement notes). | If a live caller is found, restore the `azure_openai` provider module from git history per the ADR's "Removed providers" rollback estimate (~5 engineer-days); pause the cutover. |
| 5 | **SES sole tier-1 verification.** Confirm `EMAIL_PROVIDER` / `EMAIL_FALLBACK` env vars are absent from ACA, that the SendGrid + Resend SDK names + API key envs are absent from backend / frontend / workers / lockfiles, and that an end-to-end SES probe (`POST /api/admin/email/probe?kind=ses`) returns `delivered`. | `python artifacts/syrabit-backend/scripts/ci/check_canonical_delegation.py` exits 0; ACA env-var listing has no `SENDGRID_API_KEY` / `RESEND_API_KEY` / `EMAIL_PROVIDER` / `EMAIL_FALLBACK` row. | Re-add the SES → SendGrid fallback shim from git history (~3 engineer-days per ADR); redeploy. |
| 6 | **Firebase deletion verification (web-push).** Confirm `firebase_admin` is uninstalled (`pip show firebase-admin` returns nothing in the runtime container), that `FCM_SERVER_KEY` / `FIREBASE_SERVICE_ACCOUNT` are absent from ACA + Key Vault active secrets, and that `GET /api/admin/push/migration-status` reports the FCM bucket at `purged` ≥ 99 %. The Service Worker (`public/sw.js` v15) must be the active service-worker version on the SPA. | Migration-status JSON shows `pending=0` and `tombstoned ≤ 1 %`; the umbrella's `TODO_557_PATTERN` row stays green. | Restore the `firebase_admin` adapter (~4 engineer-days per ADR); reinstate the FCM secret in Key Vault as a read-only replica until the rollback is complete. |
| 7 | **Observability narrowing verification (GCP Cloud Trace single exporter).** Confirm `OTEL_TRACES_EXPORTER=googlecloud` is the only value present in ACA env-vars (no comma list, no Axiom/AppInsights co-exporter), `traces_sample_rate=0` in `observability/sentry_setup.py`, and `/api/health/otel` reports a `last_export_ts` within the last 5 min. | `python artifacts/syrabit-backend/scripts/ci/check_canonical_delegation.py` exits 0; `curl -fsS $API/api/health/otel \| jq '.last_export_age_seconds < 300'` returns `true`. | Restore the prior `traces_sample_rate=0.1` + Azure App Insights exporter (~1 engineer-day per ADR); GCP Cloud Trace stays as a co-exporter during the rollback window. |
| 8 | **Voice paywall verification.** `routes/voice.py` `/tts`, `/stt`, `/voice/voice` must each sit behind `Depends(require_paid_plan)` OR `Depends(require_paid_plan_or_voice_preview)` (Task #581 §L9). Live-probe both as a free user (expect 402 with the per-day preview header) AND as a paid user (expect 200). | `python -c "from scripts.ci.check_canonical_delegation import _check_voice_paywall; assert not _check_voice_paywall()"`; `curl -i $API/api/voice/tts -H "Authorization: Bearer $FREE_JWT"` returns `402` with `X-Paywall-Voice-Kind`. | Re-add `Depends(require_paid_plan)` to the offending route; redeploy. |
| 9 | **Final CI landing — wire the umbrella as a deploy hard gate.** `.github/workflows/azure-container-apps-deploy.yml` carries a `canonical_delegation_gate` job that runs **before** `budget_ceiling_gate` and `exam_calendar_gate`; the `deploy` job depends on all three. Trigger a `workflow_dispatch` `mode=redeploy` and confirm the gate runs and passes. | Workflow run log shows `Canonical-delegation guard OK — scanned N files.` in the `canonical_delegation_gate` step; `deploy` job depends on `[canonical_delegation_gate, budget_ceiling_gate, exam_calendar_gate]`. | Remove the `needs: canonical_delegation_gate` line; deploy falls back to the prior multi-gate flow. |
| 10 | **Final docs landing — V4 §17 + replit.md + cost-share snapshot + ADR-0003 + this runbook all merged.** Append the §17 amendment in `infra/v4-locked-architecture.md`, update the "Cost split" line in `replit.md` to the post-cutover snapshot (**40 % CF / 30 % GCP / 15 % Az / 10 % AWS / 5 % other**), add the "Canonical specialist delegation" subsection in `replit.md`, and confirm ADR-0003 + this runbook are both committed. | `git diff` shows all five files in the same PR; `rg -c '^\| \*\*' infra/four-cloud-delegation.md` ≥ 18 (per-feature map present); `rg -n '^## §17' infra/v4-locked-architecture.md` returns one match. | Revert the docs PR; the umbrella + production code stay live (the docs are the operator-facing surface, not the enforcement). |

## 7-day post-cutover monitoring window

After step 10 lands on `main`, the on-call engineer holds a **7-day
verification window** where the canonical map is treated as
*provisional*. Each day the table below is checked; a single
threshold breach pages on-call and initiates the rollback path
defined in the same row. The window closes only after **seven
consecutive green days** — any breach restarts the counter.

| Signal | Source | Alert threshold | Rollback action |
|---|---|---|---|
| English chat 5xx rate | Sentry `route:/api/ai_chat` | > **0.5 %** of turns over any 1 h rolling window | Set `CHAT_PRIMARY_OVERRIDE=workers_ai_llama32_3b` to pin the cheap fallback as head; if 5xx persists > 30 min, redeploy the previous backend image (see "Rollback to git tag / Bicep revision" below). |
| Assamese chat 503 rate | `/api/health/sarvam` `success_rate` | < **95 %** over the trailing hour with ≥ 20 samples (already wired as a Sentry alert per Task #553) | Verify Sarvam upstream health; if Sarvam is up, redeploy previous backend image. The strict `[sarvam, workers_ai_indic]` chain is locked — there is no third option to flip to. |
| `/tts` `/stt` `/voice/voice` 402 rate for *paid* users | ACA Log Analytics `RouteRequest \| where status==402 and tier=="paid"` | > **0** in any 15 min window (paid users must never see 402 from the paywall) | Inspect `auth_deps.require_paid_plan` for a regression in the plan lookup; redeploy previous backend image if the regression is in the dispatch layer. |
| Umbrella guard regressions | `canonical_delegation_gate` job | **Any** failure on `main` post-cutover | The job blocks the deploy automatically; investigate the failing row and either correct the offending code OR (if the umbrella is wrong) revert the umbrella + open a follow-up. |
| ACA replica restart loop | Azure Monitor `ContainerAppRevisionReplicaCount` | > **3 restarts** in 30 min for the post-cutover revision | Run `az containerapp revision deactivate` on the post-cutover revision and re-route traffic to the prior revision (see rollback below). |
| Cost-share drift | Quarterly credit-runway memo (Task #550) | Cash side > **$100 / mo** for ≥ 1 day | Page founder; do **not** auto-rollback — the $100 cap is enforced separately by `MeterD` and `scripts/check_budget_ceiling.py`. |
| Sentry inbound-event volume | Sentry org-level "Stats" tab | > **4 000 events / mo** projected | Pre-emptive warning before the 5 000 / mo Sentry-Developer free-tier cap; tighten the `before_send` filter or raise sampling thresholds. No deploy rollback. |

The on-call engineer logs the daily green / red verdict in the
`#ops-alerts` channel; the seven greens close the window and tag
the canonical map version into the V4 changelog (step 9 of the
cutover table above).

### Rollback to git tag / Bicep revision

If any threshold above triggers and the in-place mitigation does
not clear the alarm within **30 minutes**, fall back to the
previous canonical-map cohort:

1. Find the last green deploy: `az containerapp revision list --name syrabit-backend -g $RG --query "[?properties.healthState=='Healthy'] | sort_by([], &properties.createdTime)[-2].name" -o tsv` (the `-2` index skips the failing post-cutover revision).
2. Re-route 100 % traffic to that revision: `az containerapp ingress traffic set --name syrabit-backend -g $RG --revision-weight $PREV_REV=100`.
3. Tag the rollback in git: `git tag -a rollback/559-cutover-$(date -u +%Y%m%dT%H%M%SZ) -m "Rolled back Task #559 canonical-map cutover; reason: <signal>"`.
4. If the umbrella guard itself is the regression, revert `artifacts/syrabit-backend/scripts/ci/check_canonical_delegation.py` to the prior commit (`git revert <SHA>`) and force the legacy `scripts/check_dead_providers.py` back to its pre-shim contents from git history.
5. File a follow-up under the rejected canonical-map row to fix the underlying issue before re-attempting the cutover.

The Bicep template (`infra/azure/aca-syrabit-backend.bicep`) is the
secondary recovery surface: a `New-AzResourceGroupDeployment` against
the previous revision tag re-creates the env-var / secret-ref
contract verbatim so even a half-deleted Container App can be
restored from the IaC artifact alone.

## Drill cadence

- Run steps 1 + 3 + 4 + 7 on **every PR** that touches `infra/`, `config.py`, `cost_caps.py`, `routes/voice.py`, or any `scripts/ci/*` file. The deploy workflow does steps 4 + 7 automatically.
- Run the full 10 steps quarterly alongside the V4 §8 DR drill so the canonical map stays in sync with the runway memo (Task #550 quarterly review cadence).

## TODO (when Tasks #557 / #558 ship)

- Step 11 (added by #557): flip the SES + self-hosted-web-push bans from TODO to active in the umbrella (`TODO_557_PATTERN`); update `replit.md` "Required env vars" to add `WEB_PUSH_VAPID_PRIVATE_KEY` and remove SendGrid / FCM keys.
- Step 12 (added by #558): flip the observability ban from TODO to active in the umbrella (`TODO_558_PATTERN`); confirm `OTEL_TRACES_EXPORTER=googlecloud` is the only value present in the ACA env-var set.
