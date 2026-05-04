"""Task #944 — Unified Log Explorer DAO.

A single ``unified_logs`` Mongo collection is shared by three log
producers — the Cloudflare edge worker, the Cloudflare Pages analytics
adapter, and this FastAPI backend itself — so the admin "Logs" panel
can filter, search, and trace requests end-to-end without juggling
three different log destinations.

Storage shape
-------------
Each document carries a small, deliberately-flat schema so the admin
table can render every column without joins:

    {
        "_id":            <uuid hex>,
        "source":         "edge" | "pages" | "backend" | "cloudflare" | "cron",
        "level":          "debug" | "info" | "warn" | "error",
        "timestamp":      ISO-8601 UTC string (when the log line was *produced*),
        "received_at":    ISO-8601 UTC string (server clock at insert time),
        "expire_at":      datetime (TTL field — Mongo deletes when reached),
        "message":        short string (≤ 500 chars),
        "status":         int|None,        # HTTP status when applicable
        "duration_ms":    int|None,        # request duration
        "method":         "GET"|"POST"|... # HTTP verb
        "route":          "/api/..."       # request path / route template
        "country":        "IN"|...         # CF cf.country / clientCountryName
        "colo":           "BLR"|...        # CF colo / datacenter
        "cache":          "hit"|"miss"|"bypass"|"dynamic"|None
        "ray_id":         "8b3a..."        # cf-ray header (high-entropy id)
        "correlation_id": "8b3a..." | request_id (used to "trace this request")
        "user_agent":     <truncated>      # short UA snippet
        "extra":          {...}            # source-specific JSON payload (≤ 1 KB)
    }

Indexes (set up at startup by ``ensure_indexes``):
    - TTL on ``expire_at`` (Mongo's built-in expireAfterSeconds=0
      pattern means "delete when this datetime is reached").
    - ``timestamp DESC`` for the default newest-first table render.
    - ``(source, timestamp DESC)`` for the per-source filter.
    - ``correlation_id`` for the "trace this request" shortcut.
    - ``(level, timestamp DESC)`` and ``(status, timestamp DESC)`` for
      filter pulldowns. These are intentionally compound (with
      ``timestamp``) so the table sort still uses the index.

All inserts go through ``insert_logs`` which clips fields, validates
``source``, and never raises (all failures are logged and reflected in
the returned ``{accepted, dropped}`` counter so the ingest endpoint
can surface a useful 422 / 207 response without 500ing the worker).
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

UNIFIED_LOGS_COLLECTION = "unified_logs"

ALLOWED_SOURCES = ("edge", "pages", "backend", "cloudflare", "cron")
ALLOWED_LEVELS = ("debug", "info", "warn", "error")
ALLOWED_CACHE_VALUES = ("hit", "miss", "bypass", "dynamic", "expired", "stale")

DEFAULT_TTL_DAYS = 14
DEFAULT_QUERY_LIMIT = 200
MAX_QUERY_LIMIT = 1000
MAX_COUNT = 5000
MAX_INGEST_BATCH = int(
    os.environ.get("LOGS_INGEST_MAX_BATCH")
    or os.environ.get("UNIFIED_LOGS_MAX_INGEST_BATCH")
    or "500"
)

_MAX_MESSAGE_LEN = 500
_MAX_ROUTE_LEN = 256
_MAX_UA_LEN = 200
_MAX_EXTRA_BYTES = 1024
_MAX_STRING_LEN = 256

_SLOW_THRESHOLD_MS = 1500


def _ttl_days() -> int:
    """Resolve TTL days at call-time so tests can monkeypatch the env.

    Canonical env var is ``LOG_RETENTION_DAYS``. ``UNIFIED_LOGS_TTL_DAYS``
    is accepted as an alias to match the name surfaced in the admin UI
    safeguards card and replit.md docs — historically the runtime
    code used the shorter name and the docs/UI used the longer one,
    so we honour both to remove operator footguns. If both are set,
    the canonical ``LOG_RETENTION_DAYS`` wins.
    """
    raw = (os.environ.get("LOG_RETENTION_DAYS")
           or os.environ.get("UNIFIED_LOGS_TTL_DAYS") or "").strip()
    if not raw:
        return DEFAULT_TTL_DAYS
    try:
        n = int(raw)
        if n <= 0:
            return DEFAULT_TTL_DAYS
        # Cap at 90 days so a runaway secret can't pin the collection
        # past the disk budget the admin actually planned for.
        return min(n, 90)
    except ValueError:
        return DEFAULT_TTL_DAYS


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _clip(value: Any, limit: int) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    return s[:limit]


def _coerce_ts(value: Any) -> str:
    """Normalise an incoming timestamp to ISO-8601 UTC.

    Accepts ISO strings, epoch seconds, epoch millis, datetimes, or
    None. Falls back to "now" so a malformed producer field can't make
    a record un-sortable.
    """
    try:
        if value is None:
            return _now_utc().isoformat()
        if isinstance(value, datetime):
            dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
        if isinstance(value, (int, float)):
            # ms vs s heuristic: anything past year 5000 in seconds is ms.
            secs = float(value) / 1000.0 if value > 1e12 else float(value)
            return datetime.fromtimestamp(secs, tz=timezone.utc).isoformat()
        s = str(value).strip()
        if not s:
            return _now_utc().isoformat()
        # Common Z-suffix → +00:00 for fromisoformat compatibility.
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return _now_utc().isoformat()


def _coerce_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except Exception:
            return None


def _coerce_extra(value: Any) -> Optional[dict]:
    if value is None:
        return None
    if not isinstance(value, dict):
        return None
    # Hard byte-cap via JSON length — keeps the per-doc footprint
    # bounded even when a producer dumps a large CF cf object.
    import json
    try:
        encoded = json.dumps(value, default=str)
    except Exception:
        return None
    if len(encoded.encode("utf-8")) > _MAX_EXTRA_BYTES:
        # Truncate by dropping noisy fields first; if still too big
        # we keep the first 1KB of JSON re-decoded as a string blob.
        trimmed = {k: v for k, v in value.items() if k in {
            "ray_id", "rayId", "ray", "host", "method", "url",
            "cache_status", "cacheStatus", "cf_cache_status",
            "edge_status", "edgeResponseStatus", "origin_status",
            "originResponseStatus", "origin_duration_ms",
            "originResponseDurationMs", "client_ip", "userAgent",
            "user_agent", "country", "colo",
        }}
        try:
            encoded2 = json.dumps(trimmed, default=str)
            if len(encoded2.encode("utf-8")) <= _MAX_EXTRA_BYTES:
                return trimmed
        except Exception:
            pass
        return {"_truncated": True, "_size": len(encoded)}
    return value


def _normalize_record(
    raw: Dict[str, Any],
    *,
    default_source: str,
    ttl_days: int,
) -> Optional[Dict[str, Any]]:
    """Coerce a producer record into the canonical shape.

    Returns ``None`` if the record is so malformed it should be
    dropped (e.g. ``source`` is set to a value not in the allowlist
    AND no ``default_source`` rescue is available). Callers must
    treat a ``None`` as "drop, do not insert".
    """
    if not isinstance(raw, dict):
        return None
    src = (raw.get("source") or default_source or "").strip().lower() or default_source
    if src not in ALLOWED_SOURCES:
        # Reject unknown sources rather than silently coercing — an
        # unknown producer is exactly the kind of drift the audit log
        # is supposed to catch.
        return None
    level = (raw.get("level") or "info").strip().lower()
    if level not in ALLOWED_LEVELS:
        # Map common HTTP / Python aliases without losing data.
        if level in ("warning", "warns"):
            level = "warn"
        elif level in ("err", "fatal", "critical"):
            level = "error"
        elif level in ("trace", "verbose"):
            level = "debug"
        else:
            level = "info"
    cache = (raw.get("cache") or "").strip().lower() or None
    if cache and cache not in ALLOWED_CACHE_VALUES:
        cache = None
    status = _coerce_int(raw.get("status"))
    if status is not None and (status < 0 or status > 999):
        status = None
    duration = _coerce_int(raw.get("duration_ms"))
    if duration is not None and duration < 0:
        duration = None

    correlation = (
        _clip(raw.get("correlation_id"), _MAX_STRING_LEN)
        or _clip(raw.get("request_id"), _MAX_STRING_LEN)
        or _clip(raw.get("ray_id"), _MAX_STRING_LEN)
        or _clip(raw.get("rayId"), _MAX_STRING_LEN)
    )
    ray_id = _clip(raw.get("ray_id") or raw.get("rayId"), _MAX_STRING_LEN)

    timestamp = _coerce_ts(raw.get("timestamp") or raw.get("ts"))
    received_at = _now_utc()
    expire_at = received_at + timedelta(days=max(1, ttl_days))

    # Honour a caller-supplied ``_id`` so producers can shape an
    # idempotency key (e.g. the Cloudflare pull derives a deterministic
    # id per (minute, path, status, method, colo) bucket so a retry
    # within the same window collapses into the same Mongo doc instead
    # of double-counting).
    rec_id = _clip(raw.get("_id"), _MAX_STRING_LEN) or uuid.uuid4().hex
    rec: Dict[str, Any] = {
        "_id": rec_id,
        "source": src,
        "level": level,
        "timestamp": timestamp,
        "received_at": received_at.isoformat(),
        "expire_at": expire_at,  # datetime so Mongo TTL treats it natively
        "message": _clip(raw.get("message"), _MAX_MESSAGE_LEN),
        "status": status,
        "duration_ms": duration,
        "method": _clip(raw.get("method"), 16),
        "route": _clip(raw.get("route") or raw.get("path") or raw.get("url"), _MAX_ROUTE_LEN),
        "country": _clip(raw.get("country") or raw.get("clientCountryName"), 8),
        "colo": _clip(raw.get("colo") or raw.get("datacenter"), 16),
        "cache": cache,
        "ray_id": ray_id,
        "correlation_id": correlation,
        "user_agent": _clip(raw.get("user_agent") or raw.get("userAgent"), _MAX_UA_LEN),
        "extra": _coerce_extra(raw.get("extra")),
    }
    return rec


def should_keep_request(*, status: Optional[int], duration_ms: Optional[int],
                        sample_rate: float) -> bool:
    """Sampling rule shared by edge + backend shippers.

    4xx / 5xx and slow (≥1500 ms) requests are ALWAYS kept regardless of
    the sample rate so the explorer never silently misses an error.
    Otherwise we keep with probability ``sample_rate`` (clamped 0..1).
    """
    if status is not None and status >= 400:
        return True
    if duration_ms is not None and duration_ms >= _SLOW_THRESHOLD_MS:
        return True
    rate = max(0.0, min(1.0, float(sample_rate)))
    if rate >= 1.0:
        return True
    if rate <= 0.0:
        return False
    return random.random() < rate


# ─────────────────────────────────────────────────────────────────────────────
# Indexes
# ─────────────────────────────────────────────────────────────────────────────


async def ensure_indexes(db) -> None:
    """Create / refresh the indexes used by the explorer.

    Idempotent — Motor's ``create_index`` is a no-op when the index
    already exists with the same spec. Failures are logged but do not
    propagate (the route still works without indexes, just slower).
    """
    if db is None:
        return
    coll = db[UNIFIED_LOGS_COLLECTION]
    try:
        # TTL: Mongo deletes when the field's datetime value is reached
        # (expireAfterSeconds=0 + a datetime field set to the deadline).
        await coll.create_index("expire_at", expireAfterSeconds=0,
                                name="ttl_expire_at")
        await coll.create_index([("timestamp", -1)],
                                name="timestamp_desc")
        await coll.create_index([("source", 1), ("timestamp", -1)],
                                name="source_timestamp_desc")
        await coll.create_index("correlation_id", sparse=True,
                                name="correlation_id_sparse")
        await coll.create_index([("level", 1), ("timestamp", -1)],
                                name="level_timestamp_desc")
        await coll.create_index([("status", 1), ("timestamp", -1)],
                                name="status_timestamp_desc",
                                sparse=True)
    except Exception as exc:
        logger.warning("[unified_logs] ensure_indexes failed: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Insert
# ─────────────────────────────────────────────────────────────────────────────


async def insert_logs(
    db,
    batch: Iterable[Dict[str, Any]],
    *,
    default_source: str,
    ttl_days: Optional[int] = None,
) -> Dict[str, int]:
    """Bulk-insert a batch of records. Always returns a counter dict.

    Records that fail normalisation are counted under ``dropped``. A
    Mongo-level write failure is logged and the affected count is
    moved into ``dropped`` so the producer sees an honest tally.
    """
    if db is None:
        return {"accepted": 0, "dropped": 0}
    ttl = ttl_days if ttl_days is not None else _ttl_days()
    accepted_docs: List[Dict[str, Any]] = []
    dropped = 0
    for raw in batch:
        rec = _normalize_record(raw, default_source=default_source, ttl_days=ttl)
        if rec is None:
            dropped += 1
            continue
        accepted_docs.append(rec)
    if not accepted_docs:
        return {"accepted": 0, "dropped": dropped}
    try:
        await db[UNIFIED_LOGS_COLLECTION].insert_many(accepted_docs, ordered=False)
        return {"accepted": len(accepted_docs), "dropped": dropped}
    except Exception as exc:
        # ``BulkWriteError`` carries per-doc results; treat duplicate
        # ``_id`` collisions as "already ingested" (counted as dropped,
        # not error). Any other write error is fatal for the whole
        # batch — fall back to the original behaviour.
        details = getattr(exc, "details", None) or {}
        write_errors = details.get("writeErrors") if isinstance(details, dict) else None
        if isinstance(write_errors, list) and write_errors:
            duplicates = sum(1 for e in write_errors if (e or {}).get("code") == 11000)
            non_dup = len(write_errors) - duplicates
            if non_dup == 0:
                accepted_count = len(accepted_docs) - duplicates
                return {"accepted": max(0, accepted_count),
                        "dropped": dropped + duplicates}
        logger.warning("[unified_logs] insert_many failed: %s", exc)
        return {"accepted": 0, "dropped": dropped + len(accepted_docs)}


# ─────────────────────────────────────────────────────────────────────────────
# Filters
# ─────────────────────────────────────────────────────────────────────────────


def build_filter(filters: Dict[str, Any]) -> Dict[str, Any]:
    """Translate the admin UI filter dict into a Mongo query doc.

    Recognised keys (all optional):
        - ``sources``: list[str]  → ``source ∈``
        - ``levels``: list[str]   → ``level ∈``
        - ``status_min`` / ``status_max``: int → ``status`` range
        - ``route_prefix``: str → ``route`` startswith
        - ``correlation_id``: str → exact match
        - ``q``: str → free-text on ``message`` / ``route`` (regex,
          case-insensitive, escaped)
        - ``since`` / ``until``: ISO timestamps → ``timestamp`` range
    """
    q: Dict[str, Any] = {}
    sources = [s for s in (filters.get("sources") or []) if s in ALLOWED_SOURCES]
    if sources:
        q["source"] = {"$in": sources}
    levels = [s for s in (filters.get("levels") or []) if s in ALLOWED_LEVELS]
    if levels:
        q["level"] = {"$in": levels}
    status_q: Dict[str, Any] = {}
    if filters.get("status_min") is not None:
        status_q["$gte"] = int(filters["status_min"])
    if filters.get("status_max") is not None:
        status_q["$lte"] = int(filters["status_max"])
    if status_q:
        q["status"] = status_q
    if filters.get("route_prefix"):
        prefix = str(filters["route_prefix"])
        # Escape regex metachars so a route like "/api/admin/" is
        # matched literally, not as a character class.
        import re as _re
        q["route"] = {"$regex": "^" + _re.escape(prefix)}
    if filters.get("correlation_id"):
        q["correlation_id"] = str(filters["correlation_id"])[:_MAX_STRING_LEN]
    if filters.get("q"):
        import re as _re
        needle = _re.escape(str(filters["q"])[:200])
        q["$or"] = [
            {"message": {"$regex": needle, "$options": "i"}},
            {"route":   {"$regex": needle, "$options": "i"}},
        ]
    ts_q: Dict[str, Any] = {}
    if filters.get("since"):
        ts_q["$gte"] = _coerce_ts(filters["since"])
    if filters.get("until"):
        ts_q["$lte"] = _coerce_ts(filters["until"])
    if ts_q:
        q["timestamp"] = ts_q
    return q


# ─────────────────────────────────────────────────────────────────────────────
# Query / count / export
# ─────────────────────────────────────────────────────────────────────────────


async def query_logs(
    db,
    *,
    filters: Dict[str, Any],
    limit: int = DEFAULT_QUERY_LIMIT,
    before: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if db is None:
        return []
    n = max(1, min(int(limit or DEFAULT_QUERY_LIMIT), MAX_QUERY_LIMIT))
    q = build_filter(filters or {})
    if before:
        # Cursor pagination — descending timestamp; "before" is the
        # last-seen timestamp on the previous page.
        q.setdefault("timestamp", {})
        q["timestamp"]["$lt"] = _coerce_ts(before)
    try:
        cur = db[UNIFIED_LOGS_COLLECTION].find(q, {"_id": 0, "expire_at": 0})
        cur = cur.sort("timestamp", -1).limit(n)
        return await cur.to_list(n)
    except Exception as exc:
        logger.warning("[unified_logs] query_logs failed: %s", exc)
        return []


async def count_logs(db, filters: Dict[str, Any]) -> int:
    if db is None:
        return 0
    q = build_filter(filters or {})
    try:
        # Cap at MAX_COUNT to keep the badge fast even on huge windows.
        return await db[UNIFIED_LOGS_COLLECTION].count_documents(q, limit=MAX_COUNT)
    except Exception as exc:
        logger.warning("[unified_logs] count_logs failed: %s", exc)
        return 0


async def iter_export(
    db,
    filters: Dict[str, Any],
    *,
    limit: int = 5000,
) -> AsyncIterator[Dict[str, Any]]:
    if db is None:
        return
    q = build_filter(filters or {})
    n = max(1, min(int(limit or 5000), 50_000))
    try:
        cur = db[UNIFIED_LOGS_COLLECTION].find(q, {"_id": 0, "expire_at": 0})
        cur = cur.sort("timestamp", -1).limit(n)
        async for doc in cur:
            yield doc
    except Exception as exc:
        logger.warning("[unified_logs] iter_export failed: %s", exc)


async def fetch_trace(
    db,
    correlation_id: str,
    *,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    """Return every log line that shares the given correlation id.

    Sorted ascending by timestamp so the trace reads top-to-bottom in
    the order the request flowed (edge → backend → cron echo).
    """
    if db is None or not correlation_id:
        return []
    q = {"correlation_id": str(correlation_id)[:_MAX_STRING_LEN]}
    try:
        cur = db[UNIFIED_LOGS_COLLECTION].find(q, {"_id": 0, "expire_at": 0})
        cur = cur.sort("timestamp", 1).limit(max(1, min(int(limit), 5000)))
        return await cur.to_list(limit)
    except Exception as exc:
        logger.warning("[unified_logs] fetch_trace failed: %s", exc)
        return []


async def clear_logs(db, *, filters: Optional[Dict[str, Any]] = None) -> int:
    """Destructive purge. Returns the deleted count.

    When ``filters`` is empty, deletes everything in the collection.
    Always logs the call so an admin clearing the log can see the
    intent in stdout/stderr (the activity-log breadcrumb is the
    source of truth — this is for ops debugging).
    """
    if db is None:
        return 0
    q = build_filter(filters or {})
    try:
        res = await db[UNIFIED_LOGS_COLLECTION].delete_many(q)
        deleted = int(getattr(res, "deleted_count", 0) or 0)
        logger.warning("[unified_logs] clear_logs deleted=%d filters=%s",
                       deleted, q)
        return deleted
    except Exception as exc:
        logger.warning("[unified_logs] clear_logs failed: %s", exc)
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# In-process backend shipper
# ─────────────────────────────────────────────────────────────────────────────


class BackendLogShipper:
    """Bounded in-memory queue + flusher for the FastAPI process's own
    served requests.

    The middleware drops a record per request via ``record_request``
    (non-blocking — full queue silently drops the record so a slow Mongo
    write can never slow down user traffic). A background coroutine
    drains the queue every ``flush_interval_s`` or whenever it reaches
    ``flush_batch_size``.

    Lifespan-managed: ``await shipper.start(db)`` from the FastAPI
    lifespan startup, and ``await shipper.stop()`` on shutdown.
    """

    def __init__(
        self,
        *,
        flush_interval_s: float = 2.0,
        flush_batch_size: int = 200,
        max_queue_size: int = 5000,
        sample_rate_env: str = "BACKEND_LOG_SAMPLE_RATE",
        default_sample_rate: float = 0.05,
    ):
        self._queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue(maxsize=max_queue_size)
        self._flush_interval_s = flush_interval_s
        self._flush_batch_size = flush_batch_size
        self._sample_rate_env = sample_rate_env
        self._default_sample_rate = default_sample_rate
        self._task: Optional[asyncio.Task] = None
        self._db = None
        self._stopping = False
        self.dropped_full = 0
        self.dropped_paused = 0
        self.accepted = 0
        self.flushed = 0

    @property
    def sample_rate(self) -> float:
        raw = (os.environ.get(self._sample_rate_env) or "").strip()
        if not raw:
            return self._default_sample_rate
        try:
            return max(0.0, min(1.0, float(raw)))
        except ValueError:
            return self._default_sample_rate

    @property
    def paused(self) -> bool:
        return _logs_paused_env()

    def record_request(
        self,
        *,
        method: Optional[str],
        route: Optional[str],
        status: Optional[int],
        duration_ms: Optional[int],
        request_id: Optional[str] = None,
        user_agent: Optional[str] = None,
        message: Optional[str] = None,
        level: Optional[str] = None,
        extra: Optional[dict] = None,
    ) -> None:
        if self._stopping or self._db is None:
            return
        if self.paused:
            self.dropped_paused += 1
            return
        if not should_keep_request(status=status, duration_ms=duration_ms,
                                   sample_rate=self.sample_rate):
            return
        rec = {
            "source": "backend",
            "level": level or _level_for_status(status),
            "timestamp": _now_utc().isoformat(),
            "message": message or _default_message(method, route, status),
            "status": status,
            "duration_ms": duration_ms,
            "method": method,
            "route": route,
            "correlation_id": request_id,
            "user_agent": user_agent,
            "extra": extra,
        }
        try:
            self._queue.put_nowait(rec)
            self.accepted += 1
        except asyncio.QueueFull:
            self.dropped_full += 1

    async def start(self, db) -> None:
        if self._task is not None:
            return
        self._db = db
        self._stopping = False
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            try:
                await self._drain_once()
            except Exception:
                pass
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _run(self) -> None:
        while not self._stopping:
            try:
                await asyncio.sleep(self._flush_interval_s)
                await self._drain_once()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("[unified_logs] backend shipper loop: %s", exc)
                # Yield so a tight error loop doesn't hot-spin a CPU.
                await asyncio.sleep(1.0)

    async def _drain_once(self) -> None:
        if self._db is None:
            return
        batch: List[Dict[str, Any]] = []
        for _ in range(self._flush_batch_size):
            try:
                batch.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if not batch:
            return
        result = await insert_logs(self._db, batch, default_source="backend")
        self.flushed += int(result.get("accepted") or 0)


def _level_for_status(status: Optional[int]) -> str:
    if status is None:
        return "info"
    if status >= 500:
        return "error"
    if status >= 400:
        return "warn"
    return "info"


def _default_message(method: Optional[str], route: Optional[str],
                     status: Optional[int]) -> str:
    parts = [str(method or "GET"), str(route or "/")]
    if status is not None:
        parts.append(f"→ {status}")
    return " ".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# Pause / kill switch helpers (read by ingest + shipper)
# ─────────────────────────────────────────────────────────────────────────────

# In-process override (admin pause/resume route mutates this so a single
# UI click takes effect without waiting for an env-var redeploy). Stored
# alongside the env so the env var is still respected as the boot-time
# default and as the manifest in deploy configs.
_RUNTIME_PAUSED: Optional[bool] = None


def _logs_paused_env() -> bool:
    if _RUNTIME_PAUSED is not None:
        return _RUNTIME_PAUSED
    raw = (
        os.environ.get("LOGS_PAUSED")
        or os.environ.get("UNIFIED_LOGS_PAUSE")
        or ""
    ).strip().lower()
    return raw in ("1", "true", "yes", "on")


def set_runtime_pause(paused: Optional[bool]) -> None:
    """Set / clear the in-process pause override. ``None`` clears it
    so the next read falls back to the LOGS_PAUSED env var."""
    global _RUNTIME_PAUSED
    _RUNTIME_PAUSED = paused


# Singleton: imported by middleware and server.py lifespan.
_BACKEND_SHIPPER: Optional[BackendLogShipper] = None


def get_backend_shipper() -> BackendLogShipper:
    global _BACKEND_SHIPPER
    if _BACKEND_SHIPPER is None:
        _BACKEND_SHIPPER = BackendLogShipper()
    return _BACKEND_SHIPPER


# Test-only helper so pytest can swap a fresh shipper between tests.
def _reset_backend_shipper_for_tests() -> None:
    global _BACKEND_SHIPPER, _RUNTIME_PAUSED
    _BACKEND_SHIPPER = None
    _RUNTIME_PAUSED = None


# ─── Task #332 — SQS consumer entrypoint ─────────────────────────────────────
#
# `services/backend/sqs_consumers/unified_logs_pull.py` invokes this on each
# `unified-logs-cf-pull` SQS tick. Body carries `{since, until}` ISO-8601
# strings. We delegate to the existing `routes.admin_logs._cf_pull_window`
# which owns the GraphQL → normalize → `insert_logs` pipeline.
async def pull_cf_window(*, since: str, until: str) -> dict:
    """Pull a [since, until] window of Cloudflare logs and store them.

    Task #332 — raises on operational failure so the SQS Lambda
    consumer surfaces the error to AWS for retry + DLQ routing.
    Successful pulls return the underlying result dict.
    """
    from routes.admin_logs import _cf_pull_window  # type: ignore — import errors must propagate
    try:
        result = await _cf_pull_window(since=since, until=until)
    except TypeError:
        # Legacy positional signature.
        result = await _cf_pull_window(since, until)  # type: ignore[misc]
    if isinstance(result, dict):
        if result.get("status") == "error":
            raise RuntimeError(f"cf-pull failed: {result.get('error')}")
        result.setdefault("since", since)
        result.setdefault("until", until)
        return result
    return {"status": "ok", "since": since, "until": until, "raw": result}
