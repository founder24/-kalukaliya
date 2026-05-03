"""Google Fact Check Tools API client.

Wraps https://factchecktools.googleapis.com/v1alpha1/claims:search to look
up published fact-checks for a claim. Used to flag generated educational
content that contradicts authoritative fact-checkers (Snopes, PolitiFact,
Alt News, BoomLive, etc.) — particularly useful for current-affairs notes
and Class 9-12 social-science topics.

Auth: requires GOOGLE_FACT_CHECK_API_KEY (falls back to GOOGLE_KG_API_KEY
since both are standard Google API keys with the relevant API enabled).
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

FACT_CHECK_API_URL = "https://factchecktools.googleapis.com/v1alpha1/claims:search"
_HTTP_TIMEOUT_S = 6.0


def _api_key() -> str:
    return (
        (os.environ.get("GOOGLE_FACT_CHECK_API_KEY") or "").strip()
        or (os.environ.get("GOOGLE_KG_API_KEY") or "").strip()
    )


def is_configured() -> bool:
    return bool(_api_key())


async def search_claims(
    query: str,
    *,
    language_code: str = "en",
    page_size: int = 10,
    review_publisher_site_filter: Optional[str] = None,
    timeout_s: float = _HTTP_TIMEOUT_S,
) -> Dict[str, Any]:
    """Search published fact-checks matching `query`.

    Returns: {
        "status": "ok"|"missing"|"disabled"|"error",
        "query": str,
        "claims": [{
            "text", "claimant", "claim_date",
            "reviews": [{"publisher_name", "publisher_site", "url",
                         "title", "review_date", "textual_rating",
                         "language_code"}, ...]
        }, ...],
        "count": int,
        "elapsed_ms": float,
        "error": Optional[str],
    }
    """
    q = (query or "").strip()
    if not q:
        return {"status": "error", "query": q, "claims": [], "count": 0,
                "elapsed_ms": 0.0, "error": "empty_query"}

    key = _api_key()
    if not key:
        return {"status": "disabled", "query": q, "claims": [], "count": 0,
                "elapsed_ms": 0.0,
                "error": "GOOGLE_FACT_CHECK_API_KEY (or GOOGLE_KG_API_KEY) not configured"}

    params: List[tuple] = [
        ("query", q),
        ("languageCode", language_code),
        ("pageSize", str(max(1, min(50, int(page_size))))),
        ("key", key),
    ]
    if review_publisher_site_filter:
        params.append(("reviewPublisherSiteFilter", review_publisher_site_filter))

    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.get(FACT_CHECK_API_URL, params=params)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        if r.status_code != 200:
            return {"status": "error", "query": q, "claims": [], "count": 0,
                    "elapsed_ms": elapsed_ms,
                    "error": f"HTTP {r.status_code}: {r.text[:200]}"}
        raw_claims = (r.json() or {}).get("claims") or []
    except httpx.TimeoutException:
        return {"status": "error", "query": q, "claims": [], "count": 0,
                "elapsed_ms": (time.perf_counter() - t0) * 1000.0,
                "error": "timeout"}
    except Exception as exc:
        return {"status": "error", "query": q, "claims": [], "count": 0,
                "elapsed_ms": (time.perf_counter() - t0) * 1000.0,
                "error": f"{type(exc).__name__}: {str(exc)[:200]}"}

    claims: List[Dict[str, Any]] = []
    for c in raw_claims:
        reviews = []
        for rv in (c.get("claimReview") or []):
            pub = rv.get("publisher") or {}
            reviews.append({
                "publisher_name": pub.get("name"),
                "publisher_site": pub.get("site"),
                "url": rv.get("url"),
                "title": rv.get("title"),
                "review_date": rv.get("reviewDate"),
                "textual_rating": rv.get("textualRating"),
                "language_code": rv.get("languageCode"),
            })
        claims.append({
            "text": c.get("text"),
            "claimant": c.get("claimant"),
            "claim_date": c.get("claimDate"),
            "reviews": reviews,
        })

    if not claims:
        return {"status": "missing", "query": q, "claims": [], "count": 0,
                "elapsed_ms": elapsed_ms, "error": None}
    return {"status": "ok", "query": q, "claims": claims, "count": len(claims),
            "elapsed_ms": elapsed_ms, "error": None}
