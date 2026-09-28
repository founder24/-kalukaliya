# Staging release-gate discovery

**Checked:** 2026-09-28  
**Result:** Blocked — no staging release candidate or approved evaluation set was available. No audit score was changed.

## Scope and safety

- Cloudflare checks used the existing API token only for read-only `GET` requests. The token value was not printed or saved.
- GitHub checks were read-only.
- No application/API requests were sent to production, no resources were deployed or changed, and no production database was queried or written.
- No Assamese text was generated, translated, or rewritten.

## Environment discovery

- No staging-specific Replit variables or secrets were present in shared, development, or production environments.
- The GitHub repository has `prod` and `production` environments, but no `staging` environment or staging deployment. No staging-named repository variables or Actions secrets were found.
- The only Cloudflare Workers are `syrabit-api-prod` and `syrabitworker-prod`; no staging Worker, staging route, or staging Access app was found.
- Cloudflare has a D1 database named `syrabit-content-preview` with 9 tables. It is only a candidate: no staging Worker or route was found to establish that it is bound to an isolated staging application.
- No staging-named KV namespace or Vectorize index was found.
- The Cloudflare Pages project is `syrabitfrontend` with the production domain `syrabit.ai`. The preview deployments returned by the API were for the older `backlog-chat-auth-ratelimit-20260920` branch, at commits `e70b04011745` and `aef1fb9ee45f`; they are not the current workspace candidate and their API isolation was not established.
- A historical command example contains `staging-api.example.com`; it is a placeholder, not a configured target.
- The local frontend preview defaults to the production API proxy, so it was not used for staging validation.

## Candidate identity and test materials

- GitHub `main`: `9287139a8c6b`.
- Remote PR branch `pr/ahsec-library-language-availability-current-main`: `d3d89484c793`.
- Current workspace branch `pr/ahsec-library-language-availability-current-main`: `d921183e39c2`.
- These SHAs do not identify one tested release candidate. The GitHub workflow-directory API returned 403; no workflow contents were obtained through that API.
- No approved chat gold set or expert-provided Assamese cases were found in the workspace. `scripts/accuracy-report.js` targets `syrabit_prod`; it was not run.
- No staging-only disposable user or staff credentials were configured. Existing test artifacts were not treated as staging credentials or reused.

## Gates not run

Staging authentication limits (including limit responses and store-unavailable fail-closed behavior), the document-marker and profile-selector browser flows, and the rubric’s staging chat-accuracy checks remain unverified. Local automated checks are not staging evidence.

The task rubric says to retain **33/100** until required gates have evidence. `AUDIT_INDEX.md` also contains an older **8.2/10** score. Both remain unchanged pending a verified candidate, approved cases, and completed gates.