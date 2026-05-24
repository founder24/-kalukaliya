"""
IndexNow endpoint: submit URLs to IndexNow API for faster indexing.
"""

import logging
from typing import List

import httpx
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

INDEXNOW_API_URL = "https://api.indexnow.org/indexnow"
BATCH_SIZE = 100


class IndexNowRequest(BaseModel):
    urls: List[str]


class IndexNowResponse(BaseModel):
    submitted: int
    failed: int
    detail: str


@router.post("/submit", response_model=IndexNowResponse)
async def submit_urls(
    body: IndexNowRequest,
    x_indexnow_secret: str = Header(None, alias="X-IndexNow-Secret"),
):
    """Submit URLs to IndexNow for rapid indexing."""
    if not x_indexnow_secret:
        raise HTTPException(status_code=403, detail="Missing IndexNow secret")

    if not settings.INDEXNOW_API_KEY:
        raise HTTPException(status_code=500, detail="INDEXNOW_API_KEY not configured")

    if x_indexnow_secret != settings.INDEXNOW_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid IndexNow secret")

    urls = body.urls
    if not urls:
        return IndexNowResponse(submitted=0, failed=0, detail="No URLs provided")

    submitted = 0
    failed = 0

    async with httpx.AsyncClient(timeout=30.0) as client:
        for i in range(0, len(urls), BATCH_SIZE):
            batch = urls[i : i + BATCH_SIZE]
            payload = {
                "host": "syrabit.ai",
                "key": settings.INDEXNOW_API_KEY,
                "urlList": batch,
            }
            try:
                resp = await client.post(INDEXNOW_API_URL, json=payload)
                if resp.status_code in (200, 202):
                    submitted += len(batch)
                else:
                    logger.warning(
                        f"IndexNow batch failed: status={resp.status_code}, body={resp.text[:200]}"
                    )
                    failed += len(batch)
            except Exception as e:
                logger.error(f"IndexNow submission error: {e}")
                failed += len(batch)

    return IndexNowResponse(
        submitted=submitted,
        failed=failed,
        detail=f"Processed {len(urls)} URLs in batches of {BATCH_SIZE}",
    )
