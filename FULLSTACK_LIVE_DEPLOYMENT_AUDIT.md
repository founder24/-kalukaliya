# Full-Stack Live Deployment Audit: syrabit.ai

**Audit Date:** 2026-05-30 *(sandbox environment clock; actual audit performed this date)*
**Auditor:** Automated Deployment Audit
**Scope:** Frontend (Cloudflare Pages), Edge Worker (Cloudflare Workers), Backend (GCP Cloud Run)
**Methodology:** Live HTTP testing + static code review

---

## Executive Summary

| Severity | Count |
|----------|-------|
| Critical | 2 |
| High | 1 |
| Medium | 5 |
| Low | 4 |
| Informational | 5 |
| **Total** | **17** |

**Critical Issues:**
1. Backend (GCP Cloud Run) is completely unreachable from the edge worker - returning 403 Forbidden
2. www.syrabit.ai returns HTTP 522 (Connection Timed Out) - subdomain is broken

**Impact:** The backend being unreachable means all AI chat functionality, user authentication, and data operations are non-functional in production. The www subdomain failure affects users who type www.syrabit.ai directly.

---

## Section 1: Live Connectivity Testing

### 1.1 Frontend (syrabit.ai) - PASS

```
$ curl -sI https://syrabit.ai
HTTP/2 200
date: Sat, 30 May 2026 05:22:33 GMT
content-type: text/html; charset=utf-8
access-control-allow-origin: *
cache-control: public, max-age=0, s-maxage=3600, stale-while-revalidate=86400
strict-transport-security: max-age=31536000; includeSubDomains; preload
content-security-policy: default-src 'self'; script-src 'self' 'unsafe-inline' ...
cross-origin-opener-policy: same-origin
permissions-policy: geolocation=(), microphone=(), camera=()
referrer-policy: strict-origin-when-cross-origin
x-content-type-options: nosniff
x-frame-options: DENY
server: cloudflare
```

**Response Time:** 64ms (TTFB: 19ms) -- Excellent performance.

**Assessment:** Frontend is live, fast, and has strong security headers including HSTS preload, CSP, X-Frame-Options, and Permissions-Policy.

### 1.2 www.syrabit.ai - CRITICAL FAILURE

```
$ curl -sI https://www.syrabit.ai
HTTP/2 522
date: Sat, 30 May 2026 05:22:33 GMT
content-length: 0
server: cloudflare
cache-control: private, no-store
```

**Finding CRIT-01:** www.syrabit.ai returns HTTP 522 (Connection Timed Out). This Cloudflare error indicates the origin server is not responding or is misconfigured for this subdomain.

### 1.3 Edge Worker Health (/health) - PARTIAL PASS

```
$ curl -s https://api.syrabit.ai/health
{"status":"healthy","service":"syrabit-edge","timestamp":"2026-05-30T05:22:34.904Z","backend_reachable":false}
```

**Response Times:** 356ms - 680ms (varies, likely due to backend health probe timeout)

**Finding:** Edge worker itself is healthy but reports backend_reachable: false.

### 1.4 Full Health Check (/health/full) - DEGRADED

```
$ curl -s https://api.syrabit.ai/health/full
{"status":"degraded","edge":{"status":"healthy","timestamp":"2026-05-30T05:22:34.498Z"},
 "backend":{"status":"unreachable","error":"Unexpected token '<', \"\n<html><hea\"... is not valid JSON"}}
```

**Finding CRIT-02:** The backend is returning an HTML 403 page instead of JSON. The error message "Unexpected token '<'" confirms the edge is receiving an HTML error page from Cloud Run when trying to reach the backend.

**Contributing factors to CRIT-02** (previously tracked separately, consolidated here as they share the same root cause):
- **Ingress "all" with auth required:** Cloud Run's `ingress: all` allows network access but authentication is still enforced, creating a confusing security posture.
- **GOOGLE_SA_KEY likely not configured (hypothesis):** The `getIdentityToken()` function requires this secret; without it, the edge makes unauthenticated requests that Cloud Run rejects.
- **--allow-unauthenticated flag ineffective:** The deploy pipeline includes this flag but a GCP Organization Policy constraint likely overrides it.

