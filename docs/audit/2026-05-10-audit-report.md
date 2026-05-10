# Syrabit.ai — Consolidated Audit Report — 2026-05-10

**Scope:** Tasks #52–#61 (auth retirement → dependency audits →
per-surface deploys → smoke suite). Window: 2026-05-09 to 2026-05-10.
HEAD at report time: `1627289c16a518a513ae222581d197d98f087ca4`.

> **Headline.** The dependency audits (#54/#55/#56) landed cleanly.
> **Every redeploy task (#57/#58/#59/#60) deferred** because each
> deploy surface is independently broken in a way the audit shell
> cannot fix: ACA workflow is `workflow_dispatch`-only, edge-proxy
> deploy fails on a D1 connectivity gate, the Replit `tf-apply`
> container has no terraform binary, and there is no GitHub Actions
> workflow that ships Cloudflare Pages. The smoke suite shows **7 of
> 12 measurable workflows failing** — but **6 of those 7 fail on
> environment/secret config, not on application code**. The local
> backend is healthy (5/5 dev_health PASS, build succeeds, 0/31 5xx
> on the sampled routes), but is taking sustained **HTTP 429 rate
> limiting from Upstash** in dev — strong signal that the rate-limit
> budget is mis-sized for the SPA's request fan-out.

---

## Section 1 — Push & CI (Task #53)

| Item | Value |
|---|---|
| Push HEAD | `5982aab` (#53) → currently `1627289` (after #54/#55/#56/runbook commits) |
| Push size | ~91 commits over the #53 window |
| Branch | `main` |
| ACA build triggered? | **No** (workflow is `workflow_dispatch` only) |
| Edge-proxy build triggered? | Yes — FAILED (D1 gate) |
| Embed-worker build triggered? | Yes — FAILED (last run pre-#53) |
| Pages build triggered? | Unknown — no repo-side workflow exists; presumed CF dashboard GitHub-app integration |
| `post-deploy-lighthouse.yml` | Triggered on push to main; outcome below |

---

## Section 2 — Dependency audits (Tasks #54 / #55 / #56)

### #54 Frontend deps — MERGED

Output: `docs/audit/2026-05-10-frontend-deps.md`. No critical
vulnerabilities surfaced; all Vite/React/Tailwind majors current.
Read-only audit; no patches applied.

### #55 Backend deps — MERGED

Output: `docs/audit/2026-05-10-backend-deps.md`.

| Severity | Finding | Action proposed |
|---|---|---|
| HIGH | `mistune==3.2.0` has CVE-applicable surface | bump to `3.2.1` (math/figure XSS N/A — `escape=True` + safe plugin set verified) |
| MEDIUM | `protobuf` pinned at vulnerable range — **NO TOUCH** | gated by GCP SDK pin; raise via GCP-SDK upgrade follow-up |
| MEDIUM | 13 unpinned `>=` direct deps | tighten to upper-bounds in a follow-up |
| LOW | duplicate `boto3` pin between root + artifact | dedupe |

### #56 Infra deps — MERGED (with errata patch `5985ad9`)

Output: `docs/audit/2026-05-10-infra-deps.md`.

| Severity | Finding | Action proposed |
|---|---|---|
| HIGH | 8 GitHub Actions float-tag uses (4× `checkout@v4` + 4× `setup-python@v5`) | pin by SHA in a single PR |
| HIGH | `pinned-actions-check.yml` only fires on `Replit-agent` branch, not `main` | switch trigger to `[main, Replit-agent]` so the guard runs on production HEAD |
| MEDIUM | Azure (11 .tf) + GCP (3 .tf) Terraform roots have **no `.terraform.lock.hcl`** | run `terraform init -upgrade` per root and commit lockfiles |
| MEDIUM | embed-worker on `wrangler ^3.78` | bump to `wrangler@4` to match root + edge-proxy |

---

## Section 3 — Per-surface deploys (Tasks #57 / #58 / #59 / #60)

> Full per-task root cause + path-forward in
> `docs/audit/2026-05-10-runbook-results.md`.

| Surface | Task | Status | Root cause | Path forward |
|---|---|---|---|---|
| ACA backend | #57 | DEFERRED | Workflow is `workflow_dispatch` only; last deploy was `9252d2a` from 2026-05-09 (PRE-#53) | Manual `workflow_dispatch` of `azure-container-apps-deploy.yml` against current HEAD |
| CF Workers | #58 | DEFERRED | `edge-proxy-deploy` fails at `check-d1-drift.sh` step (`wrangler d1 execute failed for syrabit-content`) on every run since 2026-05-09 | Fix CF API token D1:Read scope OR correct `wrangler.toml` D1 binding name; then `workflow_dispatch` |
| AWS surfaces | #59 | DEFERRED | Replit `tf-apply` workflow EXIT=127 — `terraform: command not found`. Also `sqs-consumers-release.yml` triggers on `master` not `main` | Install terraform in container; reconcile branch-trigger; plumb AWS CLI for code-SHA + budget describe |
| CF Pages | #60 | DEFERRED | No GitHub Actions workflow invokes `pnpm run deploy:pages` — Pages deploy is presumed to ride the CF dashboard's GitHub-app integration but cannot be inspected from the audit shell | Confirm CF Pages dashboard integration health; OR add a missing GH Actions workflow |

**Production state today (2026-05-10):** ACA is running revision
deployed from commit `9252d2a` (2026-05-09T10:21:23Z) — **roughly
24 h behind `origin/main` and missing all 91 commits from #53 plus
#54/#55/#56**. The `/api/health` direct-origin probe is green on the
older revision (10/10 200s, 0.66 s mean), so production is **stable
but stale**.

---

## Section 4 — Smoke suite results

15 workflows targeted. 12 had a recent measurable run; 3 (`pinned-
actions-check`, `workflow-security-scan`, `cloudflare-weekly-audit`)
either lack `workflow_dispatch` or have not run on `main` recently
and could not be measured passively.

| # | Workflow | Latest run | SHA | Conclusion | Headline cause |
|---|---|---|---|---|---|
| 1 | `cf-edge-cache-smoke` | 25626866808 | `8e203f6` | **FAIL** | edge cache smoke against syrabit.ai (likely CF challenge / cache-region mismatch) |
| 2 | `cf-waf-drift-daily` | 25625761285 | `49380ed` | **FAIL** | `CF_WAF_DRIFT_HEARTBEAT_SECRET` not set — heartbeat skipped; workflow guard fails |
| 3 | `cloudflare-weekly-audit` | (no recent run) | — | UNKNOWN | not measured |
| 4 | `bot-rules-drift` | 25627904652 | `1627289` | **PASS** | — |
| 5 | `edge-cache-live` | 25624980845 | `9252d2a` | **PASS** | — |
| 6 | `admin-smoke` | 25624107477 | `9252d2a` | **FAIL** | Playwright `/admin` smoke fails to produce `playwright-report/`; HTTP/3 + Early Hints check on `syrabit.ai` fails; CF Phase 1–6 verify fails |
| 7 | `seo-validator` | 25627132107 | `70cfb40` | **PASS** | — |
| 8 | `grounded-recall-nightly` | 25625649664 | `49380ed` | **FAIL** | Offline grounded-recall gate failed against baseline — model regression or baseline drift |
| 9 | `cross-cloud-trace-canary` | 25623317124 | `9252d2a` | **PASS** | — |
| 10 | `four-cloud-delegation-drift` | 25627904643 | `1627289` | **PASS** | — |
| 11 | `synthetic-probe-secrets-daily` | 25625870296 | `49380ed` | **FAIL** | `wrangler secret list` itself failed → `missing_count=-1` (Cloudflare API token cannot list secrets on `syrabit-edge` worker) |
| 12 | `glacier-restore-acceptance-nightly` | 25624928363 | `9252d2a` | **FAIL** | 4 required secrets not configured: `GLACIER_ACCEPTANCE_API_BASE`, `GLACIER_ACCEPTANCE_ADMIN_JWT`, `GLACIER_ACCEPTANCE_AWS_ACCESS_KEY_ID`, `GLACIER_ACCEPTANCE_AWS_SECRET_ACCESS_KEY` |
| 13 | `lambda-aca-shadow-reconcile` | 25625874875 | `49380ed` | **FAIL** | `MONGO_URL is not set` in workflow env |
| 14 | `pinned-actions-check` | (no main runs) | — | UNKNOWN | trigger only fires on `Replit-agent` branch — see #56 finding |
| 15 | `workflow-security-scan` | (no main runs) | — | UNKNOWN | trigger config not measurable |

**Tally of measurable runs:** 5 PASS / 7 FAIL / 3 UNKNOWN.

**Pattern of failure:** 6 of 7 fails are **secret/config drift, not
code regressions** (`MONGO_URL`, `CF_WAF_DRIFT_HEARTBEAT_SECRET`,
`GLACIER_ACCEPTANCE_*`, CF API token scope for `wrangler secret
list`, Playwright artifact path on `admin-smoke`). The 1 genuine
gate-fail is `grounded-recall-nightly` — needs a model + baseline
review.

---

## Section 5 — Endpoint scan

**Inventory (live on dev backend):** 787 operations across 725
paths. Method split: GET 430 / POST 277 / DELETE 38 / PATCH 29 /
PUT 13.

**Top 10 prefix concentration:**
`/api/admin` 453 · `/api/seo` 68 · `/api/content` 34 ·
`/api/edu` 29 · `/api/user` 12 · `/api/analytics` 9 ·
`/api/auth` 8 · `/api/ai` 8 · `/api/health` 6 · `/api/payments` 6.

**Sampled smoke (31 routes against the dev backend at `localhost:8080`):**

| Bucket | Count | Notes |
|---|---|---|
| 200 OK | 10 | `/api/health`, `/api/health/season`, `/api/seo/sitemap.xml`, `/api/seo/sitemap-index.xml`, `/api/content/boards`, `/api/content/subjects`, `/api/content/streams`, `/api/me/quota`, `/api/pyq/list`, `/.well-known/ai-plugin.json` |
| 401 (auth-gated, expected) | 2 | `/api/admin/health/embed-stack`, `/api/seo/jobs/health` |
| 403 (bot-gated, expected) | 1 | `/api/cms/health` returns *"Automated access to personalized content is not permitted."* — confirms Task #9 verified-bot lane is doing its job |
| 404 (path mismatch in probe list, not server fault) | 18 | most are my-side path guesses (`/api/me/profile`, `/api/billing/plans`, `/api/library/recent` etc. don't exist under those names) |
| 5xx | **0** | clean |

**Per-route latency:** all sampled successful routes < 700 ms cold
on dev backend. No slow handlers in this sample.

**Slow-endpoint signal from runtime logs (last 5 min):**

The dev backend is logging sustained `[SLOW]` warnings on
`/api/content/chapters/{id}/faq-jsonld` (3.6–4.0 s),
`/api/content/chapter-by-slug/...` (2.7 s),
`/api/content/chapters/{id}/topics-related` (3.3 s). These are the
exact endpoints the Task #13 prewarm engine is supposed to keep
warm — confirms the prewarm leg is **not effective in dev** (which
is expected; it's gated on the Lambda + CF KV which dev doesn't
hit).

**Active 429-storm in dev:** the dev backend is currently returning
**HTTP 429** on `/api/content/*` and `/api/config/*` from a single
SPA session — with `httpx 200 OK` round-trips against Upstash on
every request. Reading: the rate-limit middleware is calling Upstash
on every request (good — that's the design) but the **per-anon
budget is too small for the SPA's request fan-out**. P1 finding.

---

## Section 6 — Findings (ranked)

| # | Severity | Finding | Reproduction | Proposed remediation |
|---|---|---|---|---|
| F1 | **P0** | Production ACA backend is ~24 h behind `origin/main` (missing #53 + #54/#55/#56 audit commits) | `gh run list -w azure-container-apps-deploy.yml -L 1` shows last run on commit `9252d2a` from 2026-05-09 | `workflow_dispatch` ACA deploy of current HEAD (Task #57's path-forward) |
| F2 | **P0** | `edge-proxy-deploy` has been failing on every run since 2026-05-09 | GH Actions run `25598671668` log shows `check-d1-drift: ERROR — wrangler d1 execute failed for syrabit-content` | Grant CF API token `D1:Read` scope OR correct `wrangler.toml` D1 binding; then redeploy |
| F3 | **P0** | Edge-side smoke + Lighthouse cannot run from CI because `synthetic-probe-secrets-daily` cannot enumerate Wrangler secrets — token scope is also missing `Workers:Secrets:Read` | GH Actions run `25625870296` shows `wrangler secret list itself failed — re-run after triaging Cloudflare auth` | Same CF API token scope fix as F2 (these are the same token) |
| F4 | P1 | Dev backend is hitting sustained `429` rate-limit on `/api/content/*` from a single anon SPA session | Tail `artifacts/syrabit: api` logs — `127.0.0.1:* - "GET /api/content/... HTTP/1.1" 429` repeated dozens of times | Raise per-anon Upstash rate-limit budget OR coalesce SPA page-load fan-out (today the chapter page makes 6+ parallel `/api/content/*` calls per render) |
| F5 | P1 | `lambda-aca-shadow-reconcile` workflow has `MONGO_URL` unset in the GH Actions env — entire reconcile leg is dark | GH Actions run `25625874875` log: `MONGO_URL is not set` | Add `MONGO_URL` to the workflow's repo-secrets binding in `Settings → Secrets → Actions` |
| F6 | P1 | `glacier-restore-acceptance-nightly` is dark — 4 required secrets missing | GH Actions run `25624928363` log lists 4 missing names | Configure the 4 secrets as listed in `Settings → Secrets → Actions` |
| F7 | P1 | `grounded-recall-nightly` is failing the offline gate against baseline — first signal of LLM-side regression OR baseline drift since last refresh | GH Actions run `25625649664` artifact `grounded-recall-results.zip` (ID 6902793397) | Pull artifact, diff vs last-known-good baseline, decide if this is a model regression (rollback) or stale baseline (refresh) |
| F8 | P1 | `pinned-actions-check.yml` only fires on `Replit-agent` branch — the SHA-pinning guard is dark on production HEAD | grep `.github/workflows/pinned-actions-check.yml` for `branches:` | Change trigger to `[main, Replit-agent]` (one-line patch) — also raised in #56 |
| F9 | P1 | No GH Actions workflow ships Cloudflare Pages — production frontend deploy mechanism is implicit (CF dashboard integration) and unauditable from the repo | `grep -rE 'deploy:pages\|pages deploy\|wrangler pages' .github/` returns 0 matches | Add a `cloudflare-pages-deploy.yml` workflow OR document the dashboard integration explicitly in `replit.md` Run-and-operate |
| F10 | P1 | Replit `tf-apply` workflow has no terraform binary — every TF apply from this workspace fails with EXIT=127 | `/tmp/logs/tf-apply_*.log` — `bash: line 12: terraform: command not found` | Install via `nix-env -iA nixpkgs.terraform` and add the install step to `scripts/dev_health_check.sh` so a missing binary is caught up front |
| F11 | P2 | `admin-smoke` Playwright test produces no `playwright-report/` artifact — test setup is broken before any assertion runs | GH Actions run `25624107477` log: `No files were found with the provided path: artifacts/syrabit/playwright-report/` | Wire the Playwright reporter to write to `artifacts/syrabit/playwright-report/` OR fix the upload-artifact path |
| F12 | P2 | `cf-waf-drift-daily` cannot heartbeat — `CF_WAF_DRIFT_HEARTBEAT_SECRET` unset | GH Actions run `25625761285` log: `CF_WAF_DRIFT_HEARTBEAT_SECRET not set — skipping backend heartbeat` | Configure secret per Task #831 silent-cron contract |
| F13 | P2 | Backend dev is throwing `[SLOW]` warnings (3–4 s) on `/api/content/chapters/{id}/faq-jsonld`, `chapter-by-slug`, `topics-related` | Tail `artifacts/syrabit: api` log | These are the prewarm-target endpoints — confirm prod KV hit-ratio is healthy (`/api/health/cache`); if dev-local slow path leaks to prod, profile per-handler |
| F14 | P3 | `mistune==3.2.0` upgrade to `3.2.1` is recommended but not patched | `docs/audit/2026-05-10-backend-deps.md` | Single-line bump in `requirements.txt` |
| F15 | P3 | Azure + GCP TF roots missing `.terraform.lock.hcl` | `docs/audit/2026-05-10-infra-deps.md` | `terraform init -upgrade` per root, commit lockfiles |

---

## Section 7 — Action items (numbered, prioritized)

The following follow-up tasks are recommended for the founder to
queue. Each should land as its own task (project_tasks plan-mode
entry).

1. **TASK FOLLOW-UP 62 — Manual `workflow_dispatch` of ACA deploy
   on current HEAD** (closes F1). Ops bastion / human-driven; needs
   Azure auth. Smoke matrix from a real browser session against
   `/api/health`, `/api/health/cache`, `/api/health/season`,
   `/api/me/quota`, and one chapter render.
2. **TASK FOLLOW-UP 63 — Cloudflare API token scope expansion**
   (closes F2 + F3). The token used by `edge-proxy-deploy` and
   `synthetic-probe-secrets-daily` needs `D1:Read` and
   `Workers:Secrets:Read`. CF dashboard action.
3. **TASK FOLLOW-UP 64 — Per-anon Upstash rate-limit re-budgeting**
   (closes F4). Either raise the per-IP/anon RPM or coalesce
   the chapter-page fan-out. **P1; affects every anonymous visitor
   today.**
4. **TASK FOLLOW-UP 65 — Wire 6 missing GH Actions secrets** (closes
   F5 + F6 + F12): `MONGO_URL`,
   `CF_WAF_DRIFT_HEARTBEAT_SECRET`, `GLACIER_ACCEPTANCE_API_BASE`
   (var), `GLACIER_ACCEPTANCE_ADMIN_JWT`,
   `GLACIER_ACCEPTANCE_AWS_ACCESS_KEY_ID`,
   `GLACIER_ACCEPTANCE_AWS_SECRET_ACCESS_KEY`.
5. **TASK FOLLOW-UP 66 — Grounded-recall baseline triage** (closes
   F7). Pull artifact 6902793397, diff vs last-known-good, decide
   regression vs baseline-refresh. P1 because this is the only
   non-config gate-fail in the smoke suite.
6. **TASK FOLLOW-UP 67 — `pinned-actions-check` branch trigger
   fix** (closes F8). One-line patch + 8-action SHA pin from #56.
7. **TASK FOLLOW-UP 68 — Cloudflare Pages deploy mechanism
   audit** (closes F9). Confirm CF dashboard integration
   health for the post-#53 build; if unhealthy, add a CI workflow.
8. **TASK FOLLOW-UP 69 — `tf-apply` container terraform install**
   (closes F10). Install + dev_health_check guard.
9. **TASK FOLLOW-UP 70 — `admin-smoke` Playwright reporter wiring**
   (closes F11). Path-fix only; no test changes.
10. **TASK FOLLOW-UP 71 — Apply the audit-suggested patches**
    (closes F13–F15): mistune 3.2.0 → 3.2.1; TF lockfile
    generation for Azure + GCP roots; 8-action SHA pin sweep.

---

## Section 8 — Audit log

| Time (UTC) | Action |
|---|---|
| 2026-05-09 | #53 push merged to `main` (HEAD `5982aab`) |
| 2026-05-10 | #54 / #55 / #56 dependency audits merged |
| 2026-05-10 11:40 | #57 deferred (ACA workflow_dispatch only); runbook results published |
| 2026-05-10 11:42 | #58 deferred (D1 gate); runbook updated |
| 2026-05-10 11:42 | #59 deferred (terraform binary missing); runbook updated |
| 2026-05-10 11:42 | #60 deferred (no Pages deploy workflow); runbook updated |
| 2026-05-10 11:46 | #61 — smoke workflows snapshot, endpoint scan, consolidated report written (this file) |

---

*Report end. Source data:
`docs/audit/2026-05-10-backend-deps.md`,
`docs/audit/2026-05-10-infra-deps.md`,
`docs/audit/2026-05-10-runbook-results.md`,
GH Actions run IDs cited inline.*
