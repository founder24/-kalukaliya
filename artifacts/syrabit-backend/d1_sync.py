"""D1 Edge Database Sync — export content from MongoDB and fan it out to one
or more Cloudflare edge Workers.

Fan-out targets (Task #879)
---------------------------
Historically this module only POSTed to a single hostname (``EDGE_WORKER_URL``,
which points at the prod Worker on ``api.syrabit.ai``). That left the
preview Worker's D1 (``syrabit-content-preview``, id
``35e59391-218e-4e94-bbf5-972baa0d0b30``) starting empty after every deploy
and made every preview-tier smoke test that hit a content endpoint return
zero rows — masking regressions that depended on real data.

We now treat sync as a fan-out: prod is always the primary target, and an
optional preview target receives the same payload when the operator sets

    EDGE_WORKER_PREVIEW_URL=https://syrabit-edge-preview.<account>.workers.dev
    D1_SYNC_SECRET_PREVIEW=<value-set-via-wrangler-secret-put-env-preview>

on the Railway service. A different secret is required because Wrangler v4
keeps secrets per-env (`wrangler secret put D1_SYNC_SECRET --env preview`
does NOT inherit the prod value — see ``workers/edge-proxy/wrangler.toml``
§ ``[env.preview]``).

Failure to reach the preview hostname is logged at WARNING and never blocks
the prod sync — preview is best-effort by design.
"""
import os
import logging
import asyncio
import json
from datetime import datetime, date
from typing import Optional, List, Dict, Any, Tuple

logger = logging.getLogger(__name__)


class DateTimeEncoder(json.JSONEncoder):
    """Custom JSON encoder that converts datetime/date objects to ISO 8601 strings.
    
    This prevents serialization crashes when exporting PostgreSQL rows containing
    datetime objects directly to JSON for D1 sync.
    """
    def default(self, obj):
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        return super().default(obj)

_d1_http: Optional["httpx.AsyncClient"] = None

D1_SYNC_SECRET = os.getenv("D1_SYNC_SECRET", "").strip()
EDGE_WORKER_URL = os.getenv("EDGE_WORKER_URL", "https://api.syrabit.ai").strip().rstrip("/")

# Optional preview fan-out (Task #879). Both must be set to enable.
# A sentinel placeholder ("REPLACE_..." / "your-...") is treated as unset so
# leftover .env.example values cannot accidentally fan-out.
_PLACEHOLDER_TOKENS = {"", "REPLACE_WITH_SECURE_RANDOM_SECRET", "your-sync-secret"}
EDGE_WORKER_PREVIEW_URL = os.getenv("EDGE_WORKER_PREVIEW_URL", "").strip().rstrip("/")
D1_SYNC_SECRET_PREVIEW = os.getenv("D1_SYNC_SECRET_PREVIEW", "").strip()


def _get_http():
    global _d1_http
    if _d1_http is None:
        import httpx
        _d1_http = httpx.AsyncClient(timeout=60, limits=httpx.Limits(max_connections=5))
    return _d1_http


def _is_real_secret(value: str) -> bool:
    return bool(value) and value not in _PLACEHOLDER_TOKENS


def is_d1_configured() -> bool:
    return _is_real_secret(D1_SYNC_SECRET)


def is_preview_fanout_configured() -> bool:
    """True iff a real preview hostname AND a real preview secret are set."""
    return bool(EDGE_WORKER_PREVIEW_URL) and _is_real_secret(D1_SYNC_SECRET_PREVIEW)


def _sync_targets() -> List[Tuple[str, str, str]]:
    """Return the list of ``(label, url, secret)`` tuples to fan-out to.

    Prod is always first when configured; preview is appended when both
    ``EDGE_WORKER_PREVIEW_URL`` and ``D1_SYNC_SECRET_PREVIEW`` are real
    (non-placeholder) values. Order matters: callers treat the first entry
    as the primary target (the one whose return value drives the sync's
    overall success/failure flag).
    """
    targets: List[Tuple[str, str, str]] = []
    if is_d1_configured():
        targets.append(("prod", EDGE_WORKER_URL, D1_SYNC_SECRET))
    if is_preview_fanout_configured():
        targets.append(("preview", EDGE_WORKER_PREVIEW_URL, D1_SYNC_SECRET_PREVIEW))
    return targets


