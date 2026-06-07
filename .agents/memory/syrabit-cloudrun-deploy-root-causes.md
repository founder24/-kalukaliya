---
name: Cloud Run deploy root causes (June 2026)
description: Two compounding bugs that caused mongodb_initialized:false and 503s on api.syrabit.ai
---

## Root Cause 1 — Full-path secret refs break gcloud CLI

When Cloud Run secret refs are set via the **REST API** (PATCH), they are stored as full project-ID paths:
```
'projects/blissful-acumen-495019-t6/secrets/mongodb-uri'
```
gcloud CLI cannot validate this format and crashes with:
```
gcloud crashed (ValueError): Invalid secret path 'projects/.../secrets/mongodb-uri' in annotation
```
This breaks every subsequent `gcloud run deploy` invocation, even if the secret itself is valid.

**Fix:** Use the Cloud Run v2 REST PATCH API to normalize all secret refs back to short name form (`mongodb-uri`), then deploy the new image via REST PATCH rather than gcloud. After normalization, gcloud works again.

**How to apply:** Before any `gcloud run deploy`, verify secret refs in service spec use short names only. Check with:
```python
curl -sf "https://run.googleapis.com/v2/projects/PROJECT/locations/REGION/services/SERVICE" ...
# look for 'secret' fields starting with 'projects/' — those are the broken ones
```

## Root Cause 2 — Wrangler top-level [vars] IS inherited by named environments

`wrangler.toml` top-level `[vars]` section entries ARE inherited by `[env.production]` if that environment does not explicitly override them. `BACKEND_URL = "http://localhost:8000"` in `[vars]` was being inherited by production.

The edge code specifically checks:
```javascript
const isLocalBackend = env.BACKEND_URL?.includes('localhost') || ...;
if (isProduction && isLocalBackend && ...) return 503;
```

This caused ALL `/api/` calls through `api.syrabit.ai` to return 503.

**Fix:** Add `BACKEND_URL = "https://syrabit-backend-bl6wu3psza-el.a.run.app"` explicitly in `[env.production.vars]`. Secrets set via CF API do NOT reliably override inherited vars.

**How to apply:** Any time a new top-level var is added to `[vars]` in wrangler.toml, verify it doesn't need to be overridden in `[env.production.vars]` too.

## Root Cause 3 — deploy.yml referenced deleted GCP SM secret name

`deploy.yml` had `--update-secrets=MONGODB_URI=MONGODB_URI:latest` (uppercase). The uppercase secret was deleted and replaced with lowercase `mongodb-uri`. This caused Cloud Run deploys to fail with secret-not-found errors.

**Fix:** Changed to `MONGODB_URI=mongodb-uri:latest` in deploy.yml.

## MongoDB serverSelectionTimeoutMS

5000ms is too tight for Cloud Run cold-start → Atlas SRV lookup + TLS from asia-south1. Increased to 30000ms in `apps/backend/app/db/mongo.py`.

## Architecture gap: library-bundle has no edge cache

`/api/v1/content/library-bundle` (79 KB curriculum metadata) was a pure backend proxy — no KV fallback — so any MongoDB issue caused "Failed to load library" in the browser.

**Fix:** Added stale-while-revalidate KV cache in `apps/edge/src/index.ts` before the generic `/api/` proxy block:
- Key: `api:library-bundle:{querystring}` in `ISR_CACHE_KV`
- FRESH_TTL: 5 min (serve from KV, no backend call)
- HARD_TTL: 2 hr (KV hard expiry)
- On STALE: serve cached immediately, revalidate backend in `ctx.waitUntil()`
- On MISS + backend 200: populate KV, serve fresh response
- On MISS + backend error: pass error through (first visit only)
- Headers: `X-Cache: HIT|STALE|MISS`, `X-Cache-Age: {seconds}`

**What NOT to cache this way:** user-specific endpoints (chat, profile, conversations), streaming endpoints, anything with auth context.
