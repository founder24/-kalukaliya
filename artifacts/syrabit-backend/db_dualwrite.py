"""Phase 2 of the V4 §13 PG→Mongo migration (ADR-0001).

Best-effort dual-write helper for the ``users`` collection.

Contract (locked by ADR-0001):
- PG remains the read-of-record during Phase 2.
- Every PG write to ``users`` is mirrored into Mongo *inside the same
  request*, but the mirror is **best-effort**: a Mongo failure must NOT
  fail the request. PG is still the source of truth, so the user-facing
  write succeeded; we just lost a mirror tick.
- Per-process counters ``users.success`` / ``users.fail`` are exposed
  via :func:`get_dualwrite_counters` for the admin health panel.
- Rollback: set ``MONGO_USER_WRITES=0`` to disable all mirrors. Single
  env flip, no deploy.

Carve-out: ``routes/admin_monetization.py`` keeps its own raw paired
PG↔Mongo writes because those flows have **transactional compensating
rollback** semantics (the Mongo write *must* raise so the PG side can
be undone). Routing them through this best-effort helper would swallow
the Mongo error and break the rollback contract. Do NOT migrate them
to this helper.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Awaitable, Callable

import deps as _deps_mod

logger = logging.getLogger(__name__)

_FLAG_ENV = "MONGO_USER_WRITES"
_FALSY = frozenset({"0", "false", "no", "off"})

_DUALWRITE_COUNTERS: dict[str, int] = {
    "users.success": 0,
    "users.fail": 0,
    "users.skipped_disabled": 0,
    "users.skipped_no_db": 0,
}


def mongo_user_writes_enabled() -> bool:
    """Return True unless the operator has set ``MONGO_USER_WRITES=0``."""
    return os.environ.get(_FLAG_ENV, "1").strip().lower() not in _FALSY


def get_dualwrite_counters() -> dict[str, int]:
    """Snapshot of per-process dual-write counters (for admin/health surface)."""
    return dict(_DUALWRITE_COUNTERS)


def reset_dualwrite_counters_for_test() -> None:
    """Test-only: zero the counters between cases."""
    for k in _DUALWRITE_COUNTERS:
        _DUALWRITE_COUNTERS[k] = 0


def clamped_decrement_pipeline(
    fields: dict[str, int],
) -> list[dict[str, Any]]:
    """Build a Mongo *pipeline-update* that decrements counters with a 0
    floor — the Mongo equivalent of PG's ``GREATEST(0, col - N)``.

    Architect-flagged 2026-05-06: a raw ``$inc`` with a negative delta can
    drive Mongo counters below zero on retry / duplicate refund, breaking
    PG↔Mongo convergence and feeding false diffs to the future Phase-3
    read-shadow. Refund mirrors MUST use this pipeline form.

    Example::

        await deps.db.users.update_one(
            {"id": uid},
            clamped_decrement_pipeline({"credits_used_today": 1, "credits_used": 1}),
        )

    becomes (per field N):
        $set: {field: {$max: [0, {$subtract: [{$ifNull: [field, 0]}, N]}]}}
    """
    set_stage: dict[str, Any] = {}
    for field, n in fields.items():
        set_stage[field] = {
            "$max": [
                0,
                {"$subtract": [{"$ifNull": [f"${field}", 0]}, int(n)]},
            ]
        }
    return [{"$set": set_stage}]


async def mirror_user_write(
    op_label: str,
    fn: Callable[[], Awaitable[Any]],
) -> None:
    """Mirror a ``users``-collection write into Mongo (best-effort).

    ``op_label`` is a short tag used for logs (e.g. ``"insert"``,
    ``"update"``, ``"refund_credit"``).

    ``fn`` is a zero-arg async callable that performs the Mongo write,
    typically a lambda that closes over ``deps.db.users``. We call it
    here (rather than accepting raw args) so the caller can express any
    Mongo update expression — ``$set``, ``$inc``, ``upsert=True``, etc.

    Never raises. Records one of four outcomes:
      • ``users.skipped_disabled`` — operator turned mirroring off
      • ``users.skipped_no_db`` — Mongo client not ready (e.g. lifespan
        startup race or local dev without ``MONGO_URL``)
      • ``users.success`` — mirror landed
      • ``users.fail`` — Mongo raised; PG remains SoT
    """
    if not mongo_user_writes_enabled():
        _DUALWRITE_COUNTERS["users.skipped_disabled"] += 1
        return
    if _deps_mod.db is None:
        _DUALWRITE_COUNTERS["users.skipped_no_db"] += 1
        logger.warning(
            "dualwrite users.%s skipped: deps.db is None (Mongo not ready)",
            op_label,
        )
        return
    try:
        await fn()
        _DUALWRITE_COUNTERS["users.success"] += 1
    except Exception as e:
        _DUALWRITE_COUNTERS["users.fail"] += 1
        logger.warning(
            "dualwrite users.%s failed (PG remains SoT): %s: %s",
            op_label,
            type(e).__name__,
            e,
        )
