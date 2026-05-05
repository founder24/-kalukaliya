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

from fastapi import APIRouter, Depends

from auth_deps import get_admin_user

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

    # ── Memory brain (Voyage + Atlas) ──────────────────────────────────────
    memory_health: dict[str, Any]
    try:
        from providers import memory_brain as _mb
        memory_health = await _mb.health_check()
    except Exception as exc:
        memory_health = {"ok": False, "reason": str(exc)[:200]}
    memory_health["flag"] = {"name": "MEMORY_BRAIN_PROVIDER", "value": MEMORY_BRAIN_PROVIDER}
    memory_health["collection"] = MEMORY_BRAIN_COLLECTION

    return {
        "ok": all([
            embed_health.get("ok"),
            rerank_health.get("ok"),
            memory_health.get("ok"),
        ]),
        "embed":  embed_health,
        "rerank": rerank_health,
        "memory": memory_health,
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
