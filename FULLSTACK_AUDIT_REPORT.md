# Syrabit Full-Stack Security and Architecture Audit Report

**Date:** 2025-01-16
**Scope:** Backend (Python/FastAPI), Frontend (React/Vite), Edge (Cloudflare Workers)
**Version:** 3.0.0

---

## 1. Executive Summary

### Overall Health Assessment

The Syrabit platform demonstrates a **well-architected** production system with strong security fundamentals, clear separation of concerns, and production-grade deployment patterns. However, several medium-to-high severity issues require attention before scaling.

### Risk Rating: **MEDIUM-HIGH**

The single JWT secret used across all token types, the auth rate limiter silently degrading on Redis failure, and the unmaintained `python-jose` dependency represent the highest-priority risks.

### Summary of Findings

| Category | Critical | High | Medium | Low | Info |
|----------|----------|------|--------|-----|------|
| Security | 0 | 3 | 4 | 2 | 2 |
| Architecture | 0 | 0 | 2 | 1 | 2 |
| Code Quality | 0 | 0 | 2 | 3 | 1 |
| Dependencies | 0 | 1 | 2 | 1 | 0 |
| Testing | 0 | 1 | 2 | 1 | 0 |
| CI/CD | 0 | 1 | 2 | 1 | 1 |
| Configuration | 0 | 0 | 1 | 2 | 1 |
| Performance | 0 | 0 | 1 | 2 | 2 |
| **Total** | **0** | **6** | **16** | **13** | **9** |

---

## 2. Security Audit

### 2.1 Authentication

#### JWT Implementation
- **Library:** `python-jose` with HS256 algorithm
- **File:** `apps/backend/app/api/v1/auth.py` (lines 88-108)

**Findings:**

| # | Finding | Severity | Detail |
|---|---------|----------|--------|
| SEC-01 | Single JWT_SECRET for all token types | **HIGH** | Access tokens, refresh tokens, reset tokens, and admin session tokens all use the same `settings.JWT_SECRET` (see `create_access_token`, `create_refresh_token`, `create_reset_token` in `auth.py` and admin token minting in `admin.py` line 79). Compromise of any token type grants ability to forge all other types. |
| SEC-02 | Token blacklisting via Redis (good) | INFO | Access tokens are blacklisted on logout using SHA-256 hash with TTL matching token expiry (`auth.py` lines 298-310). |
| SEC-03 | Refresh token rotation with JTI revocation (good) | INFO | Each refresh generates a new token and revokes the old JTI (`auth.py` lines 248-270). |

**Password Security:**
- Bcrypt hashing via `bcrypt.hashpw` with auto-generated salt (`models/user.py` lines 56-62) - **Good**
- Password validation enforces 8+ chars, uppercase, lowercase, digit (`auth.py` lines 34-42) - **Good**

### 2.2 Authorization

| # | Finding | Severity | Detail |
|---|---------|----------|--------|
| SEC-04 | Admin role is a simple string comparison | **MEDIUM** | Admin access is checked via `user.role != "admin"` in `admin.py` line 80 and `payload.get("role") != "admin"` in `_validate_admin_session`. No RBAC framework, no permission granularity. Any user with `role="admin"` has full access to all admin endpoints. |

### 2.3 Input Validation

| # | Finding | Severity | Detail |
|---|---------|----------|--------|
| SEC-05 | Prompt injection filters are easily bypassed | **HIGH** | `sanitize_user_input` in `core/security.py` uses 7 hardcoded regex patterns (lines 26-34). These can be trivially bypassed via: Unicode homoglyphs (e.g., "Ignоre" with Cyrillic "о"), zero-width characters between words, mixed case not covered by patterns (e.g., "SYSTEM:" is caught but variations with whitespace/formatting are not), or novel prompt injection patterns not in the list. |
| SEC-06 | Message length cap at 4000 chars | LOW | `security.py` line 40 caps at 4000 chars. The feature spec mentions 2000 but the actual code uses 4000. This is adequate but should be documented. |

### 2.4 SSRF Protection

