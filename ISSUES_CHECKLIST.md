# SYRABIT v3.0 - CRITICAL ISSUES CHECKLIST

**Status**: 🔴 **BLOCKING** | Fix required before production deployment

---

## 🔴 CRITICAL BUGS (Fix Immediately)

### Issue #1: Authentication Bypass - `get_current_user` Always Gets `None`

**Location**: `apps/backend/app/api/v1/auth.py:47`

**Current Code**:
```python
async def get_current_user(token: str = Depends(lambda: None)) -> User:
    """Get current user from JWT token (placeholder - implement proper dependency)"""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        # ❌ token is ALWAYS None from Depends(lambda: None)
```

**Why It's Broken**:
- `Depends(lambda: None)` is a FastAPI pattern that returns `None`
- `token` parameter will always be `None`
- `jwt.decode(None, ...)` raises `JWTError`
- Error handler catches it, returns 401, but the logic is broken

**Fix**:
```python
from fastapi import Header, HTTPException, Depends
from fastapi.security import HTTPBearer

security = HTTPBearer()

async def get_current_user(credentials = Depends(security)) -> User:
    token = credentials.credentials
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
        user = await User.get(user_id)
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return user
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
```

**Impact**: 
- ❌ All /api/v1/chat requests bypass authentication
- ❌ User impersonation trivial
- ❌ Rate limiting per user broken

**Fix Time**: 30 minutes
**Severity**: 🔴 CRITICAL

---

### Issue #2: `user` Parameter Never Injected in Chat Endpoint

**Location**: `apps/backend/app/api/v1/chat.py:51`

**Current Code**:
```python
@router.post("/", response_model=ChatResponse)
async def chat(request: ChatRequest, user: User = None):  # ❌ user always None
    user_tier = user.subscription_tier if user else "free"
    user_id = str(user.id) if user else request.session_id or "anonymous"
```

**Why It's Broken**:
- `user: User = None` has no dependency injector
- Should be: `user: User = Depends(get_current_user)`
- Currently, `user` is always `None`
- All calls fallback to anonymous rate limiting

**Fix**:
```python
@router.post("/", response_model=ChatResponse)
async def chat(request: ChatRequest, user: User = Depends(get_current_user)):
    user_tier = user.subscription_tier  # Now guaranteed User, not None
    user_id = str(user.id)
    # ... rest of code
```

**Impact**:
- ❌ User tier not enforced (pro vs free)
- ❌ Per-user rate limiting broken
- ❌ Usage tracking lost

**Fix Time**: 30 minutes
**Severity**: 🔴 CRITICAL

---

### Issue #3: Anonymous Users Share Same Rate Limit Quota

**Location**: `apps/backend/app/api/v1/chat.py:36`

**Current Code**:
```python
key = f"rate:{user_id}:{time.strftime('%Y-%m')}"  # All anon users: "rate:anonymous:2026-05"
```

**Why It's Broken**:
- All anonymous users use ID = "anonymous"
- All requests share key "rate:anonymous:2026-05"
- One malicious user can exhaust quota for ALL anonymous users

**Fix**:
```python
from fastapi import Request

async def check_rate_limit(user_id: str, user_tier: str, request: Request) -> bool:
    redis = get_redis()
    
    limit = settings.RATE_LIMIT_PRO_TIER if user_tier == "pro" else settings.RATE_LIMIT_FREE_TIER
    
    # Use IP-based tracking for anonymous users
    if user_id == "anonymous":
        client_ip = request.client.host
        key = f"rate_anon:{client_ip}:{time.strftime('%Y-%m')}"
    else:
        key = f"rate:{user_id}:{time.strftime('%Y-%m')}"
    
    # ... rest of rate limiting logic
```

**Impact**:
- ❌ Denial of Service via rate limit exhaustion
- ❌ Legitimate anonymous users blocked
- ❌ SLA violation

**Fix Time**: 1 hour
**Severity**: 🔴 CRITICAL

---

