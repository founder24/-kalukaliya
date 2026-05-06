"""Task #382 — Admin health surface for the new embed stack.

Surfaces three pills for the admin dashboard:

  * **embed**   — the custom Workers-AI embed worker
                  (``providers.workers_embed.health_check``).
  * **rerank**  — Pinecone-only rerank reachability via
                  ``providers.pinecone_ai.health_check``.
  * **memory**  — the Voyage-backed Mongo memory_brain via
                  ``providers.memory_brain.health_check``.

Disabled providers (cohere, voyage on chunks, vertex_embed, workers_ai
bge-small fallback) are listed under ``dormant`` so the operator can
see the full pre-Task-#382 layout without the dispatchers actually
calling them. Each entry carries the flag name + active value so the
rollback procedure is self-documenting in the dashboard.

This route always returns 200 — individual leg failures are reported
inline so a single bad provider can't blank out the page.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query

from auth_deps import get_admin_user
import deps

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/admin/health/embed-stack")
async def admin_embed_stack_health(
    admin: dict = Depends(get_admin_user),
) -> dict[str, Any]:
    """Combined embed/rerank/memory-brain health pill (Task #382)."""
    from config import (
        EMBED_PROVIDER_PRIMARY,
        RERANK_PROVIDER,
        MEMORY_BRAIN_PROVIDER,
        MEMORY_BRAIN_COLLECTION,
        WORKERS_EMBED_URL,
    )

    # ── Embed (Workers-AI custom worker) ───────────────────────────────────
    embed_health: dict[str, Any]
    try:
        from providers import workers_embed as _we
        embed_health = await _we.health_check()
    except Exception as exc:
        embed_health = {"ok": False, "configured": False, "reason": str(exc)[:200]}
    embed_health["flag"] = {"name": "EMBED_PROVIDER_PRIMARY", "value": EMBED_PROVIDER_PRIMARY}

    # ── Task #436 — per-leg watchdog counters ──────────────────────────────
    # Surface the in-memory consecutive-failure counters maintained by the
    # Task #412 alerting loop so on-call sees "embed leg has failed 2/3
    # times" *before* the page fires (and can confirm recovery without
    # waiting for the recovery alert to land in the inbox).
    try:
        import metrics as _metrics
        _alert_snapshot = _metrics.get_embed_stack_alert_snapshot()
    except Exception as exc:  # pragma: no cover - defensive
        _alert_snapshot = {"threshold": 3, "legs": {}, "error": str(exc)[:200]}
    _alert_threshold = _alert_snapshot.get("threshold", 3)
    _alert_legs = _alert_snapshot.get("legs", {}) or {}

    def _attach_alert_state(pill: dict, leg: str) -> None:
        leg_state = _alert_legs.get(leg) or {}
        pill["consecutive_failures"] = int(leg_state.get("consecutive_failures") or 0)
        pill["firing"] = bool(leg_state.get("firing"))
        pill["alert_threshold"] = _alert_threshold

    _attach_alert_state(embed_health, "embed")

    # ── Rerank (Pinecone only) ─────────────────────────────────────────────
    # Use the rerank-specific probe (Task #382) which actually exercises
    # /rerank with the configured PINECONE_RERANK_MODEL. The generic
    # health_check probes the embed endpoint which doesn't tell us
    # whether the rerank surface is reachable.
    rerank_health: dict[str, Any]
    try:
        from providers import pinecone_ai as _pc
        rerank_health = await _pc.rerank_health_check()
    except Exception as exc:
        rerank_health = {"ok": False, "reason": str(exc)[:200]}
    rerank_health["flag"] = {"name": "RERANK_PROVIDER", "value": RERANK_PROVIDER}
    _attach_alert_state(rerank_health, "rerank")

    # ── Memory brain (Voyage + Atlas) ──────────────────────────────────────
    memory_health: dict[str, Any]
    try:
        from providers import memory_brain as _mb
        memory_health = await _mb.health_check()
    except Exception as exc:
        memory_health = {"ok": False, "reason": str(exc)[:200]}
    memory_health["flag"] = {"name": "MEMORY_BRAIN_PROVIDER", "value": MEMORY_BRAIN_PROVIDER}
    memory_health["collection"] = MEMORY_BRAIN_COLLECTION
    _attach_alert_state(memory_health, "memory_brain")

    # ── Backfill progress (Task #411) ──────────────────────────────────────
    # Surface how many legacy chunks are still on the old embed stack so the
    # admin dashboard can render a "X / Y chunks re-embedded" line right
    # next to the green/red embed pill.
    backfill: dict[str, Any]
    try:
        from aca_jobs import embed_backfill as _bf
        db = getattr(deps, "db", None)
        if db is None:
            backfill = {"ok": False, "reason": "db unavailable"}
        else:
            backfill = await _bf.get_progress(db)
            backfill["ok"] = True
    except Exception as exc:
        backfill = {"ok": False, "reason": str(exc)[:200]}

    return {
        "ok": all([
            embed_health.get("ok"),
            rerank_health.get("ok"),
            memory_health.get("ok"),
        ]),
        "embed":  embed_health,
        "rerank": rerank_health,
        "memory": memory_health,
        "backfill": backfill,
        # Task #436 — full per-leg watchdog snapshot for the dashboard
        # badge ("N/3 consecutive failures", red when firing).
        "alert_state": _alert_snapshot,
        "flags": {
            "EMBED_PROVIDER_PRIMARY":  EMBED_PROVIDER_PRIMARY,
            "RERANK_PROVIDER":         RERANK_PROVIDER,
            "MEMORY_BRAIN_PROVIDER":   MEMORY_BRAIN_PROVIDER,
            "MEMORY_BRAIN_COLLECTION": MEMORY_BRAIN_COLLECTION,
            "WORKERS_EMBED_URL":       WORKERS_EMBED_URL or None,
        },
        # Old providers kept in the repo but skipped at runtime when
        # the new flags are active. Surfaced so on-call can see exactly
        # which modules are intentionally dormant.
        "dormant": [
            {"provider": "cohere",       "reason": "embed path repointed to workers_ai_custom (Task #382)"},
            {"provider": "vertex_embed", "reason": "embed path repointed to workers_ai_custom (Task #382)"},
            {"provider": "voyage_ai",    "reason": "embed pool removed; memory_brain only (Task #382)"},
            {"provider": "workers_ai",   "reason": "@cf/baai/bge-m3 demoted to weight-0 fallback (Task #382)"},
        ],
    }


@router.get("/admin/embed/backfill/progress")
async def admin_embed_backfill_progress(
    admin: dict = Depends(get_admin_user),
) -> dict[str, Any]:
    """Task #411 — current state of the legacy → workers_ai_custom backfill."""
    from aca_jobs import embed_backfill as _bf
    db = getattr(deps, "db", None)
    if db is None:
        return {"ok": False, "reason": "db unavailable"}
    progress = await _bf.get_progress(db)
    progress["ok"] = True
    return progress


@router.post("/admin/embed/backfill/run")
async def admin_embed_backfill_run(
    max_chunks: int = Query(
        default=5000, ge=1, le=200_000,
        description="Per-call processing budget; the job resumes from the "
                    "last_processed_id stored in embed_backfill_state.",
    ),
    batch_size: int = Query(
        default=32, ge=1, le=32,
        description="Texts per /embed call. Hard-capped at the worker's "
                    "32-input limit.",
    ),
    admin: dict = Depends(get_admin_user),
) -> dict[str, Any]:
    """Task #411 — kick off (or resume) one backfill pass.

    Returns immediately if a run is already in progress. The pass itself
    runs in the background so the admin UI doesn't block on a multi-minute
    HTTP request; poll ``/admin/embed/backfill/progress`` for live state.
    """
    import asyncio as _asyncio
    from aca_jobs import embed_backfill as _bf
    db = getattr(deps, "db", None)
    if db is None:
        return {"ok": False, "reason": "db unavailable"}
    progress = await _bf.get_progress(db)
    if progress.get("running"):
        return {"ok": True, "started": False, "reason": "already_running",
                "progress": progress}
    _asyncio.create_task(
        _bf.run_backfill(db, max_chunks=max_chunks, batch_size=batch_size)
    )
    return {"ok": True, "started": True, "max_chunks": max_chunks,
            "batch_size": batch_size, "progress": progress}