| # | Finding | Severity | Detail |
|---|---------|----------|--------|
| SEC-07 | Synchronous DNS resolution in is_safe_url | **MEDIUM** | `core/security.py` lines 78-89 use `socket.getaddrinfo()` which is blocking and synchronous in an async framework. This can: (a) block the event loop causing DoS, (b) be exploited for DNS rebinding attacks since validation and actual connection use separate resolutions. The code's own comment (line 76) acknowledges this: "In production, use async DNS resolution with timeout". |

### 2.5 CORS and CSRF

| # | Finding | Severity | Detail |
|---|---------|----------|--------|
| SEC-08 | CORS properly configured with allowlist (good) | INFO | `main.py` lines 113-123 restrict origins to `settings.allowed_origins_list`. Production mode strips localhost entries (`config.py` lines 141-145). |
| SEC-09 | CSRF origin check middleware present (good) | INFO | `main.py` lines 126-138 validates Origin header on POST/PUT/DELETE requests. Admin endpoints have additional CSRF validation (`admin.py` lines 26-31). |

### 2.6 Secrets Management

| # | Finding | Severity | Detail |
|---|---------|----------|--------|
| SEC-10 | .env.shared uses placeholder values (acceptable) | LOW | `.env.shared` contains `CHANGE_ME_IN_PRODUCTION_AT_LEAST_32_CHARS_LONG` placeholder. Production validation in `config.py` lines 117-123 rejects known placeholders and enforces min 32 chars. |

### 2.7 Webhook Security

| # | Finding | Severity | Detail |
|---|---------|----------|--------|
| SEC-11 | Razorpay HMAC-SHA256 with constant-time comparison (good) | INFO | `webhooks/razorpay.py` lines 46-55 computes expected signature and uses `hmac.compare_digest()` to prevent timing attacks. Idempotency check via Redis prevents replay (`razorpay.py` lines 57-65). |

### 2.8 Rate Limiting

| # | Finding | Severity | Detail |
|---|---------|----------|--------|
| SEC-12 | Auth rate limiter silently skips on Redis failure | **HIGH** | `_check_rate_limit` in `auth.py` lines 166-185 has `except Exception: pass` which means if Redis is unavailable, rate limiting is completely disabled. An attacker who causes Redis to become unreachable (e.g., connection exhaustion) can then brute-force credentials without limit. |
| SEC-13 | IP-based auth rate limiting (good) | LOW | Per-endpoint, per-IP, per-minute buckets with configurable max attempts (login: 10, signup: 5, admin_login: 5). |

### 2.9 Admin Authentication

| # | Finding | Severity | Detail |
|---|---------|----------|--------|
| SEC-14 | Admin cookie is httponly + SameSite=strict (good) | INFO | `admin.py` lines 88-95 sets `httponly=True`, `secure=True`, `samesite="strict"`, with path scoped to `/api/v1/admin`. |
| SEC-15 | Admin token uses same JWT_SECRET as user tokens | **MEDIUM** | Admin session tokens are signed with the same `settings.JWT_SECRET` (`admin.py` line 79). Although they include a `type: "admin"` claim, a leaked user JWT secret enables forging admin sessions. This is a consequence of SEC-01. |

---

## 3. Architecture Audit

### 3.1 Layered Architecture

| # | Finding | Severity | Detail |
|---|---------|----------|--------|
| ARCH-01 | Clean separation of concerns (good) | INFO | Backend follows `api/core/db/models/services` layering. Service clients are isolated (`services/ai/`, `services/payment/`, `services/search/`). |
| ARCH-02 | Proper async/await throughout | INFO | All I/O operations use async patterns. httpx async clients, Motor async MongoDB driver, and Upstash async Redis. |

### 3.2 Edge-to-Backend Flow

| # | Finding | Severity | Detail |
|---|---------|----------|--------|
| ARCH-03 | Double JWT verification (edge AND backend) | LOW | JWT is verified at the edge (`edge/src/middleware/jwt.ts`) and again in the backend (`auth.py` `get_current_user`). This is redundant but provides defense-in-depth. The edge injects `X-User-ID` header but the backend does not trust it, re-validating the token. |

