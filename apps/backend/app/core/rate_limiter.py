"""
Rate Limiter: Token Bucket Implementation with Upstash Redis
Supports atomic operations and rate limit headers

LEGACY MODULE - DEPRECATED: Per-request burst rate limiting is now handled by the
Cloudflare Edge worker (apps/edge/src/middleware/rate-limit.ts). Monthly quota
enforcement lives in apps/backend/app/api/deps/rate_limit.py. This module is retained
only as a reference for the Lua script pattern. Do NOT import or instantiate anything
from this module in new code.
"""

import warnings
import time
from typing import Tuple

# Module-level deprecation warning: guarded to fire only once per process
_deprecation_warned = False
if not _deprecation_warned:
    warnings.warn(
        "app.core.rate_limiter is deprecated. Use app.api.deps.rate_limit for quota "
        "enforcement and the edge worker for burst rate limiting. "
        "This module will be removed in a future release.",
        DeprecationWarning,
        stacklevel=2,
    )
    _deprecation_warned = True

from upstash_redis.asyncio import Redis
from app.config import settings

# Signal that nothing should be imported from this module
__all__ = []


class RateLimiter:
    """
    Token Bucket Rate Limiter using Upstash Redis.

    DEPRECATED: Use apps/backend/app/api/deps/rate_limit.py for monthly quota
    enforcement and the edge worker for per-request burst limiting.

    Features:
    - Atomic operations via Lua scripting
    - Tier-based limits (Free vs Pro)
    - Automatic reset on monthly boundary
    - Returns remaining count and reset time for headers
    """

    def __init__(self, redis_client: Redis):
        warnings.warn(
            "RateLimiter is deprecated. Use app.api.deps.rate_limit for quota "
            "enforcement and the edge worker for burst rate limiting.",
            DeprecationWarning,
            stacklevel=2,
        )
        self.redis = redis_client

        # Lua script for atomic token bucket operation
        self.lua_script = """
        local key = KEYS[1]
        local limit = tonumber(ARGV[1])
        local ttl = tonumber(ARGV[2])
        
        local current = redis.call('GET', key)
        
        if not current then
            -- First request, initialize counter
            redis.call('SET', key, 1)
            redis.call('EXPIRE', key, ttl)
            return {1, limit - 1, ttl}
        end
        
        current = tonumber(current)
        
        if current >= limit then
            -- Rate limit exceeded
            local ttl_remaining = redis.call('TTL', key)
            return {0, 0, ttl_remaining}
        end
        
        -- Increment counter
        local new_count = redis.call('INCR', key)
        local ttl_remaining = redis.call('TTL', key)
        
        if new_count == 1 then
            -- Set expiry if it was just created
            redis.call('EXPIRE', key, ttl)
            ttl_remaining = ttl
        end
        
        return {1, limit - new_count, ttl_remaining}
        """

    async def is_allowed(
        self, user_id: str, tier: str = "free"
    ) -> Tuple[bool, int, int]:
        """
        Check if request is allowed under rate limit.

        Args:
            user_id: Unique user identifier
            tier: User subscription tier ("free" or "pro")

        Returns:
            Tuple of (allowed: bool, remaining: int, reset_seconds: int)
        """
        # Determine limit based on tier
        limit = (
            settings.RATE_LIMIT_PRO_TIER
            if tier == "pro"
            else settings.RATE_LIMIT_FREE_TIER
        )

        # Create key with current month (resets monthly)
        current_month = time.strftime("%Y-%m")
        key = f"rate:{user_id}:{current_month}"

        # Calculate TTL until end of month
        # Simple approach: 30 days in seconds (more precise calculation can be added)
        ttl = 30 * 24 * 60 * 60

        # Execute Lua script atomically
        result = await self.redis.eval(
            script=self.lua_script, keys=[key], args=[str(limit), str(ttl)]
        )

        allowed = bool(result[0])
        remaining = int(result[1])
        reset_seconds = int(result[2])

        return allowed, remaining, reset_seconds

    def get_headers(self, allowed: bool, remaining: int, reset_seconds: int) -> dict:
        """
        Generate rate limit headers for HTTP response.

        Returns:
            Dictionary of header names and values
        """
        from app.core.security import RATE_LIMIT_HEADERS

        return {
            RATE_LIMIT_HEADERS["limit"]: str(
                settings.RATE_LIMIT_PRO_TIER
                if remaining > 1000
                else settings.RATE_LIMIT_FREE_TIER
            ),
            RATE_LIMIT_HEADERS["remaining"]: str(max(0, remaining)),
            RATE_LIMIT_HEADERS["reset"]: str(int(time.time()) + reset_seconds),
        }


# Singleton instance (to be initialized with Redis client in main.py)
rate_limiter_instance = None


def get_rate_limiter(redis_client: Redis) -> RateLimiter:
    """Get or create rate limiter instance.

    DEPRECATED: Do not use. See module docstring for alternatives.
    """
    warnings.warn(
        "get_rate_limiter() is deprecated. Use app.api.deps.rate_limit for quota "
        "enforcement and the edge worker for burst rate limiting.",
        DeprecationWarning,
        stacklevel=2,
    )
    global rate_limiter_instance
    if rate_limiter_instance is None:
        rate_limiter_instance = RateLimiter(redis_client)
    return rate_limiter_instance
