# Staging release-gate discovery

**Checked:** 2026-09-28 (rechecked after PR update)
**Result:** Blocked — no staging release candidate or approved evaluation set was available. No audit score was changed.

## Scope and safety

- Cloudflare inventory used the existing API token for read-only `GET` requests and a metadata-only `SELECT` against the preview D1 catalog. No content or user rows were read; the token value was not printed or saved.
- GitHub checks were read-only.
- No application/API requests were sent to production, no Cloudflare resources were deployed or changed, and no production database was queried or written.
- No Assamese text was generated, translated, or rewritten.

## Environment discovery

- No staging-specific Replit variables or secrets were present in shared, development, or production environments.
- The GitHub repository has `prod` and `production` environments, but no `staging` environment or staging deployment. No staging-named repository variables or Actions secrets were found.
- The only Cloudflare Workers are `syrabit-api-prod` and `syrabitworker-prod`; no staging Worker, staging route, or staging Access app was found.
- `syrabit-content-preview` exists and has a content-oriented schema, but no staging Worker is bound to it and it has no auth/user table. It is not a complete staging database.
- Preview KV namespaces exist, but the API and edge Wrangler configs do not define a staging environment. Their `CONTENT_KV` preview binding points to the same namespace ID as production, so Wrangler preview is not an isolated staging substitute.
- No staging-named Vectorize index was found.
- The Cloudflare Pages project is `syrabitfrontend` with the production domain `syrabit.ai`. The preview deployments returned by the API were for the older `backlog-chat-auth-ratelimit-20260920` branch, at commits `e70b04011745` and `aef1fb9ee45f`; they are not the current workspace candidate and their API isolation was not established.
- A historical command example contains `staging-api.example.com`; it is a placeholder, not a configured target.
- The local frontend preview defaults to the production API proxy, so it was not used for staging validation.

## Candidate identity and test materials

- GitHub `main`: `9287139a8c6b`.
- PR #567 branch `pr/ahsec-library-language-availability-current-main`: `0b94ab8ea373`. The workspace `HEAD` is `eeef5a328303`; these are different candidate trees.
- PR #567 remains open and unmerged. Its frontend test fails because the candidate exposes Assamese notes while the existing rollout test expects them to remain gated. No production deployment was triggered.
- The required-status guard, Lighthouse, security, D1, edge, and frontend build checks passed. Backend Quality Checks were skipped; the staging-specific auth, browser, and chat-accuracy gates remain unverified.
- No approved chat gold set or expert-provided Assamese cases were found in the workspace. `scripts/accuracy-report.js` targets `syrabit_prod`; it was not run.
- No staging-only disposable user or staff credentials were configured. Existing test artifacts were not treated as staging credentials or reused.

## Gates not run

Staging authentication limits (including limit responses and store-unavailable fail-closed behavior), the document-marker and profile-selector browser flows, and the rubric’s staging chat-accuracy checks remain unverified. Local automated checks are not staging evidence.

The task rubric says to retain **33/100** until required gates have evidence. `AUDIT_INDEX.md` also contains an older **8.2/10** score. Both remain unchanged pending a verified candidate, approved cases, and completed gates.