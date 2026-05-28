# Full-Stack Functionality Audit Report

**Project:** Syrabit AI - Educational Assistant for Assamese Students  
**Date:** 2025-01-15  
**Auditor:** Automated Code Analysis  
**Scope:** Full-stack monorepo (Backend, Frontend, Edge, Infrastructure)

---

## Executive Summary

This audit covers the entire Syrabit AI monorepo, evaluating security, correctness, reliability, and deployment readiness across 10 critical areas. The codebase demonstrates strong architecture with a well-designed 9-pillar system (Cloudflare Edge, Azure Container Apps, Azure Cognitive Search, MongoDB Atlas, Upstash Redis, Vertex AI, Sarvam AI, Razorpay, Resend), but contains several high-severity issues that must be addressed before production deployment.

### Overall Risk Rating: **HIGH**

### Findings Summary by Severity

| Severity | Count | Description |
|----------|-------|-------------|
| CRITICAL | 1 | JWT algorithm mismatch between Edge and Backend |
| HIGH | 5 | Missing backend routes, CSP blocking production features, edge secret bypass, config fallback to localhost |
| MEDIUM | 11 | Redis inconsistency, CSRF bypass, payment state gaps, race conditions |
| LOW | 12 | Dead code, estimation inaccuracies, minor deployment issues |
| INFO | 9 | Well-designed patterns, correct implementations noted |
| **TOTAL** | **38** | |

### Key Risks

1. **JWT Algorithm Mismatch (CRITICAL):** Edge worker only supports HS256 while backend supports RS256 for production -- this will break all authenticated requests if RS256 is used.
2. **Frontend-Backend Contract Breach (HIGH):** 6+ frontend API calls target non-existent backend endpoints, resulting in guaranteed 404 errors.
3. **CSP Blocks Third-Party SDKs (HIGH):** Content Security Policy blocks PostHog, Sentry, and Turnstile from loading, breaking analytics and bot protection.
4. **Production Config Fallback to Localhost (HIGH):** If Cloudflare secrets fail to load, all API traffic routes to `http://localhost:8000`.

---

## Table of Contents

