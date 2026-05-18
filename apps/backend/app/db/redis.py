from upstash_redis import Redis
from app.config import settings
import logging

logger = logging.getLogger(__name__)

_redis: Redis | None = None


async def init_redis() -> None:
    """Initialize Upstash Redis connection"""
    global _redis
    
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
