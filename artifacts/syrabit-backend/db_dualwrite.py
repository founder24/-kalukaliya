"""Phase 2 of the V4 §13 PG→Mongo migration (ADR-0001).

Best-effort dual-write helper for the migration's per-collection mirrors.

Contract (locked by ADR-0001):
- PG remains the read-of-record during Phase 2.
- Every PG write to a migrated collection is mirrored into Mongo *inside
  the same request*, but the mirror is **best-effort**: a Mongo failure
  must NOT fail the request. PG is still the source of truth, so the
  user-facing write succeeded; we just lost a mirror tick.
- Per-process counters ``<collection>.{success,fail,skipped_disabled,
  skipped_no_db}`` are exposed via :func:`get_dualwrite_counters` for the
  admin health panel.
- Rollback: set ``MONGO_<NAME>_WRITES=0`` to disable mirrors for one
  collection (e.g. ``MONGO_USER_WRITES=0`` or
  ``MONGO_CONVERSATION_WRITES=0``). Single env flip per collection,
  no deploy.

Per-collection rollout (Phase 2):
- ``users`` ............... SHIPPED 2026-05-06 (B4)
- ``conversations`` ....... SHIPPED 2026-05-06 (this module)
- 8 remaining collections . NOT STARTED — separate sessions per the
  ADR's per-table contract.

Carve-outs (do NOT migrate to this helper):
- ``routes/admin_monetization.py`` keeps its own raw paired PG↔Mongo
  writes because those flows have **transactional compensating
  rollback** semantics (the Mongo write *must* raise so the PG side
  can be undone). Routing them through this best-effort helper would
  swallow the Mongo error and break the rollback contract.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Awaitable, Callable

import deps as _deps_mod

logger = logging.getLogger(__name__)

_FALSY = frozenset({"0", "false", "no", "off"})
_COUNTER_KEYS = ("success", "fail", "skipped_disabled", "skipped_no_db")

# Per-collection counters — auto-initialized on first use.
_DUALWRITE_COUNTERS: dict[str, int] = {}

# Env-flag naming: collection -> ENV var. Defaults to a singularised
# upper-case form (``users`` -> ``USER`` -> ``MONGO_USER_WRITES``);
# overrides live here for collections whose pluralisation isn't a
# trailing 's' or whose canonical flag name was locked by an earlier
# phase (e.g. ``MONGO_USER_WRITES`` from B4).
_FLAG_NAME_OVERRIDES: dict[str, str] = {
    "users": "USER",
    "conversations": "CONVERSATION",
}


def _ensure_counters(collection: str) -> None:
    """Initialize the four counter slots for ``collection`` on first use."""
    for k in _COUNTER_KEYS:
        _DUALWRITE_COUNTERS.setdefault(f"{collection}.{k}", 0)


def _flag_env_for(collection: str) -> str:
    """Return ``MONGO_<NAME>_WRITES`` for the collection."""
    name = _FLAG_NAME_OVERRIDES.get(
        collection,
        collection.upper().rstrip("S") or collection.upper(),
    )
    return f"MONGO_{name}_WRITES"


def mongo_collection_writes_enabled(collection: str) -> bool:
    """Return True unless the operator has set ``MONGO_<NAME>_WRITES=0``."""
    return os.environ.get(_flag_env_for(collection), "1").strip().lower() not in _FALSY


# --- Back-compat (B4): users-specific shims ------------------------------

def mongo_user_writes_enabled() -> bool:
    """B4 shim. Prefer :func:`mongo_collection_writes_enabled('users')`."""
    return mongo_collection_writes_enabled("users")


def get_dualwrite_counters() -> dict[str, int]:
    """Snapshot of per-process dual-write counters (for admin/health surface)."""
    return dict(_DUALWRITE_COUNTERS)


def reset_dualwrite_counters_for_test() -> None:
    """Test-only: zero (and forget) all per-collection counters."""
    _DUALWRITE_COUNTERS.clear()


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


async def mirror_collection_write(
    collection: str,
    op_label: str,
    fn: Callable[[], Awaitable[Any]],
) -> None:
    """Mirror an arbitrary Mongo write (best-effort) for the given collection.

    ``collection`` — Mongo collection name (e.g. ``"users"``,
    ``"conversations"``). Used for counter keys and for the env flag
    ``MONGO_<NAME>_WRITES``.

    ``op_label`` — short tag used for logs (e.g. ``"upsert"``,
    ``"update"``, ``"delete"``, ``"refund_credit"``).

    ``fn`` — zero-arg async callable that performs the Mongo write,
    typically a lambda that closes over ``deps.db.<collection>``. We call
    it here (rather than accepting raw args) so the caller can express
    any update expression — ``$set``, ``$inc``, ``upsert=True``, etc.

    Never raises. Records one of four outcomes:
      • ``<collection>.skipped_disabled`` — operator turned mirroring off
      • ``<collection>.skipped_no_db`` — Mongo client not ready (e.g.
        lifespan startup race or local dev without ``MONGO_URL``)
      • ``<collection>.success`` — mirror landed
      • ``<collection>.fail`` — Mongo raised; PG remains SoT
    """
    _ensure_counters(collection)
    if not mongo_collection_writes_enabled(collection):
        _DUALWRITE_COUNTERS[f"{collection}.skipped_disabled"] += 1
        return
    if _deps_mod.db is None:
        _DUALWRITE_COUNTERS[f"{collection}.skipped_no_db"] += 1
        logger.warning(
            "dualwrite %s.%s skipped: deps.db is None (Mongo not ready)",
            collection,
            op_label,
        )
        return
    try:
        await fn()
        _DUALWRITE_COUNTERS[f"{collection}.success"] += 1
    except Exception as e:
        _DUALWRITE_COUNTERS[f"{collection}.fail"] += 1
        logger.warning(
            "dualwrite %s.%s failed (PG remains SoT): %s: %s",
            collection,
            op_label,
            type(e).__name__,
            e,
        )


async def mirror_user_write(
    op_label: str,
    fn: Callable[[], Awaitable[Any]],
) -> None:
    """B4 shim — :func:`mirror_collection_write('users', op_label, fn)`."""
    await mirror_collection_write("users", op_label, fn)


async def mirror_conversation_write(
    op_label: str,
    fn: Callable[[], Awaitable[Any]],
) -> None:
    """Mirror a write to the ``conversations`` collection (best-effort)."""
    await mirror_collection_write("conversations", op_label, fn)
