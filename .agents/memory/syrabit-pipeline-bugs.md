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

## CF Worker JWT forwarding — logout 500 (fixed 2026-06-07)
The CF Worker replaces `Authorization` with its own Cloud Run OIDC identity token and puts the original user JWT in `X-User-JWT`. Any endpoint that reads `credentials.credentials` (HTTPBearer) will get the OIDC token, not the user JWT. Only `get_current_user()` resolved this correctly. `logout()` was decoded the OIDC token as a user JWT → always 500 on CF-routed requests.
**Fix**: Any endpoint that needs the raw user token must resolve: `X-User-JWT` first, fall back to `credentials.credentials`.

## Admin session replay after logout (fixed 2026-06-07)
`admin_logout()` only called `delete_cookie()`, which is a browser instruction. The admin JWT stayed cryptographically valid for 8 hours. Clients holding a copy of the cookie value could replay it. `_validate_admin_session()` had no blacklist check.
**Fix**: `admin_logout()` writes `blacklisted_admin_token:<sha256>` to Redis with remaining TTL. `_validate_admin_session()` checks blacklist before accepting any session. Fails open if Redis down (8h natural expiry, no lockout).
Key name pattern: `blacklisted_admin_token:<sha256_of_jwt>` (vs user: `blacklisted_token:<sha256_of_jwt>`).

## TTS 502 — GEMINI_API_KEY wrong API scope (fixed 2026-06-07)
vertex_client.text_to_speech() used GEMINI_API_KEY for texttospeech.googleapis.com.
GEMINI_API_KEY is scoped to generativelanguage.googleapis.com only.
Cloud TTS requires OAuth2 (service account). Fix: removed _use_genai_api branch
from TTS entirely — always uses _get_access_token() (OAuth2).
Rule: any Google API other than generativelanguage.googleapis.com needs OAuth2, not GEMINI_API_KEY.

## Non-streaming generate() ReadTimeout not retried (fixed 2026-06-07)
httpx client had 10s read timeout. Gemini 2.5-flash non-streaming can exceed 10s.
ReadTimeout was caught by `except Exception` not the retry loop (which only checked HTTPStatusError).
Fix: explicit httpx.ReadTimeout branch in retry loop + increased read timeout to 30s.
