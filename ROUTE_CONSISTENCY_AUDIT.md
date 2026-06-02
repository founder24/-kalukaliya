# Route Consistency Audit & Critical Path Verification

**Date:** 2025-01-XX  
**Scope:** Frontend (api.jsx) / Edge Worker (index.ts, jwt.ts, cors.ts, api-proxy.ts) / Backend (main.py, config.py, auth.py)  
**Branch:** audit/build-verification-2

---

## Table of Contents

1. [Route Consistency Audit](#1-route-consistency-audit)
2. [Auth Flow Trace](#2-auth-flow-trace)
3. [Chat Flow Trace](#3-chat-flow-trace)
4. [Content Flow Trace](#4-content-flow-trace)
5. [Environment Variable Consistency](#5-environment-variable-consistency)
6. [CORS Consistency](#6-cors-consistency)
7. [Security Observations](#7-security-observations)
8. [Summary of Issues](#8-summary-of-issues)

---

## 1. Route Consistency Audit

### 1.1 Frontend API Paths (from `apps/frontend/src/utils/api.jsx`)

All frontend calls use either `API_BASE` (`${VITE_BACKEND_URL}/api/v1`) or `WORKER_API` (`${VITE_WORKER_API_URL}/api/v1`).

#### Public Content (via WORKER_API):
| Frontend Path | Auth | Method |
|---|---|---|
| `/content/boards` | None | GET |
| `/content/classes` | None | GET |
| `/content/streams` | None | GET |
| `/content/subjects` | None | GET |
| `/content/subjects-by-course-type` | None | GET |
| `/content/subjects/{id}` | None | GET |
| `/content/chapters/{subjectId}` | None | GET |
| `/content/chunks/{chapterId}` | None | GET |
| `/content/chapters/{id}/topic-summary` | None | GET |
| `/content/chapters/{id}/topic-content` | None | GET |
| `/content/topic/{topicId}/page/{pageType}` | None | GET |
| `/content/chapter-by-slug/{board}/{class}/{subject}/{chapter}` | None | GET |
| `/seo/page/{board}/{class}/{subject}/{topic}[/{pageType}]` | None | GET |
| `/seo/page-bundle/{board}/{class}/{subject}/{topic}` | None | GET |
| `/seo/page-types/{board}/{class}/{subject}/{topic}` | None | GET |
| `/seo/related/{topicSlug}` | None | GET |

#### Auth (via API_BASE):
| Frontend Path | Auth | Method |
|---|---|---|
| `/auth/login` | None | POST |
| `/auth/signup` | None | POST |
| `/admin/login` | None | POST |
| `/admin/logout` | Cookie | POST |
| `/admin/verify` | Bearer/Cookie | GET |

#### Edu (via API_BASE, optional auth - anon-id header):
| Frontend Path | Auth | Method |
|---|---|---|
| `/edu/reader/fetch` | anon-id | POST |
| `/edu/check-url` | anon-id | POST |
| `/edu/allowlist` | None | GET |
| `/edu/request-site` | anon-id | POST |
| `/edu/educator/submit-site` | anon-id | POST |
| `/edu/educator/appeal-rejection` | anon-id | POST |
| `/edu/educator/my-submissions` | anon-id | GET |
| `/edu/educator/my-submissions/{domain}` | anon-id | DELETE |
| `/edu/educator/my-appeals` | anon-id | GET |
| `/edu/state` | anon-id | GET/POST |
| `/edu/grounded-answer` | (URL export only) | - |
| `/edu/memory/recent` | Bearer | GET |

#### Conversations (via API_BASE):
| Frontend Path | Auth | Method |
|---|---|---|
| `/conversations` | Bearer | GET |
| `/conversations/{id}` | Bearer | GET/DELETE/PATCH |
| `/conversations/anon` | anon-id | GET |
| `/conversations/anon/{id}` | anon-id | GET/DELETE |

#### Chat (via API_BASE):
| Frontend Path | Auth | Method |
|---|---|---|
| `/chat/feedback` | anon-id | POST |
| `/chat/feedback` | Bearer (admin) | GET |
| `/chat/feedback/stats` | Bearer (admin) | GET |

#### User (via API_BASE):
| Frontend Path | Auth | Method |
|---|---|---|
| `/user/onboarding` | Bearer | POST |
| `/user/payments` | Bearer | GET |

#### Payments (via API_BASE):
| Frontend Path | Auth | Method |
|---|---|---|
| `/payments/create-order` | Bearer | POST |
| `/payments/verify` | Bearer | POST |
| `/payments/recover` | Bearer | POST |
| `/payments/credit-topup` | Bearer | POST |
| `/payments/credit-topup/verify` | Bearer | POST |
| `/payments/refund-request` | Bearer | POST |

#### Trustpilot (via API_BASE):
| Frontend Path | Auth | Method |
|---|---|---|
| `/trustpilot/invitation-link` | Cookie | POST |

#### SEO (via API_BASE, admin):
| Frontend Path | Auth | Method |
|---|---|---|
| `/seo/health` | None/Bearer | GET |
| `/seo/stats` | Bearer | GET |
| `/seo/topics` | Bearer | GET/POST |
| `/seo/topics/{id}` | Bearer | DELETE |
| `/seo/extract-topics` | Bearer | POST |
| `/seo/generate` | Bearer | POST |
| `/seo/pages` | Bearer | GET |
| `/seo/pages/{id}/status` | Bearer | PATCH |
| `/seo/pilot` | Bearer | POST |
| `/seo/auto-run` | Bearer | POST |
| `/seo/jobs/{id}` | Bearer | GET |
| `/seo/diagnose-topics` | Bearer | GET |
| `/seo/backfill-notes` | Bearer | POST |
| `/seo/insights` | Bearer | GET |
| `/seo/expand/{boardSlug}` | Bearer | POST |
| `/seo/bulk-publish` | Bearer | POST |
| `/seo/subject-coverage` | Bearer | GET |
| `/seo/auto-publish/schedule` | Bearer | GET |
| `/seo/run-subject` | Bearer | POST |
| `/seo/refresh-meta` | Bearer | POST |
| `/seo/review-queue` | Bearer | GET |
| `/seo/review-queue/bulk-action` | Bearer | POST |
| `/seo/flag-low-quality` | Bearer | POST |
| `/seo/quality-audit` | Bearer | POST |
| `/seo/quality-summary` | Bearer | GET |
| `/seo/duplicate-scan` | Bearer | POST |
| `/seo/duplicate-pairs` | Bearer | GET |
| `/seo/duplicate-pairs/{id}/resolve` | Bearer | POST |
| `/seo/related-by-chapter/{id}` | None | GET |

#### Admin (via API_BASE, all require admin Bearer token):
- Dashboard, Users, Conversations, Content, Analytics (many sub-paths)
- Settings, Roadmap, Plan Config, API Config, Activity Log
- Syra voice assistant (chat, actions, execute-action, prefs, briefing, stt, tts)
- SEO management (entity, health, deep-scan, google-indexing, topic-discovery, remediation, internal-links)
- Ads, Vertex AI, IndexNow, Notifications, Alerts, Security, Logs, Cache

### 1.2 Edge Routing (from `apps/edge/src/index.ts`)

The edge worker routes:
- `OPTIONS` any path -> CORS preflight response
- `/health` -> edge health check (handled at edge, not proxied)
- `/health/full` -> edge + backend deep health check (handled at edge)
- `/robots.txt` -> `handleRobots()`
- `/sitemap*.xml` -> rewritten to `/api/v1/seo/sitemap*.xml` then proxied
- `/api/*` or `/health/*` (except /health and /health/full) -> `proxyRequest()` to backend
- `/assets/*` -> R2 bucket serve
- ISR fallback for bots
- `/` -> 302 redirect to `${ALLOWED_ORIGIN}/library`
- GET/HEAD catchall -> 302 redirect to frontend origin + path
- Otherwise -> 404

**JWT Processing** (step 2 in pipeline, for all `/api/` paths):
- `verifyJWT()` checks PUBLIC_PATHS and OPTIONAL_AUTH_PATHS
- Valid token -> injects `X-User-ID` + `X-Edge-Secret` headers
- Invalid token -> 401 response
- Anonymous on optional auth -> `X-User-ID: anonymous`

**Rate Limiting** (step 4, POST to `/api/v1/chat` or `/api/v1/ai/chat`):
- Checks `RATE_LIMIT_KV` binding
- Returns 429 if limit exceeded

### 1.3 Edge PUBLIC_PATHS (from `apps/edge/src/middleware/jwt.ts`)

```
/health
/api/v1/auth/login
/api/v1/auth/signup
/api/v1/auth/refresh
/api/v1/auth/forgot-password
/api/v1/auth/reset-password
/api/v1/admin/login
/api/v1/admin/logout
/api/webhooks
/api/v1/content/public
/api/v1/content/boards
/api/v1/content/classes
/api/v1/content/streams
/api/v1/content/subjects
/api/v1/content/chapters
/api/v1/content/chunks
/api/v1/content/chapter-by-slug
/api/v1/content/topic
```

### 1.4 Edge OPTIONAL_AUTH_PATHS

```
/api/v1/chat
/api/v1/ai/chat
/api/v1/conversations
/api/v1/conversations/anon
/api/v1/edu
```

### 1.5 Backend Route Registrations (from `apps/backend/app/main.py`)

| Router | Prefix | Tags |
|---|---|---|
| `chat.router` | `/api/v1/chat` | Chat |
| `chat.router` | `/api/v1/ai/chat` | Chat (legacy alias) |
| `conversations.router` | `/api/v1/conversations` | Conversations |
| `edu.router` | `/api/v1` | Education (paths: /edu/*) |
| `auth.router` | `/api/v1/auth` | Authentication |
| `subscription.router` | `/api/v1/subscription` | Subscription |
| `users.router` | `/api/v1/users` | Users |
| `health.router` | `/health` | Health |
| `health.router` | `/api/v1/health` | Health |
| `feedback.router` | `/api/v1/chat/feedback` | Feedback |
| `razorpay.router` | `/api/webhooks` | Webhooks |
| `admin.router` | `/api/v1/admin` | Admin |
| `admin_dashboard.router` | `/api/v1/admin` | Admin Dashboard |
| `admin_users.router` | `/api/v1/admin` | Admin Users |
| `admin_conversations.router` | `/api/v1/admin` | Admin Conversations |
| `admin_content.router` | `/api/v1/admin` | Admin Content |
| `admin_analytics.router` | `/api/v1/admin` | Admin Analytics |
| `admin_settings.router` | `/api/v1/admin` | Admin Settings |
| `admin_notifications.router` | `/api/v1/admin` | Admin Notifications |
| `admin_seo.router` | `/api/v1/admin` | Admin SEO |
| `admin_ai.router` | `/api/v1/admin` | Admin AI |
| `admin_revenue.router` | `/api/v1/admin` | Admin Revenue |
| `admin_alerts.router` | `/api/v1/admin` | Admin Alerts |
| `admin_knowledge.router` | `/api/v1/admin` | Admin Knowledge |
| `admin_translate.router` | `/api/v1/admin` | Admin Translation |
| `admin_dead_letters.router` | `/api/v1/admin` | Admin Dead Letters |
| `admin_security.router` | `/api/v1/admin` | Admin Security |
| `seo.router` | `/api/v1/seo` | SEO |
| `seo.router` | `` (root) | SEO Root |
| `indexnow.router` | `/api/v1/indexnow` | IndexNow |
| `public_content.router` | `/api/v1/content` | Public Content |
| `content.router` | `/api/v1/content` | Content |
| `public_content.router` | `/api/content` | Public Content Legacy |
| `changelog.router` | `/api/v1` | Changelog |
| `payments.router` | `/api/v1/payments` | Payments |
| `users.router` | `/api/v1/user` | Users (singular alias) |

### 1.6 Cross-Reference Findings

#### ISSUE #1: `/api/v1/content/subjects-by-course-type` NOT in PUBLIC_PATHS
- **Severity: MEDIUM**
- Frontend calls `WORKER_API + /content/subjects-by-course-type` with NO auth token
- Edge PUBLIC_PATHS includes `/api/v1/content/subjects` which uses `startsWith()` matching
- **Result:** This WORKS because `startsWith('/api/v1/content/subjects')` matches `/api/v1/content/subjects-by-course-type`
- **Status: NOT A BUG** - works by coincidence of prefix matching, but fragile if PUBLIC_PATHS logic changes

#### ISSUE #2: `/api/v1/seo/health` NOT in PUBLIC_PATHS
- **Severity: LOW**
- Frontend calls `seoHealthLive()` -> `GET ${API_BASE}/seo/health` with NO explicit auth token (just `withCredentials: true`)
- This path is NOT in PUBLIC_PATHS or OPTIONAL_AUTH_PATHS
- **Result:** Edge will require JWT. If user has no Bearer token, edge returns 401.
- **Impact:** The `seoHealthLive()` function would fail for unauthenticated users. However, it appears to be used only from admin context where tokens are available. Non-issue in practice.

#### ISSUE #3: `/api/v1/seo/related-by-chapter/{id}` NOT in PUBLIC_PATHS
- **Severity: LOW**  
- Frontend calls `seoRelatedByChapter()` with no auth headers
- This resolves to `/api/v1/seo/related-by-chapter/{chapterId}`
- NOT in PUBLIC_PATHS
- **Result:** Edge requires JWT. This will fail for anonymous users if called without auth context.
- **Impact:** If this is used on public-facing SEO pages, it would get a 401 for anonymous visitors.

#### ISSUE #4: `/api/v1/trustpilot/invitation-link` has NO backend handler
- **Severity: MEDIUM**
- Frontend calls `POST ${API_BASE}/trustpilot/invitation-link` with cookie auth
- No `trustpilot` router, module, or endpoint exists anywhere in the backend codebase (confirmed via search)
- No import or registration in `main.py`
- **Result:** This endpoint returns 404 from the backend. The frontend `generateTrustpilotInvitationLink()` function will always fail and fall back to the hardcoded URL `https://www.trustpilot.com/review/syrabit.ai`.
- **Impact:** Low user impact due to the fallback, but the dead code and missing backend handler indicate an incomplete feature implementation.

#### ISSUE #5: `/api/v1/admin/logout` in PUBLIC_PATHS
- **Severity: INFORMATIONAL**
- Edge marks `/api/v1/admin/logout` as public (no JWT required)
- Frontend calls `adminLogout()` with `withCredentials: true` (cookie-only, no Bearer token)
- This is correct - logout should work even if the JWT is expired

#### ISSUE #6: Chat feedback path ambiguity
- **Severity: INFORMATIONAL**
- Frontend `postChatFeedback()` calls `${API_BASE}/chat/feedback` (resolves to `/api/v1/chat/feedback`)
- Backend registers `feedback.router` at prefix `/api/v1/chat/feedback`
- Edge OPTIONAL_AUTH_PATHS includes `/api/v1/chat` (startsWith match)
- **Result:** Chat feedback is correctly treated as optional-auth since it starts with `/api/v1/chat`

---

## 2. Auth Flow Trace

### 2.1 Login Flow

```
Frontend: POST ${API_BASE}/auth/login -> /api/v1/auth/login
  |
  v
Edge: url.pathname.startsWith('/api/v1/auth/login') matches PUBLIC_PATHS
  -> verifyJWT returns { valid: true, userId: 'anonymous' }
  -> X-User-ID: anonymous, X-Edge-Secret set
  -> proxyRequest() forwards to backend
  |
  v
Backend: auth.router at /api/v1/auth -> handles /login endpoint
  -> Validates credentials, returns { access_token, refresh_token }
```

**Status: WORKING CORRECTLY**

### 2.2 Signup Flow

```
Frontend: POST ${API_BASE}/auth/signup -> /api/v1/auth/signup
  |
  v
Edge: PUBLIC_PATHS match -> skip JWT -> proxy to backend
  |
  v
Backend: auth.router /api/v1/auth -> handles /signup endpoint
  -> Creates user, returns tokens
```

**Status: WORKING CORRECTLY**

### 2.3 Token Refresh

```
Frontend: (no explicit refresh call found in api.jsx)
  -> AuthContext likely handles refresh internally
  |
  v
Edge: /api/v1/auth/refresh in PUBLIC_PATHS -> skip JWT -> proxy
  |
  v
Backend: auth.router /api/v1/auth -> handles /refresh endpoint
  -> Validates refresh_token, issues new access_token
```

**Observation:** No explicit `auth/refresh` call found in `api.jsx` exports, but the endpoint is properly set up in edge PUBLIC_PATHS and backend. The refresh logic is likely in `AuthContext.jsx` or handled by an axios interceptor.

**Status: INFRASTRUCTURE CORRECT** (routing is properly configured)

### 2.4 Authenticated Request Flow

```
Frontend: sends Bearer token in Authorization header
  |
  v
Edge (jwt.ts):
  1. Path NOT in PUBLIC_PATHS or OPTIONAL_AUTH_PATHS
  2. Extracts Bearer token
  3. Decodes JWT header -> detects HS256 or RS256
  4. Verifies signature using JWT_SECRET (HS256) or JWT_PUBLIC_KEY (RS256)
  5. Checks expiry and token type == 'access'
  6. Sets X-User-ID: <payload.sub>
  7. Sets X-Edge-Secret: <EDGE_SHARED_SECRET>
  |
  v
Edge (api-proxy.ts):
  1. Computes HMAC: SHA256(EDGE_SHARED_SECRET, "timestamp:userId:path")
  2. Sets X-Edge-Timestamp, X-Edge-Signature headers
  3. Injects Google Identity Token for Cloud Run auth
  4. Proxies to backend
  |
  v
Backend (auth.py get_current_user):
  1. Checks X-Edge-Secret matches settings.EDGE_SHARED_SECRET
  2. If match: verifies X-Edge-Signature HMAC (timestamp:userId:path)
  3. If HMAC valid: trusts X-User-ID, loads User from DB
  4. If no edge-trust: falls back to JWT decode from Bearer token
```

**Status: WORKING CORRECTLY** - Full end-to-end chain verified.

### 2.5 Admin Login Flow

```
Frontend: POST ${API_BASE}/admin/login -> /api/v1/admin/login
  |
  v
Edge: PUBLIC_PATHS includes /api/v1/admin/login -> skip JWT -> proxy
  |
  v
Backend: admin.router at /api/v1/admin -> handles /login endpoint
  -> Sets httpOnly cookie + returns admin token
```

**Status: WORKING CORRECTLY**

---

## 3. Chat Flow Trace

### 3.1 Chat Stream Request

```
Frontend: POST to /api/v1/chat/stream or /api/v1/ai/chat/stream
  (via streaming fetch/SSE, not visible in api.jsx exports)
  |
  v
Edge (jwt.ts):
  - OPTIONAL_AUTH_PATHS includes /api/v1/chat and /api/v1/ai/chat
  - startsWith match covers /stream suffix
  - If Bearer present -> validate, extract userId
  - If no Bearer -> userId = 'anonymous', valid = true
  |
  v
Edge (index.ts step 4 - rate limiting):
  - pathname.startsWith('/api/v1/chat') || pathname.startsWith('/api/v1/ai/chat')
  - method === 'POST'
  - Extracts X-User-ID (authenticated or 'anonymous')
  - Reads lang from body (en/as)
  - Checks RATE_LIMIT_KV -> 429 if exceeded
  - Sets X-Rate-Limited-By: edge
  |
  v
Edge (api-proxy.ts):
  - Detects /stream in pathname -> isStreamRequest = true
  - Sets Content-Type: text/event-stream
  - Sets Cache-Control: no-store
  - Sets X-Accel-Buffering: no
  - Removes Content-Length (chunked transfer)
  - Passes response.body as ReadableStream
  |
  v
Backend: chat.router registered at BOTH:
  - /api/v1/chat (primary)
  - /api/v1/ai/chat (legacy alias)
  -> chat_stream() endpoint uses get_current_user_optional
  -> Works for both authenticated and anonymous users
```

**Status: WORKING CORRECTLY** - Dual prefix registration ensures both paths work.

### 3.2 Chat Rate Limiting Edge Case

**Observation:** The edge rate limit extracts `lang` from the request body by cloning and parsing JSON. If the body is not JSON (unlikely for chat), it defaults to `lang = 'en'`. The `request.clone()` ensures the original body is preserved for the backend proxy.

**Status: CORRECT**

---

## 4. Content Flow Trace

### 4.1 Public Content Library

```
Frontend: GET ${WORKER_API}/content/boards
  -> resolves to /api/v1/content/boards
  |
  v
Edge (jwt.ts):
  - PUBLIC_PATHS includes /api/v1/content/boards
  - startsWith match -> skip JWT
  |
  v
Edge: proxies to backend
  |
  v
Backend: public_content.router at /api/v1/content
  -> Handles /boards, /classes, /streams, /subjects, /chapters, /chunks, etc.
```

**Status: WORKING CORRECTLY**

### 4.2 Content Path Coverage Analysis

| Frontend Content Path | Edge PUBLIC_PATHS Match | Status |
|---|---|---|
| `/content/boards` | `/api/v1/content/boards` | MATCH |
| `/content/classes` | `/api/v1/content/classes` | MATCH |
| `/content/streams` | `/api/v1/content/streams` | MATCH |
| `/content/subjects` | `/api/v1/content/subjects` | MATCH |
| `/content/subjects-by-course-type` | `/api/v1/content/subjects` (prefix) | MATCH (prefix) |
| `/content/subjects/{id}` | `/api/v1/content/subjects` (prefix) | MATCH (prefix) |
| `/content/chapters/{id}` | `/api/v1/content/chapters` (prefix) | MATCH (prefix) |
| `/content/chapters/{id}/topic-summary` | `/api/v1/content/chapters` (prefix) | MATCH (prefix) |
| `/content/chapters/{id}/topic-content` | `/api/v1/content/chapters` (prefix) | MATCH (prefix) |
| `/content/chunks/{id}` | `/api/v1/content/chunks` (prefix) | MATCH (prefix) |
| `/content/topic/{id}/page/{type}` | `/api/v1/content/topic` (prefix) | MATCH (prefix) |
| `/content/chapter-by-slug/...` | `/api/v1/content/chapter-by-slug` (prefix) | MATCH (prefix) |

**Status: ALL CONTENT PATHS CORRECTLY ROUTED**

### 4.3 SEO Pages (via WORKER_API)

| Frontend SEO Path | Edge PUBLIC_PATHS Match | Status |
|---|---|---|
| `/seo/page/...` | NOT in PUBLIC_PATHS | REQUIRES AUTH |
| `/seo/page-bundle/...` | NOT in PUBLIC_PATHS | REQUIRES AUTH |
| `/seo/page-types/...` | NOT in PUBLIC_PATHS | REQUIRES AUTH |
| `/seo/related/...` | NOT in PUBLIC_PATHS | REQUIRES AUTH |

#### ISSUE #7: SEO page paths called without auth via WORKER_API
- **Severity: MEDIUM**
- Frontend functions `getSeoPage()`, `getSeoPageBundle()`, `getSeoPageTypes()`, `getSeoRelated()` use `WORKER_API` with NO auth token
- These paths (`/api/v1/seo/page/...`, `/api/v1/seo/page-bundle/...`, etc.) are NOT in PUBLIC_PATHS
- Edge will require JWT authentication for these paths
- **Impact:** These appear to be used on public-facing SEO pages for search engine indexing. If called without a Bearer token, edge returns 401.
- **Mitigation:** These paths may be accessed directly by the frontend origin (not through the edge worker) if WORKER_API falls back to API_BASE and the frontend is server-side rendered. Or the ISR/bot handling at edge may intercept these before JWT check. However, the standard flow would fail for anonymous browser users.
- **Note:** The edge `handleISR()` function runs AFTER the 404 check for non-API paths. Since these ARE `/api/` paths, they go through JWT verification. This is likely a gap that needs `/api/v1/seo/page` and `/api/v1/seo/related` added to PUBLIC_PATHS.

---

## 5. Environment Variable Consistency

### 5.1 JWT_SECRET

| Layer | Config Location | Variable Name | Notes |
|---|---|---|---|
| Edge | `env.d.ts` | `JWT_SECRET: string` (required) | Set via wrangler secret |
| Backend | `config.py` | `JWT_SECRET: str` | Default: dev placeholder |

**Status: CONSISTENT** - Both layers reference the same secret name. In production, both must be set to the same value.

### 5.2 EDGE_SHARED_SECRET

| Layer | Config Location | Variable Name | Notes |
|---|---|---|---|
| Edge | `env.d.ts` | `EDGE_SHARED_SECRET: string` (required) | Set via wrangler secret |
| Backend | `config.py` | `EDGE_SHARED_SECRET: Optional[str]` | Required in production when TRUST_EDGE_AUTH=True |

**Status: CONSISTENT** - Both reference the same secret. Backend validates it is set when TRUST_EDGE_AUTH is enabled in production.

### 5.3 ALLOWED_ORIGIN vs ALLOWED_ORIGINS

| Layer | Config Location | Variable Name | Value |
|---|---|---|---|
| Edge | `wrangler.toml` | `ALLOWED_ORIGIN` | `"https://syrabit.ai"` (single value) |
| Edge | `cors.ts` | `ALLOWED_ORIGINS` (hardcoded array) | `['https://syrabit.ai', 'https://www.syrabit.ai', 'https://app.syrabit.ai']` |
| Backend | `config.py` | `ALLOWED_ORIGINS` | `"https://syrabit.ai,https://www.syrabit.ai,https://app.syrabit.ai"` (CSV) |

**Observations:**
- Edge `ALLOWED_ORIGIN` env var (singular) is used only for R2 asset CORS headers and redirect targets, NOT for the main CORS middleware
- Edge CORS middleware (`cors.ts`) uses a **hardcoded** array, not the env var
- Backend uses a CSV string parsed into a list

#### ISSUE #8: Edge CORS origins are hardcoded, not configurable
- **Severity: LOW**
- The edge worker's CORS allowlist is hardcoded in `cors.ts`, not derived from environment variables
- If a new domain needs to be added, it requires a code change and redeploy rather than an env var update
- Not a bug, but a maintainability concern

### 5.4 BACKEND_URL

| Environment | Value |
|---|---|
| Local dev (wrangler.toml [vars]) | `http://localhost:8000` |
| Production (wrangler.toml [env.production.vars]) | `https://syrabit-backend-851687450401.asia-south1.run.app` |

**Production Safety:** Edge `index.ts` has a guard that rejects requests if `BACKEND_URL` contains `localhost` in production mode. This prevents accidental localhost leakage.

**Status: CORRECT**

### 5.5 JWT_PUBLIC_KEY

| Layer | Config Location | Required |
|---|---|---|
| Edge | `env.d.ts` | Optional (`JWT_PUBLIC_KEY?: string`) |
| Backend | `config.py` | Optional (required only when `JWT_ALGORITHM=RS256`) |

**Status: CONSISTENT** - Both support RS256 as optional, both fall back to HS256.

---

## 6. CORS Consistency

### 6.1 Comparison

| Property | Edge (`cors.ts`) | Backend (`config.py`) |
|---|---|---|
| Production Origins | `['https://syrabit.ai', 'https://www.syrabit.ai', 'https://app.syrabit.ai']` | `"https://syrabit.ai,https://www.syrabit.ai,https://app.syrabit.ai"` |
| Pages Preview Regex | `/^https:\/\/[a-z0-9-]+\.syrabitfrontend\.pages\.dev$/` | `r"^https://[a-z0-9-]+\.syrabitfrontend\.pages\.dev$"` |
| Allow Methods | `GET, POST, PUT, DELETE, OPTIONS` | (handled by FastAPI CORS) |
| Allow Headers | `Content-Type, Authorization, x-anon-id, traceparent` | (handled by FastAPI CORS) |
| Credentials | `true` | `true` |

### 6.2 CORS Consistency Verdict

**Origins: MATCH** - The three production domains are identical in both layers.

**Preview Regex: MATCH** - Both use the same pattern: `[a-z0-9-]+.syrabitfrontend.pages.dev`

**Status: FULLY CONSISTENT**

### 6.3 CORS Application Points

- **Preflight (OPTIONS):** Handled at edge only (returns immediately, never reaches backend)
- **API responses:** Edge applies CORS headers via `applyCorsHeaders()` after proxy
- **Backend:** Has its own CSRF origin check in the unified middleware (rejects mutating requests from disallowed origins)
- **Double-CORS:** Both layers validate origin. This is defense-in-depth, not a conflict. Edge handles browser CORS; backend validates as CSRF protection.

---

## 7. Security Observations

### 7.1 HMAC Timing Attack Resistance

- **Edge (api-proxy.ts):** Uses `crypto.subtle.sign()` (Web Crypto) - timing-safe by nature of the crypto API
- **Backend (auth.py):** Uses `hmac.compare_digest()` for HMAC comparison - **timing-safe**
- **Edge-Secret comparison (auth.py):** Uses `hmac.compare_digest(edge_secret_header, settings.EDGE_SHARED_SECRET)` - **timing-safe**

**Status: SECURE** - All secret comparisons use constant-time operations.

### 7.2 Header Injection Prevention

Edge `index.ts` sanitizes incoming requests by stripping trust headers:
```typescript
sanitizedHeaders.delete('X-Rate-Limited-By');
sanitizedHeaders.delete('X-Edge-Secret');
```

**Observation:** `X-Edge-Signature` and `X-Edge-Timestamp` are NOT stripped from incoming requests. However, these are overwritten by `api-proxy.ts` before forwarding, so external injection is prevented.

**Status: SECURE** - Headers are either stripped or overwritten before reaching backend.

### 7.3 Edge-Trust Bypass Scenarios

The edge-trust chain requires:
1. `X-Edge-Secret` matching the shared secret (constant-time compare)
2. `X-Edge-Signature` HMAC valid for the specific timestamp:userId:path
3. Timestamp within 30 seconds

**Attack vectors considered:**
- **Replay attack:** Mitigated by 30-second timestamp window
- **Path manipulation:** HMAC includes the exact path, preventing path substitution
- **User ID spoofing:** HMAC includes the user ID from the verified JWT

**Remaining risk:** If `EDGE_SHARED_SECRET` is compromised, an attacker can forge any user identity. This is inherent to shared-secret architectures and is mitigated by:
- Secret rotation capability
- Cloud Run IAM (identity token requirement)
- Backend validation of HMAC signature (not just the shared secret)

### 7.4 Public Paths Security Review

#### Paths that SHOULD be in PUBLIC_PATHS (missing):
- `/api/v1/seo/page/*` - Needed for public SEO pages (ISSUE #7)
- `/api/v1/seo/page-bundle/*` - Needed for public SEO pages (ISSUE #7)
- `/api/v1/seo/page-types/*` - Needed for public SEO pages (ISSUE #7)
- `/api/v1/seo/related/*` - Needed for public SEO pages (ISSUE #7)
- `/api/v1/seo/related-by-chapter/*` - Called without auth (ISSUE #3)

#### Paths in PUBLIC_PATHS that are called WITH auth tokens (wasteful but not broken):
- `/api/v1/admin/login` - Frontend sends no Bearer, only body credentials. Correct.
- `/api/v1/admin/logout` - Frontend sends cookie only, no Bearer. Correct that it is public.

**No paths found that are both protected at edge AND called without tokens by the frontend for authenticated operations** (which would cause silent 401s).

### 7.5 Algorithm Confusion Prevention

Edge `jwt.ts` explicitly rejects `alg: 'none'`:
```typescript
if (!alg || alg.toLowerCase() === 'none') {
  throw new Error('Unsupported algorithm: none');
}
```

Only `HS256` and `RS256` are accepted. Any other algorithm throws an error.

**Status: SECURE**

---

## 8. Summary of Issues

| # | Severity | Description | Impact |
|---|---|---|---|
| 1 | INFORMATIONAL | `/content/subjects-by-course-type` works via prefix match on `/content/subjects` | Fragile but functional |
| 2 | LOW | `/seo/health` not in PUBLIC_PATHS | Only used in admin context, non-issue |
| 3 | LOW | `/seo/related-by-chapter/{id}` not in PUBLIC_PATHS | May fail for anonymous users on public pages |
| 4 | MEDIUM | `/trustpilot/invitation-link` has NO backend handler - always returns 404 | Frontend falls back to hardcoded URL, incomplete feature |
| 5 | INFORMATIONAL | `/admin/logout` in PUBLIC_PATHS is intentional | Correct design for expired-token logout |
| 6 | INFORMATIONAL | Chat feedback path works via OPTIONAL_AUTH prefix match on `/api/v1/chat` | Correct |
| 7 | MEDIUM | SEO page paths (`/seo/page/*`, `/seo/page-bundle/*`, `/seo/page-types/*`, `/seo/related/*`) not in PUBLIC_PATHS but called without auth | Public SEO pages may get 401 for anonymous visitors |
| 8 | LOW | Edge CORS origins are hardcoded in cors.ts, not env-configurable | Maintainability concern |

### Critical Issues: 0
### Medium Issues: 2 (SEO public paths need PUBLIC_PATHS entries; Trustpilot endpoint missing backend handler)
### Low Issues: 3
### Informational: 3

---

## Recommendations

1. **[MEDIUM - ISSUE #7]** Add SEO page paths to edge PUBLIC_PATHS:
   ```typescript
   '/api/v1/seo/page',
   '/api/v1/seo/page-bundle',
   '/api/v1/seo/page-types',
   '/api/v1/seo/related',
   ```

2. **[LOW - ISSUE #3]** Add `/api/v1/seo/related-by-chapter` to PUBLIC_PATHS if it serves public page data.

3. **[LOW - ISSUE #8]** Consider deriving CORS origins from an env var or a shared config to avoid code changes for domain updates.

4. **[MEDIUM - ISSUE #4]** Implement a Trustpilot router in the backend with a `/trustpilot/invitation-link` endpoint, or remove the dead `generateTrustpilotInvitationLink()` function from the frontend if the feature is abandoned.

---

*End of Route Consistency Audit*
