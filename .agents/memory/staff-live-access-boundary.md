---
name: Live staff Access boundary
description: Cloudflare Access currently protects both the staff Pages path and admin API path; authenticated browser verification needs both staff and Access credentials.
---

The live Cloudflare Access application covers `syrabit.ai/staff*` and `api.syrabit.ai/api/v1/admin*`. Unauthenticated admin API requests therefore receive the Access sign-in HTML response before the Worker, while `/api/v1/staff/*` reaches the Worker and returns its JSON auth response. The current admin preflight probe returns 200 with CORS headers, but an unauthenticated GET still receives the Access sign-in redirect.

**Why:** A browser verifier that only supplies the application bearer token cannot prove the staff panel works through production; it also needs the Access session or service-token headers. The portal now uses a same-origin Pages API proxy for browser calls, while the API origin remains protected and receives the application credentials plus Access context.

**How to apply:** Keep `syrabit.ai/api/v1/admin*` inside the same Access application as `syrabit.ai/staff*` so the Pages proxy receives a valid Access assertion. Treat authenticated staff browser checks as unverified until both staff fixture credentials and Cloudflare Access credentials are available. Do not interpret a public 401 from `/api/v1/staff/*` or an Access HTML response from `/api/v1/admin/*` as a completed staff-panel test.