### 3.3 Resilience Patterns

| # | Finding | Severity | Detail |
|---|---------|----------|--------|
| ARCH-04 | Circuit breaker for AI providers (good) | INFO | `core/circuit_breaker.py` implements circuit breaking for Vertex AI and Sarvam calls. |
| ARCH-05 | Graceful degradation (good) | INFO | Search returns empty list on failure. Sarvam client falls back to Vertex on failure. Services start with warnings if dependencies are unavailable. |

### 3.4 Code Organization

| # | Finding | Severity | Detail |
|---|---------|----------|--------|
| ARCH-06 | chat.py is oversized at 714 lines (27KB) | **MEDIUM** | `apps/backend/app/api/v1/chat.py` at 714 lines contains streaming and non-streaming endpoints, message processing, context assembly, rate limit checking, and response formatting all inline. Should be refactored into a chat service layer. |
| ARCH-07 | Singleton pattern with shutdown hooks (good) | INFO | Service clients (vertex_client, sarvam_client, razorpay_client) use singleton pattern with proper cleanup in the lifespan shutdown (`main.py` lines 88-94). |

### 3.5 Frontend Architecture

| # | Finding | Severity | Detail |
|---|---------|----------|--------|
| ARCH-08 | AuthContext.jsx is overly complex | **MEDIUM** | At 335 lines with complex side effects (anonymous data claiming, ads configuration, token refresh), this file is difficult to test and maintain. Should be split into auth logic, token management, and side-effect hooks. |

---

## 4. Code Quality

### 4.1 Backend

| # | Finding | Severity | Detail |
|---|---------|----------|--------|
| CQ-01 | Consistent async patterns and error handling | INFO | Proper try/except with logging, HTTPException propagation, graceful degradation. |
| CQ-02 | Incomplete type hints | **MEDIUM** | `Optional` used broadly (e.g., `models/user.py` has `Optional[str]` for role with no enum constraint). Some functions lack return type annotations. Type safety could be improved with stricter types. |
| CQ-03 | datetime.utcnow usage (deprecated) | **MEDIUM** | `models/user.py` lines 32-33 use `Field(default_factory=datetime.utcnow)` which is deprecated in Python 3.12+. Other parts of the codebase correctly use `datetime.now(timezone.utc)` (e.g., `auth.py` line 90). This inconsistency should be resolved. |
| CQ-04 | Test dependencies in requirements.in | LOW | `requirements.in` includes `pytest`, `pytest-asyncio`, and `pytest-cov` as runtime dependencies rather than dev dependencies. These get installed in the production Docker image unnecessarily. |
| CQ-05 | Edge worker well-structured | INFO | Clear middleware pipeline (`cors` -> `jwt` -> `bot` -> `rate-limit` -> `routing`) with single responsibility modules. |

### 4.2 Frontend

| # | Finding | Severity | Detail |
|---|---------|----------|--------|
| CQ-06 | In-memory token storage with sessionStorage backup | LOW | `AuthContext.jsx` uses module-level variables (`_inMemoryToken`, `_inMemoryRefreshToken`) with sessionStorage persistence. This pattern is acceptable but makes testing difficult due to module-level state. |
| CQ-07 | Fire-and-forget analytics calls wrapped in try/catch | LOW | Analytics calls are properly wrapped to prevent UI breakage (`AuthContext.jsx` lines 200, 224). |

---

## 5. Dependency Audit

### 5.1 Backend

| # | Finding | Severity | Detail |
|---|---------|----------|--------|
| DEP-01 | python-jose is unmaintained | **HIGH** | `python-jose` has not been maintained since 2022 and has known CVEs. Should migrate to `PyJWT` (already in requirements.in) or `authlib`. The project includes both `pyjwt` and `python-jose` in `requirements.in` which is redundant. |
| DEP-02 | requirements.txt with pinned hashes (good) | INFO | 173KB lockfile with SHA-256 hashes for reproducible builds. |
| DEP-03 | Dockerfile has fragile pytest-asyncio workaround | **MEDIUM** | `Dockerfile` lines 14-17 strip `pytest-asyncio==0.26.0` from requirements and install a compatible version separately. This is fragile and will break if requirements.txt changes. Test dependencies should not be in the production image at all. |

