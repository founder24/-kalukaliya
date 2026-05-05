"""Task #386 — D1 mirror for ``seo_meta``, ``audit_log`` and
``syllabus_map`` tables, plus a tiny lag tracker.

The existing ``d1_sync`` module already fans the public content
catalog (boards / classes / subjects / chapters / topics / seo_pages)
out to one or more edge Workers. Task #386 extends that fan-out with
three additional tables that the Pages Functions SSR layer needs to
render meta tags + breadcrumbs without round-tripping to the origin:

  * ``seo_meta``      — per-route meta_title / meta_description /
                        canonical / og_image / robots
  * ``audit_log``     — immutable change log for SEO-impacting writes
                        (used by the SSR layer to skip stale routes)
  * ``syllabus_map``  — board → class → subject → chapter → topic
                        breadcrumb chain (mirror of the same shape
                        already exported as ``boards`` … ``topics``)

The mirror is gated by ``D1_MIRROR_ON`` so flipping it back simply
stops the next sync from including the new tables; the previously
mirrored rows stay in D1 (harmless — Pages Functions read them
opportunistically and fall back to live origin data if absent).

A small in-process ``MIRROR_STATE`` records the last successful
sync timestamp + row counts so ``/admin/cf-health`` can render a
"D1 lag" row without needing a round-trip to the edge worker.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)


def is_enabled() -> bool:
    from config import D1_MIRROR_ON
    return bool(D1_MIRROR_ON)


# In-process mirror state. Worker-local; the cf-health route renders
# what this worker has done so the admin can compare across pods if
# needed.
_state: dict[str, Any] = {
    "last_sync_ts": None,            # epoch float
    "last_sync_row_counts": {},      # {table: count}
    "last_sync_ok": None,            # bool
    "last_sync_error": None,         # str | None
    "consecutive_failures": 0,
}


def _record_sync(ok: bool, row_counts: dict[str, int], error: str | None = None) -> None:
    _state["last_sync_ts"] = time.time()
    _state["last_sync_row_counts"] = dict(row_counts)
    _state["last_sync_ok"] = ok
    _state["last_sync_error"] = error
    if ok:
        _state["consecutive_failures"] = 0
    else:
        _state["consecutive_failures"] = int(_state.get("consecutive_failures") or 0) + 1


async def _export_seo_meta(db) -> list[dict]:
    """Read the seo_meta projection from Mongo. Best-effort — returns
    an empty list if the collection is missing so we don't crash a
    deployment where the table hasn't been populated yet."""
    if db is None:
        return []
    try:
        return await db.seo_meta.find(
            {},
            {
                "_id": 0, "route": 1, "meta_title": 1, "meta_description": 1,
                "canonical": 1, "og_image": 1, "robots": 1, "lang": 1,
                "updated_at": 1,
            },
        ).to_list(50000)
    except Exception as exc:
        logger.warning("d1_mirror: seo_meta export failed — %s", exc)
        return []


async def _export_audit_log(db, max_rows: int = 5000) -> list[dict]:
    """Last ``max_rows`` audit log entries — enough for the SSR layer
    to know what changed in the last 24h and invalidate accordingly."""
    if db is None:
        return []
    try:
        return await db.audit_log.find(
            {},
            {"_id": 0, "id": 1, "actor": 1, "action": 1, "entity": 1,
             "entity_id": 1, "ts": 1, "details": 1},
        ).sort("ts", -1).to_list(max_rows)
    except Exception as exc:
        logger.warning("d1_mirror: audit_log export failed — %s", exc)
        return []


async def _export_syllabus_map(db) -> list[dict]:
    """Flattened breadcrumb chain — one row per leaf topic with the
    full chain materialised so SSR doesn't need joins."""
    if db is None:
        return []
    try:
        return await db.syllabus_map.find(
            {},
            {"_id": 0, "topic_id": 1, "topic_slug": 1, "chapter_slug": 1,
             "subject_slug": 1, "stream_slug": 1, "class_slug": 1,
             "board_slug": 1, "lang": 1},
        ).to_list(50000)
    except Exception as exc:
        logger.warning("d1_mirror: syllabus_map export failed — %s", exc)
        return []


