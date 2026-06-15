---
name: Auth rate limit MongoDB migration
description: _check_rate_limit in auth.py was Redis-only; migrated to MongoDB; TTL index details
---

## Rule
`_check_rate_limit` in `apps/backend/app/api/v1/auth.py` must NOT import from `app.db.redis`. Redis (Upstash) was removed from the stack on June 11, 2026. The function now uses MongoDB `auth_rate_limit` collection via `get_mongo_client()`.

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

**Why:** Redis removed June 11, 2026. Every login/signup in production was logging `WARNING: Rate limiting unavailable (signup), failing open: RuntimeError`. CF WAF and bcrypt still provide outer brute-force protection when rate-limit DB is down.

**How to apply:** Never re-add `from app.db.redis import get_redis` to auth.py. If rate-limit storage needs to change again, update the MongoDB approach or use a different provider — never Redis unless Upstash is re-added to the SM secrets.
