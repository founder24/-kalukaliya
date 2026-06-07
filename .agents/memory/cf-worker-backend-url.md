---
name: CF Worker missing BACKEND_URL
description: syrabitworker-prod had no BACKEND_URL binding set, causing all API proxy calls to return 503
---

## The Problem
`syrabitworker-prod` proxies all `api.syrabit.ai/*` and `syrabit.ai/api/*` traffic to Cloud Run.
The worker script uses `env.BACKEND_URL` to build the proxy target URL:
```js
const backendUrl = `${env.BACKEND_URL.replace(/\/$/, "")}${url.pathname}${url.search}`;
```
`BACKEND_URL` was never set as a worker binding → `env.BACKEND_URL` was `undefined` → `.replace()` threw → worker returned 503 for every request.

Also missing: `JWT_SECRET`, `EDGE_SHARED_SECRET`, `JWT_PUBLIC_KEY`, `GOOGLE_SA_KEY` were all empty secret bindings.

## Fix Applied
- Used CF API `PUT /secrets` to push each secret one at a time ✅
- Used CF API `PATCH /settings` with multipart form data (`Content-Type: multipart/form-data`, not `application/json`) to add `BACKEND_URL` as a `plain_text` binding ✅

**Why:**
`PATCH /settings` returns HTTP 415 if sent as `application/json`. Must use:
```bash
curl -X PATCH .../settings \
  --form "settings={...};type=application/json"
```

## Current Values
- `BACKEND_URL` = `https://syrabit-backend-bl6wu3psza-el.a.run.app`
- All secrets sourced from GCP SM lowercase names (`jwt-secret`, `edge-shared-secret`, `jwt-public-key`, `GOOGLE_APPLICATION_CREDENTIALS_JSON`)
- Documented in `apps/edge/wrangler.toml` comment
