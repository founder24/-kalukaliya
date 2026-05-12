"""Task #87 — Schema contract stubs for future prewarm and cache-calendar edge PATCH routes.

These routes return 501 until the corresponding edge-worker KV endpoints are
introduced.  The contract constants and @patch_route_contract-decorated models
are registered now so:

1. ``scripts/ci/check_patch_route_contract.py`` finds both ``*Patch(BaseModel)``
   classes in this ``admin_edge_*.py`` file and verifies the decorator is present
   on every CI run.

2. ``schemas.edge_settings`` exports ``CANONICAL_PREWARM_KEYS``,
   ``PATCHABLE_PREWARM_KEYS``, ``CANONICAL_CACHE_CALENDAR_KEYS``, and
   ``PATCHABLE_CACHE_CALENDAR_KEYS`` as the single source of truth, so both
   the proxy filter and snapshot tests import from one place.

3. The @patch_route_contract decorator fires at module import time (= backend
   startup), so a model / frozenset divergence is caught immediately — not
   buried in a test run.

When the edge worker endpoints go live, replace each 501 handler with real KV
proxy logic and add GET/PATCH snapshot tests analogous to
``tests/test_admin_edge_settings.py``.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth_deps import get_admin_user
from schemas.edge_settings import (
    CANONICAL_CACHE_CALENDAR_KEYS,
    CANONICAL_PREWARM_KEYS,
    PATCHABLE_CACHE_CALENDAR_KEYS,
    PATCHABLE_PREWARM_KEYS,
    patch_route_contract,
)

router = APIRouter()


@patch_route_contract(PATCHABLE_PREWARM_KEYS, CANONICAL_PREWARM_KEYS)
class PrewarmSettingsPatch(BaseModel):
    """PATCH body for /admin/edge/prewarm-settings.

    Field names must exactly match ``PATCHABLE_PREWARM_KEYS`` in
    ``schemas/edge_settings.py``.  The ``@patch_route_contract`` decorator
    verifies this invariant at class definition time (i.e. when the backend
    starts).  Either field may be omitted to leave it unchanged.
    """
    enabled: Optional[bool] = None
    schedule_utc: Optional[str] = None


@patch_route_contract(PATCHABLE_CACHE_CALENDAR_KEYS, CANONICAL_CACHE_CALENDAR_KEYS)
class CacheCalendarSettingsPatch(BaseModel):
    """PATCH body for /admin/edge/cache-calendar-settings.

    Field names must exactly match ``PATCHABLE_CACHE_CALENDAR_KEYS`` in
    ``schemas/edge_settings.py``.  The ``@patch_route_contract`` decorator
    verifies this invariant at class definition time (i.e. when the backend
    starts).
    """
    force_season: Optional[str] = None


@router.get("/admin/edge/prewarm-settings")
async def admin_edge_get_prewarm_settings(
    admin: dict = Depends(get_admin_user),
) -> dict:
    """Stub — 501 until the edge worker prewarm-settings KV endpoint is live.

    When implemented this route will proxy GET /api/edge/prewarm-settings from
    the edge worker and return exactly the keys in ``CANONICAL_PREWARM_KEYS``.
    """
    raise HTTPException(
        status_code=501,
        detail=(
            "GET /admin/edge/prewarm-settings is not yet implemented — "
            "the edge worker prewarm-settings KV endpoint does not exist yet. "
            "Expected response keys: "
            + ", ".join(sorted(CANONICAL_PREWARM_KEYS))
        ),
    )


@router.patch("/admin/edge/prewarm-settings")
async def admin_edge_patch_prewarm_settings(
    data: PrewarmSettingsPatch,
    admin: dict = Depends(get_admin_user),
) -> dict:
    """Stub — 501 until the edge worker prewarm-settings KV endpoint is live.

    When implemented this route will forward only the keys in
    ``PATCHABLE_PREWARM_KEYS`` to the edge worker PUT endpoint.
    """
    raise HTTPException(
        status_code=501,
        detail=(
            "PATCH /admin/edge/prewarm-settings is not yet implemented — "
            "the edge worker prewarm-settings KV endpoint does not exist yet. "
            "Accepted patch fields: "
            + ", ".join(sorted(PATCHABLE_PREWARM_KEYS))
        ),
    )


@router.get("/admin/edge/cache-calendar-settings")
async def admin_edge_get_cache_calendar_settings(
    admin: dict = Depends(get_admin_user),
) -> dict:
    """Stub — 501 until the edge worker cache-calendar-settings KV endpoint is live.

    When implemented this route will proxy GET /api/edge/cache-calendar-settings
    and return exactly the keys in ``CANONICAL_CACHE_CALENDAR_KEYS``.
    """
    raise HTTPException(
        status_code=501,
        detail=(
            "GET /admin/edge/cache-calendar-settings is not yet implemented — "
            "the edge worker cache-calendar-settings KV endpoint does not exist yet. "
            "Expected response keys: "
            + ", ".join(sorted(CANONICAL_CACHE_CALENDAR_KEYS))
        ),
    )


@router.patch("/admin/edge/cache-calendar-settings")
async def admin_edge_patch_cache_calendar_settings(
    data: CacheCalendarSettingsPatch,
    admin: dict = Depends(get_admin_user),
) -> dict:
    """Stub — 501 until the edge worker cache-calendar-settings KV endpoint is live.

    When implemented this route will forward only the keys in
    ``PATCHABLE_CACHE_CALENDAR_KEYS`` to the edge worker PUT endpoint.
    """
    raise HTTPException(
        status_code=501,
        detail=(
            "PATCH /admin/edge/cache-calendar-settings is not yet implemented — "
            "the edge worker cache-calendar-settings KV endpoint does not exist yet. "
            "Accepted patch fields: "
            + ", ".join(sorted(PATCHABLE_CACHE_CALENDAR_KEYS))
        ),
    )
