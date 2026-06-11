"""
DEPRECATED: Upstash Redis has been removed from Syrabit.
Monthly quota tracking now uses MongoDB (app.api.deps.rate_limit + app.models.quota).
This module is a stub kept to satisfy any lingering imports; it is a no-op.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


async def init_redis() -> None:
    """No-op — Redis has been removed. Quota tracking moved to MongoDB."""
    logger.debug("init_redis() called but Redis is removed — no-op")


def get_redis():
    raise RuntimeError(
        "Redis has been removed from Syrabit. "
        "Use app.api.deps.rate_limit for quota enforcement."
    )


async def close_redis() -> None:
    """No-op — Redis has been removed."""
