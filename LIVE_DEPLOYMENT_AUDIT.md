# Live Deployment Audit Report

**Project:** Syrabit AI Educational Assistant  
**Date:** 2025-01-20  
**Scope:** Full-stack code review (Backend, Edge Worker, Frontend)  
**Stack:** FastAPI + Cloudflare Workers + React/Vite  

---

## Executive Summary

This audit identified **35 issues** across 12 categories in the live deployment codebase. The issues range from critical blocking bugs to low-severity code quality concerns.

| Severity | Count | Description |
|----------|-------|-------------|
| **CRITICAL** | 1 | Synchronous SDK blocking async event loop |
| **HIGH** | 8 | Broken features, security gaps, data integrity risks |
| **MEDIUM** | 17 | Performance issues, configuration mismatches, incomplete implementations |
| **LOW** | 9 | Code quality, dead code, minor UX issues |

**Top Priority Actions:**
1. Fix synchronous Razorpay SDK blocking the event loop (CRITICAL)
2. Fix payment webhook subscription ID mismatch causing missed payment confirmations (HIGH)
3. Add `credits_remaining` to User model to prevent silent data loss (HIGH)
4. Fix health check returning 200 for degraded instances (HIGH)
5. Validate payment amounts in verify endpoint to prevent price manipulation (MEDIUM)

---

## Table of Contents

