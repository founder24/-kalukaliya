"""IndexNow API integration for instant URL submission to Bing/Yandex."""

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel
from typing import List
import httpx
import logging

from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

INDEXNOW_ENDPOINT = "https://api.indexnow.org/indexnow"
SITE_HOST = "syrabit.ai"


class IndexNowSubmitRequest(BaseModel):
    urls: List[str]


class IndexNowSubmitResponse(BaseModel):
    submitted: int
    status: str
    detail: str = ""


@router.post("/submit", response_model=IndexNowSubmitResponse)
async def submit_urls(body: IndexNowSubmitRequest):
    """Submit URLs to IndexNow for instant indexing by Bing, Yandex, etc."""
    if not settings.INDEXNOW_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="IndexNow not configured: INDEXNOW_API_KEY is not set",
        )

    if not body.urls:
        raise HTTPException(status_code=400, detail="No URLs provided")

    if len(body.urls) > 10000:
        raise HTTPException(
            status_code=400, detail="Maximum 10000 URLs per submission"
        )

    # IndexNow batch submission payload
    payload = {
        "host": SITE_HOST,
        "key": settings.INDEXNOW_API_KEY,
        "keyLocation": f"https://{SITE_HOST}/{settings.INDEXNOW_API_KEY}.txt",
        "urlList": body.urls[:10000],
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(INDEXNOW_ENDPOINT, json=payload)

        if resp.status_code in (200, 202):
            logger.info(f"IndexNow: submitted {len(body.urls)} URLs successfully")
            return IndexNowSubmitResponse(
                submitted=len(body.urls),
                status="accepted",
                detail=f"HTTP {resp.status_code}",
            )
        else:
            logger.warning(
                f"IndexNow: submission returned HTTP {resp.status_code}: {resp.text[:200]}"
            )
            return IndexNowSubmitResponse(
                submitted=0,
                status="error",
                detail=f"IndexNow returned HTTP {resp.status_code}",
            )
    except httpx.TimeoutException:
        logger.error("IndexNow: submission timed out")
        return IndexNowSubmitResponse(
            submitted=0, status="error", detail="Request timed out"
        )
    except Exception as e:
        logger.error(f"IndexNow: submission failed: {e}")
        return IndexNowSubmitResponse(
            submitted=0, status="error", detail=str(e)[:200]
        )


@router.get("/key")
async def indexnow_key():
    """Serve the IndexNow key verification file content."""
    if not settings.INDEXNOW_API_KEY:
        raise HTTPException(
            status_code=404, detail="IndexNow key not configured"
        )
    return Response(
        content=settings.INDEXNOW_API_KEY,
        media_type="text/plain",
    )
