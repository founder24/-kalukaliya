"""Admin endpoints for newly-enabled Google APIs (Task: wire enabled APIs).

Surfaces:
  GET  /api/admin/seo/kg-search?query=...&limit=5
  GET  /api/admin/seo/pagespeed?url=...&strategy=mobile
  POST /api/admin/seo/pagespeed/batch  body: {urls: [...], strategy: "mobile"}

All endpoints are admin-gated, return HTTP 200 with errors inside the payload,
and never raise so the dashboard can render gracefully.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, Query

from auth_deps import get_admin_user
import kg_search_client
import pagespeed_service

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/admin/seo/kg-search")
async def admin_kg_search(
    query: str = Query(..., min_length=1, max_length=200,
                       description="Entity name to look up in Google Knowledge Graph."),
    limit: int = Query(5, ge=1, le=20),
    languages: Optional[str] = Query(
        None, description="Comma-separated BCP-47 language codes (e.g. 'en,hi,as')."
    ),
    types: Optional[str] = Query(
        None, description="Comma-separated schema.org types (e.g. 'Person,Place')."
    ),
    admin: dict = Depends(get_admin_user),
):
    """Search Google Knowledge Graph for canonical entity records."""
    lang_list = [s.strip() for s in (languages or "").split(",") if s.strip()] or None
    type_list = [s.strip() for s in (types or "").split(",") if s.strip()] or None
    return await kg_search_client.search_entities(
        query, limit=limit, languages=lang_list, types=type_list,
    )


@router.get("/admin/seo/pagespeed")
async def admin_pagespeed(
    url: str = Query(..., description="Public URL to audit."),
    strategy: str = Query("mobile", description="'mobile' or 'desktop'."),
    categories: Optional[str] = Query(
        None,
        description="Comma-separated Lighthouse categories "
                    "(performance,accessibility,best-practices,seo,pwa).",
    ),
    admin: dict = Depends(get_admin_user),
):
    """Run a PageSpeed Insights audit on `url` and return Core Web Vitals."""
    cat_list = [s.strip() for s in (categories or "").split(",") if s.strip()] or None
    audit = await pagespeed_service.run_pagespeed(
        url, strategy=strategy, categories=cat_list,
    )
    audit["cwv_buckets"] = pagespeed_service.summarize_cwv(audit)
    return audit


@router.post("/admin/seo/pagespeed/batch")
async def admin_pagespeed_batch(
    payload: dict = Body(...),
    admin: dict = Depends(get_admin_user),
):
    """Run PageSpeed audits on multiple URLs (max 20) concurrently.

    Body: { "urls": [...], "strategy": "mobile"|"desktop",
            "categories": ["performance", ...] }
    """
    raw_urls = payload.get("urls") or []
    urls: List[str] = [u for u in raw_urls if isinstance(u, str) and u.strip()]
    urls = urls[:20]
    if not urls:
        return {"status": "error", "error": "no_urls", "results": []}

    strategy = payload.get("strategy") or "mobile"
    categories = payload.get("categories")
    audits = await pagespeed_service.batch_pagespeed(
        urls, strategy=strategy, categories=categories,
    )
    for a in audits:
        a["cwv_buckets"] = pagespeed_service.summarize_cwv(a)

    ok = [a for a in audits if a.get("status") == "ok"]
    failed = [a for a in audits if a.get("status") != "ok"]
    return {
        "status": "ok" if not failed else ("partial" if ok else "error"),
        "count": len(audits),
        "ok_count": len(ok),
        "failed_count": len(failed),
        "results": audits,
    }