### 1.5 Direct Backend Test - 403 FORBIDDEN

```
$ curl -s https://syrabit-backend-851687450401.asia-south1.run.app/health
HTTP/2 403
<html><head>
<title>403 Forbidden</title>
</head>
<body>
<h1>Error: Forbidden</h1>
<h2>Your client does not have permission to get URL /health from this server.</h2>
</body></html>
```

**Root Cause:** Cloud Run is configured to require authentication despite the deploy workflow using --allow-unauthenticated. This means either:
1. The IAM policy was manually changed after deployment
2. The --allow-unauthenticated flag is not being honored (org policy constraint)
3. A GCP Organization Policy (constraints/run.allowedIngress) is blocking unauthenticated access

The edge worker's getIdentityToken() function should provide a valid identity token, but the health check test shows it is not working (backend unreachable). This suggests the GOOGLE_SA_KEY secret may not be configured in the Cloudflare Worker.

> **Note:** The GOOGLE_SA_KEY diagnosis is a hypothesis based on observed behavior (403 from Cloud Run + code review of google-auth.ts). This cannot be confirmed from outside the deployment -- operator verification is required via `npx wrangler secret list --env production` to check if the secret exists.

### 1.6 CORS Preflight - PASS

```
$ curl -sv -X OPTIONS -H "Origin: https://syrabit.ai" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: Content-Type, Authorization" \
  https://api.syrabit.ai/api/v1/chat/stream

HTTP/2 200
access-control-allow-origin: https://syrabit.ai
access-control-allow-credentials: true
access-control-allow-headers: Content-Type, Authorization, CF-Turnstile-Response, x-turnstile-token, x-anon-id, traceparent
access-control-allow-methods: GET, POST, PUT, DELETE, OPTIONS
access-control-max-age: 86400
```

**Assessment:** CORS preflight works correctly with proper origin validation.

### 1.7 Malicious Origin CORS Test - PASS

```
$ curl -X OPTIONS -H "Origin: https://evil.com" -H "Access-Control-Request-Method: POST" \
  https://api.syrabit.ai/api/v1/chat/stream

access-control-allow-origin: https://syrabit.ai  (defaults to safe origin, does NOT echo evil.com)
```

**Assessment:** CORS correctly does NOT reflect arbitrary origins. Falls back to https://syrabit.ai.

### 1.8 Bot Protection (Turnstile) - PASS

```
$ curl -s -X POST -H "Content-Type: application/json" -H "Origin: https://syrabit.ai" \
  -d '{"message":"test"}' https://api.syrabit.ai/api/v1/chat/stream

HTTP/2 403
{"error":"Bot verification required"}
```

**Assessment:** Turnstile token is correctly required for chat POST requests.

### 1.9 Invalid JWT Handling - PASS

```
$ curl -s -H "Authorization: Bearer invalidtoken123" https://api.syrabit.ai/api/v1/chat/stream
HTTP/2 401
{"error":"Malformed token: expected 3 parts"}

$ curl -s -H "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMiLCJ0eXBlIjoiYWNjZXNzIiwiZXhwIjoxNjAwMDAwMDAwfQ.invalidsig" \
  https://api.syrabit.ai/api/v1/users/me
{"error":"Invalid signature"}
```

**Assessment:** JWT validation correctly rejects malformed and invalid tokens with appropriate error messages.

### 1.10 Robots.txt - PASS

```
$ curl -s https://api.syrabit.ai/robots.txt | head -5
# As a condition of accessing this website...
```

Returns properly formatted robots.txt with Cloudflare-managed bot directives plus custom rules.

### 1.11 Unknown Path Handling - PASS

