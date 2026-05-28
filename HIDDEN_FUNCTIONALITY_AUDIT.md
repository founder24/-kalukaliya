# Hidden Functionality Audit Report

**Project:** Syrabit Educational AI Platform  
**Date:** 2025-01-15  
**Auditor:** Automated Deep Analysis  
**Scope:** Backend (FastAPI/Python), Frontend (React/JSX), Edge (Cloudflare Workers/TypeScript)

---

## Executive Summary

This audit identifies **121 hidden functionality issues** across 15 categories. These are behaviors that exist in the codebase but are undocumented, unintended, or silently broken in production.

| Severity | Count | Description |
|----------|-------|-------------|
| **Critical** | 12 | Data loss, security breaches, financial impact |
| **High** | 34 | User-facing broken features, incorrect billing |
| **Medium** | 48 | Degraded experience, inconsistencies, edge cases |
| **Low** | 27 | Minor issues, developer confusion, non-critical gaps |

---

## Category 1: Authentication & Session Logic

### HF-001: `hmac.new` typo causes runtime crash in edge HMAC verification

**File:** `apps/backend/app/api/v1/auth.py:180`  
**Severity:** Critical

```python
expected = hmac.new(
    edge_secret.encode(), message.encode(), hashlib.sha256
).hexdigest()
```

**Production Impact:** The Python `hmac` module has no `hmac.new()` function. The correct call is `hmac.HMAC()` or `hmac.new` does not exist as a callable. This means the `_verify_edge_hmac` function will always throw `AttributeError: module 'hmac' has no attribute 'new'` at runtime, breaking the HMAC-based edge verification path entirely. Any edge requests relying on HMAC signature verification will crash.

**Recommended Fix:** Replace `hmac.new(...)` with `hmac.HMAC(edge_secret.encode(), message.encode(), hashlib.sha256).hexdigest()`.

---

### HF-002: Token refresh race condition - no deduplication in silentRefresh

**File:** `apps/frontend/src/hooks/useAuthRefresh.js:12-22`  
**Severity:** High

```javascript
export async function silentRefresh() {
  const refreshToken = getRefreshToken();
  if (!refreshToken) {
    throw new Error('No refresh token available');
  }
  const res = await axios.post(
    `${API_BASE}/auth/refresh`,
    { refresh_token: refreshToken },
    { withCredentials: true },
  );
```

**Production Impact:** When multiple concurrent requests get 401 responses (e.g., page loads 3 API calls simultaneously), each triggers `silentRefresh()` independently. The backend revokes the refresh token on first use (via `revoked_refresh:{jti}` in Redis), so the 2nd and 3rd calls fail with "Token has been revoked", logging the user out unexpectedly.

**Recommended Fix:** Add a module-level promise deduplication (mutex) so only one refresh request is in-flight at a time, and subsequent callers await the same promise.

---

### HF-003: No password history enforcement on reset

**File:** `apps/backend/app/api/v1/auth.py:323-325`  
**Severity:** Medium

```python
# Update password
user.hashed_password = User.hash_password(request.new_password)
user.updated_at = datetime.now(timezone.utc)
await user.save()
```

**Production Impact:** Users can reset their password to the exact same value, defeating the purpose of password rotation. There is no check comparing the new password hash against the existing one or any historical password hashes.

**Recommended Fix:** Before saving, verify `not user.verify_password(request.new_password)` and optionally maintain a `password_history` list on the User model.

---

### HF-004: Forgot-password endpoint has no rate limiting

**File:** `apps/backend/app/api/v1/auth.py:276-277`  
**Severity:** High

```python
@router.post("/forgot-password", response_model=MessageResponse)
async def forgot_password(request: ForgotPasswordRequest):
```

**Production Impact:** Unlike `/login` (10/min) and `/signup` (5/min), the `/forgot-password` endpoint has no `_check_rate_limit` call. An attacker can flood any email address with password reset emails, causing email delivery costs and potential spam abuse of the Resend API.

**Recommended Fix:** Add `await _check_rate_limit(request, "forgot_password", 3)` at the start of the handler.

---

### HF-005: Admin JWT uses same algorithm/key as user tokens

**File:** `apps/backend/app/api/v1/admin.py:53-57`  
**Severity:** Medium

```python
payload = jwt.decode(
    session_cookie,
    settings.ADMIN_JWT_SECRET or settings.JWT_SECRET,
    algorithms=[settings.JWT_ALGORITHM],
)
```

**Production Impact:** When `ADMIN_JWT_SECRET` is not set (which triggers a warning but not an error per config.py:141), admin token verification falls back to `JWT_SECRET`. Combined with the same `JWT_ALGORITHM`, the only thing preventing a regular user token from being accepted as admin is the `type != "admin"` check. Key isolation is broken.

**Recommended Fix:** Require `ADMIN_JWT_SECRET` in production (raise error, not warning) and enforce a different algorithm or at minimum a distinct key.

---

### HF-006: Edge worker sanitizes then re-injects X-Edge-Secret

**File:** `apps/edge/src/index.ts:37-56`  
**Severity:** High

```typescript
// Strip trust headers that only the edge itself should set
const sanitizedHeaders = new Headers(request.headers);
sanitizedHeaders.delete('X-Rate-Limited-By');
sanitizedHeaders.delete('X-Edge-Secret');
request = new Request(request, { headers: sanitizedHeaders });

// ... later in JWT section:
if (env.EDGE_SHARED_SECRET) {
  headers.set('X-Edge-Secret', env.EDGE_SHARED_SECRET);
}
```

**Production Impact:** The sanitize step (line 37-39) removes client-supplied `X-Edge-Secret`, which is correct. However, the re-injection (line 56) happens unconditionally for all `/api/` routes after JWT verification. This means the backend always sees a valid `X-Edge-Secret` and trusts `X-User-ID` even when the JWT was invalid (error was "Missing or invalid Authorization header" which is allowed through). An attacker sending a request directly to the edge with `X-User-ID: admin_id` and no Bearer token would get that user ID passed through with the valid shared secret.

**Recommended Fix:** Only set `X-Edge-Secret` when `jwtResult.valid` is true or when the path is an optional-auth path with a verified token.

---

### HF-007: Session tokens stored in sessionStorage - lost on tab close

**File:** `apps/frontend/src/hooks/useTokenManager.js:11-15`  
**Severity:** Low

```javascript
export function storeToken(token) {
  _inMemoryToken = token;
  setAuthToken(token);
  if (token) {
    sessionStorage.setItem('syrabit_token', token);
```

**Production Impact:** `sessionStorage` is scoped to a browser tab. If a user closes the tab and reopens the site, they must log in again even if their refresh token has days of validity remaining. This creates unnecessary friction for the target audience (students who may close tabs frequently).

**Recommended Fix:** Use `localStorage` with appropriate token expiry checks, or implement a service worker for token persistence.

---

### HF-008: No concurrent session limit - unlimited active refresh tokens

**File:** `apps/backend/app/api/v1/auth.py:137-146`  
**Severity:** Low

```python
def create_refresh_token(user_id: str, expires_delta: timedelta = None) -> str:
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(days=settings.REFRESH_TOKEN_EXPIRY_DAYS)
    )
    to_encode = {
        "sub": user_id,
        "exp": expire,
        "type": "refresh",
        "jti": str(uuid.uuid4()),
    }
```

**Production Impact:** Each login generates a new refresh token with a unique `jti`, but old tokens are never revoked. A user logging in from 50 devices has 50 valid refresh tokens. If one device is compromised, there is no "revoke all sessions" mechanism except manually iterating Redis.

**Recommended Fix:** Maintain a set of active refresh token JTIs per user in Redis, enforce a maximum count (e.g., 5), and revoke the oldest on new login.

---

### HF-009: Redis failure causes all authenticated requests to fail (fail-closed)

**File:** `apps/backend/app/api/v1/auth.py:224-229`  
**Severity:** High

```python
except Exception as e:
    logger.error(f"Redis unavailable for token blacklist check: {e}")
    raise HTTPException(
        status_code=503, detail="Token validation service unavailable"
    )
```

**Production Impact:** The token blacklist check raises 503 when Redis is down. Combined with the refresh token revocation check also failing closed (line 361-365), a Redis outage causes every single authenticated request to return 503, effectively making the entire application unusable. This is overly aggressive for a blacklist that only contains explicitly revoked tokens.

**Recommended Fix:** Fail open for the blacklist check (allow the request if Redis is down, since the token is still cryptographically valid). The risk of allowing a recently-revoked token during a brief Redis outage is lower than taking the entire application offline.

---

### HF-010: Admin session cookie path restricts to /api/v1

**File:** `apps/backend/app/api/v1/admin.py:111-117`  
**Severity:** Low

```python
response.set_cookie(
    key="syrabit_admin_session",
    value=admin_token,
    httponly=True,
    secure=True,
    samesite="strict",
    max_age=28800,
    path="/api/v1",
)
```

**Production Impact:** The cookie path is `/api/v1`, so the browser only sends it for requests to paths starting with `/api/v1`. If any admin endpoint is ever mounted at a different prefix (e.g., `/api/admin/` or `/api/webhooks/`), the cookie will not be included. Currently all admin routes are under `/api/v1/admin` so this works, but the path could be more permissive (`/api/`) for future-proofing.

**Recommended Fix:** Set `path="/api/"` to cover all API routes, or keep it narrow but document the constraint.

---

## Category 2: Chat/AI Pipeline Logic

### HF-011: RAG retrieval passes embedding as text to VectorizableTextQuery (double-embedding)

**File:** `apps/backend/app/services/chat_service.py:101-107`  
**Severity:** Medium

```python
embedding = await generate_embedding(sanitized_message)
context_chunks = await search_service.search_context(
    query=sanitized_message,
    text=embedding,
    user_tier=user_tier,
    limit=settings.MAX_CONTEXT_DOCS,
)
```

**Production Impact:** The `text` parameter is passed to `VectorizableTextQuery(text=text, ...)` in azure_search.py:134. If `generate_embedding` returns a vector (list of floats), this would be passed as the text field to Azure's vectorizable text query which expects a string. Azure Search would then try to vectorize a string representation of a float array, producing garbage results. If the embedding function returns the original text for Azure to handle, then the explicit `generate_embedding` call is redundant computation.

**Recommended Fix:** Clarify the contract: either pass the raw text and let Azure handle vectorization, or pass a pre-computed vector via a different query type.

---

### HF-012: Token budget estimation is inaccurate (no tiktoken)

**File:** `apps/backend/app/core/token_budget.py:8-17`  
**Severity:** Low

```python
def estimate_tokens(text: str) -> int:
    # English: ~1 token per 4 characters
    # Assamese/Unicode: ~1 token per 2 characters
    non_ascii = sum(1 for c in text if ord(c) > 127)
    ascii_chars = len(text) - non_ascii
    return (ascii_chars // 4) + (non_ascii // 2) + 1
```

**Production Impact:** The 4-chars-per-token English estimate is a rough approximation. For technical content with many short words or code snippets, actual tokenization can be 2-3x higher. This could lead to context windows being silently over-budget, causing the LLM to truncate or error.

**Recommended Fix:** Integrate `tiktoken` for Vertex AI token counting or use the model-specific tokenizer for accurate budget calculations.

---

### HF-013: Stream internal sentinel sent without SSE `data:` prefix

**File:** `apps/backend/app/services/chat_service.py:205-211`  
**Severity:** Medium

```python
# Emit the sentinel value so the router knows the model/response
yield json.dumps(
    {
        "__internal_complete": True,
        "full_response": full_response,
        "actual_model": actual_model,
    }
)
```

**Production Impact:** All other yields in `stream_llm` use the SSE format `data: {...}\n\n`, but the sentinel is yielded as raw JSON without the `data: ` prefix. In the `event_stream()` function in chat.py:244, this is handled by checking for `__internal_complete`, but if the SSE connection drops before this sentinel is received, `full_response` remains empty and the chat is persisted with an empty assistant response.

