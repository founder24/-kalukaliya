---
name: Cloudflare ↔ GCP configuration audit
description: Full audit of all CF/GCP connections, token permissions, secret mismatches, and fixes applied.
---

## Single token covers everything
`$CLOUDFLARE_KV_API_TOKEN` (and `$CF_ACCOUNT_ID`) works for all CF API uses:
Workers KV Storage: Edit, Workers AI: Edit, Zone Cache Purge: Edit, CF Pages: Edit, R2: Read.
**Why:** Audited every CF API call in the codebase and tested each against the live token.

## Duplicate GCP SM secrets with different values
GCP SM has both uppercase and lowercase versions of key secrets — they have DIFFERENT values:
- `JWT_SECRET` (64c) ≠ `jwt-secret` (65c)  
- `EDGE_SHARED_SECRET` (64c) ≠ `edge-shared-secret` (64c, different)
- `MONGODB_URI` (79c) ≠ `mongodb-uri` (79c, different)

Cloud Run backend and `cloudflare-worker-secrets.sh` both use the **lowercase** versions. Uppercase are orphaned relics. Risk: automation using wrong names gets wrong secrets.

## Cloud Run CF KV vars (fixed 2026-06-07)
Backend needs these three to invalidate edge cache after content publish. Were missing:
- `CLOUDFLARE_KV_API_TOKEN` → mounted from SM `CF_KV_API_TOKEN`
- `CLOUDFLARE_ACCOUNT_ID` → mounted from SM `CF_ACCOUNT_ID`
- `CLOUDFLARE_KV_NAMESPACE_ID` → mounted from SM `CF_KV_NAMESPACE_ID` (= CONTENT_KV id: `981e939bcca445c481d4be818ebefee7`)

## wrangler.toml BACKEND_URL (fixed 2026-06-07)
Had `BACKEND_URL` in `[env.production.vars]` → CF REST API error 10053 when infra script tried `wrangler secret put BACKEND_URL`. Removed from production vars; only in `[vars]` as localhost for dev. Next `wrangler deploy --env production` needed before secret can be set.

## BACKEND_URL can't be set via CF REST API while it's a [vars] binding
CF API error 10053 = binding name already in use. `wrangler secret put` CLI works though. Fix: remove from toml vars, redeploy, then set secret.

## CF Pages BACKEND_BOT_URL (fixed 2026-06-07)
`_worker.js` bot renderer fell back to `https://api.syrabit.ai` (extra edge proxy hop) when `BACKEND_BOT_URL` not set. Set to direct Cloud Run URL for lower bot rendering latency.
