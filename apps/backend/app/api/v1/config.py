"""
Config endpoints — lightweight public configuration stubs.
Returns feature-flag and third-party service config that the
frontend needs before rendering certain components. All responses
gracefully return null/empty values when the feature is not configured,
so the frontend can hide the section rather than error.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(tags=["Config"])


@router.get("/trustpilot")
async def trustpilot_config():
    """
    Returns the Trustpilot profile URL and business unit ID for the
    TrustpilotReviewsSection component. Returns null when Trustpilot
    integration is not configured so the section hides gracefully.
    """
    from app.config import settings

    tp_url = getattr(settings, "TRUSTPILOT_PROFILE_URL", None)
    tp_buid = getattr(settings, "TRUSTPILOT_BUSINESS_UNIT_ID", None)

    if not tp_url and not tp_buid:
        return JSONResponse(None)

    return JSONResponse({
        "profileUrl": tp_url,
        "businessUnitId": tp_buid,
    })


@router.get("/trustpilot/aggregate")
async def trustpilot_aggregate():
    """
    Returns cached Trustpilot aggregate rating data (ratingValue,
    ratingCount) for the star-rating row and JSON-LD schema.
    Returns null when no cached data is available — frontend renders
    without the rating row to avoid layout shift.
    """
    from app.config import settings

    rating_value = getattr(settings, "TRUSTPILOT_RATING_VALUE", None)
    rating_count = getattr(settings, "TRUSTPILOT_RATING_COUNT", None)

    if rating_value is None or rating_count is None or rating_count == 0:
        return JSONResponse(None)

    return JSONResponse({
        "ratingValue": float(rating_value),
        "ratingCount": int(rating_count),
    })