### 5.2 Frontend

| # | Finding | Severity | Detail |
|---|---------|----------|--------|
| DEP-04 | TypeScript 6.0.3 does not exist | **MEDIUM** | `apps/frontend/package.json` lists `"typescript": "~6.0.3"`. TypeScript 6.x has not been released (latest stable is 5.x as of early 2025). This is likely a misconfiguration or a future-dated pre-release that may cause unexpected behavior. |
| DEP-05 | No dependabot or renovate config | LOW | No automated dependency update mechanism found in the repository. Manual dependency management increases risk of missing security patches. |

### 5.3 Edge

| # | Finding | Severity | Detail |
|---|---------|----------|--------|
| DEP-06 | Edge devDependencies are reasonable | INFO | Wrangler, TypeScript, and vitest versions are current and appropriate. |

---

## 6. Test Coverage Audit

### 6.1 Backend Tests

| # | Finding | Severity | Detail |
|---|---------|----------|--------|
| TEST-01 | Reasonable test breadth | INFO | 10 test files covering: security, auth, chat, circuit breaker, webhooks, translator, knowledge, admin, SEO, IndexNow (`apps/backend/tests/`). |
| TEST-02 | No coverage measurement in CI | **HIGH** | `pytest-cov` is in `requirements.in` but neither `ci-backend.yml` nor `deploy-all.yml` runs pytest with `--cov` flags. No coverage thresholds enforced. Regressions can merge undetected. |
| TEST-03 | Load testing scaffold present | LOW | `locustfile.py` exists in tests directory, indicating performance testing awareness. |

### 6.2 Frontend Tests

| # | Finding | Severity | Detail |
|---|---------|----------|--------|
| TEST-04 | Frontend has 28 test files | INFO | Good coverage across pages (accessibility, navigation, form handling), utilities, and hooks. Includes axe accessibility tests for key pages. |
| TEST-05 | No AuthContext unit tests | **MEDIUM** | The 335-line `AuthContext.jsx` with complex refresh logic has no dedicated unit test. Token refresh flow, error handling, and side effects are untested. |

### 6.3 Edge Tests

| # | Finding | Severity | Detail |
|---|---------|----------|--------|
| TEST-06 | Only 1 edge test file | **MEDIUM** | `apps/edge/tests/jwt.test.ts` covers JWT verification only. Rate limiting, CORS, bot detection, and proxy routing logic are untested. |

### 6.4 Missing Test Coverage

Key untested areas:
- Payment subscription lifecycle (create, charge, cancel, failed payment)
- AI service error handling and circuit breaker state transitions
- Rate limiter edge cases (Redis failure, counter overflow, month boundary)
- Password reset token single-use enforcement
- Admin session cookie lifecycle

---

## 7. CI/CD Audit

### 7.1 Deployment Pipeline

| # | Finding | Severity | Detail |
|---|---------|----------|--------|
| CICD-01 | Canary deploy with smoke tests and auto-rollback (excellent) | INFO | `deploy-all.yml` implements: quality gates -> canary deploy -> full deploy -> smoke tests -> automatic rollback on failure. This is production-grade. |
| CICD-02 | --no-frozen-lockfile defeats lockfile integrity | **HIGH** | All CI jobs use `pnpm install --no-frozen-lockfile` (e.g., `deploy-all.yml` line 33, `ci-frontend.yml` line 24). This means the lockfile can drift between CI runs, and a compromised/updated transitive dependency could slip in without review. Should use `--frozen-lockfile` in CI. |
| CICD-03 | No SAST/DAST scanning | **MEDIUM** | No security scanning tools (bandit, semgrep, trivy, snyk) are run in CI. The bandit config may exist but is not integrated into any workflow. |
| CICD-04 | No container image scanning | **MEDIUM** | Docker images are built and pushed to ACR without vulnerability scanning (`deploy-all.yml` lines 104-112). Should add trivy or Azure Defender scan before deployment. |
| CICD-05 | Secrets via env vars in container update command | LOW | `deploy-all.yml` lines 115-126 pass secrets as `--set-env-vars` CLI arguments. These are masked in logs via `::add-mask::` (lines 73-100) but could still appear in Azure activity logs or CLI error output. Consider using secret references to KeyVault directly from Container Apps configuration. |

