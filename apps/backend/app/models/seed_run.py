"""
SeedRun — persists seed-notes job progress to MongoDB.

One document per run.  The background worker upserts progress every N
chapters so the document survives server restarts, scale-out events, and
redeploys without losing the failed_ids list.

status values:
  "running"   — job is in-flight
  "completed" — all chapters processed (some may have failed)
  "error"     — job crashed before finishing
"""

from datetime import datetime, timezone
from typing import List, Optional

from beanie import Document
from pydantic import Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SeedRun(Document):
    status: str = "running"            # running | completed | error
    started_at: datetime = Field(default_factory=_now)
    finished_at: Optional[datetime] = None
    total: int = 0
    completed: int = 0
    failed: int = 0
    skipped: int = 0
    topics_seeded: int = 0
    failed_ids: List[str] = Field(default_factory=list)
    errors: List[dict] = Field(default_factory=list)
    concurrency: int = 2
    force: bool = False
    current: str = ""                  # chapter title being processed right now

    class Settings:
        name = "seed_runs"
