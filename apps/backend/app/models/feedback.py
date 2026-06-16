"""
Chat Feedback Model — Tracks user ratings per message.

Used for accuracy aggregation by language + model provider.
Compound index on (lang, model_provider, timestamp) enables efficient
aggregation queries. TTL index auto-deletes after 30 days.
"""

from beanie import Document
from pydantic import Field
from typing import Optional, Literal
from datetime import datetime


class ChatFeedback(Document):
    """Chat Feedback — one rating per assistant message."""

    user_id: str
    session_id: Optional[str] = None
    message_id: str
    lang: Literal["en", "as"]
    model_provider: str  # "sarvam"
    rating: Literal[1, -1]  # 1 = thumbs up, -1 = thumbs down
    latency_ms: Optional[int] = None
    query_text: Optional[str] = None  # First 100 chars for debugging
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "chat_feedback"
        indexes = [
            # Primary aggregation index: accuracy by lang + model over time
            [("lang", 1), ("model_provider", 1), ("timestamp", 1)],
            # User feedback history
            [("user_id", 1), ("timestamp", -1)],
            # NOTE: The TTL index on (timestamp, 30d) is managed exclusively by
            # _ensure_ttl_index() in mongo.py create_indexes(). Do NOT add a plain
            # [("timestamp", 1)] entry here — Beanie would create it as a non-TTL
            # index, which conflicts (IndexOptionsConflict code 85) with the TTL
            # version on every restart after the first deployment.
        ]