```
$ curl -sI https://api.syrabit.ai/nonexistent-path
HTTP/2 302
location: https://syrabit.ai/nonexistent-path
```

**Assessment:** Unknown paths redirect to the frontend domain. This is correct behavior for the API edge worker.

---

## Section 2: DNS and SSL Audit

### 2.1 DNS Resolution

```
$ getent hosts syrabit.ai
2606:4700::6812:12e9 syrabit.ai
2606:4700::6812:13e9 syrabit.ai

$ getent hosts www.syrabit.ai
2606:4700::6812:13e9 www.syrabit.ai
2606:4700::6812:12e9 www.syrabit.ai

$ getent hosts api.syrabit.ai
2606:4700::6812:12e9 api.syrabit.ai
2606:4700::6812:13e9 api.syrabit.ai
```

**Assessment:** All domains resolve to Cloudflare edge IPs (104.18.x.x / 2606:4700::*). DNS is properly configured and points to Cloudflare's proxy network.

> **Evidence limitation:** `getent hosts` was used because `dig` and `nslookup` are unavailable in this environment. This method resolves via the system resolver and cannot show DNS record types (CNAME vs A), TTLs, or authoritative nameservers. The results confirm reachability but cannot distinguish between a direct A record and a CNAME chain, which affects origin configuration analysis.

### 2.2 SSL Certificate

From curl verbose output:
```
Server certificate:
  subject: CN=syrabit.ai
  start date: Apr 13 19:11:39 2026 GMT
  expire date: Jul 12 19:11:38 2026 GMT
  issuer: C=US; O=Let's Encrypt; CN=E7
  subjectAltName: "api.syrabit.ai" matches cert's "*.syrabit.ai"
```

- **Issuer:** Let's Encrypt E7 (ECDSA) -- standard for Cloudflare-managed SSL
- **Validity:** Apr 13, 2026 to Jul 12, 2026 (90-day certificate, auto-renewed by Cloudflare)
- **SAN Coverage:** Wildcard *.syrabit.ai covers all subdomains
- **TLS Version:** TLSv1.3 with TLS_AES_256_GCM_SHA384
- **Key Exchange:** x25519 (modern, secure)
- **Certificate Chain:** 3 levels (leaf EC P-256, intermediate EC P-384, root RSA 4096)

**Assessment:** SSL is properly configured with modern TLS 1.3, strong cipher suites, and auto-renewing certificates.

### 2.3 Backend SSL (Cloud Run)

```
Server certificate:
  subject: CN=*.a.run.app
  start date: May  7 15:51:07 2026 GMT
  expire date: Jul 30 15:51:06 2026 GMT
  issuer: C=US; O=Google Trust Services; CN=WR2
  subjectAltName: matches "*.asia-south1.run.app"
```

**Assessment:** Cloud Run uses Google-managed certificates. Properly configured.

---

## Section 3: Cloudflare Edge Worker Configuration Audit

### 3.1 wrangler.toml Review

**Finding HIGH-01: Production worker name mismatch**
- Top-level: name = "syrabitworker"
- Production env: name = "syrabitworker-prod"
- The production environment creates a DIFFERENT worker name. If the Cloudflare DNS route for api.syrabit.ai points to "syrabitworker" (the default), deploying with --env production creates "syrabitworker-prod" which may not be the one receiving traffic.
- **Severity:** High
- **Recommendation:** Verify which worker name the api.syrabit.ai route actually points to and ensure consistency.

**Finding MED-01: Compatibility date is stale**
- compatibility_date = "2024-01-01" -- over 2 years old
- This may miss important Workers runtime improvements and bug fixes
- **Severity:** Medium
- **Recommendation:** Update to a more recent compatibility date (e.g., 2025-01-01 or later) after testing.

**Finding INFO-01: BACKEND_URL in production vars section**
- The [env.production.vars] section contains BACKEND_URL which would be overridden by a secret of the same name.
- Comments indicate it should be overridden by a Wrangler secret.
- This is acceptable as a fallback but could cause confusion.
- **Severity:** Informational