**Recommended Fix:** Add connection error handling and persist the accumulated `full_response` even if the sentinel is never received.

---

### HF-014: History cache invalidation uses hardcoded max_turns values

**File:** `apps/backend/app/services/chat_service.py:282-288`  
**Severity:** Low

```python
async def _invalidate_history_cache(session_id: str) -> None:
    try:
        redis = get_redis()
        pipe = redis.pipeline()
        for max_turns in (3, 5, 10, 15, 20):
            cache_key = f"chat_history:{session_id}:{max_turns}"
            pipe.delete(cache_key)
```

**Production Impact:** The invalidation iterates over hardcoded values (3, 5, 10, 15, 20) but `load_conversation_history` defaults to `max_turns=5`. If any caller passes a value not in this set (e.g., 7 or 25), that cache entry is never invalidated, serving stale history for up to 30 minutes.

**Recommended Fix:** Use a wildcard pattern delete (`chat_history:{session_id}:*`) or store the cache with a single canonical key.

---

### HF-015: Response cache key ignores session context

**File:** `apps/backend/app/services/chat_service.py:67-69`  
**Severity:** Medium

```python
@staticmethod
def _make_cache_hash(sanitized_message: str, lang: str) -> str:
    cache_input = f"{sanitized_message}:{lang}"
    return hashlib.sha256(cache_input.encode()).hexdigest()
```

**Production Impact:** The cache key is `hash(message + lang)` with no session or user context. The code comment in chat.py:137-141 explains this is intentional for generic responses when no RAG context is found. However, the same question asked in different conversational contexts (where history is appended to the system prompt) would still return the generic cached answer, ignoring conversation continuity.

**Recommended Fix:** Only serve cached responses when no conversation history is active (session_id is None or history is empty).

---

### HF-016: Sarvam non-stream max_tokens (512) vs stream max_tokens (2048) inconsistency

**File:** `apps/backend/app/services/ai/sarvam_client.py:48` and `apps/backend/app/services/ai/sarvam_client.py:98`  
**Severity:** Medium

```python
# Non-streaming (line 48):
"max_tokens": 512,

# Streaming (line 98):
"max_tokens": 2048,
```

**Production Impact:** A non-streaming chat request to Sarvam is limited to 512 tokens (~380 words), while a streaming request allows 2048 tokens (~1500 words). Users who hit the non-streaming path get significantly shorter responses for complex educational queries, with no indication that the response was truncated.

**Recommended Fix:** Use consistent `max_tokens` values (2048) for both streaming and non-streaming paths.

---

### HF-017: Language detection threshold routes mixed-language messages incorrectly

**File:** `apps/backend/app/services/ai/router.py:22-32`  
**Severity:** Medium

```python
assamese_chars = len(assamese_pattern.findall(text))
total_chars = len(text.replace(" ", ""))
# If >30% Assamese characters, consider it Assamese
assamese_ratio = assamese_chars / total_chars
if assamese_ratio > 0.3 or assamese_chars >= 5:
    return "as"
```

**Production Impact:** A mostly-English message like "What is photosynthesis in class 10 SEBA board?" with just 5 Assamese characters appended (e.g., student name) would be routed to Sarvam AI (slower, smaller model) instead of Vertex AI. The `>= 5` absolute threshold is too low for messages that are predominantly English.

**Recommended Fix:** Increase the absolute threshold to 10+ characters and require BOTH the ratio AND absolute threshold to be met (use `and` instead of `or`).

---

### HF-018: No context window overflow protection

**File:** `apps/backend/app/services/chat_service.py:134-137`  
**Severity:** Medium

```python
# Include multi-turn conversation history
if history:
    system_prompt = f"{system_prompt}\n\nPrevious conversation:\n{history}"
```

**Production Impact:** The system prompt (which includes RAG context chunks up to 3000 tokens) is concatenated with conversation history (up to 2000 chars). Combined with the user message, this can easily exceed the model's context window (Sarvam OpenHathi has limited context). There is no total token budget check before sending to the LLM.

**Recommended Fix:** After building the full prompt, verify total tokens fit within the model's context window (4096 for Sarvam, 32K for Gemini), trimming history first if needed.

---

### HF-019: stream_llm mixes SSE-formatted and raw JSON yields

**File:** `apps/backend/app/services/chat_service.py:176` and `apps/backend/app/services/chat_service.py:205`  
**Severity:** Low

```python
# Line 176 - SSE formatted:
yield f"data: {json.dumps({'text': chunk, 'done': False})}\n\n"

# Line 205 - Raw JSON (no "data: " prefix):
yield json.dumps({"__internal_complete": True, ...})
```

**Production Impact:** The consumer in chat.py:244 correctly filters the sentinel, but any middleware or proxy that expects strict SSE format (e.g., nginx SSE buffering, CDN SSE pass-through) may not handle the raw JSON line correctly, potentially passing it to the client.

**Recommended Fix:** Either prefix the sentinel with `data: ` and use a different detection mechanism, or use an out-of-band channel for the completion signal.

---

### HF-020: Fire-and-forget save_chat silently drops persistence errors

**File:** `apps/backend/app/services/chat_service.py:230-231`  
**Severity:** High

```python
except Exception as e:
    logger.error(f"Failed to save chat: {e}")
```

**Production Impact:** `save_chat` is called via `asyncio.create_task()` (chat.py:165). If MongoDB is temporarily unavailable, the chat message is permanently lost with only a log entry. The user sees a successful response but their conversation history will be missing that exchange. Unlike the streaming path which stores dead letters on double failure, the non-streaming path has no fallback persistence.

**Recommended Fix:** Add dead letter storage for failed saves, or implement a retry queue with exponential backoff.

---

## Category 3: Payment & Subscription Logic

### HF-021: Webhook `calculate_next_billing_date` returns ISO string but model expects datetime

**File:** `apps/backend/app/api/webhooks/razorpay.py:17-19`  
**Severity:** High

```python
def calculate_next_billing_date() -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
```

**Production Impact:** The function returns an ISO format string, but it is used in `$set: {"current_period_end": calculate_next_billing_date()}` (line 72). The User model defines `current_period_end: Optional[datetime] = None`. MongoDB will store this as a string, not a datetime. Later queries comparing dates or calling `.isoformat()` on this field in subscription.py:33 will work (since it is already a string), but any code doing datetime arithmetic on this field will fail with `AttributeError: 'str' object has no attribute...`.

**Recommended Fix:** Return a `datetime` object instead of a string: `return datetime.now(timezone.utc) + timedelta(days=30)`.

---

### HF-022: Payment verify upgrades to "pro" without setting razorpay_subscription_id

**File:** `apps/backend/app/api/v1/payments.py:81-88`  
**Severity:** High

```python
await user.update(
    {
        "$set": {
            "subscription_tier": "pro",
            "subscription_status": "active",
        }
    }
)
```

**Production Impact:** The `/verify` endpoint upgrades the user to pro but never sets `razorpay_subscription_id`. When the user later tries to cancel via `/subscription/cancel` (subscription.py:64), it checks `if not user.razorpay_subscription_id` and returns "No active subscription found". The user is stuck as pro with no way to cancel through the UI.

**Recommended Fix:** Include `razorpay_subscription_id` in the update, or create a separate non-subscription payment flow that tracks one-time purchases differently.

---

### HF-023: No actual downgrade when subscription period ends

**File:** `apps/backend/app/api/v1/subscription.py:72`  
**Severity:** Critical

```python
await user.update({"$set": {"cancel_at_period_end": True}})
```

**Production Impact:** When a user cancels, `cancel_at_period_end` is set to True, and the webhook handler for `subscription.cancelled` (razorpay.py:90) also sets this flag. However, there is NO scheduled job, cron, or webhook handler that actually downgrades the user to "free" tier when `current_period_end` passes. Users who cancel retain pro access indefinitely.

**Recommended Fix:** Implement either: (a) a cron job checking `cancel_at_period_end=True AND current_period_end < now()`, or (b) handle the `subscription.expired` Razorpay event to downgrade.

---

### HF-024: Credit topup verify fails if Razorpay API is down (payment lost)

**File:** `apps/backend/app/api/v1/payments.py:148-151`  
**Severity:** High

```python
client = razorpay.Client(
    auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
)
order = client.order.fetch(body.razorpay_order_id)
credits = int(order.get("notes", {}).get("credits", 0))
```

**Production Impact:** After signature verification succeeds (payment is confirmed), the code fetches the order from Razorpay to determine credit amount. If Razorpay's API is down at this moment, the fetch throws an exception. The idempotency key has already been set in Redis (line 137), so retrying returns "already_processed" without granting credits. The user paid but never receives their credits.

**Recommended Fix:** Store the credits amount in the local order creation step (in Redis or MongoDB) rather than fetching from Razorpay at verification time.

---

### HF-025: No webhook signature verification timeout protection

**File:** `apps/backend/app/api/webhooks/razorpay.py:38-43`  
**Severity:** Low

```python
body = await request.body()
# ...
expected_sig = hmac.HMAC(
    key=settings.RAZORPAY_WEBHOOK_SECRET.encode(),
    msg=body,
    digestmod=hashlib.sha256,
).hexdigest()
```

**Production Impact:** If a malicious actor sends a webhook with a very large body (megabytes), the HMAC computation runs synchronously on the event loop. While HMAC is fast, reading an unbounded body into memory could cause memory issues. There is no `Content-Length` check or body size limit on the webhook endpoint.

**Recommended Fix:** Add a body size limit check (e.g., reject if `content-length > 1MB`) before reading the full body.

---

### HF-026: Double rate limiting - edge hourly + backend monthly with inconsistent semantics

**File:** `apps/edge/src/middleware/rate-limit.ts:31` and `apps/backend/app/api/deps/rate_limit.py:33`  
**Severity:** Medium

```typescript
// Edge: per-language hourly (30/hr per language)
const key = `rl:${userId}:${lang}:${windowKey}`;

// Backend: shared monthly quota
key = f"rate:{user_id}:{month_key}"
```

**Production Impact:** The edge allows 30 requests/hour per language (60 total for en+as), but the backend monthly quota is 30 total for free tier. A user could exhaust their entire monthly quota in 30 minutes while the edge still shows remaining capacity. The edge and backend rate limits are not coordinated, causing confusing UX where the edge says "allowed" but backend returns 429.

