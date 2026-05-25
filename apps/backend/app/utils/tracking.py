"""PostHog event tracking helpers."""
import logging
from typing import Optional
from fastapi import Request

from app.utils.posthog import get_posthog

logger = logging.getLogger(__name__)


async def track_chat_completed(
    request: Optional[Request],
    user_id: str,
    lang: str,
    model: str,
    latency_ms: int,
    user_tier: str,
    streaming: bool = False,
) -> None:
    """
    Track chat_completed event in PostHog.

    SECURITY/PII: Never send message content, user queries, or assistant
    responses to PostHog. Only send metadata (lang, model, latency, tier).
    Sending content violates our privacy policy and DPDP Act compliance.
    """
    posthog = get_posthog(request)
    if not posthog:
        return
    try:
        properties = {
            "lang": lang,
            "model": model,
            "latency_ms": latency_ms,
            "user_tier": user_tier,
        }
        if streaming:
            properties["streaming"] = True
        posthog.capture(
            distinct_id=user_id,
            event="chat_completed",
            properties=properties,
        )
    except Exception as e:
        logger.debug(f"PostHog tracking failed: {e}")