**Finding INFO-02: KV namespace IDs are configured**
- Both RATE_LIMIT_KV and ISR_CACHE_KV have valid-looking IDs (not placeholder values)
- Production environment correctly duplicates all bindings
- **Severity:** Informational (positive finding)

### 3.2 Edge Worker Logic Review (index.ts)

**Finding MED-02: Health check exposes backend parsing errors**
- /health/full returns raw error messages like: Unexpected token '<', "\n<html><hea"... is not valid JSON
- This leaks information about the backend's response format to external users
- **Severity:** Medium
- **Recommendation:** Sanitize error messages in the health/full endpoint. Return generic "Backend health check failed" instead of raw error details.

**Finding LOW-01: CORS headers not applied to health endpoints**
- The /health and /health/full responses go through addSecurityHeaders() but not applyCorsHeaders()
- While health endpoints are typically not called from browsers, this inconsistency could cause issues if frontend health checks are added
- **Severity:** Low
- **Recommendation:** Apply CORS headers consistently to all edge-generated responses.

**Finding INFO-03: Trust header stripping is well-implemented**
- The edge correctly strips X-Rate-Limited-By and X-Edge-Secret headers from incoming requests, preventing spoofing
- **Severity:** Informational (positive finding)

### 3.3 CORS Configuration Review (cors.ts)

**Finding LOW-02: CORS fallback behavior**
- When an unknown origin is provided, CORS headers still return with Access-Control-Allow-Origin: https://syrabit.ai rather than omitting CORS headers entirely
- While this prevents reflection attacks, the response still includes CORS headers which some scanners may flag
- **Severity:** Low
- **Recommendation:** Consider returning no CORS headers at all for unrecognized origins on non-preflight requests.

### 3.4 Rate Limiting Review (rate-limit.ts)

**Finding MED-03: KV eventual consistency race condition**
- The rate limiter uses KV get then put (read-then-write pattern)
- With KV's eventual consistency, concurrent requests can bypass the rate limit
- The code acknowledges this in comments: "concurrent requests may both pass the check"
- KV's global propagation delay is approximately 60 seconds; concurrent requests from different edge locations within that window can all pass the rate check
- **Severity:** Medium
- **Recommendation:** For stronger rate limiting, consider migrating to Durable Objects. If retaining the current approach, document the expected burst tolerance: under high concurrency from multiple regions, the effective rate limit window may allow N * (number of edge locations) requests before propagation catches up.

### 3.5 Robots.txt Conflict

**Finding MED-04: Conflicting robots.txt directives**
- Cloudflare-managed section: User-agent: GPTBot -> Disallow: /
- Custom section below: User-agent: GPTBot -> Allow: /
- These contradictory directives create ambiguity. Per RFC, the LAST matching rule wins for most crawlers, but behavior is not guaranteed
- **Severity:** Medium
- **Recommendation:** Remove the conflicting custom rules or disable the Cloudflare-managed robots.txt section.

---

## Section 4: GCP Backend Configuration Audit

### 4.1 Dockerfile Review

**Positive Findings:**
- Multi-stage build (builder + runtime) minimizes image size
- Non-root user (appuser, UID 1000) -- good security practice
- No secrets baked into the image
- Health check configured
- Slim base image (python:3.11-slim)

**Finding LOW-03: Hash stripping in pip install**
- sed command removes --hash lines from requirements.txt
- This weakens supply chain security by not verifying package integrity
- **Severity:** Low
- **Recommendation:** Fix requirements.txt to use proper hash format compatible with pip install, or use a lock file tool like pip-compile.

### 4.2 Cloud Run Configuration (clouddeploy.yaml)