async def export_content_catalog(db) -> Dict[str, Any]:
    if db is None:
        return {}

    try:
        boards, classes, streams, subjects, chapters, topics, seo_pages = await asyncio.wait_for(
            asyncio.gather(
                db.boards.find({}, {"_id": 0}).to_list(200),
                db.classes.find({}, {"_id": 0}).to_list(200),
                db.streams.find({}, {"_id": 0}).to_list(500),
                db.subjects.find({"status": "published"}, {"_id": 0}).to_list(1000),
                db.chapters.find({}, {"_id": 0}).sort("order_index", 1).to_list(5000),
                db.topics.find({"status": "published"}, {"_id": 0}).sort("order", 1).to_list(20000),
                db.seo_pages.find(
                    {"status": "published"},
                    {"_id": 0, "id": 1, "slug": 1, "topic_id": 1, "page_type": 1,
                     "status": 1, "title": 1, "meta_description": 1,
                     "html_content": 1, "content": 1,
                     "board_slug": 1, "class_slug": 1, "subject_slug": 1,
                     "chapter_slug": 1, "topic_slug": 1, "word_count": 1,
                     "created_at": 1, "updated_at": 1}
                ).to_list(50000),
            ),
            timeout=30.0,
        )
    except asyncio.TimeoutError:
        logger.warning("D1 export: MongoDB query timed out after 30s")
        return {}
    except Exception as e:
        logger.error(f"D1 export error: {e}")
        return {}

    return {
        "boards": boards,
        "classes": classes,
        "streams": streams,
        "subjects": subjects,
        "chapters": chapters,
        "topics": topics,
        "seo_pages": seo_pages,
    }


