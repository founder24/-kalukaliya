"""Rate limiting dependency for FastAPI endpoints."""

import time
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Request

from app.config import settings
from app.db.redis import get_redis

logger = logging.getLogger(__name__)

# Burst rate limits (per minute)
BURST_LIMIT_FREE = 5
BURST_LIMIT_PRO = 30


async def check_rate_limit(
    user_id: str, user_tier: str, client_ip: str = None, request: Optional[Request] = None
) -> tuple[bool, int, int, str]:
    """Check if user has exceeded rate limit. Returns (allowed, current_count, limit, limit_type)."""
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

    # Check if edge already performed burst rate limiting
    edge_rate_limited = False
    if request:
        edge_rate_limited = request.headers.get("x-rate-limited-by") == "edge"

    try:
        # Monthly quota check
        month_key = time.strftime("%Y-%m", time.gmtime())
        if user_id == "anonymous" and client_ip:
            key = f"rate_anon:{client_ip}:{month_key}"
        else:
            key = f"rate:{user_id}:{month_key}"

        # Use pipeline to batch Redis HTTP calls (reduces round-trips)
        burst_limit = BURST_LIMIT_PRO if user_tier == "pro" else BURST_LIMIT_FREE
        minute_key = int(time.time() // 60)
        burst_key = f"burst:{user_id}:{minute_key}"

        if edge_rate_limited:
            # Edge already checked burst - only check monthly quota
            current_count = await redis.incr(key)
            if current_count == 1:
                next_month = datetime.now().replace(day=28) + timedelta(days=4)
                expire_at = next_month.replace(day=1, hour=0, minute=0, second=0)
                ttl = int(expire_at.timestamp() - time.time())
                await redis.expire(key, ttl)

            if current_count > limit:
                return False, current_count, limit, "monthly"
            return current_count <= limit, current_count, limit, "monthly"
        else:
            # Full rate limit check - pipeline monthly + burst together
            pipe = redis.pipeline()
            pipe.incr(key)
            pipe.incr(burst_key)
            results = await pipe.exec()

            current_count = results[0] if results and len(results) > 0 else 1
            burst_count = results[1] if results and len(results) > 1 else 1

            # Set expiry on first increment
            if current_count == 1:
                next_month = datetime.now().replace(day=28) + timedelta(days=4)
                expire_at = next_month.replace(day=1, hour=0, minute=0, second=0)
                ttl = int(expire_at.timestamp() - time.time())
                await redis.expire(key, ttl)
            if burst_count == 1:
                await redis.expire(burst_key, 60)

            if current_count > limit:
                return False, current_count, limit, "monthly"
            if burst_count > burst_limit:
                return False, burst_count, burst_limit, "burst"

            return current_count <= limit, current_count, limit, "monthly"
    except Exception as e:
        logger.warning(f"Rate limit check failed: {e} - allowing request")
        return True, 0, limit, "monthly"
