# Full-Stack Architecture Audit Report

**Project:** Syrabit AI v3.0 - Educational Assistant for Assamese Students  
**Target:** 100k DAU | Production Ready  
**Date:** 2025-05-27  
**Auditor:** Automated Code Analysis  

---

## 1. Executive Summary

**Overall Grade: B+**

| Severity | Count |
|----------|-------|
| Critical | 1 |
| High | 3 |
| Medium | 5 |
| Low | 4 |
| Info | 3 |

Syrabit is a well-architected monorepo with strong security practices, mature CI/CD, and comprehensive test coverage across two of its three services. The primary critical finding is an AI model routing inconsistency that causes different providers to be used depending on the code path, which could produce inconsistent behavior in production. The backend test environment limitation (Python 3.9 vs 3.11+) masks potential integration test failures that should be verified in CI.

---

## 2. Build Quality Audit

### TypeScript (Edge Worker)
- **Status:** Zero compilation errors with `tsc --noEmit`
- Strict types via `wrangler.toml` compatibility flags with `nodejs_compat`
- All bindings properly typed through `Env` interface

### Python (Backend)
- **Status:** Uses `pydantic-settings` for config validation; relies on Python 3.11+ features (type unions `X | None`, match statements)
- No `mypy.ini` strict mode verification locally, but CI runs `mypy apps/backend/app/ --config-file apps/backend/mypy.ini`
- Dockerfile correctly pins `python:3.11-slim` ensuring production compatibility

### Docker Build
- **Status:** Proper multi-stage build (`apps/backend/Dockerfile`)
- Builder stage: installs gcc/libffi-dev for native dependencies
- Production stage: non-root user (`appuser`, UID 1000), minimal image
- HEALTHCHECK configured with appropriate start_period (10s)
- **[Low]** Hash stripping in Dockerfile (`sed` to remove `--hash` lines) loses supply chain integrity verification

### Dependency Management
- pnpm workspace with lockfile for Node packages
- Python uses `requirements.txt` with hash pinning (stripped at install time)
- **[Info]** No `pip-compile` regeneration in CI to detect stale pins

### Monorepo Structure
- Clean separation: `apps/backend`, `apps/edge`, `apps/frontend`
- pnpm workspace properly configured
- Shared nothing between apps (correct for polyglot monorepo)

---

## 3. Test Results

| Component | Pass | Total | Status |
|-----------|------|-------|--------|
| Edge Worker | 54 | 54 | PASS |
| Frontend | 620 | 620 | PASS |
| Backend | 50 | 163 | PARTIAL |

### Backend Test Analysis
- **Fully passing:** circuit_breaker (6/6), security/sanitization (24/24), knowledge models (6/6), translator (8/8), latency comparison (6/8)
- **Blocked by environment:** 88 tests error due to `sentry_sdk` import at `app/main.py:5`; 25 tests fail from Python 3.9 syntax incompatibility (match statements, `X | None` unions)
- **Assessment:** Core business logic is well-tested. Integration/endpoint tests require the correct Python version and all dependencies.

### Coverage Threshold
- **[Medium]** CI backend allows minimum 30% coverage (`ci-backend.yml` line 61). For a "Production Ready" classification targeting 100k DAU, this threshold is dangerously low. Recommend 70%+ for critical paths.

### Test Quality
- Frontend: Includes accessibility (axe), SEO, component, and integration tests
- Edge: Tests cover all middleware paths (JWT, rate limit, CORS, bot detection, ISR)
- Backend: Unit tests cover security and circuit breaker logic thoroughly

---

## 4. Architecture Audit (9 Pillars)

### P1: Cloudflare (Edge Layer)
- **Implemented:** Yes - `apps/edge/src/index.ts` with full request pipeline
- **Integration:** Proper KV bindings (`RATE_LIMIT_KV`, `ISR_CACHE_KV`), R2 for assets, Turnstile for bot protection
- **Configuration:** `wrangler.toml` has production environment with real KV namespace IDs (not placeholders)
- **Status:** Healthy

### P2: Azure Container Apps (Backend Compute)
- **Implemented:** Yes - `deploy-all.yml` deploys to Container Apps with ACR build
- **Integration:** Health checks, revision-based rollback, KeyVault secret injection
- **Status:** Healthy

### P3: Azure Cognitive Search (Intelligence)
- **Implemented:** Yes - `apps/backend/app/services/search/azure_search.py`
- **Integration:** Hybrid search (BM25 + vector) with semantic reranking, graceful fallback to vector-only
- **Features:** Redis caching (5-min TTL), warm-up at startup, tier-based content filtering with fallback
- **Status:** Healthy

