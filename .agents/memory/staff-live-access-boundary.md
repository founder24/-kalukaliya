---
name: Live staff Access boundary
description: Cloudflare Access currently protects both the staff Pages path and admin API path; authenticated browser verification needs both staff and Access credentials.
---

The live Cloudflare Access application covers `syrabit.ai/staff*` and `api.syrabit.ai/api/v1/admin*`. Unauthenticated admin API requests therefore receive the Access sign-in HTML response before the Worker, while `/api/v1/staff/*` reaches the Worker and returns its JSON auth response. The protected admin API also returns a Cloudflare 403 to a browser CORS preflight with no CORS headers.

**Why:** A browser verifier that only supplies the application bearer token cannot prove the staff panel works through production; it also needs the Access session or service-token headers, and cross-origin admin requests must retain that boundary. With preflight blocked, browser calls from the Pages origin cannot reach the admin API even when application auth is valid.

**How to apply:** Treat authenticated staff browser checks as blocked until both the staff fixture credentials and Cloudflare Access credentials are available through the secret flow. Before calling the panel live, make the admin API CORS preflightable from `https://syrabit.ai` (or proxy those calls same-origin). Do not interpret a public 401 from `/api/v1/staff/*` or an Access HTML response from `/api/v1/admin/*` as a completed staff-panel test.