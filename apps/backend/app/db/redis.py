from typing import Optional
from upstash_redis.asyncio import Redis
from app.config import settings
import logging

logger = logging.getLogger(__name__)

_redis: Optional[Redis] = None


async def init_redis() -> None:
    """Initialize Upstash Redis connection.

    Note (HF-090): Upstash uses HTTP-based requests (not persistent TCP connections),
    so stale connection concerns do not apply. Each request is independent.
    """
    global _redis

    if not settings.UPSTASH_REDIS_REST_URL or not settings.UPSTASH_REDIS_REST_TOKEN:
        logger.warning("UPSTASH_REDIS_REST_URL/TOKEN not set — Redis disabled")
        return

    try:
        _redis = Redis(
            url=settings.UPSTASH_REDIS_REST_URL,
            token=settings.UPSTASH_REDIS_REST_TOKEN,
        )
        logger.info("Upstash Redis connection initialized successfully")
    except Exception as e:
        logger.error(f"Failed to connect to Upstash Redis: {e}")
        raise


def get_redis() -> Redis:
    """Get Redis instance"""
    if _redis is None:
        raise RuntimeError("Redis not initialized. Call init_redis() first.")
    return _redis


async def close_redis() -> None:
    """Close Redis connection (no-op for Upstash HTTP client)"""
    global _redis
    _redis = None
    logger.info("Redis connection closed")