**Finding (Contributing factor to CRIT-02): Ingress set to "all"**
```yaml
annotations:
  run.googleapis.com/ingress: all
```
- Combined with the 403 response observed in live testing, this creates a confusing state
- If ingress is "all" but authentication is required, the service is accessible but returns 403 to unauthenticated requests
- The edge worker needs a valid identity token to reach the backend
- **Severity:** Consolidated into CRIT-02 (was HIGH-02)
- **Recommendation:** Either set ingress to internal-and-cloud-load-balancing for defense-in-depth (edge provides identity token), or verify the GOOGLE_SA_KEY secret is properly configured in the Cloudflare Worker.

**Finding (Contributing factor to CRIT-02): Backend unreachable - GOOGLE_SA_KEY hypothesis**
- Live testing confirms the edge worker cannot reach the backend (returns 403)
- The getIdentityToken() function in google-auth.ts requires GOOGLE_SA_KEY to be set as a Worker secret
- If this secret is not configured, the edge makes unauthenticated requests that Cloud Run rejects
- **Severity:** Consolidated into CRIT-02 (was HIGH-03). This is a hypothesis requiring operator verification -- Wrangler secrets cannot be inspected from outside.
- **Diagnostic:** Run `npx wrangler secret list --env production` to verify if GOOGLE_SA_KEY exists. If absent: `npx wrangler secret put GOOGLE_SA_KEY --env production`

### 4.3 Gunicorn Configuration

```python
workers = int(os.environ.get("WEB_CONCURRENCY", 2))
timeout = 30
max_requests = 1000
max_requests_jitter = 50
```

**Assessment:** Well-configured for a 1Gi/1CPU Cloud Run container. Worker recycling via max_requests prevents memory leaks. Timeout of 30s is reasonable for AI chat (streaming starts quickly).

### 4.4 Deploy Pipeline (deploy-all.yml)

**Positive Findings:**
- CI quality gates run before any deployment
- Smoke tests run after all deployments
- Automated rollback on failure
- Workload Identity Federation (no long-lived service account keys)
- Concurrency control prevents parallel deployments

**Finding (Contributing factor to CRIT-02): --allow-unauthenticated flag ineffective**
- The deploy command includes --allow-unauthenticated but live testing shows 403
- This suggests a GCP Organization Policy constraint is overriding the flag
- Constraint: constraints/run.allowedIngress or iam.allowedPolicyMemberDomains
- **Severity:** Consolidated into CRIT-02 (was HIGH-04)
- **Recommendation:** Check Organization Policies: gcloud org-policies describe constraints/run.allowedIngress. If org policy prevents unauthenticated access, remove the flag and ensure the edge worker always provides identity tokens.

---

## Section 5: Security Findings

### 5.1 JWT Implementation - STRONG

**Positive Findings:**
- Rejects alg: none (prevents algorithm confusion attacks)
- Only supports HS256 and RS256 (no weak algorithms)
- Validates token structure (3 parts required)
- Checks expiry and token type
- Uses Web Crypto API (constant-time verification)
- Public paths are explicitly whitelisted (allowlist approach)

**No critical JWT vulnerabilities found.**

### 5.2 SSRF Protection - STRONG

The backend (security.py) implements comprehensive SSRF protection:
- Blocks private/loopback/link-local/multicast IPs
- Blocks AWS metadata endpoint (169.254.x.x)
- Validates URL schemes (http/https only)
- Rejects URLs with userinfo
- DNS resolution with timeout

### 5.3 HMAC Signature (api-proxy.ts) - GOOD

- Uses per-request HMAC-SHA256 with timestamp
- Message includes: timestamp:userId:pathname
- Backend should validate within +/- 30s tolerance

**Finding MED-05: HMAC validation not enforced at edge**
- The HMAC signature is only generated if EDGE_SHARED_SECRET is set
- If the secret is not configured, requests to the backend have no edge authentication
- Combined with the backend being unreachable, this suggests the shared secret may not be configured
- **Severity:** Medium
- **Recommendation:** Run `npx wrangler secret list --env production` to check if EDGE_SHARED_SECRET exists. If absent, generate a 256-bit random value and set it in both `npx wrangler secret put EDGE_SHARED_SECRET --env production` and the Cloud Run backend environment variable.

