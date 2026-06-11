"""Monthly quota usage tracking via MongoDB.

Replaces Upstash Redis quota counters. Each document tracks a user's
chat request count for a given calendar month. A TTL index on
`expires_at` auto-deletes documents at the start of the next month.
"""
from datetime import datetime
from beanie import Document
from pymongo import IndexModel, ASCENDING
from pydantic import Field


class QuotaUsage(Document):
    """Per-user monthly request quota counter."""

    user_id: str
    month: str
    count: int = Field(default=0)
    expires_at: datetime

    class Settings:
        name = "quota_usage"
        indexes = [
            IndexModel(
                [("user_id", ASCENDING), ("month", ASCENDING)],
                unique=True,
            ),
            IndexModel(
                [("expires_at", ASCENDING)],
                expireAfterSeconds=0,
            ),
        ]