async def _post_one_target(label: str, url: str, secret: str, payload: Dict[str, Any], attempt: int = 1) -> bool:
    """POST the sync payload to a single edge target. Never raises.
    
    Includes retry logic with exponential backoff for transient failures.
    Uses DateTimeEncoder to properly serialize datetime objects in the payload.
    """
    try:
        client = _get_http()
        # Use json.dumps with DateTimeEncoder to handle datetime objects,
        # then pass as data= instead of json= to have full control over serialization
        json_payload = json.dumps(payload, cls=DateTimeEncoder)
        resp = await client.post(
            f"{url}/api/edge/d1-sync",
            content=json_payload,
            headers={
                "Authorization": f"Bearer {secret}",
                "Content-Type": "application/json",
            },
            timeout=60,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("success"):
                logger.info(f"D1 sync success ({label}): {data.get('synced', {})}")
                return True
            logger.warning(f"D1 sync ({label}) returned errors: {data.get('errors', [])}")
            return False
        # Retry on 5xx server errors (transient)
        if resp.status_code >= 500 and attempt < 3:
            wait_time = (2 ** (attempt - 1)) * 2  # 2s, 4s, 8s backoff
            logger.warning(f"D1 sync ({label}) HTTP {resp.status_code}: retrying in {wait_time}s (attempt {attempt}/3)")
            await asyncio.sleep(wait_time)
            return await _post_one_target(label, url, secret, payload, attempt + 1)
        logger.warning(f"D1 sync ({label}) HTTP {resp.status_code}: {resp.text[:200]}")
        return False
    except Exception as e:
        # Retry on network/transient errors
        error_msg = str(e).lower()
        if attempt < 3 and any(s in error_msg for s in ("timeout", "connection", "network")):
            wait_time = (2 ** (attempt - 1)) * 2
            logger.warning(f"D1 sync ({label}) transient error: {e}; retrying in {wait_time}s (attempt {attempt}/3)")
            await asyncio.sleep(wait_time)
            return await _post_one_target(label, url, secret, payload, attempt + 1)
        logger.warning(f"D1 sync ({label}) trigger error: {e}")
        return False


async def trigger_d1_sync(payload: Dict[str, Any]) -> bool:
    """Fan the payload out to every configured edge target.

    Returns True iff the *primary* target (prod when configured, otherwise
    the only configured target) succeeded. Preview-target failures are
    logged but never demote the overall return to False — preview is
    best-effort and should not block CRUD / cron paths that depend on
    this function.
    """
    targets = _sync_targets()
    if not targets:
        logger.info("D1 sync not configured — skipping")
        return False

    primary_label, primary_url, primary_secret = targets[0]
    primary_ok = await _post_one_target(primary_label, primary_url, primary_secret, payload)

    if len(targets) > 1:
        # Fire secondary targets concurrently with each other (prod is
        # already done by this point). We DO await them before returning
        # so the caller gets stable logging — most callers in the repo
        # already wrap us in `asyncio.create_task(...)` (see
        # `_schedule_d1_sync_fire` in routes/admin_content.py and the
        # `seo_pages` autosync in seo_engine.py) so the preview wait
        # never blocks the user-visible CRUD response. The handful of
        # synchronous callers (manual `POST /api/admin/d1-sync`,
        # `POST /admin/d1/sync-full`) are admin-only and tolerate the
        # extra round-trip.
        secondary = targets[1:]
        results = await asyncio.gather(
            *[_post_one_target(lbl, u, s, payload) for (lbl, u, s) in secondary],
            return_exceptions=True,
        )
        for (lbl, _u, _s), res in zip(secondary, results):
            if isinstance(res, Exception):
                logger.warning(f"D1 sync ({lbl}) raised: {res}")
            elif not res:
                logger.warning(f"D1 sync ({lbl}) reported failure (best-effort)")

    return primary_ok


async def sync_full(db) -> Dict[str, Any]:
    payload = await export_content_catalog(db)
    # Task #386 — when D1_MIRROR_ON is set, fold the extended tables
    # (seo_meta + audit_log + syllabus_map) into the same payload so
    # they ride the existing fan-out instead of a second round-trip.
    # The helper no-ops cleanly when the flag is off, returning {}.
    try:
        from d1_mirror import export_extended_payload, _record_sync
        extended = await export_extended_payload(db)
    except Exception as exc:
        logger.warning("d1_sync: extended payload export failed: %s", exc)
        extended = {}
        _record_sync = None  # type: ignore
    if extended:
        payload = {**(payload or {}), **extended}
    if not payload:
        return {"success": False, "error": "Export returned empty"}
    ok = await trigger_d1_sync(payload)
    # Record extended-mirror outcome so /admin/cf-health.d1_mirror
    # surfaces a real lag number driven by the live sync, not just
    # the standalone sync_extended() entrypoint.
    if extended and _record_sync:
        try:
            _record_sync(ok,
                         {k: len(v) for k, v in extended.items()},
                         error=None if ok else "sync_full_failed")
        except Exception:
            pass
    return {
        "success": ok,
        "tables_exported": list(payload.keys()),
        "row_counts": {k: len(v) for k, v in payload.items()},
        "targets": [t[0] for t in _sync_targets()],
    }


async def sync_tables(db, tables: List[str]) -> Dict[str, Any]:
    full = await export_content_catalog(db)
    if not full:
        return {"success": False, "error": "Export returned empty"}
    payload = {k: v for k, v in full.items() if k in tables}
    if not payload:
        return {"success": False, "error": f"No matching tables: {tables}"}
    ok = await trigger_d1_sync(payload)
    return {
        "success": ok,
        "tables_synced": list(payload.keys()),
        "row_counts": {k: len(v) for k, v in payload.items()},
        "targets": [t[0] for t in _sync_targets()],
    }


async def warmup_d1_cache(db) -> Dict[str, Any]:
    """Warm up D1 edge cache by triggering a full sync.
    
    This should be called:
    1. On application startup (server.py lifespan)
    2. After deployments (CI/CD pipeline)
    3. Periodically every 6 hours (background task)
    
    Returns dict with success status and timing metrics.
    """
    import time as _t
    
    _t0 = _t.perf_counter()
    logger.info("D1 cache warm-up initiated")
    
    try:
        # Export current catalog state
        payload = await export_content_catalog(db)
        # Task #386 — extended D1 mirror tables piggy-back on the same
        # warm-up so a fresh deploy always lands with seo_meta /
        # audit_log / syllabus_map populated for the Pages SSR layer.
        try:
            from d1_mirror import export_extended_payload
            ext = await export_extended_payload(db)
            if ext:
                payload = {**(payload or {}), **ext}
        except Exception as ext_err:
            logger.warning("D1 warm-up: extended export skipped: %s", ext_err)
        if not payload:
            logger.warning("D1 warm-up: export returned empty, skipping sync")
            return {
                "success": False,
                "error": "Export returned empty",
                "duration_ms": int((_t.perf_counter() - _t0) * 1000),
            }
        
        # Count rows for logging
        row_counts = {k: len(v) for k, v in payload.items()}
        total_rows = sum(row_counts.values())
        logger.info(f"D1 warm-up: exporting {total_rows} total rows across {len(payload)} tables")
        
        # Trigger sync to all targets (prod + preview)
        ok = await trigger_d1_sync(payload)
        duration_ms = int((_t.perf_counter() - _t0) * 1000)
        
        if ok:
            logger.info(f"D1 cache warm-up completed successfully in {duration_ms}ms")
        else:
            logger.warning(f"D1 cache warm-up reported failure after {duration_ms}ms")
        
        return {
            "success": ok,
            "tables_exported": list(payload.keys()),
            "row_counts": row_counts,
            "total_rows": total_rows,
            "targets": [t[0] for t in _sync_targets()],
            "duration_ms": duration_ms,
        }
        
    except Exception as e:
        duration_ms = int((_t.perf_counter() - _t0) * 1000)
        logger.error(f"D1 warm-up failed: {type(e).__name__}: {str(e)[:200]}")
        return {
            "success": False,
            "error": f"{type(e).__name__}: {str(e)}",
            "duration_ms": duration_ms,
        }
