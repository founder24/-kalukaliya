# Task #472 — CF Redeploy from `founder24/-kalukaliya` — Handoff

**Date:** 2026-05-06
**Status:** Partially complete. Embed worker redeployed. Edge worker + git push + Pages rewire **require manual follow-up** (sandbox limitations + drift).

---

## What was done in-session

| Action | Result |
|---|---|
| Inventoried CF services | 3 live workers: `syrabit-edge`, `syrabit-edge-preview`, `syrabit-embed-worker`. 1 live Pages project: `syrabit-analytics` (currently sourced from `shaitanfiles-cloud/syrabit-zip-convert`). |
| `syrabit-embed-worker` redeploy | ✅ **Success** — version `934ea313-0f8d-49ca-8c10-71e2a9618a15`, route `embed.syrabit.ai/*`. Local FS deploy via `wrangler deploy --env production`. |
| `syrabit-edge` redeploy | ❌ **Failed** at mTLS gate (binding `MTLS_CERT` has sentinel `PENDING_CERT_PROVISIONING`, CF API code 10021). |
| `syrabit-edge-preview` redeploy | ❌ **Failed** at mTLS gate (same reason). |

### ⚠️ Side-effect to be aware of

The failed `syrabit-edge` deploy created **two R2 buckets** in the CF account before aborting at the mTLS step:

- `syrabit-assets`
- `syrabit-media`

These did not exist before. They are now empty buckets in the production CF account. If unintended, delete via dashboard or:

```bash
wrangler r2 bucket delete syrabit-assets
wrangler r2 bucket delete syrabit-media
```

The Durable Object `RateLimiter` namespace was **not** created (deploy aborted at the Workers Scripts API PUT call, after R2 provisioning but before the script upload that runs the `[[migrations]] v1` block).

---

## Drift discovered: live `syrabit-edge` ≠ repo

Live worker is missing 4 binding categories declared in `workers/edge-proxy/wrangler.toml`:

| Aspect | Live | Repo |
|---|---|---|
| `compatibility_date` | `2025-05-01` | `2026-05-01` |
| `logpush` | `false` | `true` |
| `r2_buckets` | none | `ASSETS` + `R2_MEDIA` |
| `analytics_engine_datasets` | none | `ANALYTICS` |
| `durable_objects` | none (`migrations: None`) | `RATE_LIMITER_DO` + migration `v1` creating class `RateLimiter` |
| `mtls_certificates` | none | `MTLS_CERT` (sentinel) |

The live worker is currently using **KV-based rate limiting** (the documented fallback when the DO namespace is absent). A successful deploy from this repo would activate DO-based rate limiting on first run.

User decision (logged in chat 2026-05-06): **deploy as-is, accepting the mTLS failure**. The next attempt requires `CF_MTLS_CERT_ID` to be provided (a real Cloudflare mTLS cert UUID provisioned via `cloudflare-phase6-apply.js`).

---

## Manual follow-up: git push to new repo

The user-approved approach: **force-push local `master` to a fresh branch `syrabit-import` on `founder24/-kalukaliya`**, leaving `main` untouched (the new repo's `main` already contains 123 MB of unrelated content authored by the `founder24` user — those histories share no common ancestor with this codebase).

The Replit sandbox blocks destructive git operations even from inside an assigned project task, so the push must happen from the user's own machine.

### Exact commands (run from the local repo root)

```bash
# 1. Add the new remote (skip if already added)
git remote add kalu git@github.com:founder24/-kalukaliya.git
# or via HTTPS + token:
# git remote add kalu https://<gh_pat>@github.com/founder24/-kalukaliya.git

# 2. Verify local HEAD matches the Replit working copy
git rev-parse HEAD
# Should print: c4684901433162da25bc5447b7dbbd49a2e6bc0a (or newer)

# 3. Force-push local master to a NEW branch syrabit-import on the new remote.
#    --force-with-lease is a safety net (refuses if the remote ref unexpectedly exists).
git push kalu master:syrabit-import --force-with-lease

# 4. (Optional) Confirm on GitHub:
#    https://github.com/founder24/-kalukaliya/tree/syrabit-import
```

### After the push: 4 things still to wire up

1. **Set GitHub Actions secrets** on `founder24/-kalukaliya` so the existing CI workflows (`.github/workflows/edge-proxy-deploy.yml`, `.github/workflows/embed-worker-staging-deploy.yml`, `.github/workflows/azure-container-apps-deploy.yml`) run on the new repo:
   - `CLOUDFLARE_API_TOKEN`
   - `CLOUDFLARE_ACCOUNT_ID` = `d66e40eac539fff1db270fddf384a5ec`
   - `CF_MTLS_CERT_ID` (real UUID — see `workers/edge-proxy/scripts/cloudflare-phase6-apply.js` to provision)
   - `D1_SYNC_SECRET`, `AI_FALLBACK_SECRET` (preview-env values)
   - `D1_SYNC_SECRET_PROD`, `AI_FALLBACK_SECRET_PROD` (prod values)
   - `EMBED_STAGING_SHARED_SECRET`
   - Repo variable: `CF_WORKERS_SUBDOMAIN`

2. **Update the workflow trigger branch.** Existing workflows trigger on push to `Replit-agent` or `main`. They will NOT trigger on `syrabit-import` until you add it to the `on.push.branches` list — or until you merge / rename the branch.

3. **Rewire CF Pages `syrabit-analytics`** to source from the new repo. The current source is `shaitanfiles-cloud/syrabit-zip-convert@replit-agent`. Use the CF API:
   ```bash
   curl -X PATCH \
     -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
     -H "Content-Type: application/json" \
     "https://api.cloudflare.com/client/v4/accounts/d66e40eac539fff1db270fddf384a5ec/pages/projects/syrabit-analytics" \
     -d '{
       "source": {
         "type": "github",
         "config": {
           "owner": "founder24",
           "repo_name": "-kalukaliya",
           "production_branch": "syrabit-import"
         }
       }
     }'
   ```
   The Pages GitHub App must be installed on the new repo first (CF dashboard → Pages → Connect new account/repo).

4. **Provision `CF_MTLS_CERT_ID`** and re-run the `syrabit-edge` deploy. Either:
   - Set it locally and re-run from this Replit (`bash workers/edge-proxy/scripts/inject-mtls-cert-id.js && wrangler deploy`), or
   - Push to the new repo and let CI run.

   Cleanup option if you want to abandon the drifted bindings: comment out the `[[r2_buckets]]`, `[[analytics_engine_datasets]]`, `[[durable_objects.bindings]]`, `[[migrations]]`, and `[[mtls_certificates]]` blocks in `workers/edge-proxy/wrangler.toml` to deploy a bytes-only refresh matching the current live binding shape. Don't forget to delete the two side-effect R2 buckets too.

---

## What was NOT touched

- `workers/edge-proxy/wrangler.toml` — left as-is. No drift workarounds applied.
- Pages `syrabit-analytics` source config — unchanged. Still pointing at `shaitanfiles-cloud/syrabit-zip-convert`.
- `master` branch on `founder24/-kalukaliya@main` — unchanged. Still at `0a08ae11`.
- GitHub Actions secrets on the new repo — none set.
- Dead-code workers (`syrabit-edge-proxy` in `artifacts/`, `syrabit-email`, `syrabit-email-worker`) — confirmed never deployed; left untouched.
