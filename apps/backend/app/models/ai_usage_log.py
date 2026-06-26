"""
AiUsageLog — tracks per-request token usage and latency for all AI providers.

Written by chat_service after each AI response (fire-and-forget).
Queried by /admin/ai/usage for last-24h token cost breakdown.

provider values: "sarvam_ai" | "vertex_ai" | "cf_workers_ai"
"""

from datetime import datetime, timezone
from typing import Literal, Optional

from beanie import Document
from pydantic import Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AiUsageLog(Document):
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    provider: str
    model: str
    lang: str = "en"
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    latency_ms: int = 0
    cost_usd: Optional[float] = None
    created_at: datetime = Field(default_factory=_now)

    class Settings:
        name = "ai_usage_logs"
