"""routes.admin_moderation_queue — Task #337 Rekognition admin queue.

Backs ``AdminModerationQueuePanel`` in the React admin shell. Operators
review every Rekognition-flagged upload here and either approve (the
upload is released back to the user-visible surface) or reject (the
quarantined bytes stay in Supabase under the ``quarantine/`` prefix
for audit, the queue row is closed).
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth_deps import get_admin_user as require_admin
from deps import db, supa
from services import moderation_queue as mq

logger = logging.getLogger("routes.admin_moderation_queue")
router = APIRouter(tags=["admin", "moderation"])


@router.get("/admin/moderation/queue")
async def list_queue(
    surface: Optional[str] = None,
    limit: int = 50,
    _admin: dict = Depends(require_admin),
):
    items = await mq.list_pending(db, surface=surface, limit=max(1, min(limit, 200)))
    return {"items": items, "count": len(items)}


class ResolveBody(BaseModel):
    decision: str = Field(..., description="'approved' or 'rejected'")


@router.post("/admin/moderation/queue/{queue_id}/resolve")
async def resolve_item(
    queue_id: str,
    body: ResolveBody,
    admin: dict = Depends(require_admin),
):
    try:
        ok = await mq.resolve(db, queue_id, decision=body.decision, admin_id=admin.get("sub", "admin"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="queue item not found or already resolved")
    return {"queue_id": queue_id, "decision": body.decision}


@router.get("/admin/moderation/queue/{queue_id}/preview")
async def preview_item(queue_id: str, _admin: dict = Depends(require_admin)):
    """Return a signed Supabase URL (or inlined base64) for the quarantined image."""
    if db is None:
        raise HTTPException(503, "db unavailable")
    row = await db[mq.COLLECTION].find_one({"_id": queue_id})
    if not row:
        raise HTTPException(404, "not found")
    if row.get("storage_path") and supa is not None:
        try:
            url = supa.storage.from_("study-materials").get_public_url(row["storage_path"])
            return {"url": url, "mime": row.get("mime")}
        except Exception as exc:
            logger.warning("preview signed url failed: %s", str(exc)[:200])
    if row.get("inline_b64"):
        return {
            "url": f"data:{row.get('mime','image/png')};base64,{row['inline_b64']}",
            "mime": row.get("mime"),
        }
    raise HTTPException(410, "preview unavailable")
