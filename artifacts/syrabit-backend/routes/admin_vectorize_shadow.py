"""Task #383 — dedicated ``/admin/vectorize-shadow`` endpoint.

The unified ``/admin/cf-health`` panel already surfaces the snapshot,
but the parity-investigation flow (compare recall over a longer window,
reset counters before re-running a benchmark) deserves its own URL so
the on-call doesn't have to scroll past 8 unrelated workstreams.

Endpoints:

  * ``GET  /admin/vectorize-shadow``        — full snapshot + recent samples
  * ``POST /admin/vectorize-shadow/reset``  — clear counters before a parity run
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from auth_deps import get_admin_user
from config import VECTORIZE_SHADOW_ON
from vectorize_shadow import reset_for_tests, snapshot

router = APIRouter()


@router.get("/admin/vectorize-shadow")
async def admin_vectorize_shadow(admin: dict = Depends(get_admin_user)) -> dict:
    snap = snapshot()
    snap["enabled"] = bool(VECTORIZE_SHADOW_ON)
    return snap


@router.post("/admin/vectorize-shadow/reset")
async def admin_vectorize_shadow_reset(
    admin: dict = Depends(get_admin_user),
) -> dict:
    """Zero out the in-memory counters. Use before running a fresh
    parity benchmark so the recall-overlap average isn't diluted by
    historical samples from a previous shadow build."""
    reset_for_tests()
    return {"ok": True, "reset": True, "enabled": bool(VECTORIZE_SHADOW_ON)}
