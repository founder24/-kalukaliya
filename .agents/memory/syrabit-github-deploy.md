---
name: Syrabit GitHub repo + deploy fix
description: GitHub repo location, deploy.yml optional-secrets pattern, and the --remove-secrets guard that prevents stale Cloud Run secret refs from blocking deploys.
---

## Repo
`founder24/-kalukaliya` (public, default branch: main)

## The recurring deploy failure pattern
`gcloud run deploy` with `--update-secrets` fails with "Secret …/versions/latest was not found" when:
1. A GCP secret is referenced in `--update-secrets` but doesn't exist in Secret Manager
2. A secret WAS added by a previous "optional" `gcloud run services update` call, is now in the Cloud Run service spec, but the underlying GCP secret was deleted

Both cases produce a hard failure on `Creating Revision`.

## The fix applied (deploy.yml)
- **Hard-required secrets**: `--update-secrets` in the main deploy step contains only secrets that are guaranteed to exist (core runtime secrets like MONGODB_URI, JWT_SECRET, etc.). `CF_PAGES_DEPLOY_HOOK` was moved out of here.
- **Optional secrets**: handled by a `_check()` bash function in the "Attach optional content-pipeline secrets" step:
  - If the GCP secret EXISTS → add to `UPDATES`, call `--update-secrets`
  - If the GCP secret MISSING → add env var name to `REMOVALS`, call `--remove-secrets` (with `|| true` so it's safe if the env var wasn't set)
- Optional secrets managed this way: `cf-pages-deploy-hook` (CF_PAGES_DEPLOY_HOOK), `CF_ACCOUNT_ID` (CF_ACCOUNT_ID + CLOUDFLARE_ACCOUNT_ID), `CF_KV_API_TOKEN` (CLOUDFLARE_KV_API_TOKEN), `CF_KV_NAMESPACE_ID` (CLOUDFLARE_KV_NAMESPACE_ID), `GCS_CONTENT_BUCKET` (GCS_CONTENT_BUCKET)

**Why:** Cloud Run's revision spec is immutable — once a secret ref is embedded it persists across deploys until explicitly removed. The `--remove-secrets` call cleans up stale refs proactively.

**How to apply:** Any new optional secret must go into the `_check` block, never into the main `--update-secrets` line.
