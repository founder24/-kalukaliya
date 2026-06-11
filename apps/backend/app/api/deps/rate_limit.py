"""Rate limiting dependency for FastAPI endpoints.

Monthly quota tracking is stored in MongoDB (quota_usage collection).
Per-request burst rate limiting is handled by the Cloudflare Edge worker
(apps/edge/src/middleware/rate-limit.ts).

Architecture note (HF-026): Double rate limiting is intentional:
- Edge: 30 req/hr per language (burst protection, fast rejection at the network edge)
- Backend: 30 req/month total for free tier (quota/billing enforcement)
The edge limit prevents burst abuse (e.g., scripted requests exhausting the LLM budget
in seconds). The backend limit enforces the actual subscription quota boundary.
This is NOT a bug or redundancy — removing either layer creates a gap.
"""

import time
import logging
import re as _re
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Request
from pymongo import ReturnDocument

from app.config import settings

logger = logging.getLogger(__name__)


async def check_rate_limit(
    user_id: str,
    user_tier: str,
    client_ip: str = None,
    request: Optional[Request] = None,
) -> tuple[bool, int, int, str]:
    """Check if user has exceeded monthly quota. Returns (allowed, current_count, limit, limit_type).

    Uses MongoDB quota_usage collection via an atomic upsert. No Redis required.
    The request parameter is retained for caller compatibility but is not inspected.
    """
    limit = (
        settings.RATE_LIMIT_PRO_TIER
        if user_tier == "pro"
        else settings.RATE_LIMIT_FREE_TIER
    )

    try:
        from app.db.mongo import get_mongo_client

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
    except RuntimeError:
        logger.warning("MongoDB unavailable — rate limiting disabled")
        return True, 0, limit, "monthly"

    month_key = time.strftime("%Y-%m", time.gmtime())

    if user_id == "anonymous" and client_ip:
        user_id = "ip_" + _re.sub(r"[^a-z0-9]", "_", client_ip.lower())[:55]

    now = datetime.now(timezone.utc)
    next_month = (now.replace(day=28) + timedelta(days=4)).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )

    try:
        doc = await db.quota_usage.find_one_and_update(
            {"user_id": user_id, "month": month_key},
            {
                "$inc": {"count": 1},
                "$setOnInsert": {"expires_at": next_month},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        current_count = doc["count"] if doc else 1

        if current_count > limit:
            return False, current_count, limit, "monthly"
        return True, current_count, limit, "monthly"

    except Exception as e:
        logger.warning(f"Rate limit check failed: {e} — allowing request")
        return True, 0, limit, "monthly"