### 7.2 CI Workflows

| # | Finding | Severity | Detail |
|---|---------|----------|--------|
| CICD-06 | Backend and frontend CI properly gated | INFO | `ci-backend.yml` and `ci-frontend.yml` trigger on path-specific changes and use `workflow_dispatch` for manual runs. Backend CI includes lint (ruff) and type check (mypy). |

---

## 8. Configuration and Deployment

### 8.1 Docker

| # | Finding | Severity | Detail |
|---|---------|----------|--------|
| CFG-01 | Multi-stage build, non-root user (good) | INFO | `Dockerfile` uses builder stage for compilation, final stage runs as `appuser` (UID 1000). No unnecessary tools in production image. |
| CFG-02 | Gunicorn config is sensible | INFO | `gunicorn_conf.py`: 2 workers for 1Gi container, 30s timeout, `max_requests=1000` with `max_requests_jitter=50` prevents memory leaks. |

### 8.2 Cloudflare Workers

| # | Finding | Severity | Detail |
|---|---------|----------|--------|
| CFG-03 | ISR_CACHE_KV has placeholder ID | **MEDIUM** | `wrangler.toml` line 39: `id = "placeholder-isr-cache-kv-id"`. If deployed to production without replacement, the ISR caching feature will fail. The RATE_LIMIT_KV has a real ID, so this appears to be an incomplete setup for a newer feature. |

### 8.3 Local Development

| # | Finding | Severity | Detail |
|---|---------|----------|--------|
| CFG-04 | docker-compose.yml uses hardcoded passwords | LOW | `localdevpassword` for MongoDB and Redis. Acceptable for local development since the compose file is not used in production. |
| CFG-05 | Upstash REST API proxy for local dev (good) | INFO | `docker-compose.yml` includes `hiett/serverless-redis-http` to simulate Upstash locally, avoiding the need for a remote Redis during development. |

### 8.4 Environment Validation

| # | Finding | Severity | Detail |
|---|---------|----------|--------|
| CFG-06 | Production secrets validated at startup (good) | INFO | `config.py` `validate_production_secrets` method (lines 113-140) blocks startup with placeholder JWT_SECRET and warns about missing optional services. |
| CFG-07 | Empty-string-to-None conversion (good) | LOW | `config.py` line 108 ensures environment variables that are empty strings become None, preventing silent failures with Optional fields. |

---

## 9. Performance

### 9.1 Timeouts and Limits

| # | Finding | Severity | Detail |
|---|---------|----------|--------|
| PERF-01 | 30-second Gunicorn timeout (good) | INFO | `gunicorn_conf.py` line 7: `timeout = 30`. Prevents hanging requests from consuming workers indefinitely. Aligns with chat streaming use case. |
| PERF-02 | Connection pooling configured | INFO | MongoDB: max 50, min 10 connections (`config.py` lines 53-54). httpx clients use connection pooling with 20 max connections per service. |

### 9.2 Memory Management

| # | Finding | Severity | Detail |
|---|---------|----------|--------|
| PERF-03 | Worker recycling prevents leaks (good) | INFO | `max_requests=1000` with `max_requests_jitter=50` (`gunicorn_conf.py` lines 9-10) ensures workers are recycled before memory can accumulate. |

### 9.3 Caching

| # | Finding | Severity | Detail |
|---|---------|----------|--------|
| PERF-04 | No caching for repeated search queries | **MEDIUM** | Azure Search results are fetched fresh on every request. High-traffic queries (common educational topics) could benefit from a short-lived cache (5-10 minutes) in Redis. |
| PERF-05 | Conversation history loaded per-request | LOW | Each chat request loads the full conversation history from MongoDB. For long conversations, this could become a performance bottleneck. Consider caching recent messages in Redis with TTL. |
| PERF-06 | Frontend uses React Query (good) | INFO | `@tanstack/react-query` handles client-side caching, deduplication, and background refetching. |

