# Runbook Results — Tasks #57–#60 — 2026-05-10

This doc accumulates the outcome of the four redeploy + smoke tasks
that follow the #53 push. Each section appends as the corresponding
task completes (or defers).

---

## Task #57 — Backend redeploy (ACA) + smoke

**Status: DEFERRED — blocked on manual `workflow_dispatch` + admin auth.**

### What we found

1. **The ACA deploy workflow is `workflow_dispatch` only.** Per
   `.github/workflows/azure-container-apps-deploy.yml` line 16:
   *"Trigger is workflow_dispatch only — auto-deploys on push will be
   enabled once the cutover runbook (see infra/azure/aca-cutover.md) is
   signed off."* The 91 commits in the #53 push (and the subsequent
   #54 / #55 / #56 audit-doc commits) **did not trigger an ACA build**.
2. **Latest ACA deploy run:**
   `25598733120` — commit `9252d2a` — `success` — `2026-05-09T10:21:23Z`.
   This pre-dates the #53 push by ~24 h. Production ACA is therefore
   running an **older revision** than `origin/main` (currently
   `5985ad9`).
3. **`/api/health` direct-origin probe is healthy on the older
   revision.** 10/10 200s, mean latency `0.66 s`, body
   `{"ok":true,"service":"syrabit-backend-do","version":"2.0.0","ts":1778413236}`.
   Response headers carry standard hardening (HSTS, CSP, COOP, X-RID)
   but **no `X-Build-SHA` / `X-Revision` header** — so we cannot
   passively confirm which commit is live, only that the health probe
   succeeds.
4. **All other smoke routes are blocked from the audit shell:**
    - **Direct ACA origin** — every route except `/api/health` returns
      `403 {"detail":"Direct origin access denied — must traverse the
      edge worker."}`. This is the working `OriginGate` policy
      (`X-Origin-Auth` enforcement; `ORIGIN_SHARED_SECRET` /
      `BACKEND_ORIGIN_SECRET` lock-step rotation per `replit.md`
      Gotchas). The audit shell does not hold the shared secret and
      MUST NOT.
    - **Cloudflare edge (`https://syrabit.ai/api/*`)** — every route
      returns `403` with `cf-mitigated: challenge` (Cloudflare bot
      management challenge). The audit shell is not a real browser
      and so cannot complete the challenge. This is the working WAF +
      Bot Management policy (Task #9 — verified-bot KV fast path).

### Direct-origin smoke matrix (raw)

| Route | Status | Time | Notes |
|---|---|---|---|
| `/api/health` | 200 | 0.63 s | healthy, JSON body as above |
| `/api/health/cache` | 404 | 0.63 s | not exposed at the un-gated path; reached via `/admin` after auth |
| `/api/health/season` | 404 | 0.63 s | same |
| `/api/me/quota` | 403 | 0.62 s | `OriginGate` denial — expected |
| `/api/admin/health/embed-stack` | 403 | 0.62 s | `OriginGate` denial |
| `/voice/tts` | 403 | 0.63 s | `OriginGate` denial (paywall is downstream of this) |
| `/api/auth/login` | 403 | 0.62 s | `OriginGate` denial — cannot confirm whether #52 retired this to 410 from this surface |
| `/api/auth/signup` | 403 | 0.75 s | same |
| `/api/auth/google/callback` | 403 | 0.62 s | same |

### Edge smoke matrix (raw)

| Route | Status | Body |
|---|---|---|
| `/api/health` (edge) | 403 | Cloudflare bot challenge HTML |
| `/api/health/cache` (edge) | 403 | same |
| `/api/health/season` (edge) | 403 | same |
| `/api/me/quota` (edge) | 403 | same |
| `/voice/tts` (edge) | 403 | same |
| `/api/auth/login` (edge) | 403 | same |

`server: cloudflare` + `cf-mitigated: challenge` confirm the WAF is
serving the challenge interstitial — this is the **expected user
experience for a non-browser client**, not a regression.

### What's required to actually finish #57

1. **Human-driven `workflow_dispatch`** of
   `azure-container-apps-deploy.yml` against `origin/main@5985ad9`
   with input `app=syrabit-backend`. This needs Azure auth (the OIDC
   federation already configured in `iam-azure-federation.tf`) and
   GitHub-side write on the workflow.
2. **Per-revision verification** via
   `az containerapp revision list -n syrabit-backend -g syrabit-prod-rg`
   to confirm the new revision exists, is `Provisioned`, and holds
   `trafficWeight=100`. The audit shell has no `az` CLI auth.
3. **Smoke matrix via either:**
   (a) the edge with a real browser session that has cleared the CF
   challenge (Task #9 verified-bot allowlist *would* let a known
   crawler UA through, but the smoke matrix should ride a real session
   to exercise SSO + the `/api/me/quota` paywall path), **or**
   (b) the origin with the `X-Origin-Auth` shared-secret header set
   (the on-call ops human can do this from the bastion; the audit
   shell cannot).
4. **Sentry release-tag sweep** for the new release tag — needs Sentry
   auth.

### Proposed follow-up

Single follow-up task (Task #62): *"Manually `workflow_dispatch` the
ACA deploy of `origin/main@<HEAD>` and run the smoke matrix from the
ops bastion."* This unblocks #58 (frontend redeploy via Cloudflare
Pages, which **does** auto-build on push and so is not blocked by the
same constraint), #59 (workers redeploy), and #60 (post-cutover
end-to-end).

### Append-only audit log

| Time (UTC) | Action | Outcome |
|---|---|---|
| 2026-05-10 11:40 | Pulled last 5 ACA workflow runs via GitHub API | Latest is `25598733120` (success) on commit `9252d2a` from 2026-05-09 — pre-#53 |
| 2026-05-10 11:40 | 10× `/api/health` probe (direct origin) | 10/10 200s, mean 0.66 s |
| 2026-05-10 11:40 | 9-route direct-origin smoke matrix | 1× 200, 2× 404, 6× 403 (expected `OriginGate` denials) |
| 2026-05-10 11:41 | Edge-URL probe | All routes 403 + Cloudflare bot challenge — expected for a non-browser client |
| 2026-05-10 11:41 | Capture written to this file | — |
