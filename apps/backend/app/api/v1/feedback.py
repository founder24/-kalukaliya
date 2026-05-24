"""
Feedback API — Submit and query chat feedback ratings.

Endpoints:
  POST /api/v1/chat/feedback/     — Submit thumbs up/down for a message
  GET  /api/v1/chat/feedback/stats — Get aggregated accuracy by lang + model (admin)
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from typing import Literal, Optional, List
from datetime import datetime, timedelta
import logging

from app.models.feedback import ChatFeedback
from app.api.v1.auth import get_current_user
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Feedback"])


class FeedbackRequest(BaseModel):
    """Request body for submitting feedback."""

    message_id: str = Field(..., min_length=1, max_length=100)
    rating: Literal[1, -1]
    lang: Literal["en", "as"]
    model_provider: str = Field(..., min_length=1, max_length=50)
    session_id: Optional[str] = None
    latency_ms: Optional[int] = None


class FeedbackResponse(BaseModel):
    status: str
    id: str


class AccuracyStat(BaseModel):
    lang: str
    model: str
    total: int
    positive: int
    negative: int
    accuracy: float
    satisfaction_pct: float


class StatsResponse(BaseModel):
    stats: List[AccuracyStat]
    period_days: int


@router.post("/", response_model=FeedbackResponse)
async def submit_feedback(
    request: FeedbackRequest,
    user: User = Depends(get_current_user),
):
    """
    Submit feedback (thumbs up/down) for a chat message.

    This data is used for:
    - Accuracy aggregation by language + model provider
    - Quality monitoring dashboard
    - Prompt/temperature tuning decisions
    """
    feedback = ChatFeedback(
        user_id=str(user.id),
        session_id=request.session_id,
        message_id=request.message_id,
        lang=request.lang,
        model_provider=request.model_provider,
        rating=request.rating,
        latency_ms=request.latency_ms,
        timestamp=datetime.utcnow(),
    )
    await feedback.insert()

    logger.info(
        f"Feedback submitted: user={user.id} msg={request.message_id} "
        f"rating={request.rating} lang={request.lang} model={request.model_provider}"
    )

    return FeedbackResponse(status="ok", id=str(feedback.id))


@router.get("/stats", response_model=StatsResponse)
async def get_feedback_stats(
    days: int = 7,
    user: User = Depends(get_current_user),
):
    """
    Get aggregated feedback statistics grouped by language + model provider.

    Returns accuracy (positive/total) and satisfaction percentage for each
    language-model combination over the specified time window.

    Query params:
      - days: Number of days to look back (default: 7, max: 90)
    """
    # Clamp days to reasonable range
    days = max(1, min(days, 90))
    since = datetime.utcnow() - timedelta(days=days)

    pipeline = [
        {"$match": {"timestamp": {"$gte": since}}},
        {
            "$group": {
                "_id": {"lang": "$lang", "model": "$model_provider"},
                "total": {"$sum": 1},
                "positive": {"$sum": {"$cond": [{"$eq": ["$rating", 1]}, 1, 0]}},
                "negative": {"$sum": {"$cond": [{"$eq": ["$rating", -1]}, 1, 0]}},
            }
        },
        {
            "$addFields": {
                "accuracy": {"$round": [{"$divide": ["$positive", "$total"]}, 3]},
                "satisfaction_pct": {
                    "$round": [
                        {"$multiply": [{"$divide": ["$positive", "$total"]}, 100]},
                        1,
                    ]
                },
            }
        },
        {"$sort": {"_id.lang": 1, "accuracy": -1}},
    ]

    results = await ChatFeedback.aggregate(pipeline).to_list()

    stats = [
        AccuracyStat(
            lang=r["_id"]["lang"],
            model=r["_id"]["model"],
            total=r["total"],
            positive=r["positive"],
            negative=r["negative"],
            accuracy=r["accuracy"],
            satisfaction_pct=r["satisfaction_pct"],
        )
        for r in results
    ]

    return StatsResponse(stats=stats, period_days=days)