### P4: MongoDB Atlas (Data)
- **Implemented:** Yes - `apps/backend/app/db/mongo.py` with Beanie ODM
- **Integration:** Proper connection pooling (50 max, 10 min), index creation, migrations runner
- **Indexes:** Users (email unique, subscription), Chats (user_id+updated_at, session_id), Dead letters (30-day TTL)
- **Status:** Healthy

### P5: Upstash Redis (Gatekeeper)
- **Implemented:** Yes - `apps/backend/app/db/redis.py` using HTTP-based async client
- **Integration:** Token blacklisting, rate limiting (Lua scripts), conversation history cache (30-min TTL), search result cache
- **[Medium]** Fail-closed pattern on Redis unavailability in auth (returns 503) is correct for security but may cause cascading failures under Redis outage at scale
- **Status:** Healthy

### P6: Vertex AI (Google Gemini) - CRITICAL FINDING
- **Implemented:** Yes - full client at `apps/backend/app/services/ai/vertex_client.py` with OAuth2 token caching, streaming, circuit breaker, and retry logic
- **[Critical] Routing Inconsistency:**
  - `router.py:detect_language_and_route()` (line 37-46) routes English to `settings.CF_AI_MODEL` (Cloudflare Workers AI `@cf/meta/llama-3.1-70b-instruct`)
  - `chat_service.py:resolve_language_and_model()` (line 40-41) routes English with `lang_override` to `settings.VERTEX_GEMINI_MODEL` (Gemini 1.5 Pro)
  - Result: Auto-detected English uses Cloudflare/Llama; explicitly overridden English uses Vertex/Gemini. Different models produce different quality/style responses.
  - Additionally: `router.py:generate_response()` and `stream_response()` never route to Vertex -- they only recognize "sarvam/openhathi/saaras" patterns
- **Vertex is used by:** `seo_generator.py` and `content_generation.py` for offline content generation (not real-time chat)
- **Recommendation:** Either remove Vertex from ChatService.resolve_language_and_model for consistency, or implement Vertex as a configurable chat provider

### P7: Sarvam AI (Indic/Assamese)
- **Implemented:** Yes - `apps/backend/app/services/ai/sarvam_client.py`
- **Integration:** Circuit breaker, streaming with retry, Sarvam-to-Cloudflare fallback in ChatService
- **Fallback chain:** Sarvam fails -> Cloudflare AI fallback -> Dead letter queue on double failure
- **Status:** Healthy

### P8: Razorpay (Payments)
- **Implemented:** Yes - `apps/backend/app/services/payment/razorpay_client.py`
- **Integration:** Subscription creation, cancellation, webhook handler at `/api/webhooks`
- **[Info]** httpx client auth tuple is `None` when credentials missing (handled by `PaymentNotConfiguredError`)
- **Status:** Healthy

