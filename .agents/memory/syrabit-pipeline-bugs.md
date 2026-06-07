---
name: Syrabit chat+auth pipeline bugs
description: Fixed bugs in analytics 404s, conversation_id mismatch, logout null-token, and admin auth
---

## Fixed Bugs

### Admin Auth (June 2026)
**Edge worker PUBLIC_PATHS must cover all `/api/v1/admin/` routes, not just login/logout.**
- Why: Admin uses httponly-cookie session auth, NOT Bearer JWT. The JWT middleware
  blocked /verify, /dashboard, etc. for requests with bad/missing tokens.
- Fix: replaced two explicit entries with `/api/v1/admin/` prefix in jwt.ts PUBLIC_PATHS.

**Edge worker injects GCP OIDC token as Authorization on every proxied request.**
- The OIDC token (for Cloud Run IAM) overrides any user-supplied Bearer token.
- Admin `_validate_admin_session` Bearer fallback would receive the GCP token,
  fail to decode it as HS256, and return "Invalid or expired token".
- Fix: return "No admin session" on InvalidTokenError in that fallback branch.

**Cold-start 503 showed as "Invalid credentials" in admin login UI.**
- GCP infra 503 HTML → edge worker converts to `{"error":"Backend service unavailable"}`
  (no `detail` field) → frontend only read `data.detail` → fell through to "Invalid credentials".
- Fix: read `data.error` as well; return "Service temporarily unavailable — please try again"
  for any 5xx status.

### Analytics (earlier)
Fixed `/auth/session-ping` → `/analytics/session-ping` in smoke-test.yml.

### Conversation ID mismatch (earlier)
conversation_id vs session_id key mismatch between frontend and backend.

### Logout null token (earlier)
Frontend sent null token on logout; backend rejected it.
