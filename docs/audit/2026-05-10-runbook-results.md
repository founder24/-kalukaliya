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

---

## Task #58 — Cloudflare Workers redeploy + smoke

**Status: DEFERRED — pre-existing deploy failure on `edge-proxy-deploy` blocks the redeploy path.**

### What we found

1. **`edge-proxy-deploy` has been failing since 2026-05-09** — last run
   `25598671668` (commit `9252d2a`, **PRE-#53**) failed at the
   `wrangler deploy --env preview` step with:
    ```
    > syrabit-edge@1.0.0 deploy:preview
    > bash scripts/check-d1-drift.sh && wrangler deploy --env preview
    check-d1-drift: comparing applied migrations on
      syrabit-content vs syrabit-content-preview
    check-d1-drift: ERROR — wrangler d1 execute failed for syrabit-content
    Re-run with VERBOSE=1 for the full wrangler output, or run:
      wrangler d1 execute syrabit-content --remote --command 'SELECT 1'
    to confirm wrangler can reach the DB at all.
    ERR_PNPM_RECURSIVE_RUN_FIRST_FAIL Exit status 1
    ```
   This is the **D1 drift gate** (added pre-Task #53) refusing to ship
   the worker when it cannot reach the prod D1 binding. Root cause is
   either a Cloudflare API-token permission scope (missing
   `D1:Read`) or D1 binding-name drift between `wrangler.toml` and the
   prod project.
2. **`embed-worker-staging-deploy` last failed 2026-05-08** on commit
   `00ffa7f` — also pre-#53.
3. **`email-worker-deploy.yml` does not exist in `.github/workflows/`.**
   `workers/email-worker/` ships either via the same edge-proxy
   pipeline or out-of-band (no CI surface to verify here).
4. **No new push triggered the workflow during the #54/#55/#56 audit
   commits** because `edge-proxy-deploy.yml` has `paths:` filters that
   exclude doc-only changes.

### What's required to actually finish #58

1. **Fix the D1 connectivity gate** — either grant the deploy token
   `D1:Read` scope, or correct the `wrangler.toml` binding name. This
   is a Cloudflare-dashboard action that the audit shell cannot
   perform.
2. After the fix, **manually `workflow_dispatch`** `edge-proxy-deploy`
   against `origin/main@<HEAD>`.
3. Then run the smoke probes from the original task plan
   (`X-Cache-Region: ne-india`, verified-bot lane via
   `curl -A "Googlebot/2.1"`, etc.) — these all need a real browser
   session to clear the CF challenge anyway.

### Append-only audit log

| Time (UTC) | Action | Outcome |
|---|---|---|
| 2026-05-10 11:42 | Pulled 3 worker deploy workflow runs | All 3 latest are FAILURE; root cause is `check-d1-drift.sh` ERROR not introduced by #53 |
| 2026-05-10 11:42 | Captured failed-step log via GitHub jobs API | D1 reachability gate is the failing step |

---

## Task #59 — AWS surfaces redeploy + smoke

**Status: DEFERRED — Replit `tf-apply` workflow cannot execute (Terraform binary not installed in the Replit container).**

### What we found

1. **The `tf-apply` Replit workflow finished with EXIT=127** —
   `bash: line 12: terraform: command not found`. The workflow command
   exports AWS keys + cd's into the Terraform root and calls
   `terraform apply -input=false -auto-approve -parallelism=30`, but
   the Replit container does not have a Terraform binary on `$PATH`
   (the workflow tries `export PATH=/home/runner/.local/bin:$PATH`
   but no binary is installed there either).
2. **`sqs-consumers-release.yml` ships from `master`, not `main`.**
   The trigger is `push: branches: [master]` — every #53/#54/#55/#56
   push went to `main`, so this workflow has not run for the new HEAD.
   Last successful run was a `workflow_dispatch` against `bb9d64e`
   (also failed) — pre-#53.
3. **Lambda code-SHA verification needs `aws lambda get-function`** —
   the audit shell holds `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`
   secrets but the AWS CLI is not configured for read in this shell;
   this can be done but needs a separate plumbing pass to set up
   `~/.aws/credentials` from the secrets without leaking them to logs.
4. **AWS Budgets `monthly_cost` mirror at $100** is a Terraform-managed
   resource (`account-billing.tf`) — its current state cannot be
   verified without either `terraform plan` (blocked, see #1) or
   `aws budgets describe-budget` (blocked, see #3).

### What's required to actually finish #59

1. **Install Terraform** in the Replit container (or on a CI runner)
   so `tf-apply` can complete. Recommended: `tofu` (OpenTofu) or
   pinned `terraform 1.9.x` via `nix-env -iA nixpkgs.terraform`.
2. **Reconcile branch policy** for `sqs-consumers-release.yml` —
   either change its trigger to `main` or stop pushing #59-class
   changes to `main`-only.
3. **Plumb AWS CLI auth** into the audit shell (or run from a CI
   runner) to do the per-Lambda code-SHA + budget describe sweep.

### Append-only audit log

| Time (UTC) | Action | Outcome |
|---|---|---|
| 2026-05-10 10:42 | Replit `tf-apply` workflow ran | EXIT=127 — `terraform: command not found` |
| 2026-05-10 11:42 | Pulled `sqs-consumers-release` runs | Triggers on `master`, last run was `workflow_dispatch` against `bb9d64e` (pre-#53) FAILURE |

---

## Task #60 — Frontend (Cloudflare Pages) redeploy + smoke

**Status: DEFERRED — no GitHub Actions workflow exists to invoke `pnpm run deploy:pages`.**

### What we found

1. **`package.json` defines a `deploy:pages` script** that calls
   `pnpm dlx wrangler@4 pages deploy artifacts/syrabit/dist
   --project-name=${CF_PAGES_PROJECT_NAME:-syrabit-analytics}
   --branch=${CF_PAGES_BRANCH:-master} --commit-dirty=true`.
2. **No GitHub Actions workflow invokes this script.** A repo-wide
   grep of `.github/workflows/` for `deploy:pages | pages deploy |
   wrangler.*pages` returns **zero matches** (only `post-deploy-
   lighthouse.yml` mentions the Pages deploy in comments, as the
   thing it *waits* for).
3. **Cloudflare Pages may have its own GitHub-app integration** that
   builds on push to `main` (configured in the Cloudflare dashboard,
   not the repo) — this is the most likely current path. The audit
   shell cannot read the Cloudflare dashboard to confirm the
   integration is healthy.
4. **`post-deploy-lighthouse.yml` triggers on push to `main`** but
   spends most of its budget waiting for the Pages deploy URL to
   reach `success`. Its last 5 runs are not visible from this audit
   shell without adding it to the diagnostic batch — see #61 for the
   full smoke sweep.

### What's required to actually finish #60

1. **Confirm the Cloudflare Pages deploy mechanism** — either the
   in-dashboard GitHub-app integration (recommended; verify it built
   the post-#53 HEAD) or a missing GH Actions workflow (in which case
   add one calling `pnpm run deploy:pages`).
2. **Verify the live bundle hash** at `https://syrabit.ai/` differs
   from the pre-#53 baseline — needs CF bot-challenge bypass (real
   browser session).
3. **Confirm `/login` no longer renders the signup form** — same.
4. Run the 5-page Lighthouse sweep from the original task plan.

### Append-only audit log

| Time (UTC) | Action | Outcome |
|---|---|---|
| 2026-05-10 11:42 | Searched `.github/workflows/` for Pages deploy invocation | Zero matches — Pages deploy must be running via the CF dashboard's GitHub-app integration, not via repo CI |
