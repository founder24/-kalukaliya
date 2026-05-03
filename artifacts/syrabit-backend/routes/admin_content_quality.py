"""Admin endpoints for content-quality APIs (Phase 2: Fact Check + NLP).

Routes registered under the /api prefix:
  GET  /api/admin/content/fact-check?query=&language_code=&page_size=
  POST /api/admin/content/nlp/analyze
       body: {content, language?, features?: [sentiment,entities,classify]}
"""
from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, Query

from auth_deps import get_admin_user
import fact_check_client
import nlp_client

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/admin/content/fact-check")
async def admin_fact_check(
    query: str = Query(..., min_length=1, max_length=500),
    language_code: str = Query("en"),
    page_size: int = Query(10, ge=1, le=50),
    review_publisher_site_filter: Optional[str] = Query(
        None, description="Filter to a single publisher site (e.g. 'altnews.in')."
    ),
    admin: dict = Depends(get_admin_user),
):
    """Look up published fact-checks for a claim."""
    return await fact_check_client.search_claims(
        query, language_code=language_code, page_size=page_size,
        review_publisher_site_filter=review_publisher_site_filter,
    )


@router.post("/admin/content/nlp/analyze")
async def admin_nlp_analyze(
    payload: dict = Body(...),
    admin: dict = Depends(get_admin_user),
):
    """Run any combination of NLP analyses on `content`.

    Body: {
        "content": str,
        "language": Optional[str],          # auto-detect if omitted
        "features": ["sentiment", "entities", "classify"]   # default: all
    }
    """
    content = (payload.get("content") or "").strip()
    if not content:
        return {"status": "error", "error": "empty_content"}
    language = payload.get("language")
    features = payload.get("features") or ["sentiment", "entities", "classify"]
    features = [f for f in features if f in ("sentiment", "entities", "classify")]

    coros: List = []
    feature_names: List[str] = []
    if "sentiment" in features:
        coros.append(nlp_client.analyze_sentiment(content, language=language))
        feature_names.append("sentiment")
    if "entities" in features:
        coros.append(nlp_client.analyze_entities(content, language=language))
        feature_names.append("entities")
    if "classify" in features:
        coros.append(nlp_client.classify_text(content, language=language))
        feature_names.append("classify")

    if not coros:
        return {"status": "error", "error": "no_valid_features"}

    results = await asyncio.gather(*coros, return_exceptions=True)
    feats: dict = {}
    ok_count = 0
    fail_count = 0
    for name, res in zip(feature_names, results):
        if isinstance(res, BaseException):
            feats[name] = {"status": "error", "error": repr(res)}
            fail_count += 1
        else:
            feats[name] = res
            if (res.get("status") if isinstance(res, dict) else None) == "ok":
                ok_count += 1
            else:
                fail_count += 1
    if fail_count == 0:
        top = "ok"
    elif ok_count == 0:
        top = "error"
    else:
        top = "partial"
    return {
        "status": top,
        "ok_count": ok_count,
        "failed_count": fail_count,
        "features": feats,
    }