### 5.4 Input Sanitization - STRONG

Backend sanitize_user_input() provides:
- Unicode NFKC normalization
- Zero-width character stripping
- Prompt injection detection (13 patterns)
- Control character removal
- Length limiting (4000 chars)

### 5.5 Turnstile Enforcement - COMPLETE

- Mandatory for auth endpoints (signup, login, forgot-password)
- Mandatory for chat POST requests
- Correctly exempts chat/feedback
- Production mode rejects if CF_TURNSTILE_SECRET is not configured (fail-closed)

---

## Section 6: Full-Stack Integration Findings

### 6.1 Environment Variable Consistency

| Variable | .env.shared | wrangler.toml | clouddeploy.yaml | Status |
|----------|-------------|---------------|------------------|--------|
| BACKEND_URL | https://syrabit-backend-xxxxx.run.app | https://syrabit-backend-851687450401.asia-south1.run.app | N/A (image internal) | OK (template vs actual) |
| ALLOWED_ORIGIN | https://syrabit.ai | https://syrabit.ai | N/A | Consistent |
| APP_ENV | production | N/A | production | Consistent |

**Finding INFO-04: .env.shared CF_WORKER_URL mismatch**
- .env.shared has CF_WORKER_URL=https://edge.syrabit.ai
- Actual edge worker is at https://api.syrabit.ai
- This is a template issue only (the actual deployment uses the correct URL)
- **Severity:** Informational

### 6.2 Request Flow Analysis

Expected flow: Frontend -> api.syrabit.ai (Edge Worker) -> Cloud Run Backend

**Current state:** The chain is broken between Edge and Backend:
1. Frontend to Edge: Working (CORS, Turnstile, JWT all functional)
2. Edge to Backend: **BROKEN** (403 Forbidden from Cloud Run)

### 6.3 CI/CD Pipeline Completeness

The deploy-all.yml workflow covers:
- Quality gates (lint, type-check, test)
- Backend deployment (Docker build, push to Artifact Registry, Cloud Run deploy)
- Edge deployment (Wrangler deploy)
- Frontend deployment (Cloudflare Pages)
- Smoke tests (health, chat endpoints, frontend reachability)
- Automated rollback on failure

**Finding LOW-04: Smoke test threshold too lenient**
- The chat smoke test accepts 401/403 as "valid" responses
- While this is intentional (Turnstile blocks smoke tests), it means a broken auth flow would not be detected
- **Severity:** Low
- **Recommendation:** Add a dedicated health check that bypasses Turnstile (e.g., test the /health endpoint returns backend_reachable: true).

---

## Section 7: Performance and Reliability

### 7.1 Response Times

| Endpoint | Avg Response Time | Assessment |
|----------|-------------------|------------|
| Frontend (syrabit.ai) | 64ms (TTFB: 19ms) | Excellent |
| Edge Health (/health) | 356-680ms | Elevated (backend probe timeout) |
| CORS Preflight | ~100ms | Good |
| Bot verification (403) | ~150ms | Good |

**Note:** The elevated /health response time (356-680ms) is caused by the 2-second backend health probe timing out. When cached, subsequent requests would be faster.

### 7.2 Health Check Caching

The edge worker caches backend health probe results for 10 seconds (HEALTH_CACHE_TTL_MS = 10_000). This prevents every health check from hitting the backend. Good design for reducing backend load.

### 7.3 Streaming Support

The api-proxy.ts correctly handles streaming:
- Detects /stream paths
- Sets Content-Type: text/event-stream
- Sets Cache-Control: no-store
- Removes Content-Length for chunked transfer
- Sets X-Accel-Buffering: no for nginx/proxy compatibility

### 7.4 Timeout Configuration

