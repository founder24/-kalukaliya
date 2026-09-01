"""Privacy-safe incident records for failed chat attempts."""

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId

logger = logging.getLogger(__name__)


async def store_dead_letter(
    user_id: str,
    message: str,
    lang: str,
    error: str,
    both_providers_down: bool = False,
    sarvam_error: Optional[str] = None,
    gemini_error: Optional[str] = None,
    correlation_id: Optional[str] = None,
) -> None:
    """
    Store non-identifying failure metadata in the 'dead_letters' collection.

    Called when both Sarvam AND Vertex AI fail for a user message.
    Silently logs failures to avoid cascading errors.

    When ``both_providers_down`` is True (i.e. Sarvam AND Gemini are both
    unavailable), this also fires a de-duplicated admin alert via email so the
    team is notified before users start reporting issues.

    Args:
        user_id:             Accepted for compatibility; never persisted.
        message:             Accepted for compatibility; never persisted.
        lang:                Detected language code.
        error:               Safe error category; raw text is never persisted.
        both_providers_down: Set True only when BOTH providers failed on this request.
        sarvam_error:        Sarvam-specific error string (forwarded to the alert).
        gemini_error:        Gemini-specific error string (forwarded to the alert).
    """
    try:
        from app.db.mongo import get_mongo_client
        from app.config import settings

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        collection = db["dead_letters"]

        document = {
            "correlation_id": correlation_id or str(uuid.uuid4()),
            "lang": lang,
            "error_class": (
                error
                if error in {
                    "timeout",
                    "validation",
                    "upstream_runtime",
                    "upstream_http",
                    "internal",
                }
                else "provider_failure"
            ),
            "timestamp": datetime.now(timezone.utc),
            "status": "pending",
            "retry_count": 0,
        }
        if both_providers_down:
            document["both_providers_down"] = True

        await collection.insert_one(document)
        logger.info(
            "dead_letter_stored",
            extra={
                "correlation_id": correlation_id or str(uuid.uuid4()),
                "lang": lang,
            },
        )
    except Exception as e:
        logger.warning(
            "dead_letter_store_failed",
            extra={
                "correlation_id": correlation_id or str(uuid.uuid4()),
                "error_class": type(e).__name__,
            },
        )

    # Fire the outage alert outside the try block so a MongoDB failure doesn't
    # suppress the notification (the alert itself is fire-and-forget).
    if both_providers_down:
        try:
            from app.services.comms.ai_outage_alert import record_ai_outage

            await record_ai_outage(
                correlation_id=correlation_id or str(uuid.uuid4()),
                sarvam_error=sarvam_error,
                gemini_error=gemini_error,
            )
        except Exception as alert_err:
            logger.warning(
                "ai_outage_alert_record_failed",
                extra={
                    "correlation_id": correlation_id or str(uuid.uuid4()),
                    "error_class": type(alert_err).__name__,
                },
            )


async def list_dead_letters(
    page: int = 1, page_size: int = 20, status_filter: Optional[str] = None
) -> dict:
    """
    List dead letters with pagination and optional status filter.

    Returns a dict with items, total count, page, and page_size.
    """
    from app.db.mongo import get_mongo_client
    from app.config import settings

    client = get_mongo_client()
    db = client[settings.MONGODB_DB_NAME]
    collection = db["dead_letters"]

    query = {}
    if status_filter:
        query["status"] = status_filter

    total = await collection.count_documents(query)
    skip = (page - 1) * page_size

    # Note (HF-067): skip/limit pagination is O(n) for deep pages. For production
    # scale, switch to cursor-based pagination with {"timestamp": {"$lt": last_seen}}.
    cursor = collection.find(query).sort("timestamp", -1).skip(skip).limit(page_size)
    items = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        items.append(doc)

    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def replay_dead_letter(dead_letter_id: str) -> dict:
    """
    Replay a dead letter by re-submitting the message through ChatService.

    Fetches the dead letter by ID, marks it as 'retrying', increments retry_count,
    and attempts to regenerate the response via ChatService.
    On success: marks status 'retried'.
    On failure: marks status 'retry_failed'.
    """
    from app.db.mongo import get_mongo_client
    from app.config import settings
    from app.services.chat_service import ChatService

    client = get_mongo_client()
    db = client[settings.MONGODB_DB_NAME]
    collection = db["dead_letters"]

    doc = await collection.find_one({"_id": ObjectId(dead_letter_id)})
    if not doc:
        raise ValueError("Dead letter not found")

    # Privacy-safe incident records intentionally omit replayable user payloads.
    # Reject them before changing status or incrementing retry_count.
    if not doc.get("user_id") or not doc.get("message"):
        raise ValueError("This privacy-safe incident record is not replayable")

    # Refuse replay if max retries exceeded
    if doc.get("retry_count", 0) >= 3:
        raise ValueError("Dead letter has exceeded maximum retry attempts (3)")

    # Note (HF-089): No exponential backoff between retries. Consider adding
    # a check: reject replay if last_retry < 2^retry_count minutes ago.

    # Atomically mark as retrying with status precondition to prevent concurrent replays
    result = await collection.find_one_and_update(
        {
            "_id": ObjectId(dead_letter_id),
            "status": {"$in": ["pending", "retry_failed"]},
        },
        {"$set": {"status": "retrying"}, "$inc": {"retry_count": 1}},
    )
    if result is None:
        raise ValueError("Dead letter is already being replayed")

    try:
        user_id = doc["user_id"]
        message = doc["message"]
        lang = doc.get("lang", "en")

        detected_lang, target_model = ChatService.resolve_language_and_model(
            message, lang_override=lang
        )
        context_chunks, _ = await ChatService.retrieve_context(message, "free")
        system_prompt = ChatService.build_system_prompt(detected_lang, context_chunks)
        response, _ = await ChatService.call_llm(
            system_prompt=system_prompt,
            sanitized_message=message,
            target_model=target_model,
            detected_lang=detected_lang,
            user_id=user_id,
        )

        # Mark as retried on success
        await collection.update_one(
            {"_id": ObjectId(dead_letter_id)},
            {"$set": {"status": "retried"}},
        )
        return {"status": "retried", "response_preview": response[:200]}

    except Exception as e:
        # Mark as retry_failed
        await collection.update_one(
            {"_id": ObjectId(dead_letter_id)},
            {"$set": {"status": "retry_failed"}},
        )
        return {"status": "retry_failed", "error": str(e)}