### 9.4 Edge Performance

| # | Finding | Severity | Detail |
|---|---------|----------|--------|
| PERF-07 | Rate limit KV operations are lightweight | INFO | Edge rate limiting uses Cloudflare KV with hourly windows and 2-hour TTL for automatic cleanup (`rate-limit.ts` lines 40-50). |
| PERF-08 | Chat body parsed twice at edge | LOW | The edge worker clones and parses the request body to extract `lang` for rate limiting (`index.ts` lines 72-78). This adds latency to every chat request. Consider extracting lang from a header or query param instead. |

---

## 10. Prioritized Remediation Plan

| Priority | Finding | Risk | Effort | Recommendation |
|----------|---------|------|--------|---------------|
| **P0** | SEC-12: Rate limiter fails open on Redis error | Auth brute-force possible if Redis is down | Small | Change `except Exception: pass` to `except Exception: raise HTTPException(503)` (fail-closed), or at minimum log a critical alert. Match the pattern used in `get_current_user` which already raises 503 on Redis failure. |
| **P0** | SEC-01/SEC-15: Single JWT secret for all token types | Token type confusion, privilege escalation | Medium | Derive per-purpose keys from master secret (e.g., `HMAC(JWT_SECRET, "access")`, `HMAC(JWT_SECRET, "refresh")`, `HMAC(JWT_SECRET, "admin")`). This requires coordinating the change with the edge worker which shares the same secret. |
| **P0** | DEP-01: python-jose is unmaintained | Known CVEs, no patches | Medium | Migrate to `PyJWT` (already in requirements.in). Update all `from jose import jwt` to `import jwt`. Remove `python-jose` from requirements.in. Audit the edge worker since it has its own HS256 implementation. |
| **P1** | CICD-02: --no-frozen-lockfile in CI | Supply chain attack vector | Small | Replace with `--frozen-lockfile` in all CI workflows. Run `pnpm install` locally once to ensure lockfile is up-to-date, commit it. |
| **P1** | TEST-02: No coverage measurement in CI | Regressions merge undetected | Small | Add `--cov=app --cov-report=term --cov-fail-under=60` to pytest in `ci-backend.yml`. Add coverage report as PR comment via action. |
| **P1** | SEC-05: Prompt injection filters trivially bypassed | LLM manipulation, data exfiltration | Large | Replace regex-based filtering with a layered approach: (1) LLM-based classifier for injection detection, (2) output filtering, (3) system prompt hardening. Consider a dedicated prompt firewall service. |
| **P1** | CICD-03: No SAST scanning in CI | Vulnerabilities merge unreviewed | Small | Add `bandit -r apps/backend/app/ -ll` and `semgrep --config=auto` steps to `ci-backend.yml`. |
| **P2** | ARCH-06: chat.py is 714 lines | Maintenance burden, test difficulty | Medium | Extract into `ChatService` class with separate methods for streaming, non-streaming, context assembly, and rate checking. Keep the router thin. |
| **P2** | DEP-04: TypeScript 6.0.3 does not exist | Build instability, unclear behavior | Small | Pin to a real TypeScript version (e.g., `"typescript": "~5.7.0"`). Verify builds pass with the corrected version. |
| **P2** | SEC-07: Synchronous DNS in is_safe_url | Event loop blocking, DNS rebinding | Medium | Replace `socket.getaddrinfo` with `asyncio.get_event_loop().getaddrinfo()`. Add a timeout of 3 seconds. Implement TOCTOU protection by resolving DNS once and using the resolved IP for the actual connection. |
| **P2** | CQ-03: datetime.utcnow deprecated | Python 3.12+ deprecation warning | Small | Replace `datetime.utcnow` with `datetime.now(timezone.utc)` in `models/user.py` lines 32-33. |
| **P2** | CICD-04: No container image scanning | Vulnerable base images in production | Small | Add `trivy image` scan step after ACR build in `deploy-all.yml`. Fail on HIGH/CRITICAL findings. |
| **P2** | DEP-03: Fragile Dockerfile pytest workaround | Image build breaks on dep changes | Medium | Remove test dependencies from `requirements.in`. Create `requirements-dev.in` for test/lint deps. Update Dockerfile to only install production requirements. |
| **P2** | CFG-03: Placeholder ISR_CACHE_KV ID | ISR feature broken in production | Small | Create the KV namespace via `wrangler kv:namespace create ISR_CACHE_KV` and update the ID in `wrangler.toml`. |
| **P2** | PERF-04: No search result caching | Unnecessary Azure Search calls, latency | Medium | Add Redis caching with 5-10 minute TTL for search results keyed by query hash. Invalidate on content publish. |
| **P3** | TEST-06: Edge worker has only 1 test | Low confidence in edge routing | Medium | Add tests for rate-limit.ts, cors.ts, bot detection, and the proxy routing logic in index.ts. |
| **P3** | ARCH-08: AuthContext.jsx is 335 lines | Hard to maintain, hard to test | Medium | Extract token management to `useTokenManager` hook, side effects to `useAnonymousSync` hook, and ads config to `useAdsSync` hook. |
| **P3** | CQ-04: Test deps in production image | Larger attack surface, wasted space | Small | Move pytest/pytest-asyncio/pytest-cov to a separate `requirements-dev.in` file. |
| **P3** | DEP-05: No automated dependency updates | Missed security patches | Small | Add `.github/dependabot.yml` with weekly update schedules for pip, npm, and GitHub Actions. |
| **P3** | SEC-04: No granular RBAC | All admins have equal access | Large | Implement permission-based access control with roles (super_admin, content_admin, viewer) if admin team grows beyond 2-3 people. |
| **P3** | PERF-05: Conversation history not cached | Latency on long conversations | Medium | Cache last N messages in Redis with 30-minute TTL. Invalidate on new message. |
| **P3** | CICD-05: Secrets in CLI env-var args | Potential exposure in error logs | Medium | Use Azure Container Apps secret references pointing directly to KeyVault instead of passing values via CLI. |

