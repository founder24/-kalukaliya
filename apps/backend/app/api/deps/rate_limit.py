"""Rate limiting dependency for FastAPI endpoints.

Monthly quota tracking only. Per-request burst rate limiting is handled
by the Cloudflare Edge worker (apps/edge/src/middleware/rate-limit.ts).

Architecture note (HF-026): Double rate limiting is intentional:
- Edge: 30 req/hr per language (burst protection, fast rejection at the network edge)
- Backend: 30 req/month total for free tier (quota/billing enforcement)
The edge limit prevents burst abuse (e.g., scripted requests exhausting the LLM budget
in seconds). The backend limit enforces the actual subscription quota boundary.
This is NOT a bug or redundancy - removing either layer creates a gap:
  - Without edge: a burst of 30 requests in 1 second passes backend quota but
    costs real money in LLM API calls before the monthly counter catches up.
  - Without backend: the hourly edge window resets, allowing unlimited monthly usage.
"""

import time
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Request

from app.config import settings
from app.db.redis import get_redis

logger = logging.getLogger(__name__)


async def check_rate_limit(
    user_id: str,
    user_tier: str,
    client_ip: str = None,
    request: Optional[Request] = None,
) -> tuple[bool, int, int, str]:
    """Check if user has exceeded rate limit. Returns (allowed, current_count, limit, limit_type).

    Only tracks monthly quota. Burst/per-request rate limiting is enforced at the edge layer.
    The request parameter is retained for caller compatibility but is not inspected.
    """
    limit = (
        settings.RATE_LIMIT_PRO_TIER
        if user_tier == "pro"
        else settings.RATE_LIMIT_FREE_TIER
    )

    try:
        redis = get_redis()
    except RuntimeError:
        logger.warning("Redis unavailable - rate limiting disabled")
        return True, 0, limit, "monthly"

    try:
        # Monthly quota check
        month_key = time.strftime("%Y-%m", time.gmtime())
        if user_id == "anonymous" and client_ip:
            key = f"rate_anon:{client_ip}:{month_key}"
        else:
            key = f"rate:{user_id}:{month_key}"

        # Track monthly quota
        current_count = await redis.incr(key)
        if current_count == 1:
            next_month = datetime.now(timezone.utc).replace(day=28) + timedelta(days=4)
            expire_at = next_month.replace(day=1, hour=0, minute=0, second=0)
            ttl = int(expire_at.timestamp() - time.time())
            await redis.expire(key, ttl)

        if current_count > limit:
            return False, current_count, limit, "monthly"

        return True, current_count, limit, "monthly"
    except Exception as e:
        logger.warning(f"Rate limit check failed: {e} - allowing request")
        return True, 0, limit, "monthly"