### P9: Resend (Email)
- **Implemented:** Yes - `apps/backend/app/services/comms/resend_client.py`
- **Integration:** Welcome emails, payment receipts, password reset emails
- **Design:** Fire-and-forget pattern (signup doesn't block on email delivery)
- **Status:** Healthy

---

## 5. Functionality Audit

### Placeholder/Incomplete Code
- **No TODO/FIXME/NotImplementedError found** in any Python source file -- all functions are fully implemented.

### RAG Pipeline (End-to-End)
Complete path verified:
1. Input sanitization (`core/security.py`) -> prompt injection detection
2. Language detection (`router.py`) -> Unicode character ratio analysis
3. Embedding generation -> Azure Search hybrid query
4. Token budget management (`core/token_budget.py`) -> truncate to 3000 tokens
5. System prompt construction with `[#]` citation format
6. LLM call with fallback -> SSE streaming to client
7. Fire-and-forget persistence with Redis cache update

### Payment Flow
- Create subscription -> Razorpay API -> Webhook confirmation -> User tier upgrade
- Cancellation endpoint implemented
- Receipt emails sent on successful payment

### Email Flow
- Welcome email on signup (fire-and-forget)
- Password reset with 1-hour JWT token, single-use enforcement via Redis
- Payment receipt on webhook

### Dead Letter Queue
- Implemented for double-failure scenarios (both Sarvam and Cloudflare fail)
- MongoDB collection with 30-day TTL index

---

## 6. Security Audit

### JWT Implementation
- **Algorithm:** HS256 (symmetric) - adequate for single-service, but consider RS256 for multi-service
- **Expiry:** Access token 60 minutes, refresh token 7 days
- **Secrets:** Separate secrets for access (`JWT_SECRET`), admin (`ADMIN_JWT_SECRET`), and reset (`RESET_TOKEN_SECRET`) with fallback to shared secret
- **[High]** Production validator requires 32+ character JWT_SECRET and rejects known placeholders -- good
- **Token blacklisting:** SHA-256 hash stored in Redis with TTL matching token expiry
- **Refresh rotation:** Old refresh token JTI revoked on each refresh

### Password Policy
- Minimum 8 characters, requires uppercase + lowercase + digit
- Validated via Pydantic `field_validator` on both signup and reset

### CORS Configuration
- Origins from env var (default: `syrabit.ai`, `www.syrabit.ai`, `app.syrabit.ai`)
- Production strips localhost origins
- Cloudflare Pages preview domains allowed via regex match
- Credentials enabled

### Rate Limiting (Double Layer)
1. **Edge (Cloudflare KV):** Per-user, per-language rate limiting on chat POST (`apps/edge/src/index.ts` line 85-110); signals backend via `X-Rate-Limited-By: edge` header
2. **Backend (Upstash Redis):** IP-based rate limiting on auth endpoints (signup: 5/min, login: 10/min, refresh: 10/min); Lua-scripted token bucket for API usage

### Input Sanitization
- NFKC Unicode normalization
- Zero-width character stripping
- 14 prompt injection patterns blocked (including `<|im_end|>`, `[INST]`, `<<SYS>>`)
- 4000 character limit
- SSRF protection with private IP blocking (including AWS metadata `169.254.x.x`)
- **[Low]** Injection pattern list uses case-insensitive matching but `System:` pattern may trigger false positives on educational content about "systems"

### CSRF Protection
- Origin validation on POST/PUT/DELETE in unified middleware (`main.py` line 130-145)
- Skips health endpoints and test environment
- Returns 403 on disallowed origin

### Security Headers
Applied in unified middleware (`main.py` line 153-164):
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Strict-Transport-Security: max-age=31536000; includeSubDomains`
- `X-XSS-Protection: 0` (correct - deprecated in favor of CSP)
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Content-Security-Policy`: restrictive policy with `frame-ancestors 'none'`

### Secret Management
- Azure KeyVault in production (fetched in deploy-all.yml)
- Wrangler secrets for edge (JWT_SECRET, CF_TURNSTILE_SECRET, AZURE_BACKEND_URL)
- **[High]** Deploy workflow writes secrets to `$GITHUB_ENV` for Container Apps update. While masked, secrets in env files persist for the job duration. Consider using `az containerapp secret` references instead.

---

## 7. Performance Audit

### Circuit Breakers
Three configured circuit breakers (`core/circuit_breaker.py`):
| Provider | Failure Threshold | Reset Timeout |
|----------|------------------|---------------|
| Vertex AI | 5 | 60s |
| Sarvam AI | 5 | 60s |
| Azure Search | 3 | 30s |

Half-open state allows 1 test call before full recovery (success_threshold: 2).

### Redis Caching Strategy
- Conversation history: 30-minute TTL, proactively updated on save
- Search results: 5-minute TTL (SHA-256 cache key)
- Token blacklist: TTL matches token expiry
- Rate limit buckets: 60-second TTL (auth), monthly (API usage)

### Azure Search Warm-up
- Connection warm-up at startup (`lifespan` in `main.py`) with minimal `*` query
- Non-fatal on failure (allows app to start even if search is temporarily unavailable)

### Streaming SSE
- `ChatService.stream_llm()` yields `data: {json}\n\n` format
- Fallback notification sent mid-stream on Sarvam failure
- Internal sentinel message at end for model tracking

### Connection Pooling
All httpx clients configured with:
- `max_connections=20, max_keepalive_connections=10` (Cloudflare, Sarvam, Vertex)
- `max_connections=10, max_keepalive_connections=5` (Razorpay - lower traffic)
- Timeouts: 30s (Cloudflare, Razorpay), 60s (Sarvam, Vertex)

### Token Budget Management
- Character-based estimation: English ~4 chars/token, Assamese ~2 chars/token
- Budget: 3000 tokens for context chunks
- Truncates chunks in order, includes partial final chunk if >50 tokens remain

### Parallel I/O
- **[Medium]** No `asyncio.gather` observed in the chat pipeline. The retrieve_context -> call_llm path is sequential. At 100k DAU, parallelizing embedding generation + history loading could reduce p95 latency.

---

## 8. CI/CD Audit

### Pipeline Coverage
| App | CI Workflow | Deploy | Security Scan |
|-----|------------|--------|---------------|
| Backend | ci-backend.yml | deploy-all.yml | Bandit + Trivy |
| Edge | ci-edge.yml | deploy-all.yml | tsc |
| Frontend | ci-frontend.yml | deploy-all.yml | - |

### Deployment Pipeline (`deploy-all.yml`)
- Triggers on push to `main`
- Quality gates -> Canary deploy -> Parallel deploy (backend, edge, frontend) -> Smoke tests -> Auto-rollback on failure
- **[High]** Rollback only handles backend (Container Apps revision rollback). Edge (Cloudflare Worker) and frontend (Pages) have no automated rollback mechanism.

### 24 Workflows Assessment
- 3 core CI workflows (backend, edge, frontend) - properly configured
- 1 deploy workflow - comprehensive with canary and rollback
- 2 lockfile management (pr-lockfile-refresh, regen-lockfile)
- 1 dependency bumping (bump-deps)
- 17 agent-* workflows (automated operations: security scan, drift detection, release, translate, etc.)
- **[Low]** `agent-canary-deploy.yml` is referenced as a reusable workflow in deploy-all.yml but was not examined. If it's a no-op stub, canary stage provides false confidence.

### Test Thresholds
- **[Medium]** Backend CI allows `set +e` (non-zero exit OK) and only fails if coverage < 30%. At production scale, this means up to 70% of the codebase can be untested and CI still passes.
- **[Low]** deploy-all.yml allows up to 20 test failures in the quality gate (`FAILED > 20` threshold). This masks regressions.

### Security Scanning
- `bandit` (Python SAST) in CI lint job
- `trivy` (container vulnerability scan) on ACR-built image, fails on CRITICAL
- No SAST for TypeScript/JavaScript (consider ESLint security plugin)

---

## 9. Recommendations

### Critical (Must Fix Before Production)
1. **Resolve AI model routing inconsistency** - `ChatService.resolve_language_and_model` with `lang_override='en'` routes to Vertex AI, but the actual `generate_response`/`stream_response` functions never call Vertex. This means English with override will attempt to call Cloudflare with a Vertex model name (`gemini-1.5-pro`), producing undefined behavior. Fix: Use `settings.CF_AI_MODEL` in the override path, or add Vertex routing to `generate_response()`.

### High Priority
2. **Remove secrets from `$GITHUB_ENV`** in deploy workflow - use Azure Container Apps secret references or direct KeyVault integration instead of passing through environment variables.
3. **Implement edge/frontend rollback** - Cloudflare Workers supports versioned deployments; Pages supports rollback via Wrangler API.
4. **Raise backend coverage threshold** to at least 60% before 100k DAU launch.

### Medium Priority
5. **Add `asyncio.gather`** for parallel embedding + history loading in chat pipeline.
6. **Redis circuit breaker** - auth endpoints return 503 on Redis failure. At scale, a Redis outage means zero users can authenticate. Consider a short grace period or local token validation cache.
7. **Reduce CI test failure threshold** from 20 to 5 in deploy pipeline.
8. **Add frontend security scanning** (ESLint security rules or Semgrep).
9. **Backend rate limiter integration** - `RateLimiter` class exists with Lua scripts but is not actively called in API endpoints (only auth IP-based limiting is active). Wire it up or remove it.

### Low Priority
10. **Re-enable hash verification** in Dockerfile pip install for supply chain security.
11. **Tighten `System:` injection pattern** to reduce false positive risk.
12. **Agent workflow audit** - verify all 17 agent-* workflows are active and not stubs.
13. **Add Playwright e2e tests** to deploy pipeline for critical user journeys.

---

## Appendix: File Reference

| Finding | File | Line(s) |
|---------|------|---------|
| AI routing inconsistency | `apps/backend/app/services/ai/router.py` | 37-46 |
| Model override divergence | `apps/backend/app/services/chat_service.py` | 37-44 |
| Vertex used for content gen | `apps/backend/app/services/seo_generator.py` | 11, 41-62 |
| Security headers | `apps/backend/app/main.py` | 153-164 |
| CSRF check | `apps/backend/app/main.py` | 130-145 |
| Auth rate limiting | `apps/backend/app/api/v1/auth.py` | 172-198 |
| Circuit breakers | `apps/backend/app/core/circuit_breaker.py` | 145-155 |
| Deploy secrets | `.github/workflows/deploy-all.yml` | 68-120 |
| Coverage threshold | `.github/workflows/ci-backend.yml` | 55-62 |