---

## Appendix: Files Referenced

| File | Purpose |
|------|---------|
| `apps/backend/app/main.py` | Application factory, middleware, lifespan |
| `apps/backend/app/config.py` | Pydantic settings, validation |
| `apps/backend/app/core/security.py` | Input sanitization, SSRF protection |
| `apps/backend/app/core/rate_limiter.py` | Token bucket rate limiter |
| `apps/backend/app/api/v1/auth.py` | JWT auth, login, signup, password reset |
| `apps/backend/app/api/v1/chat.py` | Chat endpoints (714 lines) |
| `apps/backend/app/api/v1/admin.py` | Admin auth via httponly cookie |
| `apps/backend/app/api/webhooks/razorpay.py` | Payment webhook handling |
| `apps/backend/app/db/mongo.py` | MongoDB connection, indexes |
| `apps/backend/app/db/redis.py` | Upstash Redis connection |
| `apps/backend/app/models/user.py` | User schema, bcrypt |
| `apps/backend/Dockerfile` | Multi-stage build |
| `apps/backend/gunicorn_conf.py` | Worker configuration |
| `apps/backend/requirements.in` | Direct dependencies |
| `apps/edge/src/index.ts` | Edge worker entry |
| `apps/edge/src/middleware/jwt.ts` | Edge JWT verification |
| `apps/edge/src/middleware/cors.ts` | CORS middleware |
| `apps/edge/src/middleware/rate-limit.ts` | KV rate limiting |
| `apps/edge/wrangler.toml` | Worker config |
| `apps/frontend/package.json` | Frontend dependencies |
| `apps/frontend/src/context/AuthContext.jsx` | Auth state management |
| `.env.shared` | Environment template |
| `docker-compose.yml` | Local development |
| `.github/workflows/deploy-all.yml` | Full deployment pipeline |
| `.github/workflows/ci-backend.yml` | Backend CI |
| `.github/workflows/ci-frontend.yml` | Frontend CI |