async def export_extended_payload(db) -> dict[str, list[dict]]:
    """Build the extended D1 payload (seo_meta + audit_log + syllabus_map).

    Returns an empty dict when the flag is off so callers can chain
    this into the existing ``d1_sync`` payload without branching on
    the flag.
    """
    if not is_enabled():
        return {}
    seo_meta, audit_log, syllabus_map = await asyncio.gather(
        _export_seo_meta(db),
        _export_audit_log(db),
        _export_syllabus_map(db),
    )
    return {
        "seo_meta": seo_meta,
        "audit_log": audit_log,
        "syllabus_map": syllabus_map,
    }


async def sync_extended(db) -> dict[str, Any]:
    """Run an extended D1 mirror sync (the three new tables only).

    The caller is expected to be the same scheduler that already
    runs ``d1_sync.sync_full`` — we deliberately keep the two payloads
    independent so a single-table failure on one side doesn't poison
    the other.
    """
    if not is_enabled():
        return {"success": False, "reason": "flag_off"}
    payload = await export_extended_payload(db)
    if not payload:
        _record_sync(False, {}, error="empty_payload")
        return {"success": False, "reason": "empty_payload"}

    try:
        from d1_sync import trigger_d1_sync
        ok = await trigger_d1_sync(payload)
    except Exception as exc:
        _record_sync(False, {k: len(v) for k, v in payload.items()},
                     error=f"{type(exc).__name__}: {exc}")
        return {"success": False, "reason": f"{type(exc).__name__}: {exc}"}

    counts = {k: len(v) for k, v in payload.items()}
    _record_sync(ok, counts, error=None if ok else "primary_target_failed")
    return {
        "success": ok,
        "tables": list(payload.keys()),
        "row_counts": counts,
    }


def lag_snapshot() -> dict[str, Any]:
    """Lag indicator for the cf-health panel.

    ``lag_seconds`` is wall-clock time since the last successful sync
    (None when no sync has happened yet). The admin UI renders red
    when it exceeds the configured threshold.
    """
    ts = _state.get("last_sync_ts")
    lag = (time.time() - ts) if ts else None
    return {
        "enabled": is_enabled(),
        "last_sync_ts": ts,
        "lag_seconds": lag,
        "row_counts": dict(_state.get("last_sync_row_counts") or {}),
        "last_sync_ok": _state.get("last_sync_ok"),
        "last_sync_error": _state.get("last_sync_error"),
        "consecutive_failures": int(_state.get("consecutive_failures") or 0),
    }


def reset_state() -> None:
    """Test helper."""
    _state.update({
        "last_sync_ts": None,
        "last_sync_row_counts": {},
        "last_sync_ok": None,
        "last_sync_error": None,
        "consecutive_failures": 0,
    })
    with _read_lock:
        _read_counters.update({
            "d1_hit": 0, "d1_miss": 0, "d1_error": 0, "mongo_fallback": 0,
        })


# ── D1 read-prefer / Mongo fallback ─────────────────────────────────────────
# D1-first lookup with Mongo loader fallback. No-op when D1_MIRROR_ON
# is unset. Auth uses DISPATCH_SHARED_SECRET against D1_READ_BASE_URL
# (defaults to EDGE_WORKER_URL).

_read_lock = threading.Lock()
_read_counters: dict[str, int] = {
    "d1_hit": 0,
    "d1_miss": 0,
    "d1_error": 0,
    "mongo_fallback": 0,
}


def _bump_read(name: str) -> None:
    with _read_lock:
        _read_counters[name] = _read_counters.get(name, 0) + 1


def read_counters_snapshot() -> dict[str, int]:
    with _read_lock:
        return dict(_read_counters)


_D1_BASE = (
    os.environ.get("D1_READ_BASE_URL", "").strip().rstrip("/")
    or os.environ.get("EDGE_WORKER_URL", "").strip().rstrip("/")
)
_D1_SECRET = (
    os.environ.get("D1_READ_SHARED_SECRET", "").strip()
    or os.environ.get("DISPATCH_SHARED_SECRET", "").strip()
)
_D1_TIMEOUT_S = float(os.environ.get("D1_READ_TIMEOUT_S", "1.5") or "1.5")


