"""Legacy FastAPI rate limiting dependency; not used by the live app.

This retired backend stores monthly quota tracking in MongoDB. The live app
reserves and reads quota in Cloudflare D1 through apps/api/src/routes/chat.ts
and apps/api/src/routes/users.ts. Do not route local app or production traffic
through this module.

The FastAPI backend remains only for offline/migration tooling. Runtime routing
is configured in the frontend Vite proxy and Cloudflare Edge Worker.
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

    Dev bypass: when APP_ENV=development the monthly quota is never enforced so
    engineers can test the full pipeline without hitting the free-tier cap.
    """
    if getattr(settings, "APP_ENV", "production") == "development":
        limit = (
            settings.RATE_LIMIT_PRO_TIER
            if user_tier == "pro"
            else settings.RATE_LIMIT_FREE_TIER
        )
        return True, 0, limit, "monthly_dev_bypass"

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