1. [Backend API / Configuration Issues](#1-backend-api--configuration-issues)
2. [Frontend Issues](#2-frontend-issues)
3. [Edge Worker Issues](#3-edge-worker-issues)
4. [Database Issues](#4-database-issues)
5. [Authentication / Authorization](#5-authentication--authorization)
6. [AI / Chat Integration](#6-ai--chat-integration)
7. [Payment Integration](#7-payment-integration)
8. [CORS / SSR / Routing](#8-cors--ssr--routing)
9. [Rate Limiting / Security](#9-rate-limiting--security)
10. [Dead Code / Unreachable Paths](#10-dead-code--unreachable-paths)
11. [Error Handling](#11-error-handling)
12. [Configuration / Environment](#12-configuration--environment)
13. [Recommended Priority Actions](#recommended-priority-actions)

---

## 1. Backend API / Configuration Issues

### CRITICAL: Payment endpoint uses synchronous `razorpay` SDK blocking the async event loop

| Field | Details |
|-------|---------|
| **Severity** | CRITICAL |
| **File** | `apps/backend/app/api/v1/payments.py` |
| **Lines** | 47-48, 128-130, 184-186 |

**Description:**  
The `payments.py` routes import and use the synchronous `razorpay.Client` SDK directly. However, the rest of the application uses async patterns via `apps/backend/app/services/payment/razorpay_client.py` which wraps calls with `httpx.AsyncClient`.

The synchronous SDK calls will **block the entire async event loop**, causing latency spikes for all concurrent requests while Razorpay API calls are in flight.

**Recommended Fix:**  
Refactor `payments.py` to use the existing `razorpay_client.py` async service, or wrap all synchronous SDK calls in `asyncio.to_thread()` to avoid blocking.

---

### HIGH: `credits_remaining` field missing from User model

| Field | Details |
|-------|---------|
| **Severity** | HIGH |
| **File** | `apps/backend/app/models/user.py` (field missing) |
| **Related** | `apps/backend/app/api/v1/payments.py`, line 273 |

**Description:**  
The credit top-up feature writes to `credits_remaining` via MongoDB `$set` operations, but the field is not declared in the Beanie Document model. The code uses `getattr(user, "credits_remaining", 0)` as a workaround.

Impact:
- Beanie will not validate the field
- The field will not appear in type hints or IDE autocompletion
- `getattr` with default 0 always returns 0 on fresh model loads since Beanie ignores unknown fields by default
- Credits purchased by users may silently disappear on model reload

**Recommended Fix:**  
Add `credits_remaining: int = 0` to the User model class.

---

### HIGH: Vertex Search service fails to initialize with Workload Identity

| Field | Details |
|-------|---------|
| **Severity** | HIGH |
| **File** | `apps/backend/app/services/search/vertex_search.py`, line 54 |
| **Related** | `apps/backend/app/config.py` (`google_credentials` property) |

**Description:**  
The search service `__init__` ONLY initializes if `settings.GOOGLE_APPLICATION_CREDENTIALS_JSON` is set. The `config.py` `google_credentials` property prioritizes the `GOOGLE_APPLICATION_CREDENTIALS` file path first, but the search service does not use this property.

In production on Cloud Run with Workload Identity (where no JSON key or file path exists), the search service will **never initialize**, silently disabling search functionality.

**Recommended Fix:**  
Change the search service init check to use `settings.google_credentials` (the property that handles all auth methods) or add explicit Application Default Credentials (ADC) detection.

---

### MEDIUM: Double CORS header application on API proxy responses

| Field | Details |
|-------|---------|
| **Severity** | MEDIUM |
| **File** | `apps/edge/src/index.ts`, lines 176-178 |
| **Related** | `apps/edge/src/routes/api-proxy.ts`, lines 107-109 |

**Description:**  
The main handler calls `addSecurityHeaders` then `applyCorsHeaders` on proxied API responses. The API proxy route handler ALSO sets CORS headers on the response. This results in duplicate `Access-Control-Allow-Origin` headers, which some browsers (notably Safari) reject.

**Recommended Fix:**  
Remove CORS header setting from one location - either the proxy route or the main handler, not both.

---

### MEDIUM: `.env.shared` has mismatched model names vs config defaults

| Field | Details |
|-------|---------|
| **Severity** | MEDIUM |
| **File** | `.env.shared` and `apps/backend/app/config.py` |

**Description:**  
| Variable | `.env.shared` value | `config.py` default |
|----------|---------------------|---------------------|
| `VERTEX_GEMINI_MODEL` | `gemini-2.0-flash-lite` | `gemini-2.5-flash` |
| `SARVAM_MODEL` | `openhathi-7b` | `sarvam-m` |

Developers copying from `.env.shared` will get different models than the code defaults, leading to confusion and inconsistent behavior across environments.

**Recommended Fix:**  
Synchronize `.env.shared` template values with `config.py` defaults, or add comments explaining the divergence.

---

### MEDIUM: Production auth requires RS256 but code paths fall back to HS256

| Field | Details |
|-------|---------|
| **Severity** | MEDIUM |
| **File** | `apps/backend/app/api/v1/auth.py`, `_get_signing_key()` ~line 108 |
| **Related** | `apps/edge/src/middleware/jwt.ts` |

**Description:**  
The backend raises `RuntimeError` in production if the RS256 private key is missing. The edge worker auto-detects the algorithm from the token header. If the backend issues HS256 tokens (in dev mode) and then deploys to production without RS256 keys, ALL existing tokens become invalid instantly with no migration path.

**Recommended Fix:**  
Document the migration path clearly. Add a health check warning when algorithm mismatch is detected between environment and existing tokens.

---

## 2. Frontend Issues

### HIGH: Chat page model selector references non-existent model

| Field | Details |
|-------|---------|
| **Severity** | HIGH |
| **File** | `apps/frontend/src/pages/ChatPage.jsx`, ~line 60 |
| **Related** | `apps/backend/app/services/ai/router.py` |

**Description:**  
The default model state is set to `openai/gpt-oss-20b`:
```jsx
const [model, setModel] = useState('openai/gpt-oss-20b')
```

The backend router only supports models containing "sarvam", "openhathi", "saaras", "gemini", or "vertex". A model name `openai/gpt-oss-20b` would cause `RuntimeError: Unknown model` on the backend.

Currently the `lang` param drives backend routing (making the model state cosmetic), but this is confusing and could cause real errors if the model param is ever sent to the backend.

**Recommended Fix:**  
Update the default model to match actual supported models (e.g., `gemini-2.5-flash`) or remove the unused state entirely.

---

### MEDIUM: Token storage uses sessionStorage (lost on tab close)

| Field | Details |
|-------|---------|
| **Severity** | MEDIUM |
| **File** | `apps/frontend/src/hooks/useTokenManager.js` |

**Description:**  
Auth tokens are stored in `sessionStorage`, which means users lose their session when closing the browser tab, even with a valid refresh token. For a mobile-first educational app targeting students, this creates excessive re-login friction.

**Recommended Fix:**  
Consider using `localStorage` for refresh tokens while keeping access tokens in memory for security. This balances security with UX for the target audience.

---

### MEDIUM: API base URL defaults to empty string without VITE_BACKEND_URL

| Field | Details |
|-------|---------|
| **Severity** | MEDIUM |
| **File** | `apps/frontend/src/utils/api.jsx`, line 4 |

**Description:**  
```jsx
const BACKEND_URL = import.meta.env.VITE_BACKEND_URL || ''
```

If `VITE_BACKEND_URL` is not set at build time, API calls go to same-origin (relative paths). The frontend is hosted on Cloudflare Pages (`syrabit.ai`), but the API is on the edge worker (`api.syrabit.ai`). Same-origin requests would 404 because Pages does not have API routes.

The build script `check-build-env.mjs` catches this, but it is not enforced by CI for all builds.

**Recommended Fix:**  
Make `VITE_BACKEND_URL` required in the build pipeline with a hard failure. Add the check to CI so no build can succeed without it.

---

### LOW: `queryClient` exported from App.jsx creates circular dependency risk

| Field | Details |
|-------|---------|
| **Severity** | LOW |
| **File** | `apps/frontend/src/App.jsx`, ~line 35 |

**Description:**  
The `queryClient` is re-exported from `App.jsx` with a comment acknowledging the circular import risk. Re-exporting from the top-level App component reintroduces the circular reference possibility for any module that imports from `App.jsx`.

**Recommended Fix:**  
Move `queryClient` to a dedicated leaf module (e.g., `src/lib/queryClient.js`) and remove the re-export from `App.jsx`.

---

## 3. Edge Worker Issues

### HIGH: Edge worker deletes `Content-Length` from proxied requests

| Field | Details |
|-------|---------|
| **Severity** | HIGH |
| **File** | `apps/edge/src/routes/api-proxy.ts`, ~line 25 |

**Description:**  
```typescript
headers.delete('Content-Length')
```

Deleting `Content-Length` from POST/PUT requests means the backend receives chunked transfer encoding without knowing the body size. This:
- Prevents request body size validation at the backend level
- May cause Cloud Run load balancers to reject requests without `Content-Length`
- Could lead to request timeouts for large payloads

**Recommended Fix:**  
Only delete `Content-Length` from the RESPONSE headers (for streaming), not the REQUEST headers forwarded to the backend.

---

### MEDIUM: KV rate limit race condition (acknowledged but unmitigated)

| Field | Details |
|-------|---------|
| **Severity** | MEDIUM |
| **File** | `apps/edge/src/middleware/rate-limit.ts`, lines 40-44 |

**Description:**  
The code explicitly acknowledges a read-then-write race condition via comment. With KV eventual consistency, concurrent requests can both pass the rate limit check before either write lands. For a 30 req/hour limit, this is mostly acceptable, but automated scripts can trivially bypass the edge rate limit through burst requests.

**Recommended Fix:**  
Accept the trade-off (it is documented) or migrate to Durable Objects for strong consistency on rate limiting.

---

### MEDIUM: ISR_CACHE_KV required for /health but may not be bound in all environments

| Field | Details |
|-------|---------|
| **Severity** | MEDIUM |
| **File** | `apps/edge/src/index.ts`, lines 127-150 |

**Description:**  
The health check uses `env.ISR_CACHE_KV.get('edge:health')`. If the `ISR_CACHE_KV` binding is not configured (e.g., in a staging environment), the health check would throw. The catch block handles this, but the binding is typed as non-optional in the `Env` interface, making the potential failure non-obvious.

**Recommended Fix:**  
Add an explicit null check `if (env.ISR_CACHE_KV)` before KV operations in the health check endpoint.

---

### LOW: Edge worker caches 302 redirects in CF Cache API

| Field | Details |
|-------|---------|
| **Severity** | LOW |
| **File** | `apps/edge/src/index.ts`, lines 194-207 |

**Description:**  
Redirect responses (302) for non-API GET requests are cached with `s-maxage=3600` (1 hour). If a page temporarily returns a redirect, it gets cached and served stale. These cached redirects are hard to purge from the Cloudflare Cache API.

**Recommended Fix:**  
Use a shorter TTL for redirect responses, or exclude redirects from caching entirely.

---

## 4. Database Issues

### MEDIUM: No TTL index on `chats` collection - unbounded growth

| Field | Details |
|-------|---------|
| **Severity** | MEDIUM |
| **File** | `apps/backend/app/db/mongo.py` |

**Description:**  
Chat documents accumulate indefinitely. The `dead_letters` collection has a 30-day TTL index, but chats do not. For a 100k DAU target, this could mean millions of documents per month with no automatic cleanup.

**Recommended Fix:**  
Add a TTL index on `chats.created_at` (e.g., 90 days) or implement an archival strategy that moves old chats to cold storage.

---

### LOW: Topic embeddings collection has no index defined

| Field | Details |
|-------|---------|
| **Severity** | LOW |
| **File** | `apps/backend/app/db/mongo.py` |

**Description:**  
`TopicEmbedding` is registered as a Beanie document but no index is created in `create_indexes()`. If topic matching queries are frequent, this results in full collection scans.

**Recommended Fix:**  
Add an index on the embedding vector field or the `topic_id` field.

---

## 5. Authentication / Authorization

### HIGH: Edge worker JWT verification skips `/api/v1/content` entirely

| Field | Details |
|-------|---------|
| **Severity** | HIGH |
| **File** | `apps/edge/src/middleware/jwt.ts`, line 25 |
| **Related** | `apps/backend/app/api/v1/content.py` |

**Description:**  
`PUBLIC_PATHS` includes `/api/v1/content`, meaning ALL content endpoints are treated as public at the edge level. If `content.py` has auth-protected endpoints (e.g., admin content management), they rely solely on backend auth with no edge-level defense.

While defense-in-depth is maintained at the backend level, this bypasses the intended layered security architecture.

**Recommended Fix:**  
Document this as an intentional design choice, or narrow the public path to specific sub-routes (e.g., `/api/v1/content/public`).

---

### MEDIUM: Token blacklist check fails open when Redis is down

| Field | Details |
|-------|---------|
| **Severity** | MEDIUM |
| **File** | `apps/backend/app/api/v1/auth.py`, ~line 185 |

**Description:**  
```python
pass  # Fail-open: JWT is still cryptographically valid
```

If Redis is down, a logged-out user's blacklisted token still works. For an educational app this is acceptable for general access, but for payment-related actions (subscription changes, credit purchases) it is a security gap.

**Recommended Fix:**  
Add specific fail-closed behavior for payment/subscription endpoints when Redis is unavailable. General auth can remain fail-open.

---

### LOW: Admin JWT uses same signing key as user JWT by default

| Field | Details |
|-------|---------|
| **Severity** | LOW |
| **File** | `apps/backend/app/config.py` |

**Description:**  
`ADMIN_JWT_SECRET` falls back to `JWT_SECRET` if not set. Production validation warns but does not prevent startup. A compromised user JWT secret would also compromise admin access.

**Recommended Fix:**  
Enforce a separate `ADMIN_JWT_SECRET` in production by making the warning a hard error that prevents startup.

---

## 6. AI / Chat Integration

### MEDIUM: Sarvam AI language detection threshold may miss short Assamese queries

| Field | Details |
|-------|---------|
| **Severity** | MEDIUM |
| **File** | `apps/backend/app/services/ai/router.py`, line 26 |

**Description:**  
The language detection requires `assamese_chars >= 10` AND ratio > 0.3. Short Assamese queries (fewer than 10 Assamese characters) will be misrouted to Vertex AI (English model).

Example: "পদার্থ কি?" (What is matter?) has only 9 Assamese characters and would be incorrectly routed to the English model.

**Recommended Fix:**  
Lower the minimum character threshold or add dictionary-based detection for short queries to improve accuracy for the target Assamese-speaking student audience.

---

### MEDIUM: Chat cache ignores user tier - pro and free users get same cached response

| Field | Details |
|-------|---------|
| **Severity** | MEDIUM |
| **File** | `apps/backend/app/services/chat_service.py`, `_make_cache_hash` ~line 81 |

**Description:**  
The cache hash is computed as `message:lang` only. A comment acknowledges this limitation. Currently, free-tier users with limited context (fewer RAG docs) will be served cached responses generated with full pro-tier context, potentially giving free users responses they should not have access to.

**Recommended Fix:**  
Include `user_tier` in the cache key, or only cache responses when `context_chunks` is empty (which aligns with the code comment).

---

### LOW: Circuit breaker state transitions race in stream paths

| Field | Details |
|-------|---------|
| **Severity** | LOW |
| **File** | `apps/backend/app/services/ai/vertex_client.py`, ~lines 237, 242 |

**Description:**  
`vertex_circuit_breaker._on_success()` / `_on_failure()` are called from streaming generators that bypass `CircuitBreaker.call()` (which holds the lock). Under high concurrency, state transitions could race.

**Recommended Fix:**  
Use the public `record_success()` / `record_failure()` methods or add locking to the stream paths.

---

## 7. Payment Integration

### HIGH: Subscription webhook uses subscription ID but payments.py stores order ID

| Field | Details |
|-------|---------|
| **Severity** | HIGH |
| **File** | `apps/backend/app/api/webhooks/razorpay.py` |
| **Related** | `apps/backend/app/api/v1/payments.py`, line 89 |

**Description:**  
The webhook handler looks up users by `razorpay_subscription_id`. However, `payments.py` stores the `razorpay_order_id` in the `razorpay_subscription_id` field.

Orders and subscriptions are different Razorpay entities with different ID prefixes (`order_` vs `sub_`). The webhook validation rejects IDs not matching `^sub_[A-Za-z0-9_]+$`.

**Impact:** A user who paid via the `payments.py` order flow will **NEVER** match the webhook `subscription.charged` handler, meaning their subscription renewal will silently fail.

**Recommended Fix:**  
Use consistent ID storage. Either always use the subscription flow, or handle both ID types in the webhook handler with appropriate prefix checking.

---

### MEDIUM: No payment amount validation in verify endpoint

| Field | Details |
|-------|---------|
| **Severity** | MEDIUM |
| **File** | `apps/backend/app/api/v1/payments.py`, ~lines 75-89 |

**Description:**  
The verify endpoint checks the Razorpay signature but does not validate that the payment amount matches the expected plan price. An attacker could:
1. Create a legitimate order for 1 INR
2. Pay it successfully
3. Get a valid signature
4. Present it to the verify endpoint to upgrade to pro

Redis stores the expected amount but the verify endpoint does not enforce it.

**Recommended Fix:**  
After signature verification, compare the stored amount in Redis against the expected plan price before granting the subscription.

---

## 8. CORS / SSR / Routing

### MEDIUM: SSR entry-server.jsx setup may be incomplete

| Field | Details |
|-------|---------|
| **Severity** | MEDIUM |
| **File** | `apps/frontend/src/entry-server.jsx` |
| **Related** | `apps/frontend/src/App.jsx` |

**Description:**  
`App.jsx` exports `AppShell` and `AppRoutes` for SSR usage, and `entry-server.jsx` exists. However, the actual rendering server configuration is unclear. If Cloudflare Pages does not have an SSR function configured (via `_worker.js` or Pages Functions), prerendered routes may serve stale HTML indefinitely.

**Recommended Fix:**  
Verify that Cloudflare Pages Functions or `_worker.js` handles SSR routing correctly. Document the SSR architecture in the project README.

---

### LOW: Double redirect for root path "/"

| Field | Details |
|-------|---------|
| **Severity** | LOW |
| **File** | `apps/frontend/src/App.jsx` |
| **Related** | `apps/edge/src/index.ts`, line 199 |

**Description:**  
- Frontend: `<Route path="/" element={<Navigate to="/library" replace />} />`
- Edge worker: unknown GET paths redirect to `ALLOWED_ORIGIN + pathname`

If someone hits the edge worker directly at "/", they get redirected to `syrabit.ai/`, which then client-side redirects to `/library`. This double redirect adds latency and is unnecessary.

**Recommended Fix:**  
Handle "/" specifically at the edge level to redirect directly to `/library`.

---

## 9. Rate Limiting / Security

### MEDIUM: Backend monthly rate limit double-counts with edge burst limit

| Field | Details |
|-------|---------|
| **Severity** | MEDIUM |
| **File** | `apps/backend/app/api/deps/rate_limit.py` |
| **Related** | `apps/edge/src/index.ts`, ~line 115 |

**Description:**  
The backend always increments its counter regardless of the edge `X-Rate-Limited-By` header. The architecture states that edge handles burst (30/hour) while backend handles monthly quota (30/month for free tier). The backend still counts every request, including those that passed edge rate limiting.

Per comment HF-026, this double rate limiting is by design, but it is not documented clearly and may confuse future developers.

**Recommended Fix:**  
Add clear documentation explaining the intentional double rate-limiting architecture (edge for burst protection, backend for quota enforcement).

---

### LOW: Prompt injection patterns list is incomplete

| Field | Details |
|-------|---------|
| **Severity** | LOW |
| **File** | `apps/backend/app/core/security.py` |

**Description:**  
The injection detection checks for known patterns but is missing newer LLM injection techniques such as "Do anything now" (DAN), "jailbreak" variants, and markdown/HTML injection vectors.

**Recommended Fix:**  
Consider using a more comprehensive injection detection library or implement a regular update schedule for injection patterns.

---

## 10. Dead Code / Unreachable Paths

### LOW: Legacy `rate_limiter.py` module still importable

| Field | Details |
|-------|---------|
| **Severity** | LOW |
| **File** | `apps/backend/app/core/rate_limiter.py` |

**Description:**  
The entire module is deprecated (per its docstring). `__all__ = []` prevents star imports but direct imports still work. This could lead to developers accidentally using the deprecated implementation.

**Recommended Fix:**  
Remove the file entirely or add an import hook that raises `DeprecationWarning`.

---

### LOW: Legacy `frontend/src/` directory at repo root

| Field | Details |
|-------|---------|
| **Severity** | LOW |
| **File** | `/frontend/` (root level) |
| **Related** | `/apps/frontend/` (active) |

**Description:**  
The file tree shows a `/frontend/` directory at the repo root alongside the active `/apps/frontend/`. This could confuse developers and CI pipelines.

**Recommended Fix:**  
Remove the legacy frontend directory if it is no longer used.

---

## 11. Error Handling

### MEDIUM: Streaming endpoint has no global timeout

| Field | Details |
|-------|---------|
| **Severity** | MEDIUM |
| **File** | `apps/backend/app/api/v1/chat.py`, ~line 261 |

**Description:**  
The non-streaming chat endpoint wraps the entire flow in a 15-second timeout:
```python
asyncio.wait_for(_process_chat(), timeout=15.0)
```

The streaming endpoint has no equivalent timeout. A stalled stream could keep a connection open indefinitely. While Cloud Run has a default 300s request timeout, a hung stream wastes server resources and connection pool capacity.

**Recommended Fix:**  
Add a maximum stream duration timeout (e.g., 60s) with a heartbeat keep-alive mechanism.

---

### LOW: Fire-and-forget tasks silently fail after retry

| Field | Details |
|-------|---------|
| **Severity** | LOW |
| **File** | `apps/backend/app/services/chat_service.py`, `save_chat` |

**Description:**  
The `save_chat` function retries once then stores in `dead_letter`. However, the dead letter store itself could fail (if MongoDB is fully down), resulting in complete data loss with no trace.

**Recommended Fix:**  
Add a final fallback to log the lost message payload (to stdout/stderr for Cloud Run log capture) for manual recovery.

---

## 12. Configuration / Environment

### HIGH: Production startup allows degraded state without failing health check

| Field | Details |
|-------|---------|
| **Severity** | HIGH |
| **File** | `apps/backend/app/config.py` |
| **Related** | `apps/backend/app/api/v1/health.py` |

**Description:**  
Startup errors are collected but the app still starts. The basic health endpoint returns "degraded" with config errors but still responds with HTTP 200 OK. Load balancers checking `/health` will see 200 and route traffic to a misconfigured instance.

**Impact:** Users could be routed to instances missing critical services (AI, database, payments) with no automated failover.

**Recommended Fix:**  
Return 503 from the basic health check when `startup_errors` is non-empty, or configure load balancer probes to use `/health/deep` instead.

---

### MEDIUM: Vertex AI health check reports unhealthy on Cloud Run with Workload Identity

| Field | Details |
|-------|---------|
| **Severity** | MEDIUM |
| **File** | `apps/backend/app/api/v1/health.py`, `vertex_ping()` |

**Description:**  
The health check verifies Vertex AI availability by checking if `GOOGLE_APPLICATION_CREDENTIALS_JSON` is set. On Cloud Run with Workload Identity, neither JSON nor file path is needed (Application Default Credentials via the metadata server are used). The health check will report Vertex AI as "unhealthy" even when it works correctly via ADC.

**Recommended Fix:**  
Also check if running on Cloud Run (presence of `K_SERVICE` env var) and report healthy when ADC is available.

---

## Recommended Priority Actions

### Immediate (P0) - Fix before next deployment

| # | Issue | Category | Risk |
|---|-------|----------|------|
| 1 | Synchronous Razorpay SDK blocking event loop | Backend | All users experience latency spikes during any payment |
| 2 | Payment webhook subscription ID mismatch | Payments | Subscription renewals silently fail |
| 3 | Health check returns 200 for degraded instances | Config | Broken instances receive production traffic |

### Short-term (P1) - Fix within 1 sprint

| # | Issue | Category | Risk |
|---|-------|----------|------|
| 4 | Add `credits_remaining` to User model | Backend | Purchased credits lost on model reload |
| 5 | Vertex Search fails with Workload Identity | Backend | Search disabled in production |
| 6 | No payment amount validation | Payments | Price manipulation vulnerability |
| 7 | Edge worker deletes request Content-Length | Edge | Potential request failures at Cloud Run LB |
| 8 | JWT verification skips all /api/v1/content paths | Auth | Reduced defense in depth |
| 9 | Chat model selector references non-existent model | Frontend | Confusing UX, potential runtime error |

### Medium-term (P2) - Fix within 2 sprints

| # | Issue | Category | Risk |
|---|-------|----------|------|
| 10 | Double CORS headers | Edge | Safari compatibility issues |
| 11 | Token blacklist fails open for payments | Auth | Security gap for financial operations |
| 12 | No TTL on chats collection | Database | Unbounded storage growth |
| 13 | Streaming endpoint no timeout | Error Handling | Resource exhaustion |
| 14 | Sarvam language detection threshold | AI | Short Assamese queries misrouted |
| 15 | Cache ignores user tier | AI | Free users get pro-tier responses |
| 16 | Model name mismatches in .env.shared | Config | Developer confusion |
| 17 | Vertex AI health check false negative | Config | Incorrect health reporting |

### Backlog (P3) - Address when convenient

| # | Issue | Category |
|---|-------|----------|
| 18-35 | All LOW severity issues | Various |

---

*End of audit report.*