### Issue #4: Missing `timedelta` Import

**Location**: `apps/backend/app/api/v1/chat.py:42`

**Current Code**:
```python
from datetime import datetime, timedelta  # ← WHERE'S timedelta?
# ...
from datetime import datetime  # Only datetime imported!
# ...
expire_at = next_month.replace(day=1, hour=0, minute=0, second=0)
ttl = int(expire_at.timestamp() - time.time())
await redis.expire(key, ttl)  # ❌ This works, but code structure suggests timedelta was intended
```

**Actual Issue**: Line 42 references `timedelta` but import only has `datetime`

**Fix**:
```python
from datetime import datetime, timedelta
```

**Impact**:
- ⚠️ May cause runtime NameError if timedelta is used elsewhere

**Fix Time**: 5 minutes
**Severity**: 🔴 MEDIUM

---

## 🟠 HIGH-PRIORITY BUGS (Fix This Sprint)

### Issue #5: Hardcoded CORS Origin in Edge Worker

**Location**: `apps/edge/src/index.ts:12`

**Current Code**:
```typescript
'Access-Control-Allow-Origin': 'https://syrabit.ai',  // Hardcoded!
```

**Why It's Bad**:
- New frontend domains require redeployment
- Can't do A/B testing on different domains
- Can't support preview URLs

**Fix**:
```typescript
export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const origin = request.headers.get('Origin');
    const allowedOrigins = (env.ALLOWED_ORIGINS || 'https://syrabit.ai').split(',');
    
    if (request.method === 'OPTIONS') {
      return new Response(null, {
        headers: {
          'Access-Control-Allow-Origin': allowedOrigins.includes(origin) ? origin : 'https://syrabit.ai',
          'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
          'Access-Control-Allow-Headers': 'Content-Type, Authorization, CF-Turnstile-Response',
        },
      });
    }
    // ...
  }
};
```

**Fix Time**: 1 hour
**Severity**: 🟠 HIGH

---

### Issue #6: Turnstile Not Required on All Authenticated Endpoints

**Location**: `apps/edge/src/index.ts:20`

**Current Code**:
```typescript
if (url.pathname.startsWith('/api/v1/chat') || url.pathname.startsWith('/api/v1/auth')) {
  const turnstileToken = request.headers.get('CF-Turnstile-Response');
  // ❌ /api/v1/subscription, /api/v1/payment NOT protected
}
```

**Why It's Bad**:
- Bot can hit /api/v1/subscription endpoint without Turnstile
- Payment endpoints exposed to automation
- Rate limiting at API level insufficient

**Fix**:
```typescript
// Protect all /api/ endpoints except /health
if (url.pathname.startsWith('/api/') && !url.pathname.startsWith('/health/')) {
  const turnstileToken = request.headers.get('CF-Turnstile-Response');
  if (!turnstileToken) {
    return new Response(JSON.stringify({ error: 'Bot verification required' }), { 
      status: 403,
      headers: { 'Content-Type': 'application/json' }
    });
  }
  // ... verify turnstile
}
```

**Fix Time**: 1 hour
**Severity**: 🟠 HIGH

---

### Issue #7: No Timeout on Azure Backend Proxy

**Location**: `apps/edge/src/routes/api-proxy.ts` (assumed)

**Problem**: If Azure hangs, Worker waits until context timeout (10-30 seconds)

**Fix**:
```typescript
export async function proxyRequest(
  request: Request,
  azureBackendUrl: string,
  env: Env
): Promise<Response> {
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 5000);  // 5 second timeout
    
    const response = await fetch(azureBackendUrl + request.url.pathname, {
      method: request.method,
      headers: request.headers,
      body: request.body,
      signal: controller.signal,
    });
    
    clearTimeout(timeoutId);
    return response;
  } catch (error) {
    if (error.name === 'AbortError') {
      return new Response(JSON.stringify({ error: 'Backend timeout' }), { status: 504 });
    }
    throw error;
  }
}
```

