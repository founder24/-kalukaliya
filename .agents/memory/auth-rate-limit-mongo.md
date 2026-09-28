---
name: Auth rate-limit MongoDB failure policy
description: MongoDB-backed auth limiter keys, expiry, and fail-closed behavior
---

## Rule
`_check_rate_limit` in `apps/backend/app/api/v1/auth.py` must NOT import from `app.db.redis`. Redis (Upstash) was removed from the stack on June 11, 2026. The function uses MongoDB's `auth_rate_limit` collection via `get_mongo_client()` and returns a generic HTTP 503 if the limiter is unavailable outside development. Development still bypasses rate limiting; exceeded counts still return HTTP 429.

## Pattern
```python
result = await db.auth_rate_limit.find_one_and_update(
    {"_id": rate_key},                          # rate_key = f"{endpoint}:{ip}:{minute_bucket}"
    {"$inc": {"count": 1}, "$setOnInsert": {"expires_at": now + timedelta(seconds=90)}},
    upsert=True,
    return_document=ReturnDocument.AFTER,
)
```

TTL index on `auth_rate_limit.expires_at` (expireAfterSeconds=0) in `mongo.py create_indexes()`.

**Why:** Allowing authentication attempts through when only the limiter operation fails leaves login, signup, password-reset, refresh, and admin login open to unbounded abuse. An edge WAF is not a replacement for the per-IP application limit.

**How to apply:** Never re-add `from app.db.redis import get_redis` to auth.py. Preserve the atomic endpoint/IP/minute upsert and 90-second TTL. Keep the development bypass, return a generic 503 for storage errors without leaking driver details, and ensure every caller propagates 429/503 instead of continuing authentication on limiter failure.
