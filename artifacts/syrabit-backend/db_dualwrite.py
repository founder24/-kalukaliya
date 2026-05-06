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
- ``conversations`` ....... SHIPPED 2026-05-06
- ``edu_notes`` ........... SHIPPED 2026-05-06 (greenfield Mongo target;
  see ADR-0001 §50 — five PG write sites in ``routes/edu_study.py``).
- ``edu_flashcards`` ...... SHIPPED 2026-05-06 (greenfield; FK child
  of edu_notes; 5 PG write sites in ``routes/edu_study.py`` —
  build_flashcards uses bulk ``insert_many`` to amortise the
  ≤2.4 k-card fan-out, review uses ``replace_one(upsert=True)``,
  claim mirror fires post-transaction with ``cards_count > 0`` gate).
- ``edu_study_settings`` .. SHIPPED 2026-05-06 (greenfield; composite
  PK ``(actor_kind, actor)`` — no surrogate id; 8 PG write sites in
  ``routes/edu_study.py`` collapsed into 5 mirror calls — streak's
  3 mutually-exclusive branches collapsed into 1 post-block upsert,
  claim's 3 writes collapsed into 1 user-side upsert + 1 anon-side
  delete after txn commit).
- ``activity_log`` ......... SHIPPED 2026-05-06 (soft-join — Mongo
  collection already populated by the existing 3rd-tier fallback in
  ``db_ops.supa_insert_activity_log``; Phase 2 adds a mirror on the
  PG-success branch so Mongo now sees *every* write, not only PG
  failures. Two centralised sites in ``db_ops.py`` cover all 8
  route-level callers — insert + clear). Rollback:
  ``MONGO_ACTIVITY_LOG_WRITES=0``.
- 4 remaining collections . NOT STARTED — separate sessions per the
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
    "edu_notes": "EDU_NOTE",
    "edu_flashcards": "EDU_FLASHCARD",
    "edu_study_settings": "EDU_STUDY_SETTING",
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


async def mirror_edu_notes_write(
    op_label: str,
    fn: Callable[[], Awaitable[Any]],
) -> None:
    """Mirror a write to the ``edu_notes`` collection (best-effort).

    Greenfield collection per ADR-0001 §50 — the Mongo target does not
    exist until Phase 2 starts populating it. Read paths still hit PG
    until Phase 4 cutover, so every mirror miss is safe.
    """
    await mirror_collection_write("edu_notes", op_label, fn)


async def mirror_edu_flashcards_write(
    op_label: str,
    fn: Callable[[], Awaitable[Any]],
) -> None:
    """Mirror a write to the ``edu_flashcards`` collection (best-effort).

    Greenfield collection per ADR-0001 §50 — Phase 2 starts populating
    Mongo on every PG flashcard write, but PG remains read-of-record
    until Phase 4 cutover. Rollback flag: ``MONGO_EDU_FLASHCARD_WRITES=0``.

    edu_flashcards is the FK child of edu_notes (one note → many cards
    via SM-2 spaced-repetition expansion). The build endpoint can
    fan-out up to ~12 cards per note × 200 notes = 2.4 k inserts in a
    single request, so callers should batch into ``insert_many`` rather
    than per-card mirror calls to avoid serial round-trip latency.
    """
    await mirror_collection_write("edu_flashcards", op_label, fn)


async def mirror_edu_study_settings_write(
    op_label: str,
    fn: Callable[[], Awaitable[Any]],
) -> None:
    """Mirror a write to the ``edu_study_settings`` collection (best-effort).

    Greenfield collection per ADR-0001 §50 — PG remains read-of-record
    until Phase 4 cutover. Rollback flag:
    ``MONGO_EDU_STUDY_SETTING_WRITES=0`` (singular form per the
    edu_notes / edu_flashcards convention).

    edu_study_settings has a composite primary key
    ``(actor_kind, actor)`` — there is no surrogate ``id`` column. The
    Mongo doc therefore uses ``{actor_kind, actor}`` as the natural
    key, with every write expressed as
    ``update_one(filter, {$set: ...}, upsert=True)`` (or ``delete_one``
    for the claim cleanup). Callers may collapse multiple branches
    into a single mirror call when the final state is determinable
    after the PG write block exits (see the streak-update + claim
    flows in ``routes/edu_study.py``).
    """
    await mirror_collection_write("edu_study_settings", op_label, fn)


async def mirror_activity_log_write(
    op_label: str,
    fn: Callable[[], Awaitable[Any]],
) -> None:
    """Mirror a write to the ``activity_log`` collection (best-effort).

    Soft-join collection: the Mongo target is *already populated* by
    the existing 3rd-tier fallback inside
    :func:`db_ops.supa_insert_activity_log` whenever both PG and the
    Supabase legacy tier raise. Phase 2 adds a mirror on the
    **PG-success** branch so Mongo now sees every write, not only the
    PG-failure ones — this is the prerequisite for the Phase-3
    read-shadow that compares per-day row-counts between the two
    stores.

    Two centralised wire-ups in ``db_ops.py`` cover all 8 route-level
    callers (admin_settings, admin_logs, admin_auth_users) — insert
    via ``mirror_activity_log_write("insert", ...)`` and the bulk
    purge via ``mirror_activity_log_write("clear", ...)``. Routes do
    NOT call this helper directly.

    Rollback flag: ``MONGO_ACTIVITY_LOG_WRITES=0`` — the existing
    fallback path keeps working unchanged because it lives below this
    helper in the call graph.
    """
    await mirror_collection_write("activity_log", op_label, fn)


async def mirror_notifications_write(
    op_label: str,
    fn: Callable[[], Awaitable[Any]],
) -> None:
    """Mirror a write to the ``notifications`` collection (best-effort).

    Soft-join collection (same pattern as ``activity_log``): the Mongo
    target is *already populated* by the existing 3rd-tier fallback in
    :func:`db_ops.supa_insert_notification` /
    :func:`db_ops.supa_delete_notification` whenever both PG and the
    Supabase legacy tier raise. Phase 2 adds a mirror on the
    **PG-success** branch so Mongo now sees every notification write,
    not only the PG-failure ones — the prerequisite for the Phase-3
    read-shadow row-count comparison.

    Two centralised wire-ups in ``db_ops.py`` cover every route-level
    caller (admin notification CRUD, push-notification dispatch helpers
    that funnel through ``supa_insert_notification``) — insert via
    ``mirror_notifications_write("insert", ...)`` and per-id delete via
    ``mirror_notifications_write("delete", ...)``. Routes do NOT call
    this helper directly.

    Rollback flag: ``MONGO_NOTIFICATION_WRITES=0`` (singularised by the
    default ``rstrip('S')`` rule — no override entry needed). The
    existing 3rd-tier fallback keeps working unchanged because it
    lives below this helper in the call graph.
    """
    await mirror_collection_write("notifications", op_label, fn)
