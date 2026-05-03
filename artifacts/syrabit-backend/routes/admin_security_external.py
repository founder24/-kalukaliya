"""Admin endpoints for Google Web Risk API (Phase 2: Security).

  GET  /api/admin/security/web-risk?uri=&threat_types=
  POST /api/admin/security/web-risk/batch  body: {uris: [...]}

Web Security Scanner (long-running automated scans) is NOT wired here
because it requires a Google service account JSON, which is not present
in the env (Vertex SA lives in CF AI Gateway BYOK only). To enable it,
provide GOOGLE_APPLICATION_CREDENTIALS_JSON.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, Query

from auth_deps import get_admin_user
import web_risk_client

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/admin/security/web-risk")
async def admin_web_risk(
    uri: str = Query(..., description="URL to check against Safe Browsing lists."),
    threat_types: Optional[str] = Query(
        None,
        description="Comma-separated threat types "
                    "(MALWARE,SOCIAL_ENGINEERING,UNWANTED_SOFTWARE).",
    ),
    admin: dict = Depends(get_admin_user),
):
    """Check a single URL against Google Web Risk."""
    types = [s.strip() for s in (threat_types or "").split(",") if s.strip()] or None
    return await web_risk_client.check_uri(uri, threat_types=types)


@router.post("/admin/security/web-risk/batch")
async def admin_web_risk_batch(
    payload: dict = Body(...),
    admin: dict = Depends(get_admin_user),
):
    """Check many URIs (max 50) against Google Web Risk concurrently."""
    raw_uris = payload.get("uris") or []
    uris: List[str] = [u for u in raw_uris if isinstance(u, str) and u.strip()]
    uris = uris[:50]
    if not uris:
        return {"status": "error", "error": "no_uris", "results": []}
    threat_types = payload.get("threat_types") or None
    results = await web_risk_client.batch_check(uris, threat_types=threat_types)
    safe = [r for r in results if r.get("safe")]
    flagged = [r for r in results if not r.get("safe") and r.get("status") == "ok"]
    failed = [r for r in results if r.get("status") != "ok"]
    return {
        "status": "ok" if not failed else ("partial" if (safe or flagged) else "error"),
        "count": len(results),
        "safe_count": len(safe),
        "flagged_count": len(flagged),
        "failed_count": len(failed),
        "results": results,
    }
