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
    model_provider: str  # "vertex" | "sarvam"
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
            # TTL: auto-delete after 30 days (MongoDB handles this)
            # Note: TTL index must be created via mongosh script
            # as Beanie doesn't support expireAfterSeconds directly
            [("timestamp", 1)],
        ]
