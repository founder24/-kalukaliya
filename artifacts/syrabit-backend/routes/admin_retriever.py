"""
Admin retriever toggle — read/write the active vector-retrieval backend.

Endpoints:
  GET  /admin/retriever/config     → current selection + per-backend status
  PUT  /admin/retriever/config     → switch the active backend (persists in
                                     db.settings; admin auth required)

The `available` list reflects every retriever the factory knows about so
the admin UI can render dropdown options without hard-coding names.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth_deps import get_admin_user
from retrievers import (
    DEFAULT_RETRIEVER,
    get_active_retriever_name,
    get_retriever_by_name,
    list_available_retrievers,
    set_active_retriever,
)

router = APIRouter(prefix="/admin/retriever", tags=["admin-retriever"])
logger = logging.getLogger("admin_retriever")


class RetrieverSwitchPayload(BaseModel):
    active: str = Field(
        ...,
        description=(
            "Backend name (e.g. 'pinecone' | 'vectorize'). "
            "Task #490: 'vertex' is no longer accepted (the Vertex Vector "
            "Search retriever was deleted) — submitting it returns HTTP 400."
        ),
    )


def _safe_status(name: str) -> dict[str, Any]:
    """Return a minimal status dict for one backend without making network
    calls — keeps the admin GET cheap and side-effect-free."""
    try:
        r = get_retriever_by_name(name)
    except ValueError as exc:
        return {"name": name, "error": str(exc)}
    return {
        "name": r.name,
        "is_configured": r.is_configured(),
        "dimensions": r.dimensions,
    }


@router.get("/config")
async def get_retriever_config(_admin=Depends(get_admin_user)) -> dict[str, Any]:
    """Show the active retriever + each backend's readiness."""
    from deps import db
    db_active: Optional[str] = None
    if db is not None:
        try:
            doc = await db.settings.find_one({"id": "retriever_config"}, {"active": 1, "_id": 0})
            db_active = ((doc or {}).get("active") or "").strip().lower() or None
        except Exception as exc:
            logger.warning("retriever_config read failed: %s", exc)

    env_active = get_active_retriever_name()
    effective = db_active if db_active in list_available_retrievers() else env_active
    return {
        "active": effective,
        "default": DEFAULT_RETRIEVER,
        "source": "db_override" if db_active else "env_or_default",
        "env_value": env_active,
        "db_override": db_active,
        "available": [_safe_status(n) for n in list_available_retrievers()],
    }


@router.put("/config")
async def update_retriever_config(
    payload: RetrieverSwitchPayload,
    _admin=Depends(get_admin_user),
) -> dict[str, Any]:
    """Switch the active retriever. Validates the target backend is
    configured before flipping; refusing returns 400 so an operator
    doesn't blackhole RAG by toggling to an unconfigured target."""
    name = (payload.active or "").strip().lower()
    if name not in list_available_retrievers():
        raise HTTPException(status_code=400, detail=f"Unknown retriever {name!r}")
    target = get_retriever_by_name(name)
    if not target.is_configured():
        raise HTTPException(
            status_code=400,
            detail=f"Retriever {name!r} is not configured — refuse switch to avoid RAG outage",
        )
    try:
        await set_active_retriever(name)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {"active": name, "ok": True}
