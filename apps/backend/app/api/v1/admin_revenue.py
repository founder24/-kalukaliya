"""
Admin Revenue/Monetization Endpoints
Subscription overview, referral config, ads management.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Request, HTTPException
import logging

from app.api.v1.admin import _validate_admin_session, _csrf_check

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/monetization/overview")
async def monetization_overview(request: Request):
    """Aggregate subscription revenue from users."""
    _validate_admin_session(request)
    try:
        from app.models.user import User

        pro_users = await User.find({"subscription_tier": "pro"}).count()
        free_users = await User.find({"subscription_tier": "free"}).count()
        total_users = pro_users + free_users
        price = 299  # INR per month placeholder

        return {
            "total_revenue": pro_users * price,
            "mrr": pro_users * price,
            "active_subscriptions": pro_users,
            "churn_rate": 0.0,
            "plan_distribution": {
                "free": free_users,
                "pro": pro_users,
            },
            "total_users": total_users,
        }
    except Exception as e:
        logger.error(f"Monetization overview error: {e}")
        return {
            "total_revenue": 0,
            "mrr": 0,
            "active_subscriptions": 0,
            "churn_rate": 0.0,
            "plan_distribution": {"free": 0, "pro": 0},
            "total_users": 0,
        }


@router.get("/monetization/referral-config")
async def referral_config(request: Request):
    """Placeholder referral config."""
    _validate_admin_session(request)
    return {
        "enabled": False,
        "reward_type": "credits",
        "reward_amount": 5,
        "source": "placeholder",
    }


@router.get("/ads/overview")
async def ads_overview(request: Request):
    """Placeholder ads overview."""
    _validate_admin_session(request)
    return {"total_earnings": 0, "impressions": 0, "clicks": 0, "source": "placeholder"}


@router.get("/ads/earnings")
async def list_ad_earnings(request: Request):
    """List ad earnings from collection."""
    _validate_admin_session(request)
    try:
        from app.db.mongo import get_mongo_client
        from app.config import settings

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        docs = await db.ad_earnings.find().sort("date", -1).to_list(length=100)
        for doc in docs:
            doc["_id"] = str(doc["_id"])
        return {"earnings": docs}
    except Exception as e:
        logger.error(f"Error listing ad earnings: {e}")
        return {"earnings": []}


@router.post("/ads/earnings")
async def create_ad_earning(request: Request):
    """Create an ad earning entry."""
    _validate_admin_session(request)
    await _csrf_check(request)
    try:
        from app.db.mongo import get_mongo_client
        from app.config import settings

        body = await request.json()
        body["created_at"] = datetime.now(timezone.utc)
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        result = await db.ad_earnings.insert_one(body)
        return {"status": "ok", "id": str(result.inserted_id)}
    except Exception as e:
        logger.error(f"Error creating ad earning: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/ads/earnings/{earning_id}")
async def delete_ad_earning(earning_id: str, request: Request):
    """Delete an ad earning entry."""
    _validate_admin_session(request)
    await _csrf_check(request)
    try:
        from bson import ObjectId
        from app.db.mongo import get_mongo_client
        from app.config import settings

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        result = await db.ad_earnings.delete_one({"_id": ObjectId(earning_id)})
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Earning not found")
        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting ad earning: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ads/earnings/csv")
async def upload_ad_earnings_csv(request: Request):
    """Placeholder CSV upload for ad earnings."""
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"status": "ok", "imported": 0, "source": "placeholder"}


@router.get("/ads/adsense/status")
async def adsense_status(request: Request):
    """Placeholder AdSense status."""
    _validate_admin_session(request)
    return {"connected": False, "publisher_id": None, "source": "placeholder"}


@router.post("/ads/adsense/sync")
async def adsense_sync(request: Request):
    """Placeholder AdSense sync."""
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"status": "ok", "synced": 0, "source": "placeholder"}