def _d1_configured() -> bool:
    return bool(_D1_BASE and _D1_SECRET)


async def _d1_get(table: str, key_field: str, key_value: str) -> Any:
    """Fetch a single row from the D1 mirror via the edge worker.

    Returns the parsed JSON body (typically a dict) on hit, ``None``
    on miss, or raises on transport error so the caller can record it
    distinctly from a logical miss.
    """
    if not _d1_configured():
        return None
    import httpx
    url = f"{_D1_BASE}/d1/read/{table}"
    headers = {"Authorization": f"Bearer {_D1_SECRET}"}
    params = {key_field: key_value}
    async with httpx.AsyncClient(timeout=_D1_TIMEOUT_S) as client:
        resp = await client.get(url, headers=headers, params=params)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        try:
            return resp.json()
        except Exception:
            return None


async def read_with_fallback(
    table: str,
    key_field: str,
    key_value: str,
    mongo_loader,  # Callable[[], Awaitable[Any]]
) -> Any:
    """D1-first read with a Mongo fallback.

    ``table``      — D1 table name (e.g. ``"seo_meta"``)
    ``key_field``  — primary key column (e.g. ``"route"``)
    ``key_value``  — primary key value
    ``mongo_loader`` — async zero-arg callable that returns the
        equivalent record from Mongo when D1 is unavailable / misses.

    Returns whatever D1 returns (typically a dict) on hit, otherwise
    delegates to ``mongo_loader``. Counters surfaced via
    :func:`read_counters_snapshot` so ``/admin/cf-health.d1_mirror``
    can report a hit-ratio.
    """
    if is_enabled() and _d1_configured() and key_value:
        try:
            row = await _d1_get(table, key_field, key_value)
        except Exception as exc:
            logger.warning("d1_mirror.read: %s/%s lookup error — %s", table, key_value, exc)
            _bump_read("d1_error")
            row = None
        else:
            if row is not None:
                _bump_read("d1_hit")
                return row
            _bump_read("d1_miss")
    _bump_read("mongo_fallback")
    return await mongo_loader()


# ── Typed read helpers ─────────────────────────────────────────────────────
async def read_seo_meta(route: str, db) -> dict | None:
    """D1-first read of an seo_meta row keyed on route path."""
    async def _mongo():
        if db is None:
            return None
        try:
            return await db.seo_meta.find_one({"route": route}, {"_id": 0})
        except Exception as exc:
            logger.warning("d1_mirror.read_seo_meta mongo fallback failed: %s", exc)
            return None
    return await read_with_fallback("seo_meta", "route", route, _mongo)


async def read_audit_log_recent(limit: int, db) -> list[dict]:
    """D1-first read of the last ``limit`` audit-log entries. The D1
    side stores them sorted by ts desc (per ``_export_audit_log``), so
    the helper just returns the slice; the Mongo fallback runs the
    same query against the live collection."""
    async def _mongo():
        if db is None:
            return []
        try:
            return await db.audit_log.find(
                {}, {"_id": 0}
            ).sort("ts", -1).to_list(int(limit))
        except Exception as exc:
            logger.warning("d1_mirror.read_audit_log_recent fallback failed: %s", exc)
            return []
    rows = await read_with_fallback("audit_log", "limit", str(int(limit)), _mongo)
    if isinstance(rows, list):
        return rows[: int(limit)]
    return []


async def read_syllabus_chain(topic_id: str, db) -> dict | None:
    """D1-first read of the breadcrumb chain for a topic. Returns the
    full ``board/class/stream/subject/chapter/topic`` slug chain so
    SSR can render breadcrumbs without joining."""
    async def _mongo():
        if db is None:
            return None
        try:
            return await db.syllabus_map.find_one(
                {"topic_id": topic_id}, {"_id": 0}
            )
        except Exception as exc:
            logger.warning("d1_mirror.read_syllabus_chain mongo fallback failed: %s", exc)
            return None
    return await read_with_fallback("syllabus_map", "topic_id", topic_id, _mongo)
