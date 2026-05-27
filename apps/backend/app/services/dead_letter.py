"""Dead Letter Storage: Persists failed chat attempts for later analysis and replay."""

import logging
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId

logger = logging.getLogger(__name__)


async def store_dead_letter(
    user_id: str,
    message: str,
    lang: str,
    error: str,
) -> None:
    """
    Store a failed chat attempt to MongoDB 'dead_letters' collection.

    Called when both Sarvam AND Vertex AI fail for a user message.
    Silently logs failures to avoid cascading errors.
    """
    try:
        from app.db.mongo import get_mongo_client
        from app.config import settings

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        collection = db["dead_letters"]

        document = {
            "user_id": user_id,
            "message": message,
            "lang": lang,
            "error": error,
            "timestamp": datetime.now(timezone.utc),
            "status": "pending",
            "retry_count": 0,
        }

        await collection.insert_one(document)
        logger.info(f"Dead letter stored for user {user_id}, lang={lang}")
    except Exception as e:
        logger.error(f"Failed to store dead letter: {e}")


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

    # Mark as retrying and increment retry_count
    await collection.update_one(
        {"_id": ObjectId(dead_letter_id)},
        {"$set": {"status": "retrying"}, "$inc": {"retry_count": 1}},
    )

    try:
        user_id = doc["user_id"]
        message = doc["message"]
        lang = doc.get("lang", "en")

        detected_lang, target_model = ChatService.resolve_language_and_model(
            message, lang_override=lang
        )
        context_chunks = await ChatService.retrieve_context(message, "free")
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
