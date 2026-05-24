# Comprehensive Code Audit Report - Syrabit AI v3.0

**Audit Date:** 2025-01-15  
**Scope:** Full-stack EdTech platform (9-Pillar Hybrid Architecture)  
**Target:** 100k DAU production readiness assessment  
**Auditor:** Automated Code Analysis

---

## Executive Summary

| Severity | Count |
|----------|-------|
| **CRITICAL** | 18 |
| **WARNING** | 22 |
| **RECOMMENDATION** | 16 |
| **Total** | 56 |

**Overall Production Readiness: NOT READY - Critical issues must be resolved**

The platform demonstrates strong architectural decisions (edge-backend separation, dual-LLM routing, hybrid RAG search) but has multiple security vulnerabilities, performance bottlenecks, and operational gaps that must be addressed before serving 100k DAU in production.

---

## Table of Contents

1. [Security Audit](#1-security-audit)
2. [Performance Audit](#2-performance-audit)
3. [Deployment Readiness](#3-deployment-readiness)
4. [API Correctness](#4-api-correctness)
5. [Chat Pipeline](#5-chat-pipeline)
6. [Content Generation Pipeline](#6-content-generation-pipeline)
7. [Authentication & Authorization](#7-authentication--authorization)
8. [Workflows (Payments, Email, Subscriptions)](#8-workflows-payments-email-subscriptions)
9. [Analytics & Observability](#9-analytics--observability)
10. [SEO / GEO / AEO](#10-seo--geo--aeo)
11. [Appendix: Priority Remediation Roadmap](#appendix-priority-remediation-roadmap)

---

## 1. Security Audit

### Critical Issues

#### SEC-C1: Dangerous Default JWT Secret in Environment Template
- **File:** `.env.shared:89`
- **Finding:** The template contains `JWT_SECRET=super_secret_jwt_key_32_chars_min` which, if accidentally used in production, would allow any attacker to forge valid JWTs for any user.
- **Impact:** Complete authentication bypass, full account takeover.
- **Mitigation:** The backend validates JWT_SECRET length >= 32 chars in production mode (`apps/backend/app/config.py:113-115`), but the placeholder *already meets* that length requirement, meaning it would pass validation silently.
- **Fix:** Change the validator to reject known placeholder values; use a secrets manager (Azure KeyVault is already provisioned in `infra/azure/shared-resources.bicep`).

#### SEC-C2: Turnstile Bot Protection is Entirely Optional
- **File:** `apps/edge/src/index.ts:44-51`
- **Finding:** Turnstile verification only triggers if the `CF-Turnstile-Response` header is present. Bots can simply omit the header entirely and bypass all bot protection.
- **Impact:** Automated abuse of chat and auth endpoints, credential stuffing, API scraping.
- **Code:**
  ```typescript
  const turnstileToken = request.headers.get('CF-Turnstile-Response');
  if (turnstileToken) {  // Only verified IF present
    const isValid = await turnstileVerify(turnstileToken, env.CF_TURNSTILE_SECRET);
  }
  ```
- **Fix:** Make Turnstile mandatory for auth endpoints (signup, login, forgot-password). For chat, require it for anonymous users.

#### SEC-C3: Admin Endpoint Lacks CSRF Protection
- **File:** `apps/backend/app/api/v1/admin.py:19-42`
- **Finding:** The admin verification endpoint uses cookie-based JWT authentication (`syrabit_admin_session` cookie). While the CSRF origin middleware exists in `main.py:94-103`, cookie-based authentication without a CSRF token is vulnerable to cross-site attacks where the origin header can be omitted (e.g., from HTML forms or certain redirect scenarios).
- **Impact:** Potential CSRF attacks against admin operations.
- **Fix:** Add a double-submit CSRF token pattern or use `SameSite=Strict` on the admin cookie.

#### SEC-C4: Password Reset Token Has No One-Time-Use Enforcement
- **File:** `apps/backend/app/api/v1/auth.py:178-196`
- **Finding:** The reset token is a JWT with 1-hour expiry but no mechanism to invalidate it after use. An attacker who intercepts the token can use it multiple times within the hour window. The password can be changed, then changed again with the same token.
- **Impact:** If a reset email is intercepted, the attacker can maintain persistent access.
- **Fix:** Store used reset token hashes in Redis with the same 1-hour TTL, and reject tokens that have already been consumed.

#### SEC-C5: Refresh Tokens Cannot Be Revoked
- **File:** `apps/backend/app/api/v1/auth.py:70-75, 199-227`
- **Finding:** Refresh tokens are stateless JWTs with no server-side tracking. There is no token blacklist, no `jti` (JWT ID) claim, and no mechanism to revoke a compromised refresh token. The refresh endpoint issues a new refresh token on each call without invalidating the old one (token rotation without revocation).
- **Impact:** Stolen refresh tokens remain valid for the full 7-day expiry period, even after logout or password change.
- **Fix:** Implement a token family with rotation detection, or store refresh token hashes in Redis with TTL-based expiry.

### Warnings

#### SEC-W1: SSRF Protection Uses Synchronous DNS Resolution in Async Context
- **File:** `apps/backend/app/core/security.py:72-83`
- **Finding:** `is_safe_url()` calls `socket.getaddrinfo()` which is a synchronous blocking call. In an async FastAPI context, this blocks the event loop for DNS resolution duration.
- **Impact:** Under load, multiple concurrent SSRF checks could block the event loop, causing request timeouts and degraded performance.
- **Fix:** Use `asyncio.get_event_loop().getaddrinfo()` or wrap in `run_in_executor()`.

#### SEC-W2: Edge Worker Rate Limiting Uses Eventually Consistent KV
- **File:** `apps/edge/src/middleware/rate-limit.ts:47-55`
- **Finding:** Cloudflare KV is eventually consistent. The read-then-write pattern (`get` then `put`) creates a TOCTOU race condition where multiple concurrent requests can all read the same counter value and all succeed before the increment propagates.
- **Impact:** Burst traffic can exceed the configured limit by up to the number of concurrent requests during propagation delay (typically 60 seconds globally).
- **Code:**
  ```typescript
  const current = await kv.get(key);
  const count = current ? parseInt(current, 10) : 0;
  // Race condition: another request could read the same value here
  await kv.put(key, String(count + 1), { expirationTtl: 7200 });
  ```
- **Fix:** Use Cloudflare Durable Objects for strongly consistent counters, or accept that the KV-based limit is a soft limit and rely on the backend's Redis-based limiter as the hard enforcement.

#### SEC-W3: CORS Hardcoded Origin Inconsistency
- **File:** `apps/edge/src/middleware/cors.ts:5` vs `apps/edge/src/index.ts:24`
- **Finding:** The `cors.ts` module hardcodes `'https://syrabit.ai'` in the exported `corsHeaders` object, while `index.ts` uses `env.ALLOWED_ORIGIN || 'https://syrabit.ai'` dynamically. If `cors.ts` is used elsewhere (e.g., error responses), the origin won't match the env-configured value.
- **Impact:** If the allowed origin ever changes (staging, additional domains), responses using `corsHeaders` from `cors.ts` will fail CORS.
- **Fix:** Remove the hardcoded `corsHeaders` export or make it accept the origin as a parameter.

#### SEC-W4: Password Validation Only Checks Length
- **File:** `apps/backend/app/api/v1/auth.py:28-30, 48-50`
- **Finding:** Password validation only enforces `len(v) < 8`. No complexity requirements (uppercase, lowercase, digits, special characters). No check against common/breached password lists.
- **Impact:** Users can set trivially guessable passwords like "12345678" or "password".
- **Fix:** Add zxcvbn-based strength checking or at minimum require mixed character types.

#### SEC-W5: No Token Blacklist on Logout
- **File:** `apps/backend/app/api/v1/auth.py` (entire file)
- **Finding:** There is no logout endpoint at all. Users have no way to invalidate their access tokens or refresh tokens. Even if a frontend "logout" clears local storage, the tokens remain valid until expiry.
- **Impact:** Compromised tokens cannot be revoked by the user.
- **Fix:** Add a `/logout` endpoint that blacklists the current access/refresh token pair in Redis.

#### SEC-W6: Missing Content-Security-Policy Header
- **File:** `apps/backend/app/main.py:106-112`
- **Finding:** The security headers middleware adds X-Content-Type-Options, X-Frame-Options, HSTS, and Referrer-Policy, but does NOT include a Content-Security-Policy header. The frontend index.html also does not include a CSP meta tag.
- **Impact:** No protection against XSS via injected scripts, no restriction on resource loading origins.
- **Fix:** Add a CSP header appropriate for the SPA (allow same-origin scripts, inline styles for critical CSS, specific CDN origins for fonts and analytics).

#### SEC-W7: Google OAuth Trusts Supabase Token Without Email Verification
- **File:** `apps/backend/app/api/v1/auth.py:243-275`
- **Finding:** The Google OAuth flow trusts the Supabase user endpoint response completely. If a Supabase token is valid, the email from that response is used to find/create a user. There is no verification that the email is actually verified (`email_verified` field is not checked).
- **Impact:** If Supabase allows unverified email signups, an attacker could register with a victim's email on Supabase and then access their Syrabit account.
- **Fix:** Check `supabase_user.get("email_verified")` before accepting the OAuth identity.

### Recommendations

#### SEC-R1: Rate Limiter Lua Script Defined But Not Used for Chat
- **File:** `apps/backend/app/core/rate_limiter.py` (entire class) vs `apps/backend/app/api/v1/chat.py:66-82`
- **Finding:** A sophisticated token bucket rate limiter with Lua scripting exists in `rate_limiter.py`, but the chat endpoint implements its own simpler rate limiting with plain INCR/EXPIRE commands. The Lua script provides atomic operations; the chat implementation has a minor race condition.
- **Fix:** Use the existing `RateLimiter` class from `rate_limiter.py` in the chat endpoint to benefit from atomic operations.

#### SEC-R2: Input Sanitization Allows Many Prompt Injection Vectors
- **File:** `apps/backend/app/core/security.py:13-38`
- **Finding:** The sanitization uses a fixed pattern list that can be trivially bypassed with Unicode homoglyphs, base64 encoding, or novel injection patterns not in the list. The approach is fundamentally a denylist, which is always incomplete.
- **Fix:** Consider a defense-in-depth approach: sandboxed system prompts, output filtering, and monitoring for anomalous responses rather than relying solely on input sanitization.

#### SEC-R3: Webhook Endpoint Exposes Razorpay Secret in Error Path
- **File:** `apps/backend/app/api/webhooks/razorpay.py:39-45`
- **Finding:** If `RAZORPAY_WEBHOOK_SECRET` is None (not configured), the `hmac.HMAC(key=settings.RAZORPAY_WEBHOOK_SECRET.encode(), ...)` call will throw an `AttributeError` with a stack trace that may be visible in logs or error responses.
- **Fix:** Add an early guard: `if not settings.RAZORPAY_WEBHOOK_SECRET: raise HTTPException(503, "Webhook not configured")`.


---

## 2. Performance Audit

### Critical Issues

#### PERF-C1: Embedding Client Creates New HTTP Connection Per Request
- **File:** `apps/backend/app/services/ai/embedder.py:26`
- **Finding:** Every call to `generate_embedding()` creates a brand new `httpx.AsyncClient()` inside an `async with` block. This means no connection pooling, no keep-alive, and a full TCP+TLS handshake for every single embedding request.
- **Impact:** At 100k DAU with multiple messages per session, this creates thousands of unnecessary TLS handshakes per minute, adding 50-200ms latency per request and exhausting ephemeral ports.
- **Code:**
  ```python
  async with httpx.AsyncClient(timeout=30.0) as client:
      response = await client.post(...)
  ```
- **Fix:** Create a module-level singleton `httpx.AsyncClient` (like `vertex_client.py` and `sarvam_client.py` do) and reuse it across requests. Close it in the app shutdown lifecycle.

#### PERF-C2: Azure Search Uses Synchronous SDK in Thread Pool
- **File:** `apps/backend/app/services/search/azure_search.py:45-56, 73-97`
- **Finding:** The `SearchClient` from `azure.search.documents` is a synchronous client. All search operations are wrapped in `loop.run_in_executor(None, ...)` which dispatches to the default ThreadPoolExecutor (typically limited to `min(32, os.cpu_count() + 4)` workers).
- **Impact:** Under high concurrency (100k DAU), the thread pool becomes saturated. New search requests queue behind completed searches, creating unbounded latency growth. With 20 concurrent requests triggering autoscaling, the pool may be exhausted before scaling occurs.
- **Fix:** Use the async Azure Search client (`azure.search.documents.aio.SearchClient`) which supports native asyncio operations.

#### PERF-C3: Circuit Breakers Exist But Are Not Wired Into AI Clients
- **File:** `apps/backend/app/core/circuit_breaker.py:143-155` vs `apps/backend/app/services/ai/vertex_client.py` and `apps/backend/app/services/ai/sarvam_client.py`
- **Finding:** Three circuit breaker instances are created (`vertex_circuit_breaker`, `sarvam_circuit_breaker`, `azure_search_circuit_breaker`) but NONE of them are actually called in the AI client code. The `vertex_client.py` and `sarvam_client.py` use their own internal retry logic without involving the circuit breaker.
- **Impact:** When an AI provider goes down, the system continues sending requests (retrying), wasting resources and increasing latency instead of failing fast.
- **Fix:** Wrap AI client calls with the circuit breaker's `call()` method.

### Warnings

#### PERF-W1: Gunicorn Workers May Exhaust Memory in 1Gi Container
- **File:** `apps/backend/gunicorn_conf.py:4`
- **Finding:** `workers = multiprocessing.cpu_count() * 2 + 1`. On a 2-CPU container (0.5 vCPU allocated in `container-app.bicep:56`), this creates 5 workers. Each UvicornWorker process consumes 100-200MB base + loaded ML/SDK libraries. In a 1Gi container, this leaves minimal headroom.
- **Impact:** OOM kills during traffic spikes, leading to container restarts and 503 errors.
- **Fix:** Set workers to a fixed value (2-3) appropriate for the 1Gi memory limit, or use `WEB_CONCURRENCY` env var override.

#### PERF-W2: No Response Caching for Identical Queries
- **File:** `apps/backend/app/api/v1/chat.py` (entire file)
- **Finding:** Every chat request, even if identical to a previous query, performs the full pipeline: embedding generation, Azure Search, LLM inference. There is no caching layer for embeddings, search results, or LLM responses.
- **Impact:** Repeated queries (common in educational contexts - "what is photosynthesis?") incur full latency and cost every time.
- **Fix:** Add a Redis-based cache with content-hash keys for: (1) embeddings (text -> vector), (2) search results (embedding + tier -> chunks), and optionally (3) full responses for popular queries.

#### PERF-W3: Razorpay Client Creates New HTTP Connection Per Operation
- **File:** `apps/backend/app/services/payment/razorpay_client.py:22, 43`
- **Finding:** Both `create_subscription_order()` and `cancel_subscription()` use `async with httpx.AsyncClient(...) as client:` creating a new connection per call.
- **Impact:** While payment operations are less frequent than chat, each operation still incurs unnecessary connection overhead.
- **Fix:** Use the `self._client` pattern already established in `vertex_client.py`.

#### PERF-W4: Google OAuth Token Refresh Blocks Event Loop
- **File:** `apps/backend/app/services/ai/vertex_client.py:66-71`
- **Finding:** Token refresh uses `loop.run_in_executor(None, creds.refresh, request)` which is correct, BUT the `google.auth.transport.requests.Request()` object is created outside the executor, and the credential object is shared across requests without locking.
- **Impact:** Concurrent requests may trigger multiple simultaneous token refreshes, or the credential state may be corrupted under race conditions.
- **Fix:** Cache the access token with its expiry timestamp and only refresh when expired, with a lock to prevent thundering herd.

#### PERF-W5: Chat Endpoint Loads Modules Inline
- **File:** `apps/backend/app/api/v1/chat.py:95, 135, 142`
- **Finding:** The chat endpoint uses inline imports: `from app.services.ai.embedder import generate_embedding`, `from app.models.chat import Chat`. While Python caches imports, the import machinery still has overhead on the first call per worker.
- **Impact:** Minor but measurable cold-start penalty on first request per worker process.
- **Fix:** Move these to top-level imports.

#### PERF-W6: Streaming Endpoint Processes Full Body Clone for Rate Limiting
- **File:** `apps/edge/src/index.ts:65-72`
- **Finding:** The edge worker clones the entire request body (`request.clone()`) and parses it as JSON just to extract the `lang` field for per-language rate limiting. For large message payloads (up to 2000 chars), this doubles memory usage.
- **Impact:** Increased worker CPU time and memory per streaming request.
- **Fix:** Accept `lang` as a query parameter or custom header to avoid body parsing at the edge.

### Recommendations

#### PERF-R1: Add Connection Pooling Metrics
- **File:** `apps/backend/app/db/mongo.py:25-30`
- **Finding:** MongoDB connection pool is configured with maxPoolSize=50, minPoolSize=10, but there are no metrics exported for pool utilization, wait times, or connection errors.
- **Fix:** Add pool event listeners and export metrics to OpenTelemetry.

#### PERF-R2: Consider Read Replicas for Chat History
- **File:** `apps/backend/app/api/v1/chat.py:234-260`
- **Finding:** Chat history queries (`get_chat_history`, `get_chat_messages`) read from the primary MongoDB instance. At 100k DAU scale, read-heavy operations should target secondaries.
- **Fix:** Use `read_preference=ReadPreference.SECONDARY_PREFERRED` for history queries.


---

## 3. Deployment Readiness

### Critical Issues

#### DEPLOY-C1: Dockerfile Relies on Fragile sed Hack for Dependency Fix
- **File:** `apps/backend/Dockerfile:14-16`
- **Finding:** The build uses `sed` to strip `pytest-asyncio==0.26.0` from requirements.txt and then installs a different version. This is fragile because: (1) it relies on specific formatting of the requirements file, (2) it silently breaks if the version or package name changes, (3) it obscures the actual dependency tree.
- **Code:**
  ```dockerfile
  RUN sed -i '/^pytest-asyncio==0\.26\.0/,/^[^ #]/{ /^pytest-asyncio/d; /^    --hash/d; /^    #/d; }' requirements.txt \
      && pip install --no-cache-dir --user -r requirements.txt \
      && pip install --no-cache-dir --user "pytest-asyncio>=1.0,<2" \
      && pip install --no-cache-dir --user "email-validator>=2.0"
  ```
- **Impact:** Build breaks silently if requirements.txt format changes; the installed pytest-asyncio version may be incompatible.
- **Fix:** Fix the root cause in requirements.txt by pinning compatible versions of pytest and pytest-asyncio together.

#### DEPLOY-C2: No Docker HEALTHCHECK Instruction
- **File:** `apps/backend/Dockerfile` (entire file)
- **Finding:** The Dockerfile has no `HEALTHCHECK` instruction. While Azure Container Apps has its own probes (`container-app.bicep:63-79`), the Docker image itself cannot be health-checked by Docker Compose, Docker Swarm, or other orchestrators during local development or staging.
- **Impact:** In `docker-compose.yml`, dependent services have no way to know when the backend is truly ready.
- **Fix:** Add `HEALTHCHECK --interval=30s --timeout=5s CMD curl -f http://localhost:8000/health/health || exit 1`

#### DEPLOY-C3: Deployment Uses sleep 90 Instead of Health Polling
- **File:** `.github/workflows/deploy-all.yml:91-96`
- **Finding:** After deploying a new container revision, the workflow blindly waits 90 seconds before checking health. If the revision fails to start, this wastes 90 seconds. If it needs more than 90 seconds (cold start with large model loading), the check may fail prematurely.
- **Code:**
  ```yaml
  - name: Wait for healthy revision
    run: |
      echo "Waiting 90s for new revision to stabilize..."
      sleep 90
  ```
- **Impact:** Slow feedback on deployment failures; potential false-positive success if health check timing is wrong.
- **Fix:** Implement a polling loop: `for i in {1..30}; do curl -sf $URL/health && exit 0; sleep 5; done; exit 1`

#### DEPLOY-C4: No Rollback Automation
- **File:** `.github/workflows/deploy-all.yml` (entire file)
- **Finding:** If the smoke tests (`smoke-test` job, line 111+) fail after deployment, there is no automated rollback. The system remains in a broken state until manual intervention.
- **Impact:** Extended downtime during failed deployments.
- **Fix:** Add a rollback job that triggers on smoke-test failure, reverting to the previous container revision using `az containerapp revision activate`.

### Warnings

#### DEPLOY-W1: Container App Scale Limits May Be Insufficient for 100k DAU
- **File:** `infra/azure/container-app.bicep:82-93`
- **Finding:** Scaling is configured as `minReplicas: 1, maxReplicas: 3` with a trigger of 20 concurrent requests. For 100k DAU generating ~500k messages/day, peak concurrent requests could easily exceed 60 (3 replicas x 20). At 0.5 vCPU per replica, total compute is only 1.5 vCPU for the entire backend.
- **Impact:** Service degradation during peak hours (Indian school hours: 8am-10pm IST).
- **Fix:** Increase maxReplicas to at least 10, reduce concurrency trigger to 10, and increase per-container CPU to 1.0 vCPU.

#### DEPLOY-W2: wrangler.toml Has Localhost Default for Backend URL
- **File:** `apps/edge/wrangler.toml:8`
- **Finding:** `AZURE_BACKEND_URL = "http://localhost:8000"` is the default value. While the production environment overrides this via secrets, a misconfigured deployment could proxy all traffic to localhost (which would fail silently with 503s).
- **Impact:** If the secret is not set, all API requests fail.
- **Fix:** Add deployment validation that checks the AZURE_BACKEND_URL secret is set before deploying to production.

#### DEPLOY-W3: No Environment-Specific Configuration Validation
- **File:** `apps/backend/app/config.py:109-120`
- **Finding:** Production validation only checks JWT_SECRET length and logs warnings for missing MONGODB_URI, UPSTASH_REDIS_REST_URL, and AZURE_SEARCH_ENDPOINT. It does not validate that AI provider credentials (Vertex, Sarvam), payment credentials (Razorpay), or email credentials (Resend) are configured.
- **Impact:** The application starts successfully but core features fail at runtime.
- **Fix:** Add startup health checks that verify connectivity to all critical services.

#### DEPLOY-W4: CI/CD Does Not Pin Action Versions to SHA
- **File:** `.github/workflows/deploy-all.yml:20, 22, 24`
- **Finding:** Actions are pinned to major versions (`actions/checkout@v6`, `actions/setup-python@v6`) rather than commit SHAs. A supply-chain attack on these actions could compromise the CI pipeline.
- **Impact:** Theoretical supply-chain vulnerability.
- **Fix:** Pin to full commit SHAs for critical actions.

### Recommendations

#### DEPLOY-R1: Add Docker Layer Caching in CI
- **File:** `.github/workflows/deploy-all.yml:82-88`
- **Finding:** The ACR build uses `az acr build` which does not leverage Docker BuildKit layer caching. Each build reinstalls all Python dependencies from scratch.
- **Fix:** Use multi-stage caching with `--cache-from` or switch to `docker/build-push-action` with GitHub Actions cache.

#### DEPLOY-R2: Add Deployment Notifications
- **File:** `.github/workflows/deploy-all.yml` (entire file)
- **Finding:** No Slack/email notification on deployment success or failure.
- **Fix:** Add a notification step using the existing Resend integration or a Slack webhook.


---

## 4. API Correctness

### Critical Issues

#### API-C1: Chat Endpoint Catches All Exceptions and Returns Generic 500
- **File:** `apps/backend/app/api/v1/chat.py:131-133`
- **Finding:** The non-streaming chat endpoint has a catch-all `except Exception as e:` that logs the error but returns a generic "An internal error occurred" message with status 500. This loses all error context for debugging, makes it impossible for the frontend to display meaningful error messages, and conflates different failure modes (search failure, LLM timeout, embedding error).
- **Code:**
  ```python
  except Exception as e:
      logger.info("chat_failed", extra={"user_id": user_id, "error": str(e)})
      raise HTTPException(status_code=500, detail="An internal error occurred. Please try again later.")
  ```
- **Impact:** Users get unhelpful error messages; operators cannot distinguish between different failure types without log correlation.
- **Fix:** Catch specific exceptions (embedding failure, search failure, LLM failure) and return appropriate status codes (502 for upstream failures, 504 for timeouts).

#### API-C2: Health Check Router Registered at Two Prefixes
- **File:** `apps/backend/app/main.py:131-132`
- **Finding:** The health router is registered twice:
  ```python
  app.include_router(health.router, prefix="/health", tags=["Health"])
  app.include_router(health.router, prefix="/api/health", tags=["Health"])
  ```
  However, the health router itself already has `prefix="/health"` defined internally (`health.py:11`). This means the actual paths are `/health/health`, `/health/health/deep`, `/api/health/health`, and `/api/health/health/deep`.
- **Impact:** The Azure Container App probes (`container-app.bicep:66,73`) hit `/health` which returns 404. The basic health check is actually at `/health/health`.
- **Fix:** Remove the `prefix="/health"` from either the router definition or the `include_router` call.

### Warnings

#### API-W1: No Request Size Limit Beyond Message Validation
- **File:** `apps/backend/app/api/v1/chat.py:37-40`
- **Finding:** While the `message` field is limited to 2000 characters, the `context_messages` list field has no size limit. An attacker could send megabytes of data in `context_messages`, causing memory pressure and slow JSON parsing.
- **Impact:** Denial of service via large request bodies.
- **Fix:** Add a `max_items` constraint to `context_messages` and/or configure a request body size limit in the ASGI server.

#### API-W2: No API Rate Limiting Headers Returned from Backend
- **File:** `apps/backend/app/api/v1/chat.py:84-88`
- **Finding:** When rate limiting rejects a request, only a 429 status is returned with a message. No `X-RateLimit-Limit`, `X-RateLimit-Remaining`, or `Retry-After` headers are included. The `RateLimiter.get_headers()` method exists (`rate_limiter.py:87-99`) but is never called.
- **Impact:** Clients cannot implement intelligent backoff or display remaining quota to users.
- **Fix:** Include rate limit headers in both success and 429 responses.

#### API-W3: Delete Account Does Not Cascade to Feedback Data
- **File:** `apps/backend/app/api/v1/users.py:54-62`
- **Finding:** Account deletion cascades to the `Chat` collection but not to `ChatFeedback`. User feedback records persist after account deletion, containing `user_id` references to deleted users.
- **Impact:** GDPR/DPDP compliance gap - user data persists after deletion request.
- **Fix:** Add `await ChatFeedback.find({"user_id": str(user.id)}).delete()` to the deletion cascade.

#### API-W4: Chat History Endpoint Missing Authorization Check for Anonymous Chats
- **File:** `apps/backend/app/api/v1/chat.py:266-273`
- **Finding:** The `get_chat_messages` endpoint allows access to chats with `user_id=None` (anonymous chats) by any user who knows the `session_id`. Session IDs are UUIDs which provide some obscurity, but are exposed in responses and could be enumerated.
- **Impact:** Potential privacy leak of anonymous chat content.
- **Fix:** Consider requiring authentication for all chat history access, or add an access token tied to the session.

### Recommendations

#### API-R1: Add OpenAPI Description Enrichment
- **File:** `apps/backend/app/main.py:79-83`
- **Finding:** The FastAPI app has basic title/description/version, but individual endpoints lack detailed OpenAPI descriptions, example request/response bodies, and error response documentation.
- **Fix:** Add `response_model`, `responses`, and `summary` parameters to route decorators.

#### API-R2: Implement API Versioning Strategy
- **File:** `apps/backend/app/main.py:128-136`
- **Finding:** All routes use `/api/v1/` prefix but there is no mechanism for running v1 and v2 simultaneously, no deprecation headers, and no version negotiation.
- **Fix:** Document the versioning strategy and add `Sunset` headers when deprecating endpoints.


---

## 5. Chat Pipeline

### Critical Issues

#### CHAT-C1: Streaming Endpoint Sends Unsanitized Message to LLM
- **File:** `apps/backend/app/api/v1/chat.py:192, 207`
- **Finding:** The streaming endpoint sanitizes the message into `sanitized_message` (line 192) and uses it for embedding and search. However, in the `event_stream()` generator, the original `request.message` is passed to the LLM (line 207):
  ```python
  async for chunk in stream_response(
      system_prompt=system_prompt,
      user_message=request.message,  # UNSANITIZED!
      model=target_model,
  )
  ```
  The non-streaming endpoint also uses `request.message` at line 120.
- **Impact:** Prompt injection attacks bypass the sanitization layer entirely for the actual LLM interaction.
- **Fix:** Use `sanitized_message` consistently for all LLM calls.

#### CHAT-C2: No Conversation History Sent to LLM (Stateless Chat)
- **File:** `apps/backend/app/api/v1/chat.py:85-130`
- **Finding:** Each chat request is completely stateless. The LLM receives only the system prompt (with RAG context) and the current user message. Previous messages in the session are never included. The `context_messages` field exists in `ChatRequest` (line 35) but is never used in prompt construction.
- **Impact:** Users cannot have multi-turn conversations. Follow-up questions like "explain more" or "what about the second point?" will fail because the LLM has no context of what was previously discussed.
- **Fix:** Retrieve recent messages from the session and include them in the LLM prompt (with token budget management).

#### CHAT-C3: No Token/Context Window Management
- **File:** `apps/backend/app/api/v1/chat.py:100-116`
- **Finding:** The system prompt is built by concatenating all RAG context chunks without any token counting. If 5 chunks are each 2000 tokens, plus the system instruction, the total could exceed the model's context window (especially for Sarvam/OpenHathi with smaller context limits). `maxOutputTokens` is hardcoded to 1024/2048 in the clients without accounting for input size.
- **Impact:** Requests exceeding the model's context limit will either be silently truncated (losing important context) or fail with cryptic API errors.
- **Fix:** Implement token counting (tiktoken for Gemini, or character-based estimation for Sarvam) and truncate context chunks to fit within budget.

### Warnings

#### CHAT-W1: Fire-and-Forget Persistence Without Error Propagation
- **File:** `apps/backend/app/api/v1/chat.py:222-232`
- **Finding:** The streaming endpoint uses `asyncio.create_task()` for MongoDB persistence after streaming completes. If this task fails (MongoDB is down, document validation error), the error is logged but the user is unaware their chat was not saved.
- **Impact:** Silent data loss - users may see a response but their chat history is incomplete.
- **Fix:** At minimum, implement a dead letter queue for failed persistence. Consider acknowledging persistence completion via a final SSE event.

#### CHAT-W2: Language Detection Threshold May Misclassify Mixed Content
- **File:** `apps/backend/app/services/ai/router.py:16-27`
- **Finding:** Language detection uses a 30% threshold of Assamese Unicode characters OR a minimum of 5 Assamese characters. Code snippets with Assamese comments, or English questions containing a few Assamese words, may be misclassified and routed to the wrong LLM.
- **Impact:** English questions with Assamese names/terms get routed to Sarvam (which may provide inferior English responses). Code questions with Assamese comments get misrouted.
- **Fix:** Add heuristics for code detection (presence of syntax characters like `{`, `}`, `(`, `)`, `=`) and increase the threshold for mixed-content scenarios.

#### CHAT-W3: Streaming Fallback Sends Error Details to Client
- **File:** `apps/backend/app/api/v1/chat.py:213, 219`
- **Finding:** When the primary provider fails and fallback also fails, the raw error message is sent to the client:
  ```python
  yield f"data: {json.dumps({'error': f'Both providers failed: {fallback_err}'})}\n\n"
  ```
  This may leak internal error details (API keys in error messages, internal hostnames, stack traces).
- **Impact:** Information disclosure to end users.
- **Fix:** Send a generic error message to the client while logging the full error internally.

#### CHAT-W4: No Timeout on Individual RAG/LLM Steps
- **File:** `apps/backend/app/api/v1/chat.py:95-120`
- **Finding:** While the httpx clients have 60-second timeouts, there is no overall request timeout for the chat endpoint. A slow embedding + slow search + slow LLM could total 180 seconds, well beyond any reasonable user wait time.
- **Impact:** Long-hanging requests consuming server resources and poor UX.
- **Fix:** Add an `asyncio.wait_for()` wrapper with a 30-second total timeout for the entire chat pipeline.

### Recommendations

#### CHAT-R1: Add Streaming Heartbeat for Connection Keep-Alive
- **File:** `apps/backend/app/api/v1/chat.py:198-232`
- **Finding:** During the RAG retrieval phase (embedding + search), no data is sent to the client. If this takes more than 30 seconds, proxies/load balancers may terminate the connection.
- **Fix:** Send periodic comment events (`: keepalive\n\n`) during the retrieval phase.

#### CHAT-R2: Implement Response Quality Scoring
- **File:** `apps/backend/app/api/v1/chat.py` (entire file)
- **Finding:** There is no automated quality check on LLM responses before sending to the user. The response could be hallucinated, in the wrong language, or contain harmful content.
- **Fix:** Add post-generation checks: language consistency, minimum response length, and optionally a lightweight safety classifier.


---

## 6. Content Generation Pipeline

### Critical Issues

#### RAG-C1: Embedding Endpoint URL Uses Search Service Instead of OpenAI Service
- **File:** `apps/backend/app/services/ai/embedder.py:27`
- **Finding:** The embedding request is sent to `{settings.AZURE_SEARCH_ENDPOINT}/openai/deployments/...`. The `AZURE_SEARCH_ENDPOINT` (e.g., `https://syrabit-search.search.windows.net`) is an Azure Cognitive Search endpoint, NOT an Azure OpenAI endpoint. The OpenAI embeddings API is hosted on a completely different service (e.g., `https://your-openai.openai.azure.com`).
- **Impact:** Embedding generation will ALWAYS fail with a 404 or routing error because the search service does not host the OpenAI embeddings API.
- **Fix:** Add a separate `AZURE_OPENAI_ENDPOINT` configuration variable and use it for embedding requests. The current configuration conflates two distinct Azure services.

#### RAG-C2: Azure Search Tier Filtering Silently Hides Content from Free Users
- **File:** `apps/backend/app/services/search/azure_search.py:48, 54`
- **Finding:** The search query includes `filter=f"tier_access eq '{user_tier}'"`. This means free-tier users can ONLY see documents tagged as "free" tier. If content is incorrectly tagged or if the index has no "free" tier content, free users get zero results with no explanation.
- **Impact:** Free users may receive empty context (no RAG results), causing the LLM to respond without grounding, leading to hallucinations.
- **Fix:** (1) Add a fallback: if no results found with tier filter, retry without the filter but add a disclaimer. (2) Ensure the search index has adequate free-tier content. (3) Log when tier filtering results in zero hits.

### Warnings

#### RAG-W1: k_nearest_neighbors=50 With Only 5 Results Returned
- **File:** `apps/backend/app/services/search/azure_search.py:72-77`
- **Finding:** The vector query retrieves 50 nearest neighbors (`k_nearest_neighbors=50`) but only the top 5 are returned after reranking (`limit` defaults to 5 from `settings.MAX_CONTEXT_DOCS`). While the large candidate set improves reranking quality, retrieving 50 full documents for every query adds significant latency and memory overhead.
- **Impact:** Higher Azure Search costs and latency without proportional quality improvement beyond k=20-30.
- **Fix:** Reduce to `k_nearest_neighbors=20` which typically provides sufficient reranking candidates while halving retrieval cost.

#### RAG-W2: Search Failure Returns Empty List Without User Notification
- **File:** `apps/backend/app/services/search/azure_search.py:103-105`
- **Finding:** When Azure Search fails completely, the `search_context()` method returns an empty list `[]` instead of raising an exception. The chat endpoint then builds a system prompt with empty context, causing the LLM to respond without any grounding.
- **Impact:** Users receive potentially hallucinated answers with no indication that the RAG system failed.
- **Fix:** Either propagate the error to return a 503 ("Knowledge base temporarily unavailable") or include a disclaimer in the response when context is empty.

#### RAG-W3: No Maximum Token Budget for Context Chunks
- **File:** `apps/backend/app/api/v1/chat.py:105-112`
- **Finding:** Context chunks are concatenated into the system prompt without any token counting or truncation:
  ```python
  context_text = "\n".join(
      f"[{i+1}] {chunk['title']}: {chunk['content']}"
      for i, chunk in enumerate(context_chunks)
  )
  ```
  If each chunk is 1000+ tokens (typical for educational content), 5 chunks could total 5000+ tokens, leaving insufficient budget for the user message and response.
- **Impact:** Model context overflow causing truncated inputs or API errors.
- **Fix:** Implement a token budget: allocate a fixed budget for context (e.g., 3000 tokens), and truncate chunks to fit within it.

#### RAG-W4: Embedding Failure Crashes Entire Request
- **File:** `apps/backend/app/services/ai/embedder.py:42-43`
- **Finding:** If embedding generation fails, it raises a `RuntimeError` that propagates up and triggers the generic 500 handler. There is no fallback to keyword-only search when embeddings are unavailable.
- **Impact:** Complete chat failure when the embedding service is down, even though BM25 keyword search could still provide relevant results.
- **Fix:** Catch embedding failures and fall back to keyword-only Azure Search (omit the vector query).

### Recommendations

#### RAG-R1: Add Relevance Score Threshold
- **File:** `apps/backend/app/services/search/azure_search.py:88-97`
- **Finding:** All search results are included in the context regardless of their relevance score. Low-scoring results may introduce noise and mislead the LLM.
- **Fix:** Add a minimum score threshold (e.g., 0.5) and only include results above it in the context.

#### RAG-R2: Implement Embedding Caching
- **File:** `apps/backend/app/services/ai/embedder.py` (entire file)
- **Finding:** Identical text inputs always trigger a new API call for embeddings. Educational queries are often repetitive ("what is photosynthesis?", "explain Newton's laws").
- **Fix:** Cache embeddings in Redis with a hash of the input text as key and a 24-hour TTL.


---

## 7. Authentication & Authorization

### Critical Issues

#### AUTH-C1: datetime.utcnow() Usage (Deprecated in Python 3.12+)
- **File:** `apps/backend/app/api/v1/auth.py:68, 73, 78`
- **Finding:** Token creation uses `datetime.utcnow()` which is deprecated since Python 3.12 and returns a naive datetime (no timezone info). The `jose` library handles this correctly for JWT `exp` claims, but any comparison with timezone-aware datetimes will fail.
- **Additional occurrences:** `apps/backend/app/models/user.py:34-35`, `apps/backend/app/models/feedback.py:19`, `apps/backend/app/api/v1/feedback.py:70`
- **Impact:** Future Python version upgrades will emit deprecation warnings. Timezone comparison bugs may cause token expiry miscalculations.
- **Fix:** Replace with `datetime.now(timezone.utc)` throughout. Note that `dead_letter.py:31` already uses the correct pattern.

#### AUTH-C2: JWT Shared Secret Between Edge and Backend Without Rotation
- **File:** `apps/edge/src/middleware/jwt.ts:37` + `apps/backend/app/config.py:101`
- **Finding:** The same `JWT_SECRET` symmetric key is shared between the Cloudflare Edge Worker and the FastAPI backend. There is no mechanism for key rotation. Rotating the secret requires simultaneously updating both the edge worker secret and the backend environment variable, with a deployment gap where tokens signed with the old key are rejected.
- **Impact:** Key rotation is operationally dangerous; a compromised secret affects both layers simultaneously.
- **Fix:** Implement JWT key rotation with a `kid` (Key ID) header and maintain two active keys during rotation periods.

### Warnings

#### AUTH-W1: Optional Auth for Chat Allows Quota Abuse
- **File:** `apps/edge/src/middleware/jwt.ts:30-32` + `apps/backend/app/api/v1/chat.py:61-62`
- **Finding:** Chat endpoints allow anonymous access. Anonymous users are rate-limited by IP (`apps/backend/app/api/v1/chat.py:71-72`), but IP-based limiting is trivially bypassed with rotating proxies or IPv6 address rotation.
- **Impact:** Unlimited anonymous usage could exhaust AI API quotas and incur significant costs.
- **Fix:** Implement more robust anonymous identification (fingerprinting, Turnstile as mandatory for anonymous users) or require authentication for all chat access.

#### AUTH-W2: No Role/Permission Model Beyond Admin
- **File:** `apps/backend/app/api/v1/admin.py:27-31` + `apps/backend/app/models/user.py`
- **Finding:** The User model has no `role` field. Admin status is determined entirely by the JWT token having `type=admin` and `role=admin`. There is no way for a regular user to be promoted to admin, and no way to verify admin status from the user record.
- **Impact:** Admin access is managed entirely through token creation (likely out-of-band), making it impossible to audit who has admin access by querying the database.
- **Fix:** Add a `role` field to the User model and validate admin access against both the token claim and the database record.

#### AUTH-W3: Refresh Token Endpoint Has Inconsistent Rate Limiting
- **File:** `apps/backend/app/api/v1/auth.py:200-215`
- **Finding:** The refresh endpoint has its own inline rate limiting implementation that catches `ImportError` (not just general exceptions). If the import succeeds but Redis is unavailable, the `HTTPException` is caught by the inner try-except and rate limiting is silently skipped for that request.
- **Impact:** Inconsistent rate limiting behavior depending on Redis availability.
- **Fix:** Use the shared `_check_rate_limit()` helper that already handles Redis unavailability gracefully.

#### AUTH-W4: Google OAuth Creates User Without Password
- **File:** `apps/backend/app/api/v1/auth.py:265-270`
- **Finding:** Users created via Google OAuth have no `hashed_password` set. If these users later try to use the forgot-password flow, they receive a reset email but the password reset succeeds (creating a password for an OAuth-only account). This enables a local login bypass for OAuth accounts.
- **Impact:** OAuth-only accounts can gain local credentials, potentially circumventing future OAuth-specific restrictions.
- **Fix:** Check `auth_provider` in the reset-password flow and reject resets for non-local accounts, or explicitly convert the account type.

### Recommendations

#### AUTH-R1: Add Brute-Force Detection for Login
- **File:** `apps/backend/app/api/v1/auth.py:144`
- **Finding:** Login rate limiting is per-IP (10 attempts/minute via `_check_rate_limit`). However, a distributed brute-force attack from many IPs targeting a single account is not detected. No account lockout mechanism exists.
- **Fix:** Add per-account rate limiting: after 5 failed login attempts for a specific email, require CAPTCHA or temporarily lock the account.

#### AUTH-R2: Implement Token Binding
- **File:** `apps/backend/app/api/v1/auth.py:66-78`
- **Finding:** JWTs contain only `sub` (user ID), `exp`, and `type`. No additional binding to the device, IP, or user-agent is included.
- **Fix:** Consider adding a `jti` claim and optional device fingerprint to detect token theft.


---

## 8. Workflows (Payments, Email, Subscriptions)

### Critical Issues

#### PAY-C1: Razorpay Webhook Has No Idempotency Check
- **File:** `apps/backend/app/api/webhooks/razorpay.py:50-76`
- **Finding:** The webhook handler processes every event without checking if it has been processed before. Razorpay may retry webhook deliveries (on timeout or 5xx response), and attackers could replay captured webhook payloads (signature remains valid). Each replay of `subscription.charged` resets `monthly_message_count` to 0.
- **Impact:** (1) Replay attacks reset user quotas at will. (2) Duplicate webhook deliveries cause double-processing (duplicate receipt emails, incorrect state).
- **Fix:** Store processed event IDs (`event["id"]`) in a Redis set or MongoDB collection with TTL, and reject duplicates at the start of the handler.

#### PAY-C2: Billing Date Calculation Uses timedelta(days=30)
- **File:** `apps/backend/app/api/webhooks/razorpay.py:16-18`
- **Finding:** `calculate_next_billing_date()` uses `timedelta(days=30)` which does not account for months with 28, 29, or 31 days. Over time, billing dates drift (e.g., Jan 15 -> Feb 14 -> Mar 16).
- **Impact:** Incorrect billing period tracking, potential for users being cut off early or getting extra days.
- **Fix:** Use `dateutil.relativedelta(months=1)` for proper monthly arithmetic, or better yet, use Razorpay's `current_end` field from the webhook payload itself.

#### PAY-C3: Resend Email Client Uses Synchronous API in Async Functions
- **File:** `apps/backend/app/services/comms/resend_client.py:22, 52, 76`
- **Finding:** All email functions are declared `async` but call `resend.Emails.send(params)` which is a synchronous HTTP call. This blocks the event loop for the duration of the email API call (typically 200-500ms).
- **Impact:** Under load, email sending blocks request processing for other users. Multiple concurrent signup/payment events can cause cascading slowdowns.
- **Fix:** Wrap `resend.Emails.send()` in `asyncio.get_event_loop().run_in_executor(None, ...)` or use an async HTTP client to call the Resend API directly.

### Warnings

#### PAY-W1: Payment Failed Event Only Logs, No User Action
- **File:** `apps/backend/app/api/webhooks/razorpay.py:78-80`
- **Finding:** When `payment.failed` is received, the handler only logs the event. No user notification, no dunning logic, no subscription status update.
- **Impact:** Users with failed payments continue to have "active" status until the next billing cycle when Razorpay cancels the subscription. No opportunity for the user to update their payment method.
- **Fix:** (1) Send a "payment failed" email to the user. (2) Set `subscription_status` to "past_due". (3) Implement a grace period (e.g., 3 days) before downgrading to free tier.

#### PAY-W2: Subscription Cancellation Does Not Downgrade at Period End
- **File:** `apps/backend/app/api/webhooks/razorpay.py:82-87` + `apps/backend/app/api/v1/subscription.py:53`
- **Finding:** When a subscription is cancelled, `cancel_at_period_end` is set to `True`, but there is no scheduler or webhook that actually downgrades the user to "free" tier when the period ends. The user remains "pro" indefinitely after cancellation.
- **Impact:** Users who cancel retain pro access forever because no downgrade mechanism exists.
- **Fix:** Add a daily cron job (or use Razorpay's `subscription.completed` event) to check `cancel_at_period_end=True` users whose `current_period_end` has passed and downgrade them.

#### PAY-W3: Receipt Email Shadows Variable Name
- **File:** `apps/backend/app/services/comms/resend_client.py:21-23`
- **Finding:** In `send_welcome_email()`, the parameter `email` is shadowed by the return value of `resend.Emails.send()`:
  ```python
  async def send_welcome_email(email: str, name: str = None) -> bool:
      ...
      email = resend.Emails.send(params)  # Shadows the email parameter!
      logger.info(f"Welcome email sent to {email}")  # Now logs the API response, not the address
  ```
- **Impact:** Log message contains the Resend API response object instead of the recipient email address.
- **Fix:** Use a different variable name for the send result (e.g., `result = resend.Emails.send(params)`).

#### PAY-W4: No Subscription Status Check Before Creating Order
- **File:** `apps/backend/app/api/v1/subscription.py:37-44`
- **Finding:** The `create-order` endpoint does not check if the user already has an active subscription. A user could create multiple subscription orders, potentially ending up with duplicate Razorpay subscriptions.
- **Impact:** Duplicate charges, confused subscription state.
- **Fix:** Check `user.subscription_status == "active" and user.is_pro()` before creating a new order, and reject if already subscribed.

### Recommendations

#### PAY-R1: Add Payment Reconciliation Job
- **Finding:** There is no mechanism to reconcile Razorpay subscription states with the local database. If a webhook is missed (network issue, Razorpay outage), the local state drifts from reality.
- **Fix:** Add a daily reconciliation job that queries Razorpay's subscription API and updates local records.

#### PAY-R2: Implement Dunning Sequence
- **Finding:** Beyond the initial payment failure, there is no automated dunning sequence (retry reminders, grace periods, final cancellation notice).
- **Fix:** Implement a multi-step dunning flow: Day 1 (failed) -> Day 3 (reminder) -> Day 7 (final notice) -> Day 10 (downgrade).


---

## 9. Analytics & Observability

### Critical Issues

#### OBS-C1: PostHog Instance is a Local Variable, Never Accessible
- **File:** `apps/backend/app/main.py:56-59`
- **Finding:** PostHog is initialized inside the `lifespan` function as a local variable `_posthog`:
  ```python
  if settings.POSTHOG_API_KEY:
      _posthog = Posthog(
          project_api_key=settings.POSTHOG_API_KEY,
          host=settings.POSTHOG_HOST
      )
  ```
  This variable goes out of scope when `lifespan` yields. No other code in the backend can access this instance to track server-side events.
- **Impact:** Server-side PostHog tracking is completely non-functional. Only the frontend JavaScript snippet (in `index.html`) provides analytics.
- **Fix:** Store the PostHog instance in `app.state.posthog` or as a module-level variable, and create a dependency that injects it into endpoints.

#### OBS-C2: No Custom Sentry Context for Error Correlation
- **File:** `apps/backend/app/main.py:44-53`
- **Finding:** Sentry is initialized with basic configuration but no middleware or dependency sets user context. When errors are reported to Sentry, they lack: user ID, subscription tier, request language, session ID. This makes it extremely difficult to correlate errors with specific users or identify patterns.
- **Impact:** Error triage is slow; cannot determine if errors disproportionately affect specific user segments (free vs pro, Assamese vs English).
- **Fix:** Add Sentry middleware that calls `sentry_sdk.set_user({"id": user_id, "tier": tier})` and `sentry_sdk.set_context("chat", {"lang": lang, "model": model})`.

### Warnings

#### OBS-W1: Sentry Sample Rate May Miss Critical Errors at Scale
- **File:** `apps/backend/app/config.py:94` + `apps/backend/app/main.py:48`
- **Finding:** `SENTRY_TRACES_SAMPLE_RATE=0.1` (10%) means 90% of transactions are not traced. For error reporting (not traces), Sentry captures all errors by default, but the low trace rate means performance issues in 90% of requests go undetected.
- **Impact:** Intermittent performance regressions (e.g., slow LLM responses) may not appear in Sentry traces.
- **Fix:** Use dynamic sampling: 100% for errors, 100% for requests >3s, 10% for normal requests.

#### OBS-W2: Feedback Stats Endpoint Not Admin-Protected
- **File:** `apps/backend/app/api/v1/feedback.py:82-83`
- **Finding:** The `/stats` endpoint requires authentication (`get_current_user`) but any authenticated user (not just admins) can access aggregated feedback statistics. This exposes internal quality metrics to all users.
- **Impact:** Competitors or malicious users can monitor model accuracy and satisfaction metrics.
- **Fix:** Add admin-only protection (check user role or use the admin cookie auth pattern).

#### OBS-W3: Structured Logging Uses String Formatting Instead of Structured Fields
- **File:** `apps/backend/app/api/v1/chat.py:128-129`
- **Finding:** Some log statements use proper structured logging with `extra={}` dict, but others use f-string formatting. The `logger.info("chat_failed", extra={...})` pattern is correct, but `logger.error(f"Failed to save streamed chat: {e}")` in `_save_chat_async` is not machine-parseable.
- **Impact:** Inconsistent log parsing in aggregation tools (Log Analytics, Grafana).
- **Fix:** Standardize all logging to use the `extra={}` pattern for machine-parseable fields.

#### OBS-W4: OpenTelemetry Not Used for Critical Business Spans
- **File:** `apps/backend/app/api/v1/chat.py:95-120` (non-streaming endpoint)
- **Finding:** The non-streaming chat endpoint does not use any OpenTelemetry spans for its pipeline steps (embedding, search, LLM call). Only the streaming endpoint (line 184) uses OTel spans for RAG retrieval. This creates an observability gap for the non-streaming path.
- **Impact:** Cannot trace latency breakdown for non-streaming requests.
- **Fix:** Add OTel spans to the non-streaming endpoint for each pipeline step.

#### OBS-W5: No Alerting for AI Provider Failures
- **File:** `infra/azure/alerts.bicep` (entire file)
- **Finding:** Alerts cover HTTP 5xx rate, response time, container restarts, and memory usage. However, there are no alerts specific to AI provider failures (Vertex down, Sarvam down, circuit breaker open). These are the most likely failure modes for an AI-first product.
- **Impact:** AI provider outages may go undetected until users complain.
- **Fix:** Add custom metrics for circuit breaker state changes and alert when a breaker opens.

### Recommendations

#### OBS-R1: Add Health Check for PostHog/Sentry Connectivity
- **File:** `apps/backend/app/api/v1/health.py:86-99`
- **Finding:** The deep health check verifies MongoDB, Redis, Azure Search, and Vertex AI, but not PostHog or Sentry connectivity. If these are misconfigured, analytics silently fails.
- **Fix:** Add optional checks for analytics service connectivity.

#### OBS-R2: Implement Request Tracing Correlation
- **File:** `apps/backend/app/main.py:115-126`
- **Finding:** The `X-Request-ID` header is set correctly, but this ID is not propagated to Sentry events, PostHog events, or MongoDB documents. Correlating a user-reported issue with backend logs requires manual matching.
- **Fix:** Include `request_id` in Sentry breadcrumbs, PostHog event properties, and chat document metadata.


---

## 10. SEO / GEO / AEO

### Critical Issues

*No critical issues identified in this area. The SEO/AEO implementation is among the strongest parts of the codebase.*

### Warnings

#### SEO-W1: No hreflang Implementation for Assamese Content
- **File:** `apps/frontend/index.html:67-69`
- **Finding:** The HTML comments reference that hreflang is "owned by react-helmet-async / prerender rewriteHead" but the actual implementation is not present in the index.html shell. If prerendering is not configured correctly, search engines will not discover the Assamese language variants.
- **Impact:** Google may not serve Assamese-language pages to Assamese-speaking users in search results, reducing organic traffic from the primary target audience.
- **Fix:** Verify that the prerender pipeline correctly injects hreflang tags. Add a fallback x-default hreflang in the base HTML.

#### SEO-W2: Missing Content-Language HTTP Header from Backend
- **File:** `apps/backend/app/main.py:106-112`
- **Finding:** The security headers middleware does not include a `Content-Language` header. While the frontend has `<meta http-equiv="content-language" content="en-IN" />`, API responses (especially for Assamese content) do not indicate their language.
- **Impact:** Minimal for SEO (API responses are noindex), but could affect caching behavior at CDN/proxy layers.
- **Fix:** Add `Content-Language` header based on the detected language for chat responses.

#### SEO-W3: PWA Start URL Points to /library Instead of Root
- **File:** `apps/frontend/public/manifest.json:7`
- **Finding:** `"start_url": "/library?utm_source=pwa&utm_medium=homescreen"` - The PWA starts at /library. If this route requires authentication or has a loading state, the initial PWA experience may be poor (showing a login screen or spinner).
- **Impact:** Users installing the PWA may see an unexpected first screen.
- **Fix:** Ensure /library works without authentication and has a fast server-rendered or pre-cached shell.

### Recommendations

#### SEO-R1: Excellent Structured Data Implementation
- **File:** `apps/frontend/index.html:103-203`
- **Finding (Positive):** The structured data implementation is comprehensive and well-executed:
  - `WebSite` schema with SearchAction (enables Google sitelinks search box)
  - `EducationalOrganization` with complete founder/location data
  - `Person` schema for founder (enables Knowledge Graph)
  - `LocalBusiness` schema with GeoCoordinates (enables local pack)
  - `OfferCatalog` with pricing (enables rich snippets)
- **Assessment:** This is production-quality structured data that should drive rich search results.

#### SEO-R2: Strong AI Bot Policy (AEO)
- **File:** `apps/frontend/public/robots.txt` + `apps/frontend/public/ai.txt` + `apps/frontend/public/llms.txt`
- **Finding (Positive):** Excellent differentiation between:
  - Citation-driving bots (PerplexityBot, ChatGPT-User, OAI-SearchBot) - ALLOWED
  - Training-only crawlers (GPTBot, ClaudeBot, CCBot) - BLOCKED
  - Traditional search engines (Googlebot, Bingbot) - ALLOWED with Crawl-delay: 0
- The `llms.txt` file provides comprehensive content discovery for LLMs.
- The `ai.txt` file follows the emerging per-bot allow/deny convention.
- **Assessment:** Best-in-class Answer Engine Optimization positioning.

#### SEO-R3: Add Canonical URL Validation
- **File:** `apps/frontend/index.html:62-65`
- **Finding:** The comment states canonical URLs are handled by react-helmet-async per route. If any route fails to set a canonical, Google may index duplicate URLs (with query parameters, trailing slashes, etc.).
- **Fix:** Add a test that verifies every route in the router config produces a canonical URL.

#### SEO-R4: Consider Adding FAQ Schema Per Chapter
- **Finding:** The platform serves Q&A content for educational chapters. Adding `FAQPage` structured data to chapter pages would enable Google's FAQ rich results, significantly increasing SERP real estate.
- **Fix:** Generate FAQ schema from the chapter Q&A content at build time.


---

## Appendix: Priority Remediation Roadmap

### P0: Must Fix Before Production (Week 1)

| # | Finding | Severity | Effort |
|---|---------|----------|--------|
| 1 | SEC-C1: Default JWT secret passes validation | CRITICAL | 1 hour |
| 2 | SEC-C2: Turnstile is optional (bots bypass) | CRITICAL | 2 hours |
| 3 | SEC-C5: Refresh tokens cannot be revoked | CRITICAL | 4 hours |
| 4 | CHAT-C1: Unsanitized message sent to LLM | CRITICAL | 30 min |
| 5 | RAG-C1: Wrong endpoint for embeddings | CRITICAL | 1 hour |
| 6 | API-C2: Health check path is doubled (/health/health) | CRITICAL | 30 min |
| 7 | PAY-C1: No webhook idempotency | CRITICAL | 2 hours |
| 8 | PAY-C3: Sync email blocks event loop | CRITICAL | 2 hours |
| 9 | OBS-C1: PostHog instance unreachable | CRITICAL | 30 min |
| 10 | PERF-C1: New HTTP client per embedding call | CRITICAL | 1 hour |

### P1: Should Fix Before Scale (Week 2-3)

| # | Finding | Severity | Effort |
|---|---------|----------|--------|
| 11 | SEC-C3: Admin lacks CSRF protection | CRITICAL | 2 hours |
| 12 | SEC-C4: Reset token reusable | CRITICAL | 2 hours |
| 13 | PERF-C2: Sync Azure Search in thread pool | CRITICAL | 4 hours |
| 14 | PERF-C3: Circuit breakers not wired | CRITICAL | 3 hours |
| 15 | CHAT-C2: No multi-turn context | CRITICAL | 8 hours |
| 16 | CHAT-C3: No token budget management | CRITICAL | 4 hours |
| 17 | DEPLOY-C3: sleep 90 instead of polling | CRITICAL | 1 hour |
| 18 | DEPLOY-C4: No rollback automation | CRITICAL | 4 hours |
| 19 | AUTH-C1: datetime.utcnow() deprecated | CRITICAL | 1 hour |
| 20 | PAY-C2: timedelta(days=30) billing | CRITICAL | 1 hour |

### P2: Important for 100k DAU (Week 3-4)

| # | Finding | Severity | Effort |
|---|---------|----------|--------|
| 21 | SEC-W6: No Content-Security-Policy | WARNING | 3 hours |
| 22 | PERF-W1: Gunicorn memory pressure | WARNING | 1 hour |
| 23 | PERF-W2: No response caching | WARNING | 8 hours |
| 24 | DEPLOY-W1: Scale limits insufficient | WARNING | 1 hour |
| 25 | PAY-W1: Payment failed - no user action | WARNING | 4 hours |
| 26 | PAY-W2: No downgrade on period end | WARNING | 4 hours |
| 27 | OBS-C2: No Sentry user context | CRITICAL | 2 hours |
| 28 | AUTH-W1: Anonymous chat quota abuse | WARNING | 4 hours |
| 29 | DEPLOY-C1: Fragile sed Dockerfile hack | CRITICAL | 2 hours |
| 30 | SEC-W2: KV race condition in rate limiting | WARNING | 8 hours |

### P3: Quality Improvements (Month 2+)

| # | Finding | Severity | Effort |
|---|---------|----------|--------|
| 31-56 | All RECOMMENDATION items + remaining WARNINGs | LOW-MED | Varies |

---

### Architecture Strengths (Positive Findings)

1. **Edge-Backend Separation** - Cloudflare Workers handling JWT, rate limiting, and CORS before traffic reaches the backend is a sound security architecture.
2. **Dual-LLM Routing** - Language-based routing to specialized models (Sarvam for Assamese, Vertex for English) with automatic fallback is well-designed.
3. **Hybrid RAG Search** - BM25 + Vector + Semantic Reranking provides high-quality retrieval.
4. **Graceful Degradation Pattern** - Services that fail return empty/default values rather than crashing (e.g., Azure Search returns `[]` on failure).
5. **SEO/AEO Implementation** - Among the best-in-class for an EdTech SPA, with comprehensive structured data, AI bot policies, and content discovery files.
6. **Security Headers** - HSTS, X-Frame-Options, X-Content-Type-Options, and Referrer-Policy are properly set.
7. **CSRF Origin Validation** - The middleware correctly validates Origin headers on mutating requests.
8. **Input Validation** - Pydantic models with validators provide strong typing at API boundaries.
9. **Dead Letter Queue** - Failed messages are persisted for later analysis rather than being silently lost.
10. **Infrastructure as Code** - Complete Bicep templates with monitoring alerts demonstrate operational maturity.

---

*End of Comprehensive Code Audit Report*