1. [Frontend-Backend Integration](#1-frontend-backend-integration)
2. [API Endpoint Correctness](#2-api-endpoint-correctness)
3. [Error Handling](#3-error-handling)
4. [Security Vulnerabilities](#4-security-vulnerabilities)
5. [Configuration Issues](#5-configuration-issues)
6. [AI Service Integration](#6-ai-service-integration)
7. [Database and Caching](#7-database-and-caching)
8. [Payment Integration](#8-payment-integration)
9. [Deployment and Infrastructure](#9-deployment-and-infrastructure)
10. [Static Analysis Findings](#10-static-analysis-findings)
11. [Summary Table](#summary-table)
12. [Recommendations Priority Matrix](#recommendations-priority-matrix)

---

## 1. Frontend-Backend Integration

### API-001: Missing Backend Routes for Conversation CRUD

| Property | Value |
|----------|-------|
| **Severity** | HIGH |
| **Affected Files** | `apps/frontend/src/utils/api.jsx`, `apps/backend/app/api/v1/chat.py` |
| **Impact** | Frontend conversation management features are non-functional |

**Description:**  
The frontend defines multiple API calls for conversation management that have no corresponding backend endpoints. These calls will return 404 in production, breaking the user experience for conversation history features.

**Frontend calls with no backend handler:**
```javascript
// apps/frontend/src/utils/api.jsx
export const getConversation = (id) => api.get(`/api/v1/conversations/${id}`);
export const deleteConversation = (id) => api.delete(`/api/v1/conversations/${id}`);
export const updateConversation = (id, data) => api.patch(`/api/v1/conversations/${id}`, data);
export const getAnonConversations = () => api.get('/api/v1/conversations/anon');
export const getAnonConversation = (id) => api.get(`/api/v1/conversations/anon/${id}`);
export const deleteAnonConversation = (id) => api.delete(`/api/v1/conversations/anon/${id}`);
```

**Backend only provides:**
```python
# apps/backend/app/api/v1/chat.py
# GET /api/v1/chat/conversations -> alias for get_chat_history
# GET /api/v1/chat/{session_id}/messages -> get messages for a session
```

**Impact:** Users cannot view individual conversations, delete conversations, rename conversations, or manage anonymous chat sessions. These are core UX features.

**Remediation:**  
Implement the missing CRUD endpoints in the backend:
- `GET /api/v1/conversations/{id}` - Retrieve single conversation
- `DELETE /api/v1/conversations/{id}` - Delete a conversation
- `PATCH /api/v1/conversations/{id}` - Update conversation metadata (title, etc.)
- `GET /api/v1/conversations/anon` - List anonymous conversations (by IP/session)
- `GET /api/v1/conversations/anon/{id}` - Retrieve single anonymous conversation
- `DELETE /api/v1/conversations/anon/{id}` - Delete anonymous conversation

---

### API-002: Frontend API Base URL Architecture

| Property | Value |
|----------|-------|
| **Severity** | INFO |
| **Affected Files** | `apps/frontend/src/utils/api.jsx` |
| **Impact** | N/A - well-designed |

**Description:**  
The frontend correctly uses multiple API base URLs for different service tiers:
- `VITE_BACKEND_URL` for direct backend access (auth, chat)
- `VITE_WORKER_API_URL` for edge-proxied content routes
- Fallback logic when worker URL is not configured

This is a solid pattern for the edge proxy architecture, allowing gradual migration of routes through the Cloudflare edge.

---

### API-003: Token Storage in sessionStorage

| Property | Value |
|----------|-------|
| **Severity** | MEDIUM |
| **Affected Files** | `apps/frontend/src/hooks/useTokenManager.js` |
| **Impact** | Tokens accessible via XSS attacks |

**Description:**  
JWT tokens are stored in `sessionStorage` with an in-memory fallback. While `sessionStorage` is better than `localStorage` (cleared on tab close), tokens remain accessible to any JavaScript running on the page.

```javascript
// apps/frontend/src/hooks/useTokenManager.js
let _inMemoryToken = null;

export const setAuthToken = (token) => {
  _inMemoryToken = token;
  sessionStorage.setItem('auth_token', token);
};
```

**Impact:** If an XSS vulnerability exists anywhere in the application (including third-party scripts like PostHog or Sentry), attackers can exfiltrate JWT tokens.

**Remediation:**  
Consider using httpOnly cookies for token storage (requires backend CORS and cookie configuration changes). If sessionStorage must be used, ensure strict CSP and input sanitization across all routes.

---

### API-004: Streaming Response Contract Verified

| Property | Value |
|----------|-------|
| **Severity** | INFO |
| **Affected Files** | `apps/frontend/src/utils/api.jsx`, `apps/backend/app/api/v1/chat.py`, `apps/edge/src/routes/api-proxy.ts` |
| **Impact** | N/A - correct implementation |

**Description:**  
The SSE streaming contract is correctly implemented across all three tiers:
- Frontend expects: `data: {"text": "...", "done": false}\n\n`
- Backend sends exactly this format with `done: true` on final event including metadata
- Edge proxy passes streams through without buffering

---

## 2. API Endpoint Correctness

### ENDPOINT-001: Content-Length Deletion in Proxy

| Property | Value |
|----------|-------|
| **Severity** | LOW |
| **Affected Files** | `apps/edge/src/routes/api-proxy.ts` |
| **Impact** | Suboptimal but functional HTTP forwarding |

**Description:**  
The edge proxy removes `Content-Length` from ALL forwarded requests, including POST/PUT requests with bodies. This forces chunked transfer encoding on the upstream connection.

```typescript
// apps/edge/src/routes/api-proxy.ts
headers.delete('Content-Length');
```

**Impact:** FastAPI handles chunked encoding correctly, so this is not a functional issue. However, it adds unnecessary overhead for body parsing and prevents the backend from pre-allocating buffers.

**Remediation:**  
Only delete Content-Length for responses where the proxy modifies the body, or for requests where streaming is required. For standard forwarded requests, preserve the original Content-Length.

---

### ENDPOINT-002: Health Endpoint Duplication

| Property | Value |
|----------|-------|
| **Severity** | INFO |
| **Affected Files** | `apps/backend/app/main.py`, `apps/edge/src/index.ts` |
| **Impact** | Multiple code paths for same functionality |

**Description:**  
Health checks are available at multiple paths with different behaviors:
- `/health` - Backend direct, also handled by Edge itself
- `/api/v1/health` - Backend via API prefix
- `/api/health` - Legacy redirect (301) to `/health`
- `/health/deep` - Deep health check (Edge proxies to backend)

The edge handles `/health` locally (returns 200 immediately) while proxying `/health/deep` to the backend for database connectivity checks. This is architecturally sound for reducing latency on basic health probes.

---

### ENDPOINT-003: Admin Router Prefix Collision Risk

| Property | Value |
|----------|-------|
| **Severity** | MEDIUM |
| **Affected Files** | `apps/backend/app/main.py`, `apps/backend/app/api/v1/admin.py`, `apps/backend/app/api/v1/admin_dashboard.py`, `apps/backend/app/api/v1/admin_users.py` |
| **Impact** | Potential silent route shadowing |

**Description:**  
Multiple admin routers are registered with the same prefix `/api/v1/admin`. In FastAPI, if two routers define the same HTTP method + path combination, the first registered router wins silently with no warning.

```python
# apps/backend/app/main.py (conceptual)
app.include_router(admin.router, prefix="/api/v1/admin")
app.include_router(admin_dashboard.router, prefix="/api/v1/admin")
app.include_router(admin_users.router, prefix="/api/v1/admin")
```

**Impact:** If any two admin modules define endpoints at the same sub-path (e.g., `/stats`), one will be silently unreachable. This creates maintenance risk as the admin panel grows.

**Remediation:**  
Use unique prefixes for each admin module:
- `/api/v1/admin/dashboard` for dashboard stats
- `/api/v1/admin/users` for user management
- `/api/v1/admin/system` for system operations

Or consolidate into a single admin router file with clear path separation.

---

## 3. Error Handling

### ERR-001: Chat Endpoint Comprehensive Error Handling

| Property | Value |
|----------|-------|
| **Severity** | INFO |
| **Affected Files** | `apps/backend/app/api/v1/chat.py` |
| **Impact** | N/A - well-implemented |

**Description:**  
The chat endpoint demonstrates best-practice error handling with specific exception mapping:

```python
# apps/backend/app/api/v1/chat.py
try:
    response = await chat_service.process_message(...)
except HTTPException:
    raise  # passthrough
except RuntimeError as e:
    if "timeout" in str(e).lower():
        raise HTTPException(status_code=504, detail="AI service timeout")
    elif "unavailable" in str(e).lower():
        raise HTTPException(status_code=503, detail="AI service unavailable")
    raise HTTPException(status_code=502, detail="AI service error")
except asyncio.TimeoutError:
    raise HTTPException(status_code=504, detail="Request timeout")
except httpx.HTTPStatusError:
    raise HTTPException(status_code=502, detail="Upstream service error")
except ValueError as e:
    raise HTTPException(status_code=400, detail=str(e))
except Exception:
    raise HTTPException(status_code=500, detail="Internal server error")
```

Fire-and-forget background tasks use `_log_task_exception` callbacks to ensure failures are logged without blocking the response.

---

### ERR-002: Redis Unavailability Inconsistency

| Property | Value |
|----------|-------|
| **Severity** | MEDIUM |
| **Affected Files** | `apps/backend/app/api/v1/auth.py`, `apps/backend/app/api/deps/rate_limit.py`, `apps/backend/app/api/webhooks/razorpay.py` |
| **Impact** | Inconsistent degradation behavior when Redis fails |

**Description:**  
The application handles Redis failures differently depending on the module:

| Module | Redis Failure Behavior | Pattern |
|--------|----------------------|---------|
| `auth.py` - `get_current_user()` | Returns HTTP 503 | Fail-closed (secure) |
| `rate_limit.py` - `check_rate_limit()` | Allows request through | Fail-open (permissive) |
| `razorpay.py` - webhook idempotency | Returns HTTP 503 | Fail-closed (secure) |

**Impact:** When Redis goes down:
- Authentication breaks completely (503 for all authenticated requests)
- Rate limiting stops entirely (all requests pass through)
- Payment webhooks are rejected (preventing duplicate processing)

The combination means: during a Redis outage, unauthenticated endpoints lose all rate protection while authenticated endpoints become completely unavailable. This creates an asymmetric failure mode that could be exploited.

**Remediation:**  
Standardize on a consistent failure strategy:
1. Rate limiting should fail-closed with a generous fallback (allow N requests per IP from in-memory counter)
2. Auth should have a short-lived token verification cache to survive brief Redis outages
3. Document the expected behavior during Redis failures in runbooks

---

### ERR-003: Password Reset Token Redis Failure Silently Passes

| Property | Value |
|----------|-------|
| **Severity** | MEDIUM |
| **Affected Files** | `apps/backend/app/api/v1/auth.py` |
| **Impact** | Reset tokens can be reused if Redis is unavailable |

**Description:**  
The password reset flow uses Redis for single-use token tracking. If Redis is unavailable during token validation, the check silently passes:

```python
# apps/backend/app/api/v1/auth.py - reset_password()
try:
    used = await redis.get(f"reset_used:{token_hash}")
    if used:
        raise HTTPException(status_code=400, detail="Token already used")
    await redis.set(f"reset_used:{token_hash}", "1", ex=3600)
except Exception:
    pass  # defense in depth, token still has 1h expiry
```

**Impact:** If Redis is unavailable, an attacker who intercepts a reset token can use it multiple times within the 1-hour JWT expiry window. The bare `pass` on exception means this happens completely silently with no logging.

**Remediation:**  
1. At minimum, log the Redis failure at WARNING level
2. Consider failing closed: if Redis is unavailable, reject the reset attempt with "Service temporarily unavailable, please try again"
3. The JWT-based expiry provides a time-bound safety net, but multi-use weakens the security model

---

## 4. Security Vulnerabilities

### SEC-001: JWT Algorithm Mismatch Between Edge and Backend

| Property | Value |
|----------|-------|
| **Severity** | CRITICAL |
| **Affected Files** | `apps/edge/src/middleware/jwt.ts`, `apps/backend/app/api/v1/auth.py` |
| **Impact** | Complete authentication failure in production if RS256 is used |

**Description:**  
The Edge worker and Backend support different JWT signing algorithms, creating an incompatibility that would break all authenticated requests in production:

**Edge worker (HS256 ONLY):**
```typescript
// apps/edge/src/middleware/jwt.ts
const key = await crypto.subtle.importKey(
  'raw',
  encoder.encode(secret),
  { name: 'HMAC', hash: 'SHA-256' },
  false,
  ['verify']
);
// Hardcoded to HMAC-SHA256 verification
```

**Backend (supports HS256 AND RS256):**
```python
# apps/backend/app/api/v1/auth.py
def _get_signing_key():
    if settings.JWT_PRIVATE_KEY:  # RS256 in production
        return settings.JWT_PRIVATE_KEY, "RS256"
    return settings.JWT_SECRET, "HS256"  # HS256 in dev
```

**Impact:** If production is configured with `JWT_PRIVATE_KEY` (RS256), the backend signs tokens with RS256 but the Edge worker attempts HS256 verification. This means:
- Every request through the Edge with a JWT will fail verification
- The Edge will reject all authenticated traffic
- Users cannot access any protected resource through the CDN

**Remediation:**  
Option A: Add RS256 support to the Edge worker using `crypto.subtle.importKey` with RSASSA-PKCS1-v1_5 algorithm.  
Option B: Ensure production always uses HS256 (simpler but less secure for distributed systems).  
Option C: Make the Edge algorithm configurable via environment variable matching the backend's signing algorithm.

---

### SEC-002: Edge Shared Secret Trust Bypass

| Property | Value |
|----------|-------|
| **Severity** | HIGH |
| **Affected Files** | `apps/backend/app/api/v1/auth.py`, `apps/edge/src/routes/api-proxy.ts` |
| **Impact** | User impersonation if backend is directly accessible |

**Description:**  
The backend trusts user identity based on a shared secret header:

```python
# apps/backend/app/api/v1/auth.py - get_current_user()
edge_secret = request.headers.get("X-Edge-Secret")
if edge_secret == settings.EDGE_SHARED_SECRET:
    user_id = request.headers.get("X-User-ID")
    # Trust the user_id without JWT verification
```

The Edge injects these headers for ALL `/api/` requests:
```typescript
// apps/edge/src/routes/api-proxy.ts
if (env.EDGE_SHARED_SECRET) {
  headers.set('X-Edge-Secret', env.EDGE_SHARED_SECRET);
  headers.set('X-User-ID', userId || '');
}
```

**Impact:**  
- If an attacker can bypass the Edge (direct backend access via Azure Container Apps URL), they can forge both headers
- The shared secret is a single static value with no rotation mechanism
- No per-request HMAC or timestamp prevents replay
- Security depends entirely on Azure network isolation

**Remediation:**  
1. Ensure Azure Container Apps ingress is configured to only accept traffic from Cloudflare IP ranges
2. Add a per-request HMAC: `X-Edge-Signature: HMAC(secret, timestamp + user_id + path)`
3. Include `X-Edge-Timestamp` and reject requests older than 30 seconds
4. Implement secret rotation mechanism (dual-secret support during rollover)

---

### SEC-003: Default JWT Secret in Non-Production

| Property | Value |
|----------|-------|
| **Severity** | LOW |
| **Affected Files** | `apps/backend/app/config.py` |
| **Impact** | Token forgery if APP_ENV misconfigured |

**Description:**  
A default JWT secret is hardcoded for development:

```python
# apps/backend/app/config.py
JWT_SECRET: str = "dev-only-secret-not-for-production-use-32chars"
```

A production validator exists that rejects known placeholder secrets. However, if `APP_ENV` is set to anything other than `"production"` (e.g., `"staging"`, `"prod"`, or left unset), the default applies.

**Impact:** Low risk due to the validator, but any environment not explicitly named "production" would use the default secret, allowing token forgery.

**Remediation:**  
Expand the validator to reject defaults for any non-development environment (i.e., reject if `APP_ENV not in ["development", "test"]` and secret matches the default).

---

### SEC-004: CSRF Origin Check Bypass

| Property | Value |
|----------|-------|
| **Severity** | MEDIUM |
| **Affected Files** | `apps/backend/app/main.py` |
| **Impact** | CSRF protection ineffective for non-browser clients |

**Description:**  
The CSRF origin validation is skipped entirely when no `Origin` header is present:

```python
# apps/backend/app/main.py - unified middleware
if origin and origin not in allowed_origins:
    return JSONResponse(status_code=403, content={"detail": "Origin not allowed"})
```

**Impact:**  
- API clients (curl, Postman, automated scripts) never send Origin headers
- Combined with cookie-based auth (`withCredentials: true` in frontend), an attacker's server-side script can make authenticated requests without triggering CSRF protection
- This is common in API-first architectures but creates risk when cookies are the auth mechanism

**Remediation:**  
For cookie-based auth flows, require either:
1. A custom header (e.g., `X-Requested-With`) that CORS preflight would block cross-origin
2. SameSite=Strict on auth cookies
3. Double-submit cookie pattern

---

### SEC-005: Turnstile Token Not Validated for All Sensitive Endpoints

| Property | Value |
|----------|-------|
| **Severity** | MEDIUM |
| **Affected Files** | `apps/edge/src/middleware/turnstile.ts`, `apps/edge/src/index.ts` |
| **Impact** | Automated brute force possible on password reset |

**Description:**  
Cloudflare Turnstile bot protection is selectively applied:

**Protected endpoints:**
- POST `/api/v1/auth/signup`
- POST `/api/v1/auth/login`
- POST `/api/v1/auth/forgot-password`
- POST `/api/v1/chat` (non-feedback)

**Unprotected sensitive endpoints:**
- POST `/api/v1/auth/reset-password` - Allows automated brute-forcing of reset tokens
- POST `/api/v1/auth/refresh` - Token refresh without bot check
- PUT `/api/v1/users/me` - Profile modification

**Impact:** An attacker can write a script to attempt password resets without solving a Turnstile challenge, potentially brute-forcing short or predictable reset tokens.

**Remediation:**  
Add Turnstile validation to `/api/v1/auth/reset-password`. Token refresh and profile updates are lower risk since they require valid existing tokens.

---

### SEC-006: Content Security Policy Blocks Production Features

| Property | Value |
|----------|-------|
| **Severity** | HIGH |
| **Affected Files** | `apps/edge/src/middleware/security-headers.ts` (or equivalent in index.ts) |
| **Impact** | Third-party integrations broken in production |

**Description:**  
The Edge sets restrictive CSP headers on all proxied responses:

```
Content-Security-Policy: script-src 'self'
```

This blocks:
- **PostHog SDK** - `script-src` blocks loading `https://app.posthog.com/static/array.js`
- **Sentry SDK** - Cannot load error tracking script
- **Cloudflare Turnstile** - Widget JavaScript cannot execute
- **Inline scripts** - Any framework-injected scripts fail

Meanwhile, `connect-src` allows `https://app.posthog.com` for API calls, but the SDK script itself cannot load due to `script-src 'self'`.

**Impact:** Analytics, error tracking, and bot protection are all non-functional in production. The Turnstile widget specifically is critical for the security flow - if it cannot render, signup/login forms are broken.

**Remediation:**  
Update CSP to include required third-party sources:
```
script-src 'self' https://challenges.cloudflare.com https://static.cloudflareinsights.com https://app.posthog.com;
connect-src 'self' https://app.posthog.com https://*.sentry.io https://*.ingest.sentry.io;
```
Consider using nonces for inline scripts rather than `'unsafe-inline'`.

---

## 5. Configuration Issues

### CFG-001: Local Backend URL in wrangler.toml

| Property | Value |
|----------|-------|
| **Severity** | HIGH |
| **Affected Files** | `apps/edge/wrangler.toml` |
| **Impact** | Complete API outage if Cloudflare secret is unset |

**Description:**  
The wrangler configuration contains a localhost fallback for the backend URL:

```toml
# apps/edge/wrangler.toml
[vars]
AZURE_BACKEND_URL = "http://localhost:8000"

[env.production.vars]
ALLOWED_ORIGIN = "https://syrabit.com"
# NOTE: AZURE_BACKEND_URL is NOT overridden here - relies on secret
```

The `[env.production]` section does NOT override `AZURE_BACKEND_URL` in vars. It relies entirely on a Cloudflare secret being set. If the secret is deleted, expires, or fails to load, the worker falls back to the top-level var: `http://localhost:8000`.

**Impact:** All API traffic would be routed to nowhere, resulting in connection timeouts or refused connections for every user request. This is a single-point-of-failure for the entire application.

**Remediation:**  
1. Set `AZURE_BACKEND_URL` in `[env.production.vars]` to the actual Azure Container Apps URL
2. Add a startup check in the worker that validates the URL is not localhost in production
3. Add monitoring/alerting for 5xx responses from the proxy that would catch this quickly

---

### CFG-002: TypeScript Type vs Runtime Reality for EDGE_SHARED_SECRET

| Property | Value |
|----------|-------|
| **Severity** | MEDIUM |
| **Affected Files** | `apps/edge/src/env.d.ts`, `apps/edge/src/routes/api-proxy.ts` |
| **Impact** | Type safety gap, handled at runtime |

**Description:**  
The TypeScript declaration marks `EDGE_SHARED_SECRET` as required (non-optional string), but the runtime code defensively checks for its existence:

```typescript
// apps/edge/src/env.d.ts
interface Env {
  EDGE_SHARED_SECRET: string;  // Declared as required
}

// apps/edge/src/routes/api-proxy.ts
if (env.EDGE_SHARED_SECRET) {  // Runtime check for undefined
  headers.set('X-Edge-Secret', env.EDGE_SHARED_SECRET);
}
```

**Impact:** TypeScript will not flag code paths where `EDGE_SHARED_SECRET` might be undefined, giving false confidence. The runtime check handles it, but the type should match reality.

**Remediation:**  
Either:
- Make the type `EDGE_SHARED_SECRET?: string` (optional) to match runtime behavior
- Or remove the runtime check and fail fast if the secret is missing (throw on worker startup)

---

### CFG-003: Hardcoded Razorpay Plan ID

| Property | Value |
|----------|-------|
| **Severity** | LOW |
| **Affected Files** | `apps/backend/app/config.py` |
| **Impact** | Plan changes require environment variable update |

**Description:**  
```python
# apps/backend/app/config.py
RAZORPAY_PLAN_ID: str = "plan_pro_monthly"
```

The plan ID must match an actual plan created in the Razorpay dashboard. If the plan is changed or a new tier is added, this requires an environment variable update and redeployment.

**Impact:** Low - this is standard for payment configuration. However, if multi-tier pricing is ever needed, this single-value approach won't scale.

**Remediation:**  
Consider storing plan mappings in a configuration dictionary or database for future multi-tier support.

---

### CFG-004: 42 Environment Variables with Safe Defaults

| Property | Value |
|----------|-------|
| **Severity** | INFO |
| **Affected Files** | `apps/backend/app/config.py` |
| **Impact** | N/A - well-designed |

**Description:**  
All 42 environment variables have Optional typing with sensible defaults, allowing the application to start without any configuration. Features fail gracefully at call-time when their specific variables are missing (e.g., Razorpay calls fail if RAZORPAY_KEY is unset, but the rest of the app works).

This is a good pattern for developer experience and progressive deployment.

---

## 6. AI Service Integration

### AI-001: Correct AI Routing Verified

| Property | Value |
|----------|-------|
| **Severity** | INFO |
| **Affected Files** | `apps/backend/app/services/ai/router.py`, `apps/backend/app/services/ai/cloudflare_client.py`, `apps/backend/app/api/v1/chat.py` |
| **Impact** | N/A - correctly implemented per requirements |

**Description:**  
The AI routing correctly implements the project architecture:

```python
# apps/backend/app/services/ai/router.py
async def route_request(language: str, ...):
    if language == "en":
        return await vertex_client.generate(...)  # Vertex AI (Gemini)
    elif language == "as":
        return await sarvam_client.generate(...)  # Sarvam AI (OpenHathi)
    else:
        raise ValueError("Cloudflare Workers AI is not used for chat")
```

Cloudflare Workers AI (`cloudflare_client.py`) is ONLY imported in `chat.py` for:
- `/image` endpoint (OCR via Workers AI Vision)
- `/tts` endpoint (Text-to-Speech via Workers AI)

This matches the documented architecture: "Cloudflare Workers AI is ONLY for OCR/TTS."

---

### AI-002: Sarvam-to-Vertex Fallback

| Property | Value |
|----------|-------|
| **Severity** | INFO |
| **Affected Files** | `apps/backend/app/services/chat_service.py` |
| **Impact** | N/A - good resilience pattern |

**Description:**  
The chat service implements a graceful degradation path:

1. Assamese request -> Sarvam AI (primary)
2. If Sarvam fails -> Vertex AI (fallback, may respond in English)
3. If BOTH fail -> Dead letter stored in MongoDB for replay
4. Circuit breakers protect both services (5 failures -> open for 30-60 seconds)

This ensures students always get a response, even if not in their preferred language during service degradation.

---

### AI-003: Token Budget Character-Based Estimation

| Property | Value |
|----------|-------|
| **Severity** | LOW |
| **Affected Files** | `apps/backend/app/core/token_budget.py` |
| **Impact** | Slight over/under-estimation of context window usage |

**Description:**  
Token counting uses character-based heuristics rather than model-specific tokenizers:

```python
# apps/backend/app/core/token_budget.py
CHARS_PER_TOKEN = {
    "en": 4,   # English: ~4 characters per token
    "as": 2,   # Assamese: ~2 characters per token (Indic scripts)
}
MAX_CONTEXT_TOKENS = 3000  # Conservative budget
```

**Impact:** For English, this slightly overestimates (GPT-4 averages ~3.5 chars/token, Gemini varies). For Assamese with its complex script, 2 chars/token is reasonable but model-specific. The 3000-token budget is conservative enough that estimation errors rarely cause truncation issues.

**Remediation:**  
For higher accuracy (if context window optimization is needed), integrate the model-specific tokenizer (e.g., `tiktoken` for Gemini-compatible, Sarvam's tokenizer for OpenHathi). Not urgent given the conservative budget.

---

### AI-004: Vertex AI OAuth Token Async Safety

| Property | Value |
|----------|-------|
| **Severity** | LOW |
| **Affected Files** | `apps/backend/app/services/ai/vertex_client.py` |
| **Impact** | Theoretical only - not a practical concern |

**Description:**  
The Vertex AI client uses an `asyncio.Lock()` for OAuth token refresh:

```python
# apps/backend/app/services/ai/vertex_client.py
class VertexClient:
    def __init__(self):
        self._token_lock = asyncio.Lock()
    
    async def _ensure_token(self):
        async with self._token_lock:
            if self._token_expired():
                await self._refresh_token()
```

**Impact:** The lock is an instance variable on a singleton. If multiple event loops existed (e.g., in testing or unusual deployment), contention could occur. In production with FastAPI's single event loop, this is perfectly safe.

**Remediation:** None needed for production. For test environments, ensure the singleton is properly scoped to the test's event loop.

---

## 7. Database and Caching

### DB-001: MongoDB Indexes Well-Defined

| Property | Value |
|----------|-------|
| **Severity** | INFO |
| **Affected Files** | `apps/backend/app/db/mongo.py` |
| **Impact** | N/A - correctly implemented |

**Description:**  
MongoDB indexes are created on application startup covering all common query patterns:

| Collection | Index | Type |
|------------|-------|------|
| users | email | unique |
| users | subscription_id | sparse |
| users | language, created_at | standard |
| chats | user_id + updated_at | compound |
| chats | session_id | standard |
| dead_letters | timestamp | TTL (30 days) |
| dead_letters | user_id + timestamp | compound |
| dead_letters | status + timestamp | compound |
| content (boards) | slug | unique |
| content (classes) | board_id | standard |

This covers the primary access patterns for user lookup, chat history retrieval, and dead letter management.

---

### DB-002: Missing Negative Caching for Search Results

| Property | Value |
|----------|-------|
| **Severity** | MEDIUM |
| **Affected Files** | `apps/backend/app/services/search/azure_search.py` |
| **Impact** | Azure Search overload for repeated no-result queries |

**Description:**  
Search results are only cached when content is found:

```python
# apps/backend/app/services/search/azure_search.py
async def search(self, query: str, ...):
    context_chunks = await self._execute_search(query)
    if context_chunks:  # Only cache non-empty results
        await self._cache_results(cache_key, context_chunks)
    return context_chunks
```

**Impact:** If users repeatedly ask questions about topics not in the content database (e.g., subjects not yet uploaded), each query hits Azure Cognitive Search directly. Under load with many students asking similar questions, this could:
- Exhaust Azure Search query quota
- Increase latency for all search operations
- Increase Azure billing costs

**Remediation:**  
Cache empty results with a shorter TTL (e.g., 5 minutes vs 1 hour for valid results):
```python
if context_chunks:
    await self._cache_results(cache_key, context_chunks, ttl=3600)
else:
    await self._cache_results(cache_key, [], ttl=300)  # Negative cache
```

---

### DB-003: Chat History Pagination Loads Full Document

| Property | Value |
|----------|-------|
| **Severity** | LOW |
| **Affected Files** | `apps/backend/app/api/v1/chat.py` |
| **Impact** | Memory overhead for long conversations |

**Description:**  
Chat message pagination is performed in Python after loading the entire document:

```python
# apps/backend/app/api/v1/chat.py - get_chat_messages()
chat = await db.chats.find_one({"_id": chat_id})
messages = chat.messages[skip : skip + limit]  # In-memory slicing
```

**Impact:** For a chat with 500+ messages (power users over weeks), the entire message array is loaded into memory just to return a page of 20 messages. With many concurrent users, this increases memory pressure.

**Remediation:**  
Use MongoDB's `$slice` projection operator:
```python
chat = await db.chats.find_one(
    {"_id": chat_id},
    {"messages": {"$slice": [skip, limit]}}
)
```
This performs pagination at the database level, only transferring the requested messages over the wire.

---

### DB-004: Rate Limit Redis Key Lifetime

| Property | Value |
|----------|-------|
| **Severity** | LOW |
| **Affected Files** | `apps/backend/app/api/deps/rate_limit.py` |
| **Impact** | Theoretical key accumulation, mitigated by key naming |

**Description:**  
Rate limiting uses Redis keys with month-based naming:
```
rate:{user_id}:{YYYY-MM}
rate_anon:{ip}:{YYYY-MM}
```

The TTL is only set on first increment (`if current_count == 1`). If a crash occurs between `INCR` and `EXPIRE`, the key persists without TTL.

**Impact:** Due to month being part of the key name, stale keys from crashed processes naturally become irrelevant the next month. Upstash free tier may accumulate keys if many unique IPs are seen, but paid tier handles this at scale.

**Remediation:**  
Use Redis `INCREX` pattern or Lua script for atomic increment-with-expiry. Alternatively, run a periodic cleanup job for keys without TTL.

---

### DB-005: Race Condition in Rate Limiting

| Property | Value |
|----------|-------|
| **Severity** | MEDIUM |
| **Affected Files** | `apps/backend/app/api/deps/rate_limit.py` |
| **Impact** | Rate limits can be slightly exceeded under concurrent load |

**Description:**  
Rate limit check uses separate INCR and EXPIRE operations:

```python
# apps/backend/app/api/deps/rate_limit.py
current_count = await redis.incr(key)
if current_count == 1:
    await redis.expire(key, ttl)  # Only set TTL on first increment
if current_count > limit:
    raise HTTPException(status_code=429)
```

**Impact:** Under high concurrency, multiple requests can read the same count before any increments are committed, allowing the limit to be exceeded by the number of concurrent requests in that window. For the 100 msg/month free tier, this is negligible. For the rate_anon limits (lower), it could allow brief bursts.

**Remediation:**  
Use a Lua script or Redis pipeline for atomic check-and-increment:
```lua
local current = redis.call('INCR', KEYS[1])
if current == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return current
```

---

## 8. Payment Integration

### PAY-001: Razorpay Webhook Signature Verification

| Property | Value |
|----------|-------|
| **Severity** | INFO |
| **Affected Files** | `apps/backend/app/api/webhooks/razorpay.py` |
| **Impact** | N/A - correctly implemented |

**Description:**  
Webhook verification follows best practices:

```python
# apps/backend/app/api/webhooks/razorpay.py
expected_signature = hmac.new(
    key=settings.RAZORPAY_WEBHOOK_SECRET.encode(),
    msg=raw_body,  # Raw body, not parsed JSON
    digestmod=hashlib.sha256
).hexdigest()

if not hmac.compare_digest(expected_signature, received_signature):
    raise HTTPException(status_code=401)
```

Key security properties:
- Uses raw body for signature computation (not re-serialized JSON)
- Timing-safe comparison via `hmac.compare_digest()`
- Event deduplication via Redis (`webhook_processed:{event_id}` with 7-day TTL)

---

### PAY-002: Payment State Machine Incomplete

| Property | Value |
|----------|-------|
| **Severity** | MEDIUM |
| **Affected Files** | `apps/backend/app/api/webhooks/razorpay.py` |
| **Impact** | Subscription state can drift from payment provider state |

**Description:**  
The webhook handler only processes a subset of Razorpay subscription events:

| Event | Handled? | Impact if Missed |
|-------|----------|------------------|
| `subscription.charged` | Yes | N/A |
| `payment.failed` | Yes (logs only) | N/A |
| `subscription.cancelled` | Yes | N/A |
| `subscription.paused` | **NO** | User retains pro access while paused |
| `subscription.resumed` | **NO** | User not re-upgraded after resume |
| `payment.authorized` | **NO** | Pre-capture flow not supported |
| `subscription.pending` | **NO** | New sub waiting for payment not tracked |

**Impact:**  
- If a user pauses their subscription via Razorpay dashboard, the backend never downgrades them
- If a user resumes, the backend doesn't know to restore pro access
- The `payment.failed` handler only logs without notifying the user or triggering dunning logic

**Remediation:**  
1. Add handlers for `subscription.paused` (downgrade to free) and `subscription.resumed` (restore pro)
2. Implement dunning flow for `payment.failed`: notify user, retry count tracking, auto-cancel after N failures
3. Consider adding a reconciliation cron job that syncs subscription status with Razorpay API weekly

---

### PAY-003: Subscription Cancellation Idempotency

| Property | Value |
|----------|-------|
| **Severity** | LOW |
| **Affected Files** | `apps/backend/app/api/v1/subscription.py` |
| **Impact** | Poor UX on double-cancel, but no data corruption |

**Description:**  
The cancel endpoint sets `cancel_at_period_end = True` and calls Razorpay's cancel API. There's no guard against calling cancel on an already-cancelled subscription.

```python
# apps/backend/app/api/v1/subscription.py - /cancel
try:
    razorpay_client.subscription.cancel(subscription_id)
except Exception as e:
    # Razorpay returns error if already cancelled
    raise HTTPException(status_code=400, detail=str(e))
```

**Impact:** Users clicking "Cancel" twice see an error message. The first call succeeds; the second propagates a Razorpay API error to the frontend.

**Remediation:**  
Check `cancel_at_period_end` before calling Razorpay:
```python
if user.cancel_at_period_end:
    return {"message": "Subscription already scheduled for cancellation"}
```

---

### PAY-004: Monthly Message Count Reset Only on Charge Event

| Property | Value |
|----------|-------|
| **Severity** | MEDIUM |
| **Affected Files** | `apps/backend/app/api/webhooks/razorpay.py` |
| **Impact** | Users may exhaust quota with no reset if webhook fails |

**Description:**  
The monthly message counter is reset only when a `subscription.charged` event is received:

```python
# apps/backend/app/api/webhooks/razorpay.py - handle_subscription_charged()
await db.users.update_one(
    {"_id": user_id},
    {"$set": {"monthly_message_count": 0, "last_charge_date": now}}
)
```

**Impact:**  
- If Razorpay doesn't send the charged event (network issue, webhook misconfigured, Cloudflare blocks it), the user's count never resets
- Free users have NO reset mechanism at all - their count grows indefinitely until the month key in Redis changes
- There is no cron job or scheduled task to reset counts independently

**Remediation:**  
1. Add a daily cron job (or Azure Container Apps scheduled task) that resets counts for users whose billing date has passed
2. For free users, implement a Redis key with monthly TTL or a check against current month at rate-limit time
3. Add monitoring for "charged" webhook delivery success rate

---

## 9. Deployment and Infrastructure

### DEPLOY-001: Docker Multi-Stage Build

| Property | Value |
|----------|-------|
| **Severity** | INFO |
| **Affected Files** | `apps/backend/Dockerfile` |
| **Impact** | N/A - well-structured |

**Description:**  
The Dockerfile uses multi-stage builds with proper security practices:
- Hash stripping from `requirements.txt` for reproducibility (accepts the trade-off of losing integrity verification)
- Runs as non-root user (`appuser:1000`)
- Uses `python:3.11-slim` base image for minimal attack surface
- Health check included in image definition

---

### DEPLOY-002: Docker Compose Credentials in Plaintext

| Property | Value |
|----------|-------|
| **Severity** | LOW |
| **Affected Files** | `docker-compose.yml` |
| **Impact** | Credentials visible in version control |

**Description:**  
Local development credentials are hardcoded:

```yaml
# docker-compose.yml
services:
  mongo:
    environment:
      MONGO_INITDB_ROOT_PASSWORD: localdevpassword
  redis:
    command: redis-server --requirepass localredispassword
```

**Impact:** These are clearly labeled for local development only. The `.env.shared` file referenced in the README is not present in the repository (preventing accidental credential commits). Production uses Azure-managed secrets.

**Remediation:**  
Consider using a `.env.local` file (gitignored) even for development passwords, as a good hygiene practice that prevents any future copy-paste of docker-compose.yml to production contexts.

---

### DEPLOY-003: Health Check Uses Python Interpreter

| Property | Value |
|----------|-------|
| **Severity** | LOW |
| **Affected Files** | `apps/backend/Dockerfile` |
| **Impact** | ~200ms overhead per health check probe |

**Description:**  
```dockerfile
# apps/backend/Dockerfile
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"
```

**Impact:** Each health check spawns a Python interpreter (~200ms startup), imports urllib, and makes the HTTP request. With 30-second intervals, this adds negligible CPU overhead but is suboptimal. `curl` or `wget` are not available in `python:3.11-slim`.

**Remediation:**  
Install `curl` in the build stage or use a compiled health check binary. Alternatively, accept the trade-off since 200ms every 30 seconds is negligible for a production container.

---

### DEPLOY-004: No Resource Limits in docker-compose.yml

| Property | Value |
|----------|-------|
| **Severity** | LOW |
| **Affected Files** | `docker-compose.yml` |
| **Impact** | Unbounded resource usage in local development |

**Description:**  
No memory or CPU limits are set on any service in docker-compose.yml. In development this is standard practice; production uses Azure Container Apps resource configuration.

**Remediation:**  
For development environments with limited RAM (8GB laptops), consider adding:
```yaml
deploy:
  resources:
    limits:
      memory: 512M
```

---

### DEPLOY-005: Wrangler Production Missing AZURE_BACKEND_URL Override

| Property | Value |
|----------|-------|
| **Severity** | HIGH |
| **Affected Files** | `apps/edge/wrangler.toml` |
| **Impact** | Complete outage if Cloudflare secret is deleted |

**Description:**  
This is the same root cause as CFG-001 but from a deployment perspective:

```toml
# apps/edge/wrangler.toml
[vars]
AZURE_BACKEND_URL = "http://localhost:8000"  # Default fallback

[env.production.vars]
ALLOWED_ORIGIN = "https://syrabit.com"
# AZURE_BACKEND_URL is NOT overridden - relies on Cloudflare secret
```

**Impact:** The deployment has a single point of failure: one Cloudflare secret. If it's accidentally deleted during a dashboard cleanup, rotated incorrectly, or if Cloudflare's secret store has an issue, the entire application routes API calls to localhost and returns 503 for every request.

**Remediation:**  
1. Add the production URL as a `[env.production.vars]` entry (secrets override vars, so the secret still takes precedence if set)
2. Add a deployment check that verifies `AZURE_BACKEND_URL` is set and is not localhost before deploying to production
3. Add alerting on the edge worker's error rate to catch this quickly

---

## 10. Static Analysis Findings

### STATIC-001: Cloudflare Client Dead Code for Chat

| Property | Value |
|----------|-------|
| **Severity** | LOW |
| **Affected Files** | `apps/backend/app/services/ai/cloudflare_client.py` |
| **Impact** | Maintainability confusion |

**Description:**  
The Cloudflare AI client contains `generate()` and `stream_generate()` methods for chat that are never called from the chat flow:

```python
# apps/backend/app/services/ai/cloudflare_client.py
class CloudflareClient:
    async def generate(self, messages, model="@cf/meta/llama-2-7b-chat-int8"):
        """Generate chat completion via Workers AI"""
        ...  # NEVER called from router.py or chat_service.py
    
    async def generate_image_description(self, image_data):
        """OCR via Workers AI Vision - USED"""
        ...
    
    async def text_to_speech(self, text, language):
        """TTS via Workers AI - USED"""
        ...
```

The router explicitly rejects Cloudflare for chat:
```python
# apps/backend/app/services/ai/router.py
raise ValueError("Cloudflare Workers AI is not used for chat")
```

**Impact:** New developers may incorrectly believe Cloudflare Workers AI is an active chat provider, leading to configuration attempts or incorrect routing changes.

**Remediation:**  
Remove the unused `generate()` and `stream_generate()` methods, or mark them with a clear deprecation comment explaining they exist for potential future use but are not part of the active architecture.

---

### STATIC-002: Frontend Calls Non-Existent Backend Endpoints

| Property | Value |
|----------|-------|
| **Severity** | HIGH |
| **Affected Files** | `apps/frontend/src/utils/api.jsx` |
| **Impact** | Multiple frontend features are broken |

**Description:**  
The frontend API utility defines functions that call backend endpoints which do not exist:

```javascript
// apps/frontend/src/utils/api.jsx - These all return 404
export const getConversation = (id) => api.get(`/api/v1/conversations/${id}`);
export const deleteConversation = (id) => api.delete(`/api/v1/conversations/${id}`);
export const updateConversation = (id, data) => api.patch(`/api/v1/conversations/${id}`, data);
export const getAnonConversations = () => api.get('/api/v1/conversations/anon');
export const getAnonConversation = (id) => api.get(`/api/v1/conversations/anon/${id}`);
export const deleteAnonConversation = (id) => api.delete(`/api/v1/conversations/anon/${id}`);
```

**Backend routes that actually exist:**
- `GET /api/v1/chat/conversations` (alias for history listing)
- `GET /api/v1/chat/{session_id}/messages` (messages for one session)

**Impact:** Any UI component using these functions shows errors or empty states. Conversation deletion, renaming, and anonymous conversation management are completely non-functional.

**Remediation:**  
Either:
1. Implement the missing backend endpoints (preferred for complete feature set)
2. Remove the frontend functions and disable the UI components that depend on them
3. Update the frontend to use existing endpoints (`/api/v1/chat/conversations` for listing)

---

### STATIC-003: `saveOnboarding` Endpoint May Not Exist

| Property | Value |
|----------|-------|
| **Severity** | MEDIUM |
| **Affected Files** | `apps/frontend/src/utils/api.jsx`, `apps/backend/app/api/v1/users.py` |
| **Impact** | Onboarding data may not be persisted |

**Description:**  
The frontend calls an onboarding endpoint:
```javascript
// apps/frontend/src/utils/api.jsx
export const saveOnboarding = (data) => api.post('/api/v1/user/onboarding', data);
```

However:
- The backend users router is registered at `/api/v1/users` (plural)
- Discovered endpoints: `GET /me`, `PUT /me`, `DELETE /me`
- No `/user/onboarding` or `/users/onboarding` endpoint found

**Impact:** The onboarding flow (language preference, grade selection, board selection) may not persist user preferences, causing the onboarding screen to show repeatedly.

**Remediation:**  
1. Verify if a `/users/onboarding` endpoint exists in a file not covered by this audit
2. If not, implement `POST /api/v1/users/onboarding` to save language, grade, and board preferences
3. Update the frontend URL to match the backend prefix (`/users/` not `/user/`)

---

### STATIC-004: Potential Missing Import for Tracking Utility

| Property | Value |
|----------|-------|
| **Severity** | LOW |
| **Affected Files** | `apps/backend/app/api/v1/chat.py` |
| **Impact** | ImportError on startup if module is missing |

**Description:**  
```python
# apps/backend/app/api/v1/chat.py
from app.utils.tracking import track_chat_completed
```

The `app/utils/tracking.py` module was not found in the primary service files listing. If this file does not exist, the application would fail to start with an ImportError.

**Impact:** If the import fails, the entire backend is non-functional. However, since the app presumably starts in development, this file likely exists but was outside the audit's file discovery scope.

**Remediation:**  
Verify the file exists at `apps/backend/app/utils/tracking.py`. If it doesn't, either create it with a no-op implementation or remove the import.

---

### STATIC-005: Circuit Breaker State Not Async-Safe

| Property | Value |
|----------|-------|
| **Severity** | LOW |
| **Affected Files** | `apps/backend/app/core/circuit_breaker.py` |
| **Impact** | Theoretical race in state transitions |

**Description:**  
The circuit breaker uses plain instance variables without asyncio.Lock protection:

```python
# apps/backend/app/core/circuit_breaker.py
class CircuitBreaker:
    def _on_failure(self):
        self.failure_count += 1  # Not atomic
        if self.failure_count >= self.threshold:
            self.state = "open"  # Race with concurrent _on_success
            self.opened_at = time.time()
```

**Impact:** In Python's asyncio (single-threaded cooperative multitasking), this is safe because no context switch occurs between reading and writing an instance variable in synchronous code. The `+=` and `=` operations happen within a single Python bytecode frame without yielding.

If the code were used with threading, or if `await` statements were added between read and write, it would be unsafe. Currently, this is a theoretical concern only.

**Remediation:**  
Add an `asyncio.Lock` for defense-in-depth if the circuit breaker is ever modified to include async operations in state transitions.

---

### STATIC-006: CSP Blocks Required External Resources (Duplicate of SEC-006)

| Property | Value |
|----------|-------|
| **Severity** | HIGH |
| **Affected Files** | `apps/edge/src/index.ts` or `apps/edge/src/middleware/security-headers.ts` |
| **Impact** | Third-party JavaScript integrations non-functional |

**Description:**  
See [SEC-006](#sec-006-content-security-policy-blocks-production-features) for full details. The `script-src 'self'` policy blocks PostHog, Sentry, and critically, Cloudflare Turnstile - which is required for the security flow itself.

---

### STATIC-007: Frontend Chat Route Not Auth-Guarded

| Property | Value |
|----------|-------|
| **Severity** | MEDIUM |
| **Affected Files** | `apps/frontend/src/App.jsx` |
| **Impact** | Profile-dependent UI may error for anonymous users |

**Description:**  
```jsx
// apps/frontend/src/App.jsx
<Route path="/chat" element={<ChatPage />} />           {/* No AuthGuard */}
<Route path="/history" element={<AuthGuard><HistoryPage /></AuthGuard>} />
<Route path="/profile" element={<AuthGuard><ProfilePage /></AuthGuard>} />
```

The chat route is intentionally unguarded to support anonymous usage (with IP-based rate limiting). However, if ChatPage renders profile-dependent components (avatar, name, preferences), these may throw errors or show broken UI for unauthenticated users.

**Impact:** This is architecturally intentional (anonymous chat is a feature), but any component in ChatPage that assumes `user` is non-null will fail silently or throw.

**Remediation:**  
Ensure all components within ChatPage use optional chaining or conditional rendering for user-dependent data:
```jsx
{user?.name && <span>{user.name}</span>}
```

---

---

## Summary Table

| ID | Title | Severity | Category | Affected Layer |
|----|-------|----------|----------|----------------|
| SEC-001 | JWT Algorithm Mismatch Between Edge and Backend | CRITICAL | Security | Edge + Backend |
| API-001 | Missing Backend Routes for Conversation CRUD | HIGH | Integration | Frontend + Backend |
| SEC-002 | Edge Shared Secret Trust Bypass | HIGH | Security | Edge + Backend |
| SEC-006 | CSP Blocks Required External Resources | HIGH | Security | Edge |
| CFG-001 | Local Backend URL in wrangler.toml | HIGH | Configuration | Edge |
| DEPLOY-005 | Wrangler Production Missing Backend URL Override | HIGH | Deployment | Edge |
| STATIC-002 | Frontend Calls Non-Existent Backend Endpoints | HIGH | Static Analysis | Frontend |
| API-003 | Token Storage in sessionStorage | MEDIUM | Integration | Frontend |
| ENDPOINT-003 | Admin Router Prefix Collision Risk | MEDIUM | API | Backend |
| ERR-002 | Redis Unavailability Inconsistency | MEDIUM | Error Handling | Backend |
| ERR-003 | Password Reset Token Redis Failure | MEDIUM | Error Handling | Backend |
| SEC-004 | CSRF Origin Check Bypass | MEDIUM | Security | Backend |
| SEC-005 | Turnstile Not on All Sensitive Endpoints | MEDIUM | Security | Edge |
| CFG-002 | TypeScript Type vs Runtime for EDGE_SHARED_SECRET | MEDIUM | Configuration | Edge |
| DB-002 | Missing Negative Caching for Search | MEDIUM | Database | Backend |
| DB-005 | Race Condition in Rate Limiting | MEDIUM | Database | Backend |
| PAY-002 | Payment State Machine Incomplete | MEDIUM | Payment | Backend |
| PAY-004 | Monthly Count Reset Only on Charge | MEDIUM | Payment | Backend |
| STATIC-003 | saveOnboarding Endpoint May Not Exist | MEDIUM | Static Analysis | Frontend + Backend |
| STATIC-007 | Frontend Chat Route Not Auth-Guarded | MEDIUM | Static Analysis | Frontend |
| ENDPOINT-001 | Content-Length Deletion in Proxy | LOW | API | Edge |
| SEC-003 | Default JWT Secret in Non-Production | LOW | Security | Backend |
| CFG-003 | Hardcoded Razorpay Plan ID | LOW | Configuration | Backend |
| AI-003 | Token Budget Character-Based Estimation | LOW | AI | Backend |
| AI-004 | Vertex AI OAuth Token Edge Case | LOW | AI | Backend |
| DB-003 | Chat History Pagination In-Memory | LOW | Database | Backend |
| DB-004 | Rate Limit Redis Key Lifetime | LOW | Database | Backend |
| PAY-003 | Subscription Cancellation Idempotency | LOW | Payment | Backend |
| DEPLOY-002 | Docker Compose Credentials in Plaintext | LOW | Deployment | Infrastructure |
| DEPLOY-003 | Health Check Uses Python Interpreter | LOW | Deployment | Backend |
| DEPLOY-004 | No Resource Limits in docker-compose | LOW | Deployment | Infrastructure |
| STATIC-001 | Cloudflare Client Dead Code for Chat | LOW | Static Analysis | Backend |
| STATIC-004 | Potential Missing Import for Tracking | LOW | Static Analysis | Backend |
| STATIC-005 | Circuit Breaker State Not Async-Safe | LOW | Static Analysis | Backend |
| API-002 | Frontend API Base URL Architecture | INFO | Integration | Frontend |
| API-004 | Streaming Response Contract Verified | INFO | Integration | All |
| ENDPOINT-002 | Health Endpoint Duplication | INFO | API | Backend + Edge |
| ERR-001 | Chat Endpoint Comprehensive Error Handling | INFO | Error Handling | Backend |
| CFG-004 | 42 Environment Variables with Safe Defaults | INFO | Configuration | Backend |
| AI-001 | Correct AI Routing Verified | INFO | AI | Backend |
| AI-002 | Sarvam-to-Vertex Fallback | INFO | AI | Backend |
| DB-001 | MongoDB Indexes Well-Defined | INFO | Database | Backend |
| PAY-001 | Razorpay Webhook Signature Verification | INFO | Payment | Backend |
| DEPLOY-001 | Docker Multi-Stage Build | INFO | Deployment | Backend |

---

## Recommendations Priority Matrix

### Immediate (Before Production Launch)

| Priority | Finding | Effort | Risk if Unresolved |
|----------|---------|--------|-------------------|
| P0 | SEC-001: Fix JWT algorithm mismatch | Medium | Auth completely broken |
| P0 | CFG-001/DEPLOY-005: Add production backend URL to wrangler vars | Low | Complete outage on secret loss |
| P0 | SEC-006: Fix CSP to allow Turnstile, PostHog, Sentry | Low | Security + analytics broken |
| P1 | API-001/STATIC-002: Implement missing conversation CRUD endpoints | High | Core UX features non-functional |
| P1 | SEC-002: Add per-request HMAC to edge-backend trust | Medium | User impersonation risk |

### Short-Term (First Sprint Post-Launch)

| Priority | Finding | Effort | Risk if Unresolved |
|----------|---------|--------|-------------------|
| P2 | ERR-002: Standardize Redis failure behavior | Medium | Inconsistent degradation |
| P2 | PAY-002: Complete payment state machine | Medium | Subscription state drift |
| P2 | PAY-004: Add monthly reset cron job | Low | Users locked out of quota |
| P2 | DB-002: Add negative caching for search | Low | Cost overrun under load |
| P2 | SEC-005: Add Turnstile to password reset | Low | Automated brute force |

### Medium-Term (Within 30 Days)

| Priority | Finding | Effort | Risk if Unresolved |
|----------|---------|--------|-------------------|
| P3 | ERR-003: Log Redis failures in password reset | Low | Silent security degradation |
| P3 | SEC-004: Add CSRF double-submit cookie | Medium | Cross-site request risk |
| P3 | DB-005: Atomic rate limit increment | Low | Minor limit bypass |
| P3 | ENDPOINT-003: Separate admin router prefixes | Low | Route shadowing potential |
| P3 | STATIC-003: Verify/implement onboarding endpoint | Medium | Onboarding flow broken |

### Low Priority (Backlog)

| Priority | Finding | Effort | Risk if Unresolved |
|----------|---------|--------|-------------------|
| P4 | DB-003: MongoDB $slice for chat pagination | Low | Memory inefficiency |
| P4 | STATIC-001: Remove dead code in cloudflare_client | Low | Maintainability |
| P4 | API-003: Consider httpOnly cookies for tokens | High | XSS token theft risk |
| P4 | AI-003: Integrate model-specific tokenizers | Medium | Slight budget inaccuracy |
| P4 | DEPLOY-003: Optimize health check | Low | Negligible overhead |

---

## Methodology

This audit was performed through static code analysis of the following components:

- **Backend:** All Python files in `apps/backend/app/` including API routes, services, models, middleware, and configuration
- **Edge:** All TypeScript files in `apps/edge/src/` including middleware, routes, and type definitions
- **Frontend:** Key React files in `apps/frontend/src/` including API utilities, hooks, and routing
- **Infrastructure:** `docker-compose.yml`, `Dockerfile`, `wrangler.toml`, and Azure Bicep templates in `infra/`

Analysis focused on:
1. Contract mismatches between frontend expectations and backend implementations
2. Security vulnerabilities in authentication, authorization, and data protection
3. Error handling consistency across service boundaries
4. Configuration management and deployment safety
5. Third-party integration correctness (AI services, payments, search)

---

*Report generated via automated code analysis. Manual verification recommended for CRITICAL and HIGH findings before production deployment.*