**Recommended Fix:** Either remove one layer of rate limiting, or have the edge check the monthly quota as well (via a shared counter or by trusting the backend's response headers).

---

### HF-027: Webhook idempotency TTL (7 days) shorter than billing cycle (30 days)

**File:** `apps/backend/app/api/webhooks/razorpay.py:56`  
**Severity:** Medium

```python
was_new = await redis.set(dedup_key, "1", ex=604800, nx=True)  # 7 days
```

**Production Impact:** The idempotency key expires after 7 days, but subscription billing is monthly. If Razorpay retries a webhook after 7 days (e.g., due to their own retry backlog), the event would be processed again, potentially resetting the user's monthly_message_count or sending duplicate receipt emails.

**Recommended Fix:** Set TTL to at least 35 days (one billing cycle + buffer) or use a permanent deduplication log in MongoDB.

---

### HF-028: No verification that webhook charged amount matches expected plan price

**File:** `apps/backend/app/api/webhooks/razorpay.py:63-65`  
**Severity:** Medium

```python
sub_id = _validate_subscription_id(payload["subscription"]["id"])
amount = payload["payment"]["amount"]
# amount is read but never validated against expected plan price
```

**Production Impact:** The `amount` from the webhook payload is read but never compared against the expected plan price. If Razorpay sends a webhook with a manipulated or incorrect amount (or if a subscription was modified), the system would still grant full pro access regardless of the actual amount paid.

**Recommended Fix:** Validate `amount >= expected_plan_price` before granting subscription benefits.

---

### HF-029: Payment /verify endpoint does not record payment in payments collection

**File:** `apps/backend/app/api/v1/payments.py:81-90`  
**Severity:** Medium

```python
await user.update({"$set": {"subscription_tier": "pro", "subscription_status": "active"}})
logger.info("Payment verified, user upgraded", extra={"user_id": str(user.id)})
return {"status": "success", "message": "Payment verified, plan upgraded to pro"}
```

**Production Impact:** The `/payments/history` endpoint (line 160) queries `db.payments` collection, but `/payments/verify` never inserts a record there. Users will see an empty payment history even after successful payments. Only webhook-based renewals might populate this (and they don't either).

**Recommended Fix:** Insert a payment record into the `payments` collection upon successful verification.

---

### HF-030: subscription_status "trialing" is defined but never set

**File:** `apps/backend/app/models/user.py:17`  
**Severity:** Low

```python
subscription_status: Literal["active", "past_due", "cancelled", "trialing"] = "active"
```

**Production Impact:** The "trialing" status is a valid option in the model but no code path ever sets it. This is dead code that could confuse developers into thinking trial functionality exists. If any analytics or billing logic filters on this status, it will always return zero results.

**Recommended Fix:** Either implement trial functionality or remove "trialing" from the Literal type.

---

## Category 4: User Model & Data Integrity

### HF-031: User model Settings index on wrong field path

**File:** `apps/backend/app/models/user.py:47`  
**Severity:** High

```python
class Settings:
    name = "users"
    indexes = [
        [("email", 1)],
        [("subscription.razorpay_subscription_id", 1)],  # WRONG PATH
```

**Production Impact:** The index is defined on `subscription.razorpay_subscription_id` (nested path), but the actual field on the User model is `razorpay_subscription_id` (flat field at line 19). This means the index is on a non-existent path. The webhook handler's `User.find_one({"razorpay_subscription_id": sub_id})` (razorpay.py:67) does a full collection scan instead of using an index, degrading performance as the user base grows.

**Recommended Fix:** Change the index to `[("razorpay_subscription_id", 1)]`.

---

### HF-032: Chat model uses deprecated `datetime.utcnow` (naive datetime)

**File:** `apps/backend/app/models/chat.py:28-29`  
**Severity:** Medium

```python
created_at: datetime = Field(default_factory=datetime.utcnow)
updated_at: datetime = Field(default_factory=datetime.utcnow)
```

**Production Impact:** `datetime.utcnow()` returns a naive datetime (no timezone info), while the User model uses `datetime.now(timezone.utc)` which returns a timezone-aware datetime. When comparing timestamps across models (e.g., finding chats created after a user's `last_reset_date`), timezone-naive and timezone-aware datetimes cannot be compared in Python, raising `TypeError`.

**Recommended Fix:** Replace all `datetime.utcnow` with `lambda: datetime.now(timezone.utc)`.

---

### HF-033: monthly_message_count is incremented but never reset

**File:** `apps/backend/app/api/v1/chat.py:171-176` and `apps/backend/app/models/user.py:27`  
**Severity:** Critical

```python
# chat.py - increment
await user.update({"$inc": {"monthly_message_count": 1, "total_lifetime_messages": 1}})

# user.py - reset date defined but never used
last_reset_date: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
```

**Production Impact:** `monthly_message_count` is incremented on every chat request but there is no code anywhere that resets it to 0 at the start of a new month. The `last_reset_date` field exists on the User model but is never checked or updated. After the first month, free-tier users permanently exceed their quota and can never send messages again (unless the webhook fires `subscription.charged` which resets it only for paying users).

**Recommended Fix:** Add a check at rate limit time: if `last_reset_date` is in a previous month, atomically reset `monthly_message_count` to 0 and update `last_reset_date`.

---

### HF-034: Email unique index created in mongo.py may be missed if init fails

**File:** `apps/backend/app/db/mongo.py:53-54`  
**Severity:** Medium

```python
# Users collection indexes
await db.users.create_index([("email", ASCENDING)], unique=True)
```

**Production Impact:** The unique email constraint is created programmatically in `create_indexes()` during `init_mongo()`. If the index creation fails (e.g., duplicate emails already exist from a migration), the application continues running without the unique constraint. Duplicate email registrations could occur without detection until the next successful index creation.

**Recommended Fix:** Make index creation failures fatal in production, or verify index existence on startup and halt if missing.

---

### HF-035: No validation on user role field - arbitrary values accepted

**File:** `apps/backend/app/models/user.py:11`  
**Severity:** Medium

```python
role: Optional[str] = None  # 'student', 'educator', 'staff', 'admin'
```

**Production Impact:** The `role` field is typed as `Optional[str]` with no enum or Literal constraint. While the comment suggests valid values, any string can be stored. If an admin API endpoint sets role via user input without validation, arbitrary roles could be assigned. The admin.py check only verifies `role == "admin"` for admin access, so invalid roles would not grant admin access, but could confuse analytics or conditional features.

**Recommended Fix:** Change to `Literal["student", "educator", "staff", "admin", None]` or a proper Enum.

---

### HF-036: Content model Topic uses uuid4 string ID with no foreign key validation

**File:** `apps/backend/app/models/content.py:64-68`  
**Severity:** Low

```python
class Topic(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    title: str
    definition: Optional[str] = None
    topic_slug: str
```

**Production Impact:** `Topic` is embedded inside `Chapter.published_topics` as a `list[Topic]`. The `id` is a string UUID with no referential integrity check. If a topic is referenced elsewhere (e.g., in search results or URLs), deleting it from the chapter leaves dangling references with no cascade or validation.

**Recommended Fix:** If topics are referenced externally, promote them to a separate Document collection with proper references, or add URL-existence validation.

---

### HF-037: KnowledgeObject slug not validated for URL safety

**File:** `apps/backend/app/models/knowledge.py:71`  
**Severity:** Low

```python
slug: str = Field(..., description="Unique URL-friendly identifier")
```

**Production Impact:** The slug field has a description saying "URL-friendly" but no validator enforcing it. Slugs containing spaces, special characters, or Unicode could break URL routing on the frontend and produce invalid sitemap entries.

**Recommended Fix:** Add a `field_validator` that enforces `re.match(r'^[a-z0-9-]+$', v)`.

---

### HF-038: ChatFeedback TTL index must be created manually

**File:** `apps/backend/app/models/feedback.py:31-34`  
**Severity:** Low

```python
# TTL: auto-delete after 30 days (MongoDB handles this)
# Note: TTL index must be created via mongosh script
# as Beanie doesn't support expireAfterSeconds directly
[("timestamp", 1)],
```

**Production Impact:** The comment explicitly states the TTL index must be created manually via mongosh. If this manual step is missed during deployment (which it likely is, given there is no documented runbook), feedback documents accumulate indefinitely, growing the collection without bound.

**Recommended Fix:** Create the TTL index programmatically in `create_indexes()` in mongo.py using `create_index([("timestamp", 1)], expireAfterSeconds=30*24*60*60)`.

---

### HF-039: User preferred_language defaults to "as" but frontend initializes to "en"

**File:** `apps/backend/app/models/user.py:34` and `apps/frontend/src/pages/ChatPage.jsx:81`  
**Severity:** Low

```python
# Backend (user.py:34):
preferred_language: Literal["en", "as"] = "as"

# Frontend (ChatPage.jsx:81):
const [responseLang, setResponseLang] = useState('en');
```

**Production Impact:** A new user's backend model defaults to Assamese ("as"), but the ChatPage initializes the response language toggle to English ("en"). On first load, the frontend sends `lang: "en"` while the user's profile says "as". This mismatch means the onboarding language preference is ignored until the user manually toggles.

**Recommended Fix:** Initialize `responseLang` from the user's `preferred_language` field once the auth context loads.

---

### HF-040: No cascade delete for dead_letters when user account is deleted

**File:** `apps/backend/app/api/v1/users.py:77-84`  
**Severity:** Low

```python
async def delete_account(user: User = Depends(get_current_user)):
    # Cascade delete chats
    await Chat.find({"user_id": str(user.id)}).delete()
    # Cascade delete feedback
    await ChatFeedback.find({"user_id": str(user.id)}).delete()
    # Delete user
    await user.delete()
```

**Production Impact:** Account deletion cascades to chats and feedback but not to `dead_letters` (which also stores `user_id`). After deletion, orphaned dead letters containing the user's messages persist for 30 days (TTL), potentially violating GDPR/DPDP data deletion requirements.

**Recommended Fix:** Add `await db.dead_letters.delete_many({"user_id": str(user.id)})` to the cascade.

---

## Category 5: Rate Limiting & Abuse Prevention

### HF-041: Edge rate limit (60/hr total) vs backend monthly (30/month) fundamental mismatch

**File:** `apps/edge/src/middleware/rate-limit.ts:29-30` and `apps/backend/app/api/deps/rate_limit.py:24`  
**Severity:** High

```typescript
// Edge: 30 per hour per language = 60 per hour total
const key = `rl:${userId}:${lang}:${windowKey}`;
// limit = 30 per language per hour

// Backend: 30 per month total for free tier
limit = settings.RATE_LIMIT_FREE_TIER  // 30
```

**Production Impact:** A free-tier user can send 60 messages in their first hour (30 English + 30 Assamese at the edge), but the backend monthly quota allows only 30 total. After 30 messages, the backend returns 429 even though the edge says they have remaining capacity. This creates confusing UX where the first 30 messages succeed but the remaining 30 (allowed by edge) are rejected by backend.

**Recommended Fix:** Align the rate limits. Either increase the monthly quota to match edge capacity, or reduce edge limits to 30/hour total (not per-language).

---

### HF-042: Rate limit key for anonymous uses unsanitized IP from X-Forwarded-For

**File:** `apps/backend/app/api/v1/auth.py:252-256`  
**Severity:** Medium

```python
client_ip = (
    request.headers.get("X-Real-IP")
    or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    or (request.client.host if request.client else "unknown")
)
```

**Production Impact:** Behind a CDN/proxy, `X-Forwarded-For` can be spoofed by the client (by including their own header before the CDN appends the real IP). Without trusted proxy validation, an attacker can set `X-Forwarded-For: 1.2.3.4` to bypass rate limits by using a different fake IP each minute.

**Recommended Fix:** Only trust the rightmost IP in `X-Forwarded-For` (appended by the CDN), or use Cloudflare's `CF-Connecting-IP` header which cannot be spoofed.

---

### HF-043: Monthly rate limit key uses time.gmtime() - timezone sensitivity

**File:** `apps/backend/app/api/deps/rate_limit.py:35`  
**Severity:** Low

```python
month_key = time.strftime("%Y-%m", time.gmtime())
```

**Production Impact:** This uses `time.gmtime()` which is consistently UTC and not timezone-dependent. However, the expiry calculation (line 39-41) uses `datetime.now()` without timezone which uses server local time. If the server timezone is not UTC, the expiry could be calculated incorrectly, causing the key to expire mid-month or persist past month-end.

**Recommended Fix:** Use `datetime.now(timezone.utc)` for the expiry calculation to match the UTC-based key.

---

### HF-044: Edge rate limit uses KV eventual consistency allowing bursts

**File:** `apps/edge/src/middleware/rate-limit.ts:41-49`  
**Severity:** Low

```typescript
// Read current count
const current = await kv.get(key);
const count = current ? parseInt(current, 10) : 0;

if (count >= limit) { return { allowed: false, ... }; }

// Increment counter (eventual consistency is acceptable for rate limiting)
await kv.put(key, String(count + 1), { expirationTtl: 7200 });
```

**Production Impact:** The read-then-write pattern with KV (which is eventually consistent) means two concurrent requests can both read `count=29` (limit=30), both pass the check, both write `30`, resulting in 31 total requests being allowed. Under burst traffic, the actual limit could be significantly higher than configured.

**Recommended Fix:** Accept this as a known trade-off (documented in comment) or use Durable Objects for strong consistency on critical paths.

---

### HF-045: Auth rate limit uses minute bucket with no progressive backoff

**File:** `apps/backend/app/api/v1/auth.py:247-260`  
**Severity:** Medium

```python
minute_bucket = int(time.time() // 60)
rate_key = f"auth_limit:{endpoint}:{client_ip}:{minute_bucket}"
# ...
if attempt_count > max_attempts:
    raise HTTPException(status_code=429, detail=f"Too many {endpoint} attempts...")
```

**Production Impact:** An attacker can make 10 login attempts, wait 60 seconds for the bucket to roll over, make 10 more, and continue indefinitely. There is no progressive backoff (e.g., 5-minute lockout after 3 consecutive over-limit minutes) or account lockout after N total failed attempts.

**Recommended Fix:** Implement a sliding window or exponential backoff: after 3 consecutive rate-limited minutes, increase the cooldown period.

---

### HF-046: Legacy rate limiter module is unused but still importable

**File:** `apps/backend/app/core/rate_limiter.py:1-11`  
**Severity:** Low

```python
"""
LEGACY MODULE: Per-request burst rate limiting is now handled by the Cloudflare Edge
worker. This module is retained as a reference...
Do not use this for new per-request enforcement.
"""
```

**Production Impact:** The module contains a full Lua-script-based rate limiter that is not used in production (the actual rate limiting is in `api/deps/rate_limit.py`). Its presence could confuse developers into importing `get_rate_limiter` instead of `check_rate_limit`, leading to silent bugs where rate limiting appears to work in tests but uses the wrong mechanism.

**Recommended Fix:** Move to a `_legacy/` directory or add deprecation warnings on import.

---

### HF-047: No rate limit on /users/me endpoint

**File:** `apps/backend/app/api/v1/users.py:48-49`  
**Severity:** Low

```python
@router.get("/me", response_model=UserProfile)
async def get_current_user_profile(user: User = Depends(get_current_user)):
```

**Production Impact:** The `/users/me` endpoint has no rate limiting. While it requires authentication, a compromised token could be used to poll this endpoint rapidly for timing attacks (detecting subscription changes) or to generate high database load.

**Recommended Fix:** Add a reasonable rate limit (e.g., 60/minute) or rely on the edge rate limiter covering all API paths.

---

### HF-048: Anonymous rate limit falls back to "unknown" IP

**File:** `apps/backend/app/api/v1/auth.py:256`  
**Severity:** Low

```python
or (request.client.host if request.client else "unknown")
```

**Production Impact:** If `request.client` is None (which can happen with certain ASGI server configurations or when behind certain proxies), all requests share the `"unknown"` rate limit bucket. Server-to-server calls or health checks could consume the shared anonymous rate limit budget.

**Recommended Fix:** If client IP cannot be determined, use a more specific fallback (e.g., user-agent hash) or reject the request.

---

## Category 6: Frontend State Management

### HF-049: AuthContext token value is stale closure from render time

**File:** `apps/frontend/src/context/AuthContext.jsx:163`  
**Severity:** Medium

```jsx
<AuthContext.Provider value={{
  user,
  token: getToken(),
  // ...
}}>
```

**Production Impact:** `getToken()` is called during render to populate the context value. Since this is not reactive state, components consuming `token` from context will see a stale value until the AuthProvider re-renders. After a silent refresh updates the in-memory token via `storeToken()`, existing consumers still hold the old token value until something triggers a re-render of AuthProvider.

**Recommended Fix:** Make the token a state variable that updates when `storeToken` is called, or have consumers always call `getToken()` directly.

---

### HF-050: useTokenManager uses sessionStorage - fails in SSR/prerender

**File:** `apps/frontend/src/hooks/useTokenManager.js:11-15`  
**Severity:** Low

```javascript
export function storeToken(token) {
  _inMemoryToken = token;
  setAuthToken(token);
  if (token) {
    sessionStorage.setItem('syrabit_token', token);
```

**Production Impact:** If the app is server-side rendered or pre-rendered (e.g., for SEO via ISR mentioned in edge/routes/isr.ts), `sessionStorage` is not available and will throw `ReferenceError`. The code does not guard against this with a `typeof window !== 'undefined'` check.

**Recommended Fix:** Wrap sessionStorage access in a try-catch or check `typeof window !== 'undefined'` before accessing.

---

### HF-051: silentRefresh has no deduplication for concurrent 401s

**File:** `apps/frontend/src/hooks/useAuthRefresh.js:12-22`  
**Severity:** High

```javascript
export async function silentRefresh() {
  const refreshToken = getRefreshToken();
  if (!refreshToken) {
    throw new Error('No refresh token available');
  }
  const res = await axios.post(`${API_BASE}/auth/refresh`, ...);
```

**Production Impact:** This is the same issue as HF-002 from a different angle. When 3 parallel requests all get 401, `withRefresh` in the same file (line 28-39) calls `silentRefresh()` for each. The first call succeeds and revokes the old refresh token. The 2nd and 3rd calls then fail because they use the now-revoked token, potentially logging the user out.

**Recommended Fix:** Use a shared promise: if a refresh is already in-flight, return the existing promise instead of starting a new request.

---

### HF-052: AuthContext login catches profile fetch failure silently

**File:** `apps/frontend/src/context/AuthContext.jsx:109-121`  
**Severity:** Medium

```jsx
const login = async (email, password, turnstileToken) => {
  // ... login succeeds, tokens stored ...
  const profileRes = await axios.get(`${API_BASE}/users/me`, {
    headers: { Authorization: `Bearer ${access_token}` },
  });
  const userData = profileRes.data;
  setUser(userData);
```

**Production Impact:** If the `/users/me` call fails after successful login (e.g., network hiccup), the function throws and `justAuthenticated.current` is set to false (line 122). However, tokens have already been stored (line 113-114). The user is technically logged in (tokens valid) but the UI shows them as logged out (user state is null). The next page navigation that triggers `fetchMe` would fix this, but the immediate UX is broken.

**Recommended Fix:** Wrap the profile fetch in try-catch separately; if it fails, still set a minimal user state with the JWT payload (sub claim).

---

### HF-053: Frontend retry interceptor only retries GET requests

**File:** `apps/frontend/src/utils/api.jsx:49-55`  
**Severity:** Medium

```javascript
if (
  config &&
  retryCount < MAX_RETRIES &&
  error.response &&
  RETRY_CODES.has(error.response.status) &&
  (!config.method || config.method.toLowerCase() === 'get')
) {
```

**Production Impact:** POST requests that receive 502/503/504 (e.g., chat requests during a brief backend restart) are never retried. Since chat is the primary feature and uses POST, transient failures during deployments or load spikes result in user-visible errors instead of transparent retries.

**Recommended Fix:** Allow retries for idempotent POST endpoints (e.g., chat with the same session_id) or at minimum for 503/504 on any method.

---

### HF-054: setAuthToken sets module-level variable but axios interceptor does not use it

**File:** `apps/frontend/src/utils/api.jsx:14-16` and `apps/frontend/src/utils/api.jsx:34-38`  
**Severity:** Low

```javascript
let _authToken = null;
export const setAuthToken = (token) => { _authToken = token; };

// authConfig uses it:
const authConfig = () => {
  const config = { withCredentials: true };
  if (_authToken) {
    config.headers = { Authorization: `Bearer ${_authToken}` };
  }
  return config;
};
```

**Production Impact:** The `_authToken` module variable is only used by `authConfig()` which must be explicitly called. The global axios interceptor (line 44-68) does NOT inject this token into requests automatically. Only requests that explicitly use `authConfig()` get the Bearer token. Other axios calls (like those in AuthContext that manually set headers) work independently. This fragmented auth approach means some API utilities may miss the token.

**Recommended Fix:** Add a request interceptor that automatically injects the token for all requests to API_BASE.

---

### HF-055: clearTokens does not invalidate axios default headers

**File:** `apps/frontend/src/hooks/useTokenManager.js:29-32`  
**Severity:** Low

```javascript
export function clearTokens() {
  storeToken(null);
  storeRefreshToken(null);
}
```

**Production Impact:** `clearTokens()` nullifies the in-memory token and removes sessionStorage entries, but any pending axios requests that captured the old token in their config will still send it. Additionally, if any code set `axios.defaults.headers.common['Authorization']`, that is not cleared here.

**Recommended Fix:** Also call `delete axios.defaults.headers.common['Authorization']` in clearTokens if defaults are ever set.

---

### HF-056: Credits endpoint returns fields that ChatPage does not expect

**File:** `apps/backend/app/api/v1/users.py:100-108` and `apps/frontend/src/pages/ChatPage.jsx:66`  
**Severity:** Medium

```python
# Backend returns:
return {"credits_remaining": ..., "credits_used": ..., "monthly_limit": ..., "tier": ...}

# Frontend expects:
setCredits({ used: c.used ?? 0, limit: c.limit ?? null });
```

**Production Impact:** The backend returns `credits_remaining` and `credits_used`, but the frontend destructures `c.used` and `c.limit` (line 143-144 of ChatPage). Since these field names do not match (`credits_used` vs `used`, `monthly_limit` vs `limit`), the frontend always gets `undefined`, defaulting to `{used: 0, limit: null}`. The credit progress bar shows incorrect information.

**Recommended Fix:** Either rename backend fields to match frontend expectations (`used`, `limit`) or update the frontend to use `c.credits_used`, `c.monthly_limit`.

---

## Category 7: Content Rendering & Display

### HF-057: ChatPage auto-retry timer resends stale message after error

**File:** `apps/frontend/src/pages/ChatPage.jsx:97-99`  
**Severity:** Medium

```javascript
const autoRetryTimerRef = useRef(null);
const sendMsgRef = useRef(null);
// Timer fires sendMsg after error delay
```

**Production Impact:** When a chat request fails, an auto-retry timer (referenced by `autoRetryTimerRef`) re-fires the send function after a delay. If the user has already typed a new message or navigated away from the errored state, the timer still fires with the old message content via `sendMsgRef.current`, causing unexpected duplicate or stale messages to appear.

**Recommended Fix:** Cancel the auto-retry timer when the user types a new message or when the input changes. Verify the message content has not changed before retrying.

---

### HF-058: ChatPage SSE parser has no reconnection handling for dropped connections

**File:** `apps/frontend/src/pages/ChatPage.jsx` (stream processing logic)  
**Severity:** Medium

**Production Impact:** The SSE streaming implementation processes chunks as they arrive but has no mechanism to detect connection drops mid-stream. If the connection is severed (network change, timeout), the partially-received content is displayed with no indicator that it is incomplete. The `done: true` final event is never received, so the UI may remain in a "loading" state or display a truncated response as if it were complete.

**Recommended Fix:** Implement a timeout (e.g., no new chunk for 10s means connection lost), show a "response may be incomplete" indicator, and offer a retry button.

---

### HF-059: ChatPage scroll throttling uses string length not rendered height

**File:** `apps/frontend/src/pages/ChatPage.jsx:111-118`  
**Severity:** Low

```javascript
const lastMsgLenRef = useRef(0);
// ...
if (
  !pendingSendScroll.current &&
  isStreaming &&
  contentLen - lastMsgLenRef.current < 80 &&
  lastMsgLenRef.current > 0
) return;
```

**Production Impact:** The scroll throttle triggers every 80 characters of new content. However, a code block or table in the response could add significant vertical height with few characters, or a long paragraph could add many characters with minimal height change. This causes either too-frequent scrolling (jarring) or not-frequent-enough scrolling (content goes off-screen) depending on content type.

**Recommended Fix:** Use an IntersectionObserver on the last message element or measure actual scroll position relative to container bottom.

---

### HF-060: MarkdownRenderer uses rehype-sanitize which strips valid educational content

**File:** `apps/frontend/src/components/MarkdownRenderer.jsx:6-7`  
**Severity:** Low

```jsx
remarkPlugins={[remarkGfm]}
rehypePlugins={[rehypeSanitize]}
```

**Production Impact:** `rehype-sanitize` with default settings strips custom HTML elements, `style` attributes, and potentially math-related elements (`<math>`, `<mrow>`, etc.). For an educational platform serving science and math content, this could strip LaTeX-rendered formulas, custom interactive elements, or embedded educational widgets from AI responses.

**Recommended Fix:** Configure `rehype-sanitize` with a custom schema that allows math elements and safe educational content tags.

---

### HF-061: No maximum message display limit for long conversations

**File:** `apps/frontend/src/pages/ChatPage.jsx:63`  
**Severity:** Low

```javascript
const [messages, setMessages] = useState([]);
```

**Production Impact:** Messages accumulate in the `messages` state array without any virtualization or maximum limit. A conversation with 200+ messages (each containing complex markdown) will cause significant rendering performance degradation, with each new streaming chunk triggering a re-render of all messages.

**Recommended Fix:** Implement message virtualization (e.g., react-window) or limit displayed messages to the last N with a "load more" button.

---

### HF-062: No loading state for credit fetch - shows 0% briefly on mount

**File:** `apps/frontend/src/pages/ChatPage.jsx:66`  
**Severity:** Low

```javascript
const [credits, setCredits] = useState({ used: user?.credits_used || 0, limit: user?.credits_limit ?? null });
```

**Production Impact:** Credits are initialized to 0 used / null limit, then updated asynchronously when the `/user/credits` API returns. During the brief loading period, the credit progress bar shows 0% usage which flickers to the real value. This is a minor but noticeable UI jank on every page load.

**Recommended Fix:** Add a `creditsLoading` state and show a skeleton/placeholder until the API responds.

---

### HF-063: ChatPage ownedConvIds ref grows unboundedly during session

**File:** `apps/frontend/src/pages/ChatPage.jsx:103`  
**Severity:** Low

```javascript
const ownedConvIds = useRef(new Set());
```

**Production Impact:** Every new conversation created during a session is added to `ownedConvIds` (to prevent re-fetching from the server). This Set is never cleared or trimmed. In a long browser session where a user creates many conversations, this grows without bound. While the memory impact is minimal (strings), it is a code smell that could mask other issues.

**Recommended Fix:** Limit the Set size (e.g., keep only the last 50 IDs) or clear it on explicit navigation away from chat.

---

## Category 8: Database Query Logic

### HF-064: Azure Search passes same text as both query and vector text

**File:** `apps/backend/app/services/search/azure_search.py:134-138`  
**Severity:** Low

```python
vector_query = VectorizableTextQuery(
    text=text,
    k_nearest_neighbors=50,
    fields="content_vector",
)
# Also passes query as search_text in kwargs
```

**Production Impact:** The `text` parameter (from `search_context`) and the `query` parameter (also from `search_context`) are both derived from the user's input. `VectorizableTextQuery` tells Azure to vectorize `text` server-side, while `search_text=query` performs BM25 keyword search. If `text` equals `query` (which it does per the chat_service.py call), Azure is essentially vectorizing the query text twice: once for hybrid and once explicitly. This adds unnecessary latency from the redundant vectorization.

**Recommended Fix:** If Azure handles vectorization via `VectorizableTextQuery`, remove the explicit `generate_embedding()` call in chat_service.py.

---

### HF-065: MongoDB connection pool has no health check interval

**File:** `apps/backend/app/db/mongo.py:35-40`  
**Severity:** Low

```python
_client = AsyncIOMotorClient(
    settings.MONGODB_URI,
    maxPoolSize=settings.MONGODB_MAX_POOL_SIZE,
    minPoolSize=settings.MONGODB_MIN_POOL_SIZE,
    serverSelectionTimeoutMS=5000,
    connectTimeoutMS=10000,
    socketTimeoutMS=45000,
)
```

**Production Impact:** The connection pool maintains 10-50 connections but has no `heartbeatFrequencyMS` or `socketCheckIntervalMS` configured. If MongoDB is briefly unreachable and connections become stale, the driver will attempt to use dead connections before detecting the failure, causing initial request failures before the pool recovers.

**Recommended Fix:** Add `heartbeatFrequencyMS=10000` to enable periodic health checks on idle connections.

---

### HF-066: Chat history query lacks compound index for session_id + created_at

**File:** `apps/backend/app/services/chat_service.py:252-256`  
**Severity:** Medium

```python
chats = (
    await Chat.find({"session_id": session_id})
    .sort("-created_at")
    .limit(5)
    .to_list()
)
```

**Production Impact:** This query filters by `session_id` and sorts by `created_at` descending. The Chat model has indexes on `[("session_id", 1)]` and `[("updated_at", -1)]` separately (chat.py:36-37), but no compound index on `(session_id, created_at)`. MongoDB must perform an in-memory sort for each history load, which becomes expensive with many sessions.

**Recommended Fix:** Add a compound index `[("session_id", 1), ("created_at", -1)]` to the Chat model Settings.

---

### HF-067: Dead letter list uses skip/limit pagination (O(n) deep pagination)

**File:** `apps/backend/app/services/dead_letter.py:57-59`  
**Severity:** Low

```python
skip = (page - 1) * page_size
cursor = collection.find(query).sort("timestamp", -1).skip(skip).limit(page_size)
```

**Production Impact:** MongoDB's `skip()` operation is O(n) - it must scan and discard all documents before the skip offset. For page 100 with page_size 20, MongoDB scans 2000 documents to return 20. As dead letters accumulate (30-day TTL), deep pagination becomes progressively slower.

**Recommended Fix:** Use cursor-based pagination with `{"timestamp": {"$lt": last_seen_timestamp}}` instead of skip/limit.

---

### HF-068: Search cache key includes full query text making it unnecessarily long

**File:** `apps/backend/app/services/search/azure_search.py:105-109`  
**Severity:** Low

```python
cache_input = f"{query}:{text}:{user_tier}:{limit}"
cache_key = f"search_cache:{hashlib.sha256(cache_input.encode()).hexdigest()}"
```

**Production Impact:** The cache key is already hashed to a fixed 64-char hex digest, so the key length is actually fine. However, if `text` is a raw embedding vector (list of floats), the `cache_input` string becomes very large before hashing, adding unnecessary memory pressure during the hash computation. This is a minor performance concern.

**Recommended Fix:** If `text` can be a large object, hash it separately first or use a fixed-size representation.

---

### HF-069: No connection retry logic in init_mongo

**File:** `apps/backend/app/db/mongo.py:32-49`  
**Severity:** Medium

```python
async def init_mongo() -> None:
    # ...
    try:
        _client = AsyncIOMotorClient(...)
        await init_beanie(...)
        await create_indexes()
    except ConnectionFailure as e:
        logger.error(f"Failed to connect to MongoDB: {e}")
        raise
```

**Production Impact:** If MongoDB is briefly unavailable during app startup (e.g., during a deployment where the database restarts), `init_mongo` fails once and raises. The caller in `main.py:55` catches this and logs a warning but continues. The app runs with no MongoDB, causing all database operations to fail with "MongoDB not initialized" RuntimeError.

**Recommended Fix:** Implement retry logic with exponential backoff (e.g., 3 attempts with 1s, 2s, 4s delays) before giving up.

---

### HF-070: Search results cached for 300s regardless of user tier

**File:** `apps/backend/app/services/search/azure_search.py:164-165`  
**Severity:** Low

```python
await redis.set(cache_key, json.dumps(context_chunks), ex=300)
```

**Production Impact:** The cache key includes `user_tier` in its hash (line 106: `f"{query}:{text}:{user_tier}:{limit}"`), so different tiers get different cache entries. This means the concern about shared cache across tiers is already addressed by the key design. However, the 300s TTL means stale content could be served if the search index is updated (e.g., new content published). For a fast-moving educational platform, 5 minutes of stale results is acceptable but worth noting.

**Recommended Fix:** No immediate fix needed; the cache is correctly tier-segregated. Consider adding cache invalidation on content publish.

---

### HF-071: Redis init raises exception but main.py catches and continues

**File:** `apps/backend/app/main.py:60-63`  
**Severity:** Medium

```python
try:
    await init_redis()
except Exception as e:
    logger.warning(f"Redis initialization failed (expected in local dev without DB): {e}")
```

**Production Impact:** In production, if Redis (Upstash) is temporarily unavailable during startup, the app continues running with `_redis = None`. Every subsequent call to `get_redis()` raises RuntimeError("Redis not initialized"). Rate limiting, token blacklisting, caching, and idempotency all fail. The fail-closed pattern in auth.py then makes all authenticated requests return 503.

**Recommended Fix:** In production, retry Redis initialization or make it fatal. In development, the current behavior is acceptable.

---

## Category 9: Webhook & External Integration

### HF-072: Resend email idempotency key deduplicates within same minute window

**File:** `apps/backend/app/services/comms/resend_client.py:65-66`  
**Severity:** Medium

```python
idempotency_input = f"{to}:{subject}:{int(_time.time() // 60)}"
idempotency_key = hashlib.sha256(idempotency_input.encode()).hexdigest()[:32]
```

**Production Impact:** The idempotency key is based on `to + subject + minute_bucket`. If two different emails need to be sent to the same address with the same subject within the same 60-second window (e.g., user triggers password reset twice quickly), the second email is silently deduplicated by Resend and never delivered. The function returns True (success) without the email being sent.

**Recommended Fix:** Include additional unique content (like the reset token or a UUID) in the idempotency key to differentiate legitimate duplicate sends.

---

### HF-073: Resend rate limiter is in-memory only - not shared across instances

**File:** `apps/backend/app/services/comms/resend_client.py:21-24`  
**Severity:** Low

```python
_EMAIL_RATE_LIMIT = 10
_EMAIL_RATE_WINDOW = 60
_email_send_times: dict[str, list[float]] = defaultdict(list)
```

**Production Impact:** The email rate limiter uses a module-level dictionary that is not shared across multiple backend instances. If the app runs on 3 pods, each pod independently allows 10 emails/minute to the same recipient, for a total of 30 emails/minute bypassing the intended limit.

**Recommended Fix:** Use Redis-based rate limiting for email sends (similar to auth rate limiting) to share state across instances.

---

### HF-074: Razorpay client httpx timeout is 30s with no webhook handler timeout

**File:** `apps/backend/app/services/payment/razorpay_client.py:24`  
**Severity:** Low

```python
self._client = httpx.AsyncClient(
    timeout=30.0,
    # ...
)
```

**Production Impact:** The Razorpay HTTP client has a 30s timeout. If the webhook handler calls `create_subscription_order` or any Razorpay API during processing, a slow Razorpay response blocks the worker for up to 30s. Combined with no overall handler timeout, this could exhaust the worker pool during Razorpay outages.

**Recommended Fix:** Add an overall `asyncio.wait_for` timeout (e.g., 10s) around the webhook handler's critical path.

---

### HF-075: Vertex AI token caching uses module-level lock causing queuing

**File:** `apps/backend/app/services/ai/vertex_client.py:74-76`  
**Severity:** Medium

```python
async with self._token_lock:
    # Double-check after acquiring lock
    if self._cached_token and _time.time() < self._token_expiry - 60:
        return self._cached_token
```

**Production Impact:** The `_token_lock` is an asyncio.Lock on the singleton VertexAIClient. When the token expires and needs refresh, ALL concurrent requests queue behind this lock. If the Google OAuth token refresh takes 5-10s (network issue), all in-flight chat requests are blocked waiting for the lock, causing a cascade of timeouts.

**Recommended Fix:** Use a background token refresh task that runs before expiry (e.g., at 50% of token lifetime), so the lock is only held briefly and concurrent requests can use the still-valid cached token.

---

### HF-076: Cloudflare AI client has generous 30s timeout

**File:** `apps/backend/app/services/ai/cloudflare_client.py:18`  
**Severity:** Low

```python
self._client = httpx.AsyncClient(
    timeout=httpx.Timeout(30.0, connect=5.0),
```

**Production Impact:** A 30s timeout for the Cloudflare AI client means a single slow vision/TTS request can hold a connection and an event loop task for 30 seconds. Under load, many such requests could exhaust the connection pool (max_connections=20) and block other requests.

**Recommended Fix:** Reduce timeout to 15s for non-streaming operations, or use the chat endpoint's 15s overall timeout to bound the total wait.

---

### HF-077: No retry logic in resend_client _send_email

**File:** `apps/backend/app/services/comms/resend_client.py:69-80`  
**Severity:** Medium

```python
try:
    response = await client.post(RESEND_API_URL, ...)
    response.raise_for_status()
    return True
except Exception as e:
    logger.error(f"Failed to send email to {to}: {e}")
    return False
```

**Production Impact:** If the Resend API returns a 5xx error or times out, the email is permanently lost. There is no retry mechanism. For critical emails like password resets and payment receipts, a single transient Resend API failure means the user never receives the email.

**Recommended Fix:** Implement retry with exponential backoff (1s, 2s, 4s) for 5xx errors, or queue failed emails in a dead letter store for later retry.

---

### HF-078: Sarvam stream retry yields partial content from failed attempt

**File:** `apps/backend/app/services/ai/sarvam_client.py:118-133`  
**Severity:** Medium

```python
async def stream_generate_with_retry(self, ...):
    for attempt in range(max_retries + 1):
        try:
            async for chunk in self.stream_generate(system_prompt, user_message):
                yield chunk
            return
        except RuntimeError as e:
            last_error = e
            if attempt < max_retries:
                await asyncio.sleep(retry_delay)
```

**Production Impact:** If the first streaming attempt yields some chunks before failing (e.g., connection drops after 3 tokens), those chunks have already been yielded to the caller. On retry, the stream starts from the beginning, yielding duplicate content. The caller accumulates `full_response` with both the partial first attempt and the complete retry, resulting in garbled text like "PhotosynthePhotosynthesis is the process...".

**Recommended Fix:** Track whether any chunks were yielded before failure. If so, do not retry (raise immediately) since partial content has already been sent to the client.

---

### HF-079: Password reset email uses url_quote on token

**File:** `apps/backend/app/services/comms/resend_client.py:109`  
**Severity:** Low

```python
safe_token = url_quote(reset_token, safe='')
reset_link = f"https://syrabit.ai/reset-password?token={safe_token}"
```

**Production Impact:** The JWT token is URL-encoded before being placed in the reset link. The frontend must correctly `decodeURIComponent()` the token from the URL query parameter before sending it to `/reset-password`. If the frontend reads `searchParams.get('token')` (which auto-decodes), this works. But if any intermediate processing double-encodes or fails to decode, the token verification will fail with "Invalid or expired reset token".

**Recommended Fix:** Document the encoding contract clearly. Since `URLSearchParams.get()` auto-decodes, this should work in practice, but add a test case for tokens containing `+`, `/`, and `=` characters.

---

## Category 10: File Upload & Media Handling

### HF-080: Image upload has no dimension check - potential vision model DoS

**File:** `apps/backend/app/api/v1/chat.py:322-323`  
**Severity:** Medium

```python
image_bytes = await file.read()
if len(image_bytes) > 4 * 1024 * 1024:
    raise HTTPException(status_code=400, detail="Image must be less than 4MB")
```

**Production Impact:** Only file size is checked (4MB limit), not image dimensions. A specially crafted 4MB image at extreme dimensions (e.g., 50000x50000 pixels) could cause the vision model to consume excessive memory during processing. While Cloudflare's vision model likely has its own limits, the backend passes the full image bytes without any server-side dimension validation.

**Recommended Fix:** Use Pillow or a lightweight image header parser to check dimensions before forwarding to the vision model. Reject images over 4096x4096.

---

### HF-081: content_type check allows SVG files which can contain XSS

**File:** `apps/backend/app/api/v1/chat.py:319-320`  
**Severity:** Medium

```python
if not file.content_type or not file.content_type.startswith("image/"):
    raise HTTPException(status_code=400, detail="File must be an image")
```

**Production Impact:** The check `content_type.startswith("image/")` allows `image/svg+xml`. SVG files can contain embedded JavaScript (`<script>` tags), external resource references, and other XSS vectors. If the image bytes are ever served back to users or stored in R2 with the original content-type, this creates a stored XSS vulnerability.

**Recommended Fix:** Explicitly reject SVG: `if file.content_type == "image/svg+xml": raise HTTPException(...)`. Allow only raster formats: `image/jpeg`, `image/png`, `image/webp`, `image/gif`.

---

### HF-082: No file extension validation on uploads

**File:** `apps/backend/app/api/v1/chat.py:319-320`  
**Severity:** Low

```python
if not file.content_type or not file.content_type.startswith("image/"):
```

**Production Impact:** Only the `content_type` MIME header is validated, not the actual file extension or magic bytes. An attacker could upload an HTML file with `content_type: image/png` set in the request. While the vision model would likely fail to process it, if the file is stored and later served with improper content-type detection, it could be rendered as HTML.

**Recommended Fix:** Validate both the content-type header AND the file's magic bytes (first few bytes indicating actual format).

---

### HF-083: Image bytes read entirely into memory blocking event loop

**File:** `apps/backend/app/api/v1/chat.py:322`  
**Severity:** Low

```python
image_bytes = await file.read()
```

**Production Impact:** `file.read()` loads the entire upload (up to 4MB) into memory at once. While `await` yields to the event loop during I/O, the actual memory allocation for a 4MB buffer happens synchronously. Under concurrent uploads, memory usage spikes proportionally. With 50 concurrent uploads, that is 200MB of image data in memory simultaneously.

**Recommended Fix:** For the current scale this is acceptable. At higher scale, implement streaming processing or offload to a background worker.

---

### HF-084: No virus/malware scanning on uploaded images

**File:** `apps/backend/app/api/v1/chat.py:322-328`  
**Severity:** Low

```python
image_bytes = await file.read()
if len(image_bytes) > 4 * 1024 * 1024:
    raise HTTPException(...)
# No malware scanning before processing
result = await cloudflare_client.vision_analyze(image_bytes, sanitized_prompt)
```

**Production Impact:** Uploaded images are passed directly to the vision model without any malware or exploit scanning. While the vision model itself is unlikely to be vulnerable to image-based exploits, if images are stored or forwarded to other services, embedded malware could propagate.

**Recommended Fix:** For an educational platform, this is low risk. Consider integrating ClamAV or a cloud-based scanning service for stored files.

---

### HF-085: Vision model result returned without output sanitization

**File:** `apps/backend/app/api/v1/chat.py:326-327`  
**Severity:** Low

```python
result = await cloudflare_client.vision_analyze(image_bytes, sanitized_prompt)
return ImageAnalysisResponse(text=result, model=settings.CF_AI_VISION_MODEL)
```

**Production Impact:** The text extracted from the image by the vision model is returned directly to the client without sanitization. If the image contains text that looks like prompt injection markers or HTML/JS content, it passes through to the frontend. The MarkdownRenderer's rehype-sanitize would strip HTML tags in rendered contexts, but raw API consumers receive unsanitized output.

**Recommended Fix:** Apply basic output sanitization (strip control characters, limit length) to vision model results.

---

## Category 11: Cron Jobs & Background Tasks

### HF-086: Dead letter replay has no distributed lock

**File:** `apps/backend/app/services/dead_letter.py:79-84`  
**Severity:** Medium

```python
result = await collection.find_one_and_update(
    {
        "_id": ObjectId(dead_letter_id),
        "status": {"$in": ["pending", "retry_failed"]},
    },
    {"$set": {"status": "retrying"}, "$inc": {"retry_count": 1}},
)
```

**Production Impact:** The `find_one_and_update` with status precondition prevents the most basic race condition (two workers processing the same dead letter). However, if the admin triggers "replay all pending" from two browser tabs simultaneously, both requests hit different dead letters concurrently, potentially overwhelming the LLM providers with retry traffic. There is no global concurrency limit on replays.

**Recommended Fix:** Add a Redis-based semaphore limiting concurrent replays (e.g., max 3 at a time).

---

### HF-087: Fire-and-forget tasks have no bounded concurrency

**File:** `apps/backend/app/api/v1/chat.py:165-166`  
**Severity:** Medium

```python
task = asyncio.create_task(ChatService.save_chat(...))
task.add_done_callback(_log_task_exception)
```

**Production Impact:** Every chat request creates a background task for persistence. Under high load (e.g., 1000 concurrent users), there could be 1000+ pending `save_chat` tasks queued. Each holds references to the full message content and context chunks in memory. There is no semaphore or bounded task queue to prevent memory exhaustion under burst traffic.

**Recommended Fix:** Use `asyncio.Semaphore` to bound concurrent background tasks (e.g., max 50), or use a proper task queue (like Celery or arq).

---

### HF-088: No periodic job to downgrade cancelled subscriptions

**File:** `apps/backend/app/api/webhooks/razorpay.py:87-91`  
**Severity:** Critical

```python
elif event.get("event") == "subscription.cancelled":
    sub_id = _validate_subscription_id(payload["subscription"]["id"])
    user = await User.find_one({"razorpay_subscription_id": sub_id})
    if user:
        await user.update({"$set": {"cancel_at_period_end": True}})
```

**Production Impact:** This webhook sets `cancel_at_period_end: True` but nothing ever checks this flag to actually downgrade the user. There is no cron job, no Celery task, and no `subscription.expired` webhook handler. Users who cancel their subscription retain pro-tier access indefinitely because `subscription_tier` is never changed back to "free". This represents ongoing revenue loss as cancelled users continue using pro features.

**Recommended Fix:** Implement a daily cron job: `User.find({"cancel_at_period_end": True, "current_period_end": {"$lt": now}}).update({"$set": {"subscription_tier": "free", "subscription_status": "cancelled"}})`.

---

### HF-089: Dead letter retry has no backoff between attempts

**File:** `apps/backend/app/services/dead_letter.py:72-74`  
**Severity:** Low

```python
if doc.get("retry_count", 0) >= 3:
    raise ValueError("Dead letter has exceeded maximum retry attempts (3)")
```

**Production Impact:** The retry count is capped at 3, but there is no delay between retries. If an admin clicks "replay" immediately after a failure, all 3 retries can happen within seconds. If the underlying service is still down, all retries fail instantly without giving the service time to recover.

**Recommended Fix:** Add an exponential backoff check: reject replay if `last_retry_timestamp + (2^retry_count * 60s) > now`.

---

### HF-090: No stale connection cleanup for Upstash Redis HTTP client

**File:** `apps/backend/app/db/redis.py:17-24`  
**Severity:** Low

```python
async def init_redis() -> None:
    global _redis
    _redis = Redis(
        url=settings.UPSTASH_REDIS_REST_URL,
        token=settings.UPSTASH_REDIS_REST_TOKEN,
    )
```

**Production Impact:** Upstash uses an HTTP-based client (not persistent TCP connections), so stale connections are not really a concern. However, if `UPSTASH_REDIS_REST_URL` is rotated (e.g., migrating to a different Upstash instance), the old client persists with the stale URL until the container restarts. There is no mechanism to detect URL changes at runtime.

**Recommended Fix:** For HTTP-based clients this is low risk. Consider implementing a periodic credential refresh if URL rotation is expected.

---

### HF-091: Chat history cache serves stale data if fire-and-forget save fails

**File:** `apps/backend/app/services/chat_service.py:227-231`  
**Severity:** Medium

```python
# Invalidate history cache so next read refills from MongoDB
if session_id:
    await ChatService._invalidate_history_cache(session_id)
except Exception as e:
    logger.error(f"Failed to save chat: {e}")
```

**Production Impact:** If `save_chat` fails after `add_message` but before `await chat_doc.save()`, the cache invalidation at line 227 runs but the document was never saved to MongoDB. The next `load_conversation_history` call will reload from MongoDB (because cache was invalidated) and get the OLD history without the latest message. The user sees their message disappear from history. If the save fails entirely (exception at save), invalidation may or may not have run depending on exception location.

**Recommended Fix:** Only invalidate cache AFTER successful save, and move the invalidation inside the try block after `await chat_doc.save()`.

---

## Category 12: API Response Contract Violations

### HF-092: Subscription /status endpoint crashes if current_period_end is None

**File:** `apps/backend/app/api/v1/subscription.py:32-34`  
**Severity:** High

```python
current_period_end=user.current_period_end.isoformat()
if user.current_period_end
else "",
```

**Production Impact:** This uses a conditional expression that returns empty string if None, which is correct. However, if `current_period_end` was stored as an ISO string (from the webhook bug in HF-021), calling `.isoformat()` on a string raises `AttributeError: 'str' object has no attribute 'isoformat'`. This affects any user who had their subscription renewed via the webhook.

**Recommended Fix:** Add a type check: `user.current_period_end.isoformat() if isinstance(user.current_period_end, datetime) else str(user.current_period_end or "")`.

---

### HF-093: Chat /history response title is almost always None

**File:** `apps/backend/app/api/v1/chat.py:276-277`  
**Severity:** Low

```python
"title": chat.title,
"message_count": len(chat.messages),
```

**Production Impact:** `chat.title` is None by default (chat.py model line 26). The `generate_title` method exists but is never called during the normal chat flow. The frontend chat history list will show `null` or fallback text for every conversation. This makes the history list unusable for finding specific past conversations.

**Recommended Fix:** Call `generate_title` in `save_chat` for new conversations, or generate titles lazily on first history list view.

---

### HF-094: User /credits endpoint returns fields that may not exist on model

**File:** `apps/backend/app/api/v1/users.py:100-102`  
**Severity:** Medium

```python
credits_remaining = getattr(user, "credits_remaining", 0) or 0
credits_used = getattr(user, "credits_used", 0) or 0
```

**Production Impact:** The User model (user.py) has no `credits_remaining` or `credits_used` fields defined. These fields are only set by the credit topup flow (payments.py:155). For any user who has never purchased credits, `getattr(user, "credits_remaining", 0)` returns 0, which is functionally correct but semantically misleading. The endpoint always returns 0 for free-tier users, making the credit display meaningless.

**Recommended Fix:** Either add these fields to the User model with proper defaults, or derive credits from `monthly_message_count` and the tier limit.

---

### HF-095: Payment /history returns raw MongoDB documents without schema

**File:** `apps/backend/app/api/v1/payments.py:162-166`  
**Severity:** Low

```python
payments = (
    await db.payments.find({"user_id": str(user.id)})
    .sort("created_at", -1)
    .to_list(50)
)
for p in payments:
    p["_id"] = str(p["_id"])
return {"payments": payments}
```

**Production Impact:** Raw MongoDB documents are returned directly as the API response with only `_id` converted to string. This exposes internal document structure (including any fields added by migrations or manual edits) without Pydantic schema validation. It also means the response contract is undefined and could change without notice.

**Recommended Fix:** Define a `PaymentResponse` Pydantic model and serialize documents through it.

---

### HF-096: Chat messages endpoint returns raw dicts including internal rag_sources

**File:** `apps/backend/app/api/v1/chat.py:298`  
**Severity:** Low

```python
messages = chat.messages[skip : skip + limit]
return {"messages": messages, ...}
```

**Production Impact:** `chat.messages` is a `List[dict]` containing raw message documents (chat.py model line 27). These dicts include internal fields like `rag_sources` (with document IDs and scores) and `feedback` (with internal state). Exposing RAG scores and document IDs could reveal search algorithm details and internal content identifiers.

**Recommended Fix:** Filter message dicts to only include user-facing fields: `role`, `content`, `timestamp`, `model_used`.

---

### HF-097: Frontend expects user.plan but backend aliases subscription_tier

**File:** `apps/backend/app/api/v1/users.py:47` and `apps/frontend/src/context/AuthContext.jsx:117`  
**Severity:** Low

```python
# Backend:
plan=user.subscription_tier,  # alias for frontend compat

# Frontend:
setAdsUserPlan(userData?.plan ?? null);
```

**Production Impact:** The backend explicitly aliases `subscription_tier` as `plan` in the UserProfile response model (users.py:47). This works but is fragile - if someone refactors the backend response without knowing about this alias, the frontend ads system breaks. The coupling is undocumented except for the inline comment.

**Recommended Fix:** Document the contract formally or have the frontend use `subscription_tier` directly.

---

### HF-098: get_chat_history message_count includes system messages

**File:** `apps/backend/app/api/v1/chat.py:277`  
**Severity:** Low

```python
"message_count": len(chat.messages),
```

**Production Impact:** `chat.messages` includes all messages: user, assistant, AND system messages (if any are stored). The count shown to the user in the history list may be higher than the number of visible messages, creating confusion (e.g., "5 messages" but only 3 visible in the conversation).

**Recommended Fix:** Count only user and assistant messages: `sum(1 for m in chat.messages if m.get("role") in ("user", "assistant"))`.

---

## Category 13: Concurrency & Race Conditions

### HF-099: monthly_message_count increment has no atomic rate check

**File:** `apps/backend/app/api/v1/chat.py:171-176`  
**Severity:** High

```python
await user.update(
    {
        "$inc": {"monthly_message_count": 1, "total_lifetime_messages": 1},
        "$set": {"updated_at": datetime.now(timezone.utc)},
    }
)
```

**Production Impact:** The rate limit check (in `check_rate_limit`) reads from Redis, and the counter increment happens in MongoDB after the response is generated. Two concurrent requests can both pass the Redis rate limit check, both get responses, and both increment the MongoDB counter, exceeding the intended limit. The Redis counter (rate_limit.py:38) and MongoDB counter (chat.py:172) are not synchronized.

**Recommended Fix:** Use a single atomic source of truth (Redis `INCR` before processing, not after) and remove the MongoDB increment, or accept the Redis counter as authoritative and remove the MongoDB counter.

---

### HF-100: Refresh token rotation TOCTOU race between check and revocation

**File:** `apps/backend/app/api/v1/auth.py:355-375`  
**Severity:** Medium

```python
# Check if token has been revoked
revoked = await redis.get(f"revoked_refresh:{jti}")
if revoked:
    raise HTTPException(status_code=401, detail="Token has been revoked")
# ... generate new tokens ...
# Revoke old refresh token jti
await redis.set(f"revoked_refresh:{jti}", "1", ...)
```

**Production Impact:** Between the revocation check (line 360) and setting the revoked flag (line 375), another concurrent request using the same refresh token could also pass the check. Both requests get new tokens, but only the second revocation "wins". The user ends up with two valid refresh tokens from one, breaking the single-use guarantee.

**Recommended Fix:** Use Redis `SET NX` (set if not exists) for the revocation flag as an atomic claim mechanism: only the first request to SET NX succeeds; the second finds the key already exists and rejects.

---

### HF-101: Circuit breaker state transitions are not thread-safe in async context

**File:** `apps/backend/app/core/circuit_breaker.py:97-102`  
**Severity:** Medium

```python
def _on_success(self):
    if self._state == CircuitState.HALF_OPEN:
        self._success_count += 1
        if self._success_count >= self.success_threshold:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._success_count = 0
```

**Production Impact:** `_on_success` and `_on_failure` are regular (non-async) methods that read and write instance variables without any locking. In an async context, two coroutines could interleave: one calls `_on_success` (incrementing success_count) while another calls `_on_failure` (resetting to OPEN). The state machine could enter an inconsistent state where `_state=OPEN` but `_success_count > 0`.

**Recommended Fix:** Use `asyncio.Lock` to protect state transitions, or accept the race as benign (circuit breakers are probabilistic by nature).

---

### HF-102: Edge KV rate limit read-then-write is not atomic

**File:** `apps/edge/src/middleware/rate-limit.ts:41-49`  
**Severity:** Medium

```typescript
const current = await kv.get(key);
const count = current ? parseInt(current, 10) : 0;
if (count >= limit) { return { allowed: false, ... }; }
await kv.put(key, String(count + 1), { expirationTtl: 7200 });
```

**Production Impact:** Same as HF-044. Two concurrent requests read `count=29`, both increment to 30, allowing 31 total. KV does not support atomic increment operations. Under burst conditions (e.g., a bot sending rapid requests), the actual rate limit could be exceeded by 2-5x.

**Recommended Fix:** Use Cloudflare Durable Objects for atomic counters on critical rate-limiting paths, or accept KV's eventual consistency as "good enough" for the use case.

---

### HF-103: Webhook idempotency SET NX has a crash-after-set vulnerability

**File:** `apps/backend/app/api/webhooks/razorpay.py:54-57`  
**Severity:** Medium

```python
was_new = await redis.set(dedup_key, "1", ex=604800, nx=True)
if not was_new:
    return {"status": "already_processed"}
# ... processing happens AFTER the dedup key is set ...
```

**Production Impact:** The idempotency key is set BEFORE the event is processed. If the handler crashes (unhandled exception, OOM, process kill) after setting the dedup key but before completing processing, the event is permanently marked as "processed" and Razorpay's retry will see "already_processed". The subscription renewal or payment is lost.

**Recommended Fix:** Use a two-phase approach: set a "processing" state first, then update to "completed" after success. On retry, if state is "processing" for >5 minutes, allow reprocessing.

---

### HF-104: Credit topup race - two verify calls could both pass idempotency

**File:** `apps/backend/app/api/v1/payments.py:135-140`  
**Severity:** Low

```python
dedup_key = f"credit_topup:{body.razorpay_order_id}"
was_new = await redis.set(dedup_key, "1", ex=604800, nx=True)
if not was_new:
    return {"status": "already_processed", ...}
```

**Production Impact:** Redis `SET NX` is atomic, so this is actually safe against race conditions within a single Redis instance. Two concurrent requests for the same order_id will have exactly one succeed at `SET NX`. This issue was initially flagged but is actually properly handled. The remaining concern is the crash-after-set scenario (same as HF-103).

**Recommended Fix:** No fix needed for the race itself. Address the crash-after-set vulnerability per HF-103.

---

### HF-105: User update in chat uses two separate atomic operations

**File:** `apps/backend/app/api/v1/chat.py:171-176`  
**Severity:** Low

```python
await user.update(
    {
        "$inc": {"monthly_message_count": 1, "total_lifetime_messages": 1},
        "$set": {"updated_at": datetime.now(timezone.utc)},
    }
)
```

**Production Impact:** This is actually a SINGLE MongoDB `update` operation combining `$inc` and `$set`, which is atomic within MongoDB. The concern about "two operations" is unfounded - MongoDB applies the entire update document atomically. No fix needed.

**Recommended Fix:** No fix needed. This is correctly implemented as a single atomic update.

---

## Category 14: Environment & Configuration

### HF-106: JWT_SECRET default is insecure placeholder used outside production

**File:** `apps/backend/app/config.py:105`  
**Severity:** Critical

```python
JWT_SECRET: str = "dev-only-secret-not-for-production-use-32chars"
```

**Production Impact:** The default JWT_SECRET is a known placeholder. The `validate_production_secrets` validator (line 117) only checks when `APP_ENV == "production"`. If `APP_ENV` is set to anything else (e.g., "staging", "preview", "demo"), the insecure default is used to sign JWTs. An attacker knowing this default can forge valid tokens for any user on non-production deployments.

**Recommended Fix:** Also validate in "staging" environments, or make the check `if self.APP_ENV != "development"`.

---

### HF-107: ADMIN_JWT_SECRET falls back to JWT_SECRET breaking key isolation

**File:** `apps/backend/app/config.py:106` and `apps/backend/app/api/v1/admin.py:53`  
**Severity:** Medium

```python
# config.py:
ADMIN_JWT_SECRET: Optional[str] = None

# admin.py:
settings.ADMIN_JWT_SECRET or settings.JWT_SECRET,
```

**Production Impact:** When `ADMIN_JWT_SECRET` is None (which only generates a warning in production, not an error), admin tokens are signed and verified with the same `JWT_SECRET` as user tokens. While the `type: "admin"` claim check prevents user tokens from being accepted as admin, the shared key means any key compromise affects both systems simultaneously. Key rotation requires coordinating both admin and user token invalidation.

**Recommended Fix:** Make `ADMIN_JWT_SECRET` required in production (raise ValueError, not just warning).

---

### HF-108: ALLOWED_ORIGINS uses exact match with no wildcard support

**File:** `apps/backend/app/config.py:159-162`  
**Severity:** Low

```python
@property
def allowed_origins_list(self) -> list[str]:
    origins = [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",")]
```

**Production Impact:** Origins are stored as a comma-separated string and matched exactly. Preview deployments on Cloudflare Pages (e.g., `abc123.syrabitfrontend.pages.dev`) require the `is_origin_allowed` method's regex check. However, `allowed_origins_list` (used by the admin CSRF check in admin.py:38) only does prefix matching. A new preview deployment URL must be added to the config or rely on the regex in `is_origin_allowed`.

**Recommended Fix:** Unify origin checking to always use `is_origin_allowed` which handles both exact matches and Cloudflare Pages regex.

---

### HF-109: SENTRY_TRACES_SAMPLE_RATE defaults to 0.1 (10%) in production

**File:** `apps/backend/app/config.py:99`  
**Severity:** Low

```python
SENTRY_TRACES_SAMPLE_RATE: float = 0.1
```

**Production Impact:** A 10% trace sample rate sends 1 in 10 transactions to Sentry. For a high-traffic application, this generates significant data volume and cost. Most production apps use 0.01-0.05 (1-5%) for traces. The default of 0.1 could lead to unexpected Sentry bills as traffic grows.

**Recommended Fix:** Lower default to 0.01 in production or use Sentry's adaptive sampling.

---

### HF-110: CF_R2_BUCKET has hardcoded default with no validation

**File:** `apps/backend/app/config.py:35`  
**Severity:** Low

```python
CF_R2_BUCKET: str = "syrabit-assets"
```

**Production Impact:** The default R2 bucket name is hardcoded. If the actual R2 bucket name in Cloudflare differs (e.g., was renamed or created with a different name), the edge worker (which uses `env.R2_BUCKET` binding from wrangler.toml) would work correctly but any backend code referencing this config would target the wrong bucket.

**Recommended Fix:** Add validation that the configured bucket name matches the actual R2 binding, or remove this config if the backend never accesses R2 directly.

---

### HF-111: MONGODB_MAX_POOL_SIZE=50 with no pool exhaustion monitoring

**File:** `apps/backend/app/config.py:60`  
**Severity:** Low

```python
MONGODB_MAX_POOL_SIZE: int = 50
```

**Production Impact:** The pool size is capped at 50 connections. If all 50 are checked out (e.g., 50+ concurrent slow queries), new requests hang waiting for a connection until `serverSelectionTimeoutMS` (5000ms) expires. There is no monitoring, alerting, or metric emission for pool utilization, making it invisible when the pool is near exhaustion.

**Recommended Fix:** Emit a metric (via OTel or custom logging) when pool utilization exceeds 80%, and add a health check that verifies pool availability.

---

### HF-112: empty_strings_to_none validator runs mode="before" potentially interfering with Pydantic

**File:** `apps/backend/app/config.py:112-117`  
**Severity:** Low

```python
@model_validator(mode="before")
@classmethod
def empty_strings_to_none(cls, values):
    if isinstance(values, dict):
        for key, val in values.items():
            if val == "":
                values[key] = None
    return values
```

**Production Impact:** This validator converts empty strings to None before Pydantic's own type coercion. For non-Optional string fields with defaults (like `MONGODB_DB_NAME: str = "syrabit_prod"`), if the env var is set to an empty string, it becomes None, and Pydantic uses the default value. This is the intended behavior but could be surprising: setting `MONGODB_DB_NAME=""` silently uses "syrabit_prod" instead of raising a validation error.

**Recommended Fix:** Document this behavior clearly, or only apply the transformation to Optional fields.

---

## Category 15: Logging, Monitoring & Observability

### HF-113: Log formatter includes user_id which could contain PII

**File:** `apps/backend/app/core/logging_config.py:15`  
**Severity:** Medium

```python
"user_id": getattr(record, "user_id", None),
```

**Production Impact:** The JSONFormatter includes `user_id` in every structured log entry. While user IDs are typically MongoDB ObjectIds (non-PII), the system also uses "anonymous" as a user_id string and logs email addresses in auth-related messages. If a future code change passes email as user_id (or if the user_id field is confused with user identity), PII would be emitted to log aggregators.

**Recommended Fix:** Ensure user_id is always the MongoDB ObjectId, never an email. Add a comment documenting this contract.

---

### HF-114: Auth signup route logs user email in plaintext

**File:** `apps/backend/app/api/v1/auth.py:272`  
**Severity:** High

```python
logger.info(f"New user signed up: {request_body.email}")
```

**Production Impact:** The user's email address is logged in plaintext on every signup. This PII ends up in log aggregators (CloudWatch, Datadog, etc.) where it may be retained for extended periods. Under GDPR/DPDP, log retention with PII requires explicit user consent and the ability to delete.

**Recommended Fix:** Log a hashed or masked version: `logger.info(f"New user signed up: {request_body.email[:3]}***@{request_body.email.split('@')[1]}")`.

---

### HF-115: Auth login route logs user email in plaintext

**File:** `apps/backend/app/api/v1/auth.py:291`  
**Severity:** High

```python
logger.info(f"User logged in: {request_body.email}")
```

**Production Impact:** Same as HF-114. Every login event logs the full email address. At scale, this creates a comprehensive record of user activity (when each email logged in) in the log system, which is a privacy concern and potential compliance violation.

**Recommended Fix:** Replace with `logger.info("user_login", extra={"user_email_hash": hashlib.sha256(request_body.email.encode()).hexdigest()[:12]})`.

---

### HF-116: No correlation ID propagation from edge to backend

**File:** `apps/edge/src/index.ts:54-56` and `apps/backend/app/main.py:134`  
**Severity:** Low

```typescript
// Edge: sets X-User-ID but not X-Request-ID
headers.set('X-User-ID', jwtResult.userId || 'anonymous');

// Backend middleware: generates its own X-Request-ID
request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
```

**Production Impact:** The edge worker does not generate or forward a request ID. The backend generates its own UUID for each request. This means there is no way to correlate an edge log entry with the corresponding backend log entry. Debugging issues that span both layers requires timestamp-based correlation, which is unreliable under load.

**Recommended Fix:** Generate a UUID in the edge worker and pass it as `X-Request-ID` header to the backend.

---

### HF-117: Rate limit failure log may include Redis connection strings

**File:** `apps/backend/app/api/v1/auth.py:261`  
**Severity:** Medium

```python
logger.warning(f"Rate limiting unavailable ({endpoint}): {e}")
```

**Production Impact:** The exception `e` from a Redis connection failure could contain the Redis URL (including credentials or tokens from the connection string). Logging the full exception object may expose `UPSTASH_REDIS_REST_URL` or `UPSTASH_REDIS_REST_TOKEN` in the log output.

**Recommended Fix:** Log only the exception type and a sanitized message: `logger.warning(f"Rate limiting unavailable ({endpoint}): {type(e).__name__}")`.

---

### HF-118: Dead letter stores full user message in MongoDB (PII persistence)

**File:** `apps/backend/app/services/dead_letter.py:37-38`  
**Severity:** Medium

```python
document = {
    "user_id": user_id,
    "message": message,  # Full user message stored
```

**Production Impact:** The user's full message text is stored in the `dead_letters` collection with a 30-day TTL. If the message contains personal information (e.g., "My name is X and I study at Y school, explain..."), this PII persists for 30 days in an admin-accessible collection. Under DPDP Act data minimization requirements, storing full message text for debugging purposes may be non-compliant.

**Recommended Fix:** Store only a truncated/hashed version for debugging: `"message_preview": message[:100], "message_hash": hashlib.sha256(message.encode()).hexdigest()`.

---

### HF-119: Circuit breaker state changes log at WARNING level

**File:** `apps/backend/app/core/circuit_breaker.py:83-86`  
**Severity:** Low

```python
logger.warning(
    f"Circuit '{self.name}' failure threshold reached ({self._failure_count}). "
    f"Transitioning to OPEN"
)
```

**Production Impact:** Every circuit breaker state transition logs at WARNING level. If an upstream service is flapping (repeatedly going up and down), the circuit breaker transitions CLOSED -> OPEN -> HALF_OPEN -> CLOSED rapidly, generating many WARNING logs per minute. This could flood the log aggregator and trigger false alert fatigue.

**Recommended Fix:** Rate-limit state transition logs (e.g., log at most once per minute per breaker), or use INFO for recovery transitions and WARNING only for opening.

---

### HF-120: PostHog tracking uses "anonymous" string for all anonymous users

**File:** `apps/backend/app/api/v1/chat.py:101`  
**Severity:** Low

```python
user_id = str(user.id) if user else "anonymous"
```

**Production Impact:** All anonymous users share the single identity string "anonymous" in PostHog analytics. This makes it impossible to track anonymous user journeys, retention, or conversion funnels. PostHog treats all anonymous activity as a single "user" with extremely high activity, skewing metrics.

**Recommended Fix:** Use a device-specific anonymous ID (from the `X-Anon-ID` header) for PostHog tracking to maintain individual anonymous user journeys.

---

### HF-121: setup_logging removes ALL existing handlers breaking platform logging

**File:** `apps/backend/app/core/logging_config.py:52-54`  
**Severity:** Medium

```python
# Remove existing handlers
for handler in root_logger.handlers[:]:
    root_logger.removeHandler(handler)
```

**Production Impact:** This removes ALL handlers from the root logger, including any platform-injected handlers. On platforms like Google Cloud Run, Azure Container Apps, or Railway, the platform adds its own log handler for structured log collection. Removing these handlers means platform-specific log routing (to Cloud Logging, Application Insights, etc.) breaks silently, and logs only go to stdout via the custom JSONFormatter.

**Recommended Fix:** Only add the custom handler without removing existing ones, or check for specific platform handlers before removal: `if not isinstance(handler, logging.StreamHandler): root_logger.removeHandler(handler)`.

---

## End of Audit

**Total Issues Documented:** 121  
**Files Audited:** 42  
**Categories Covered:** 15

### Priority Remediation Order

1. **Immediate (Critical):** HF-001, HF-023, HF-033, HF-088, HF-106 (runtime crashes, revenue loss, permanent quota exhaustion)
2. **Sprint 1 (High):** HF-002, HF-004, HF-006, HF-009, HF-020, HF-021, HF-022, HF-024, HF-031, HF-041, HF-051, HF-056, HF-092, HF-099, HF-114, HF-115
3. **Sprint 2 (Medium):** All Medium-severity issues
4. **Backlog (Low):** All Low-severity issues