**Fix Time**: 1 hour
**Severity**: 🟠 HIGH

---

### Issue #8: Rate Limit Endpoint Not Protected

**Location**: `apps/backend/app/api/v1/auth.py:108`

**Current Code**:
```python
@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(refresh_token: str):
    # ❌ NO RATE LIMITING!
    # Attacker can call 1000x/sec to brute-force tokens
```

**Fix**:
```python
from app.core.rate_limiter import RateLimiter

rate_limiter = RateLimiter()

@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    refresh_token: str,
    request: Request = None  # Get client IP
):
    # Rate limit by IP: 10 requests per minute
    client_ip = request.client.host if request else "unknown"
    if not await rate_limiter.check_rate_limit(f"refresh:{client_ip}", limit=10, window=60):
        raise HTTPException(status_code=429, detail="Too many refresh attempts")
    
    # ... existing code
```

**Fix Time**: 1 hour
**Severity**: 🟠 HIGH

---

## 🟡 MEDIUM-PRIORITY ISSUES (Plan for Next Sprint)

### Issue #9: No Refresh Token Rotation

**Location**: `apps/backend/app/api/v1/auth.py:122`

**Problem**: Old refresh tokens never expire; if leaked, attacker has permanent access

**Solution**: Implement token family tracking

**Fix Time**: 3 hours
**Severity**: 🟡 MEDIUM

---

### Issue #10: Missing Frontend Error Boundaries

**Location**: `apps/frontend/src/` (not provided)

**Problem**: No error boundaries = app crash on API failures

**Fix Time**: 2 hours
**Severity**: 🟡 MEDIUM

---

### Issue #11: No Integration Tests

**Location**: `apps/backend/tests/`

**Current**: Only 2 unit test files (security, circuit-breaker)
**Missing**: API endpoint tests, auth tests, payment tests, DB tests

**Fix Time**: 8 hours for basic coverage
**Severity**: 🟡 MEDIUM

---

### Issue #12: No Secrets Rotation

**Location**: `apps/backend/app/config.py`

**Problem**: Secrets loaded once at startup; no dynamic rotation support

**Fix Time**: 4 hours (integrate KeyVault SDK)
**Severity**: 🟡 MEDIUM

---

## Summary Table

| Issue | Severity | Component | Fix Time | Status |
|-------|----------|-----------|----------|--------|
| Auth Bypass | 🔴 CRITICAL | Backend | 30 min | ⏳ TODO |
| Missing Dependency Injection | 🔴 CRITICAL | Backend | 30 min | ⏳ TODO |
| Anon Rate Limit Collision | 🔴 CRITICAL | Backend | 1 hour | ⏳ TODO |
| Missing timedelta Import | 🔴 CRITICAL | Backend | 5 min | ⏳ TODO |
| Hardcoded CORS | 🟠 HIGH | Edge | 1 hour | ⏳ TODO |
| Turnstile Not Universal | 🟠 HIGH | Edge | 1 hour | ⏳ TODO |
| No Backend Timeout | 🟠 HIGH | Edge | 1 hour | ⏳ TODO |
| No Refresh Rate Limit | 🟠 HIGH | Backend | 1 hour | ⏳ TODO |
| No Refresh Rotation | 🟡 MEDIUM | Backend | 3 hours | 📋 PLAN |
| No Error Boundaries | 🟡 MEDIUM | Frontend | 2 hours | 📋 PLAN |
| Missing Tests | 🟡 MEDIUM | Backend | 8 hours | 📋 PLAN |
| No Secrets Rotation | 🟡 MEDIUM | Backend | 4 hours | 📋 PLAN |

**Total Time to Fix Critical**: ~2.5 hours  
**Total Time to Fix High Priority**: ~4 hours  
**Total Time to Fix All**: ~19.5 hours

---

**Generated**: May 22, 2026  
**Action Required**: Do not deploy to production until all 🔴 CRITICAL and 🟠 HIGH issues are resolved.
