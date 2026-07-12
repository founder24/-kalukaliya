"""
Feedback API — Submit and query chat feedback ratings.

Endpoints:
  POST /api/v1/chat/feedback/     — Submit thumbs up/down for a message
  GET  /api/v1/chat/feedback/stats — Get aggregated accuracy by lang + model (admin)
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Literal, Optional, List
from datetime import datetime, timedelta
import logging

from app.models.feedback import ChatFeedback
from app.api.v1.auth import get_current_user
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Feedback"])


def _require_admin_or_staff(user: User) -> User:
    if getattr(user, "role", None) not in ("admin", "staff"):
        raise HTTPException(status_code=403, detail="Admin or staff access required")
    return user


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


class FeedbackItem(BaseModel):
    id: str
    user_id: str
    session_id: Optional[str]
    message_id: str
    lang: str
    model_provider: str
    rating: int
    latency_ms: Optional[int]
    query_text: Optional[str]
    timestamp: datetime
    archived: bool
    read: bool


class FeedbackListResponse(BaseModel):
    data: List[FeedbackItem]
    total: int
    limit: int
    offset: int


class FeedbackPatchRequest(BaseModel):
    action: Literal["archive", "unarchive", "read", "unread"]


@router.get("/", response_model=FeedbackListResponse)
async def list_feedback(
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0),
    filter: Optional[Literal["likes", "dislikes", "archived", "unread"]] = None,
    user: User = Depends(get_current_user),
):
    """Admin: list all feedback entries with optional filter."""
    _require_admin_or_staff(user)
    query: dict = {}
    if filter == "likes":
        query["rating"] = 1
    elif filter == "dislikes":
        query["rating"] = -1
    elif filter == "archived":
        query["archived"] = True
    elif filter == "unread":
        query["read"] = {"$ne": True}

    total = await ChatFeedback.find(query).count()
    items = (
        await ChatFeedback.find(query)
        .sort(-ChatFeedback.timestamp)
        .skip(offset)
        .limit(limit)
        .to_list()
    )
    return FeedbackListResponse(
        data=[
            FeedbackItem(
                id=str(f.id),
                user_id=f.user_id,
                session_id=f.session_id,
                message_id=f.message_id,
                lang=f.lang,
                model_provider=f.model_provider,
                rating=f.rating,
                latency_ms=f.latency_ms,
                query_text=f.query_text,
                timestamp=f.timestamp,
                archived=getattr(f, "archived", False),
                read=getattr(f, "read", False),
            )
            for f in items
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.patch("/{feedback_id}")
async def patch_feedback(
    feedback_id: str,
    body: FeedbackPatchRequest,
    user: User = Depends(get_current_user),
):
    """Admin: archive, unarchive, mark read, or mark unread a feedback entry."""
    _require_admin_or_staff(user)
    from beanie import PydanticObjectId

    item = await ChatFeedback.get(PydanticObjectId(feedback_id))
    if not item:
        raise HTTPException(status_code=404, detail="Feedback not found")

    updates: dict = {}
    if body.action == "archive":
        updates["archived"] = True
    elif body.action == "unarchive":
        updates["archived"] = False
    elif body.action == "read":
        updates["read"] = True
    elif body.action == "unread":
        updates["read"] = False

    if updates:
        await item.update({"$set": updates})

    logger.info(f"Feedback {feedback_id} patched: action={body.action} by admin={user.id}")
    return {"ok": True, "action": body.action, "id": feedback_id}


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

    results = await ChatFeedback.aggregate(pipeline).to_list(length=None)

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
