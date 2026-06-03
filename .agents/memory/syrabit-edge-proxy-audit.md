---
name: Syrabit edge proxy audit fixes
description: Root cause and fixes for production test failures — wrong BACKEND_URL, CORS gaps, and GCP error normalization
---

## Root cause of 14/47 production failures

### 1. Wrong BACKEND_URL in wrangler.toml (PRIMARY — cascades to all API 404s)
`https://syrabit-backend-851687450401.asia-south1.run.app` is NOT a valid Cloud Run URL.
Valid format: `https://<service>-<hash>-<region-code>.a.run.app`
The wrong domain (`asia-south1.run.app`) resolves to GCP infrastructure that returns JSON 404 for all paths. Because the response has `application/json` content-type, the edge's non-JSON normalization guard skipped it — so JSON 404 passed through to every caller.
Fixed to: `https://syrabit-backend-bl6wu3psza-el.a.run.app`

**Why:** Commit set the value before the Cloud Run service was first deployed; the hash-based URL was pasted without the `.a.run.app` suffix.

**How to apply:** Always verify with `gcloud run services describe syrabit-backend --region asia-south1 --format='value(status.url)'`. The stable service URL does NOT change across redeploys. Can also override at runtime: `wrangler secret put BACKEND_URL --env production`.

### 2. CORS headers missing from edge-local /health and /health/full responses
The `/health` and `/health/full` handlers called `addSecurityHeaders()` but not `applyCorsHeaders()`. Proxied `/api/*` and `/health/*` routes already got CORS via the proxy path, but the two edge-local health handlers did not.
Fixed: added `applyCorsHeaders(headers, origin)` to both handlers.

### 3. GCP IAM 401/403 HTML not normalized to 503
The normalization guard converted non-JSON 404/5xx to 503, but 401 and 403 HTML from GCP IAM passed through with their original status codes. Since `GOOGLE_SA_KEY` may not be set in CF secrets, Cloud Run returns 403 for unauthenticated requests — which then leaked to API callers.
Fixed: added 401 and 403 to `isInfraError` check in `api-proxy.ts`. JSON 401/422 from FastAPI still pass through unchanged (the guard only applies when content-type is NOT application/json).

### 4. Smoke test silently tolerated 404 on chat endpoint
`deploy-all.yml` smoke test accepted any status < 500 as success on chat POST. 404 (broken proxy) was treated as passing. Fixed to explicitly reject 404.

### 5. Regression tests added
`apps/edge/tests/api-proxy.test.ts` now covers: GCP HTML 401→503, GCP HTML 403→503, FastAPI JSON 401 passthrough, FastAPI JSON 422 passthrough.
