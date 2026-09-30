# Staging release-gate discovery and setup

**Checked:** 2026-09-28 (rechecked after PR update)
**Result:** Isolated staging resources and local gates are prepared. Live staging checks are blocked on the staging access secret; no Worker has been deployed and no audit score was changed.

## Scope and safety

- Created isolated staging D1, KV, R2, and Vectorize resources; applied the staging D1 migrations through `0035_auth_rate_limits.sql`.
- Added four English-only synthetic hierarchy rows in staging D1 for the profile-selector browser test. No chapter, note, or Assamese content was added.
- No production authentication, mutation, chat-generation, or deployment request was intentionally made. A screenshot of the local main-app preview was captured; its Vite proxy defaults to the production API, and `/` redirects to `/library`, which fetches public library data. One or more read-only production API/database reads may therefore have occurred. Vite/browser logs do not provide request-level confirmation, so the earlier claim that no production API requests occurred is unverified. No further main-preview checks were made after identifying this risk. Cloudflare secret values were not printed or saved in project files.
- GitHub checks were read-only.
- No Assamese text was generated, translated, or rewritten.
- The staging Worker access token is not present in Replit Secrets. Replit’s supported secret flow requires the user to enter a value; the Agent cannot generate and write a Replit Secret programmatically. The public Worker remains gated and no staging Worker deployment has been attempted.

## Environment discovery

- The Replit secret `STAGING_ACCESS_TOKEN` is absent. It is required by both the staging Worker and the local Vite staging proxy.
- The GitHub repository has `prod` and `production` environments, but no `staging` environment or staging deployment. No staging-named repository variables or Actions secrets were found.
- Created Cloudflare staging resources: D1 `syrabit-db-staging`; KV `syrabit-content-staging`, `syrabit-rate-limit-staging`, and `syrabit-isr-cache-staging`; R2 `syrabit-assets-staging`; Vectorize `syrabit-rag-staging` (1024 dimensions, cosine). Completion of the six requested Vectorize metadata indexes remains unverified.
- Added isolated Wrangler environments for private API Worker `syrabit-api-staging` and gated public edge Worker `syrabitworker-staging`. The API service binding and stateful bindings target staging resources; staging cron, web search, and referral runtime are disabled.
- The edge gate returns 503 when its secret is missing and 401 when the header is absent or incorrect. CORS preflight is allowed without forwarding; the gate header is removed before the API service-binding hop. Vite adds the token server-side only for an exact `STAGING_ACCESS_HOST` match ending in `.workers.dev`; the helper does not enforce HTTPS or require the staging Worker name, so it can forward the token to a misconfigured production workers.dev host.
- `syrabit-content-preview` exists and has a content-oriented schema, but no staging Worker is bound to it and it has no auth/user table. It is not a complete staging database.
- The Cloudflare Pages project is `syrabitfrontend` with the production domain `syrabit.ai`. The preview deployments returned by the API were for the older `backlog-chat-auth-ratelimit-20260920` branch, at commits `e70b04011745` and `aef1fb9ee45f`; they are not the current workspace candidate and their API isolation was not established.
- The main frontend workflow remains unchanged and uses its production proxy by default. The staging Playwright spec does not enforce a staging `BASE_URL` or Vite proxy target, so running it with defaults can route signup/profile requests to production. Do not run it until staging-only target enforcement is added.

## Candidate identity and test materials

- GitHub `main`: `9287139a8c6b`.
- PR #567 branch `pr/ahsec-library-language-availability-current-main`: `0b94ab8ea373`. The workspace `HEAD` is `eeef5a328303`; these are different candidate trees.
- PR #567 remains open and unmerged. Its frontend test fails because the candidate exposes Assamese notes while the existing rollout test expects them to remain gated. No production deployment was triggered.
- The required-status guard, Lighthouse, security, D1, edge, and frontend build checks passed. Backend Quality Checks were skipped.
- No approved chat gold set or expert-provided Assamese cases were found in the workspace. `scripts/accuracy-report.js` targets `syrabit_prod`; it was not run.
- No staging-only test account has been created yet. The browser test is configured to create a disposable student through the staging proxy, save the synthetic English profile selectors, reload them, and check both `has_document=1` and the legacy `document_id` marker without sending a chat-generation request.
- Local verification passed: frontend `validate`; edge unit and runtime suites (107 tests total); staging-gate tests included. The staging Playwright test parses and is listed, but has not run against a deployed Worker.

## Gates not run

Staging auth/rate-limit behavior, including limit responses and store-unavailable fail-closed behavior, has not been tested live. The document-marker and profile-selector browser flows have not run against staging. The rubric’s staging chat-accuracy checks remain blocked until expert-approved evaluation cases are supplied. Local automated checks are not staging evidence.

The task rubric says to retain **33/100** until required gates have evidence. `AUDIT_INDEX.md` also contains an older **8.2/10** score. Both remain unchanged pending a verified candidate, approved cases, and completed gates.