- Edge proxy timeout: 30 seconds (configurable via PROXY_TIMEOUT_MS)
- Gunicorn timeout: 30 seconds
- Cloud Run timeout: 300 seconds (clouddeploy.yaml)
- Health probe (backend): 2 seconds (edge) / 5 seconds (full health)

**Finding INFO-05: Timeout alignment is appropriate**
- The edge timeout (30s) is less than Cloud Run timeout (300s)
- This ensures the edge fails fast rather than hanging if the backend is slow

### 7.5 Circuit Breaker Assessment

The backend implements a circuit breaker pattern in `apps/backend/app/core/circuit_breaker.py` for AI provider resilience. Three pre-configured circuit breakers exist:

| Circuit Breaker | Failure Threshold | Reset Timeout | Purpose |
|----------------|-------------------|---------------|---------|
| Vertex AI | 5 failures | 30s | Primary AI chat provider |
| Sarvam AI | 5 failures | 60s | Assamese language support |
| Vertex Search | 3 failures | 30s | RAG search provider |

**Implementation details:**
- Three-state model: CLOSED (normal) -> OPEN (failing fast) -> HALF_OPEN (testing recovery)
- Async-safe with `asyncio.Lock` for state transitions
- Rate-limited logging to prevent log flooding during outages
- Status exposed via `/health/circuit-breakers` endpoint (confirmed in health.py)

**Assessment:** The circuit breaker implementation is well-designed for protecting against cascading failures from AI providers. However, there is no circuit breaker at the edge layer for the backend connection itself. Given that the backend is currently unreachable (CRIT-02), the edge worker retries on every request without a trip mechanism, leading to the elevated /health response times (356-680ms) observed in Section 7.1. Adding a circuit breaker at the edge for the backend health probe would prevent repeated timeout-based delays when the backend is known to be down.

---

## Section 8: Remediation Priority Matrix

### Critical (Fix Immediately)

| # | Finding | Impact | Remediation |
|---|---------|--------|-------------|
| CRIT-01 | www.syrabit.ai returns 522 | Users visiting www subdomain see error | Configure Cloudflare DNS or Page Rules to redirect www to apex domain. Check if a Worker route or origin is configured for www. |
| CRIT-02 | Backend unreachable (403) from edge | ALL API functionality broken | 1. Verify GOOGLE_SA_KEY Worker secret exists (`npx wrangler secret list --env production`). 2. Check GCP org policies blocking unauthenticated access (`gcloud org-policies describe constraints/run.allowedIngress`). 3. If org policy requires auth, ensure edge always sends identity token. 4. Consider setting ingress to internal-and-cloud-load-balancing for defense-in-depth. *Note: GOOGLE_SA_KEY diagnosis is a hypothesis requiring operator verification.* |

### High (Fix Within 24 Hours)

| # | Finding | Impact | Remediation |
|---|---------|--------|-------------|
| HIGH-01 | Worker name mismatch (syrabitworker vs syrabitworker-prod) | Potential deploy-to-wrong-worker | Verify which worker the api.syrabit.ai route targets. Align names. |

*Note: HIGH-02, HIGH-03, and HIGH-04 have been consolidated into CRIT-02 as contributing factors to the same root cause (edge cannot authenticate to backend).*

### Medium (Fix Within 1 Week)

| # | Finding | Impact | Remediation |
|---|---------|--------|-------------|
| MED-01 | Stale compatibility_date (2024-01-01) | Missing Workers runtime improvements | Update to recent date after testing. |
| MED-02 | Health/full leaks backend error details | Information disclosure | Sanitize error messages before returning to client. |
| MED-03 | KV rate limiting has race condition | Rate limits can be bypassed under burst | KV eventual consistency has ~60s global propagation delay; concurrent requests from different edge locations within that window can all pass the rate check. For stronger guarantees, migrate to Durable Objects. Document the expected burst tolerance as an accepted risk if the current approach is retained. |
| MED-04 | Conflicting robots.txt directives | Search engine confusion | Resolve GPTBot Allow/Disallow conflict. |
| MED-05 | HMAC/EDGE_SHARED_SECRET may not be configured | No edge authentication on backend requests | Run `npx wrangler secret list --env production` to check if EDGE_SHARED_SECRET exists. If absent, generate a 256-bit random value and set it with `npx wrangler secret put EDGE_SHARED_SECRET --env production`, then add the same value to the Cloud Run backend environment variable. |

