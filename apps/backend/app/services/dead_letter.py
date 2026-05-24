"""Dead Letter Storage: Persists failed chat attempts for later analysis."""

import logging
from datetime import datetime, timezone

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
        }

        await collection.insert_one(document)
        logger.info(f"Dead letter stored for user {user_id}, lang={lang}")
    except Exception as e:
        logger.error(f"Failed to store dead letter: {e}")