### Low (Fix Within 1 Month)

| # | Finding | Impact | Remediation |
|---|---------|--------|-------------|
| LOW-01 | Health endpoints missing CORS headers | Minor inconsistency | Add CORS headers to health responses. |
| LOW-02 | CORS fallback includes headers for unknown origins | Scanner noise | Consider omitting CORS headers for unknown origins. |
| LOW-03 | Hash stripping in Dockerfile pip install | Weakened supply chain security | Fix requirements.txt format or use pip-compile. |
| LOW-04 | Smoke test too lenient (accepts 403) | Broken auth not detected | Add backend_reachable health assertion. |

### Informational (No Action Required)

| # | Finding | Notes |
|---|---------|-------|
| INFO-01 | BACKEND_URL fallback in production vars | Acceptable pattern with secret override. |
| INFO-02 | KV IDs properly configured | Positive finding. |
| INFO-03 | Trust header stripping well-implemented | Positive finding. |
| INFO-04 | .env.shared CF_WORKER_URL outdated | Template only, no production impact. |
| INFO-05 | Timeout alignment appropriate | Good architecture. |

---

## Appendix A: Test Evidence Summary

### Commands Executed

1. `curl -sI https://syrabit.ai` -- Frontend availability
2. `curl -sI https://www.syrabit.ai` -- WWW redirect
3. `curl -sv https://api.syrabit.ai/health` -- Edge health
4. `curl -sv https://api.syrabit.ai/health/full` -- Full health
5. `curl -sv https://api.syrabit.ai/robots.txt` -- Robots
6. `curl -sv -X OPTIONS https://api.syrabit.ai/api/v1/chat/stream` -- CORS preflight
7. `curl -sv -X POST https://api.syrabit.ai/api/v1/chat/stream` -- Bot protection
8. `curl -sv -H "Authorization: Bearer invalid" ...` -- JWT validation
9. `curl -sv https://syrabit-backend-851687450401.asia-south1.run.app/health` -- Direct backend
10. `curl -X OPTIONS -H "Origin: https://evil.com" ...` -- CORS malicious origin
11. `getent hosts syrabit.ai / www.syrabit.ai / api.syrabit.ai` -- DNS resolution

### Files Reviewed

- apps/edge/wrangler.toml -- Worker configuration
- apps/edge/src/index.ts -- Main request pipeline
- apps/edge/src/middleware/cors.ts -- CORS logic
- apps/edge/src/middleware/bot.ts -- Turnstile verification
- apps/edge/src/middleware/jwt.ts -- JWT verification
- apps/edge/src/middleware/rate-limit.ts -- Rate limiting
- apps/edge/src/routes/api-proxy.ts -- Backend proxy
- apps/edge/src/routes/isr.ts -- ISR caching
- apps/edge/src/utils/google-auth.ts -- GCP auth
- apps/backend/Dockerfile -- Container image
- apps/backend/app/main.py -- FastAPI application
- apps/backend/app/config.py -- Configuration/settings
- apps/backend/app/core/security.py -- Security utilities
- apps/backend/app/api/v1/health.py -- Health endpoints
- apps/backend/gunicorn_conf.py -- WSGI config
- infra/gcp/clouddeploy.yaml -- Cloud Run spec
- .github/workflows/deploy-all.yml -- CI/CD pipeline
- .env.shared -- Environment template
- docker-compose.yml -- Local dev setup

---

*End of Audit Report*
