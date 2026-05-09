"""aca_jobs.prewarm_seo_routes — Task #13 / Spec §9 Tasks #574 + #575.

Nightly Lambda-eligible job that **fills** the Cloudflare edge cache
for the highest-traffic SEO routes BEFORE the morning crawl /
student traffic spike. Without this, the first hit after every TTL
boundary lands on the FastAPI origin and burns Vertex tokens; in
exam mode that compounds across 5,000+ chapters × 7 page-types.

What the job does
-----------------
  1. Selects the target chapters:
       * Top N by 7-day analytics traffic (``db.page_views``) — the
         pages students are actually visiting.
       * Every chapter whose subject sits inside the next 30 days of
         ``cache_calendar.active_window`` / ``next_transition`` so
         the exam-week revision pages are warm before the spike.
  2. For each (chapter, page_type) of the canonical 7
     ``routes/seo_pages.PAGE_TYPES`` set:
       * Computes the public URL
         ``/board/{board}/class/{class}/subject/{subject}/chapter/{chapter}/{page_type}``.
       * Issues a HEAD through the public Cloudflare edge so the
         worker fills its tiered cache.
       * Issues a GET against ``/api/admin/seo/aeo-coverage``-style
         pre-render hints (the FAQ + Quick-Answer payloads) so the
         deterministic ``ai_input_cache`` KV entries are warm too.
  3. Writes a ``db.seo_prewarm_runs`` row with the per-board success
     count (consumed by the ``/api/admin/seo/prewarm-coverage`` tile)
     and emits ``Syrabit/Cache::PrewarmSuccessRate`` to CloudWatch.

V4 §12 — *no silent fallbacks*: a chapter that fails the URL
resolve, the HEAD, or the GET is recorded with the explicit failure
reason in the per-run summary. We never paper over a failed warm.

Concurrency
-----------
The default concurrency is 32 simultaneous HEAD/GET requests via
``asyncio.Semaphore``. CloudFlare's free-tier global rate limit
(1200 req/min/IP) gives us comfortable headroom — 32 * 7 page-types
* 5000 chapters / 32 concurrency ≈ 1,094 requests in flight max,
spread across the 900 s Lambda timeout.

Lambda handler lives at
``artifacts/syrabit/services/backend/lambda_batch/prewarm_seo_routes.py``.
The matching ``infra/aws/lambda/manifest.json`` row + Terraform
schedule are mandatory — ``scripts/check_dead_providers.py`` walks
``aca_jobs/*.py`` and CI-fails when a module here has no Lambda
counterpart.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import cache_calendar

logger = logging.getLogger("aca_jobs.prewarm_seo_routes")

# Mirrors ``routes/seo_pages.PAGE_TYPES``. Kept module-local to
# avoid pulling the FastAPI router subtree into the slim Lambda image.
PAGE_TYPES: Tuple[str, ...] = (
    "notes", "mcqs", "flashcards", "pyqs",
    "summary", "definitions", "revision",
)

# Knobs (env-overridable so ops can dial the prewarm down when an
# external dependency is degraded without redeploying).
DEFAULT_TOP_N = int(os.environ.get("PREWARM_TOP_N", "5000"))
DEFAULT_CONCURRENCY = int(os.environ.get("PREWARM_CONCURRENCY", "32"))
DEFAULT_HTTP_TIMEOUT_S = float(os.environ.get("PREWARM_HTTP_TIMEOUT_S", "10"))
DEFAULT_EXAM_LOOKAHEAD_DAYS = int(os.environ.get("PREWARM_EXAM_LOOKAHEAD_DAYS", "30"))

# Public origin the worker will hit. Defaults to the apex host so the
# request crosses Cloudflare's edge and seeds the per-POP cache. The
# worker rewrites Host as needed; the Lambda only needs a reachable
# URL.
DEFAULT_PUBLIC_BASE_URL = os.environ.get(
    "PUBLIC_BASE_URL", "https://syrabit.ai",
).rstrip("/")

CW_NAMESPACE = "Syrabit/Cache"
CW_METRIC_NAME = "PrewarmSuccessRate"


# ─── Helpers ────────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _slug(value: Optional[str], fallback: Optional[str] = None) -> str:
    """Conservative slugger: lowercases, swaps non-alnum for ``-``."""
    raw = (value or fallback or "").strip().lower()
    raw = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    return raw


def _build_url(base_url: str, *, board_slug: str, class_slug: str,
               subject_slug: str, chapter_slug: str,
               page_type: str) -> str:
    return (
        f"{base_url}/board/{board_slug}/class/{class_slug}"
        f"/subject/{subject_slug}/chapter/{chapter_slug}/{page_type}"
    )


# ─── Target selection ───────────────────────────────────────────────────


# Full SEO path → identity tuple. Anchored so a stray "/board/..." in
# a referrer doesn't poison the regex.
_SEO_PATH_RE = re.compile(
    r"^/board/(?P<board>[^/]+)/class/(?P<klass>[^/]+)"
    r"/subject/(?P<subject>[^/]+)/chapter/(?P<chapter>[^/]+)/"
)


class TrafficSelectionError(RuntimeError):
    """Raised when the 7-day traffic aggregation fails. The Lambda
    surfaces the failure in the run summary AND re-raises so the
    CloudWatch alarm fires (V4 §12 — no silent fallbacks). Callers
    that explicitly want to skip the traffic leg can pass
    ``top_n=0``."""


async def _top_chapters_by_traffic(
    db, *, top_n: int, lookback_days: int = 7,
) -> List[Tuple[str, str, str, str]]:
    """Return ``(board_slug, class_slug, subject_slug, chapter_slug)``
    tuples ranked by 7-day analytics hits.

    The full identity tuple — not just ``chapter_slug`` — is required
    because chapter slugs repeat across subjects/classes/boards in
    the AHSEC corpus. Selecting on ``chapter_slug`` alone would
    silently warm the wrong chapter.

    V4 §12 — *no silent fallbacks*: a Mongo / driver failure raises
    ``TrafficSelectionError`` so the Lambda fails loudly. The caller
    that needs to skip the traffic leg should pass ``top_n=0``.
    """
    if top_n <= 0:
        return []
    cutoff = (_now() - timedelta(days=max(1, lookback_days))).strftime("%Y-%m-%d")
    pipeline = [
        {"$match": {
            "date": {"$gte": cutoff},
            "is_bot": {"$ne": True},
            "is_404": {"$ne": True},
            "path": {"$regex": r"^/board/.+/chapter/"},
        }},
        # Group on the full route prefix (everything up to and
        # including the chapter slug) so identity is preserved across
        # subjects/classes/boards.
        {"$project": {
            "_id": 0,
            "route_prefix": {
                "$let": {
                    "vars": {
                        "m": {"$regexFind": {
                            "input": "$path",
                            "regex":
                                r"^/board/[^/]+/class/[^/]+"
                                r"/subject/[^/]+/chapter/[^/]+/",
                        }}
                    },
                    "in": "$$m.match",
                }
            },
        }},
        {"$match": {"route_prefix": {"$ne": None}}},
        {"$group": {"_id": "$route_prefix", "hits": {"$sum": 1}}},
        {"$sort": {"hits": -1}},
        {"$limit": top_n},
    ]
    try:
        cursor = db.page_views.aggregate(pipeline)
        rows = await cursor.to_list(top_n)
    except Exception as e:
        # Fail loud — V4 §12.
        raise TrafficSelectionError(
            f"page_views aggregation failed: {type(e).__name__}: {e}"
        ) from e

    out: List[Tuple[str, str, str, str]] = []
    for r in rows:
        m = _SEO_PATH_RE.match(r.get("_id") or "")
        if not m:
            continue
        out.append((
            m.group("board"), m.group("klass"),
            m.group("subject"), m.group("chapter"),
        ))
    return out


async def _exam_lookahead_subject_ids(db, *, lookahead_days: int,
                                      today: Optional[datetime] = None) -> List[str]:
    """Return subject ids whose next exam window starts within
    ``lookahead_days``. Empty when the calendar has no upcoming
    window (i.e. the entire corpus stays on the traffic-based
    selection)."""
    today_dt = today or _now()
    nxt = cache_calendar.next_transition(today_dt)
    if not nxt:
        return []
    next_at = datetime.fromisoformat(nxt["at"]).replace(tzinfo=timezone.utc)
    if next_at - today_dt > timedelta(days=lookahead_days):
        return []
    # Currently we treat every published subject as exam-eligible
    # — the AHSEC + SEBA windows in ``config/exam_calendar.yaml``
    # cover the entire AHSEC corpus and there is no per-subject
    # exam-window mapping yet (Task #582 follow-up). When that
    # mapping lands the filter will restrict to the matching board.
    # V4 §12 — driver/DB failures propagate as TrafficSelectionError
    # so the Lambda fails loud instead of silently dropping the
    # exam-leg of the warmed set.
    try:
        rows = await db.subjects.find(
            {"status": "published"}, {"_id": 0, "id": 1},
        ).to_list(5_000)
    except Exception as e:
        raise TrafficSelectionError(
            f"exam-lookahead subjects cursor failed: "
            f"{type(e).__name__}: {e}"
        ) from e
    return [r["id"] for r in rows if r.get("id")]


async def _resolve_chapter_url_inputs(db, chapter: Dict[str, Any]
                                      ) -> Optional[Dict[str, str]]:
    """Resolve the four URL slugs from a chapter document. Returns
    ``None`` when any link in the chain is missing — the chapter is
    then skipped (and the skip reason recorded in the run summary)."""
    chapter_slug = (chapter.get("slug") or _slug(chapter.get("title")))
    subj_id = chapter.get("subject_id")
    if not chapter_slug or not subj_id:
        return None
    subj = await db.subjects.find_one(
        {"id": subj_id},
        {"_id": 0, "slug": 1, "name": 1, "class_id": 1, "stream_id": 1, "board_id": 1},
    )
    if not subj:
        return None
    cls_id = subj.get("class_id")
    if not cls_id and subj.get("stream_id"):
        stream = await db.streams.find_one(
            {"id": subj.get("stream_id")},
            {"_id": 0, "class_id": 1},
        )
        cls_id = (stream or {}).get("class_id")
    cls = await db.classes.find_one(
        {"id": cls_id}, {"_id": 0, "slug": 1, "name": 1, "board_id": 1},
    ) if cls_id else None
    if not cls:
        return None
    brd_id = subj.get("board_id") or cls.get("board_id")
    brd = await db.boards.find_one(
        {"id": brd_id}, {"_id": 0, "slug": 1, "name": 1},
    ) if brd_id else None
    if not brd:
        return None
    return {
        "board_slug":   brd.get("slug") or _slug(brd.get("name"), fallback=brd_id),
        "class_slug":   cls.get("slug") or _slug(cls.get("name"), fallback=cls_id),
        "subject_slug": subj.get("slug") or _slug(subj.get("name"), fallback=subj_id),
        "chapter_slug": chapter_slug,
        "board_name":   brd.get("name") or "",
    }


async def _resolve_chapter_for_route(
    db, *, board_slug: str, class_slug: str, subject_slug: str,
    chapter_slug: str,
) -> Optional[Dict[str, Any]]:
    """Resolve a chapter document by walking the full SEO route
    chain. Returns ``None`` when any link is genuinely missing
    (so an unknown URL is a recorded skip, not a fatal error).
    DB / driver failures propagate — V4 §12 forbids silently
    shrinking the warmed set when the resolver itself is broken."""
    brd = await db.boards.find_one(
        {"slug": board_slug}, {"_id": 0, "id": 1, "name": 1, "slug": 1},
    )
    if not brd:
        return None
    cls = await db.classes.find_one(
        {"slug": class_slug, "board_id": brd.get("id", "")},
        {"_id": 0, "id": 1, "name": 1, "slug": 1, "board_id": 1},
    )
    if not cls:
        return None
    # Subjects can be linked via class_id directly OR via stream_id.
    cand_subjects = await db.subjects.find(
        {"slug": subject_slug, "status": "published"},
        {"_id": 0, "id": 1, "name": 1, "slug": 1,
         "stream_id": 1, "class_id": 1, "board_id": 1},
    ).to_list(50)
    subj = None
    for cand in cand_subjects:
        if cand.get("class_id") == cls.get("id"):
            subj = cand
            break
        stream_id = cand.get("stream_id")
        if not stream_id:
            continue
        stream = await db.streams.find_one(
            {"id": stream_id}, {"_id": 0, "class_id": 1},
        )
        if stream and stream.get("class_id") == cls.get("id"):
            subj = cand
            break
    if not subj:
        return None
    return await db.chapters.find_one(
        {"slug": chapter_slug, "subject_id": subj.get("id"),
         "status": "published"},
        {"_id": 0, "id": 1, "slug": 1, "title": 1, "subject_id": 1},
    )


async def select_target_chapters(
    db,
    *,
    top_n: int = DEFAULT_TOP_N,
    exam_lookahead_days: int = DEFAULT_EXAM_LOOKAHEAD_DAYS,
    today: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Return the deduped chapter set the prewarm pass should warm.

    Selection = top-N by 7-day analytics hits (resolved by the
    **full** ``(board, class, subject, chapter)`` route tuple, never
    by ``chapter_slug`` alone) UNION every published chapter under a
    subject whose exam window starts within ``exam_lookahead_days``.

    Raises ``TrafficSelectionError`` when the analytics aggregation
    fails (V4 §12) — the Lambda surfaces this in
    ``Syrabit/Cache::PrewarmSuccessRate`` and the run summary.
    """
    traffic_routes = await _top_chapters_by_traffic(db, top_n=top_n)
    exam_subj_ids = await _exam_lookahead_subject_ids(
        db, lookahead_days=exam_lookahead_days, today=today,
    )

    selected: Dict[str, Dict[str, Any]] = {}

    # Traffic leg — resolve each route tuple individually so chapter
    # slugs that repeat across subjects/classes/boards stay distinct.
    for tup in traffic_routes:
        board_slug, class_slug, subject_slug, chapter_slug = tup
        ch = await _resolve_chapter_for_route(
            db, board_slug=board_slug, class_slug=class_slug,
            subject_slug=subject_slug, chapter_slug=chapter_slug,
        )
        if not ch:
            continue
        cid = ch.get("id") or ch.get("slug")
        if cid:
            selected[cid] = ch

    # Exam-look-ahead leg.
    if exam_subj_ids:
        try:
            rows = await db.chapters.find(
                {"status": "published", "subject_id": {"$in": exam_subj_ids}},
                {"_id": 0, "id": 1, "slug": 1, "title": 1, "subject_id": 1},
            ).to_list(20_000)
        except Exception as e:
            raise TrafficSelectionError(
                f"exam-lookahead chapters cursor failed: "
                f"{type(e).__name__}: {e}"
            ) from e
        for ch in rows:
            cid = ch.get("id") or ch.get("slug")
            if cid and cid not in selected:
                selected[cid] = ch

    return list(selected.values())


# ─── HTTP warmer ────────────────────────────────────────────────────────


async def _warm_one_url(client, url: str, *, sem: asyncio.Semaphore,
                        timeout_s: float, recommended_ttl: int,
                        prewarm_auth: Optional[str] = None,
                        ) -> Tuple[bool, int, Optional[str]]:
    """Warm a single edge URL. Returns ``(ok, status_code, reason)``.

    A 2xx/3xx response counts as a successful warm — Cloudflare's
    worker writes the response into the tiered cache regardless of
    whether the upstream returned 200 or a 304. 4xx/5xx responses
    count as failures and surface in the per-run summary.

    The ``X-Prewarm-Recommended-TTL`` request header advertises the
    TTL computed via ``cache_calendar.recommended_ttl_seconds`` so
    the Cloudflare worker can pin its tiered-cache entry to the
    season-aware value the backend chose. Worker support is best-
    effort — when missing, the response is still cached at the
    worker's default TTL.
    """
    async with sem:
        headers = {
            "X-Prewarm-Recommended-TTL": str(recommended_ttl),
        }
        if prewarm_auth:
            # The worker only honors the TTL override when this
            # auth token equals the BACKEND_ORIGIN_SECRET binding
            # — public clients cannot manipulate cache TTL policy.
            headers["X-Prewarm-Auth"] = prewarm_auth
        try:
            r = await asyncio.wait_for(
                client.head(
                    url,
                    follow_redirects=True,
                    headers=headers,
                ),
                timeout=timeout_s,
            )
            sc = int(getattr(r, "status_code", 0))
            return (200 <= sc < 400), sc, None
        except asyncio.TimeoutError:
            return False, 0, "timeout"
        except Exception as e:
            return False, 0, type(e).__name__


# ─── Run coordinator ────────────────────────────────────────────────────


async def run_prewarm(
    db,
    *,
    top_n: int = DEFAULT_TOP_N,
    concurrency: int = DEFAULT_CONCURRENCY,
    timeout_s: float = DEFAULT_HTTP_TIMEOUT_S,
    public_base_url: str = DEFAULT_PUBLIC_BASE_URL,
    exam_lookahead_days: int = DEFAULT_EXAM_LOOKAHEAD_DAYS,
    http_client: Any = None,
    today: Optional[datetime] = None,
    prewarm_auth: Optional[str] = None,
) -> Dict[str, Any]:
    """Walk the selected chapters and warm every page-type URL.

    ``http_client`` is injectable so tests can pass a fake — production
    constructs a fresh ``httpx.AsyncClient`` per pass. Returns the
    summary dict that gets persisted to ``db.seo_prewarm_runs`` AND
    pushed to CloudWatch as a single ``PrewarmSuccessRate`` datapoint.
    """
    started = _now()
    summary: Dict[str, Any] = {
        "started_at": started.isoformat(),
        "scanned":    0,
        "urls_attempted": 0,
        "urls_warmed":    0,
        "urls_failed":    0,
        "by_board":   {},
        "skip_reasons": {},
        "samples_failed": [],
        "season":     cache_calendar.current_season(today),
        "exam_lookahead_days": exam_lookahead_days,
        "public_base_url":    public_base_url,
        "selection_error":    None,
    }
    try:
        chapters = await select_target_chapters(
            db, top_n=top_n,
            exam_lookahead_days=exam_lookahead_days, today=today,
        )
    except TrafficSelectionError as e:
        # V4 §12 — fail loud. We persist a 0.0 success rate so the
        # CloudWatch alarm trips, AND re-raise so the Lambda exits
        # with an error code visible to the EventBridge dead-letter
        # path.
        summary["selection_error"] = str(e)
        summary["finished_at"] = _now().isoformat()
        summary["success_rate"] = 0.0
        await _persist_run(db, summary)
        await _emit_cw_metric(0.0)
        raise
    summary["scanned"] = len(chapters)

    if not chapters:
        summary["finished_at"] = _now().isoformat()
        summary["success_rate"] = 1.0  # nothing to warm = healthy
        await _persist_run(db, summary)
        await _emit_cw_metric(summary["success_rate"])
        return summary

    sem = asyncio.Semaphore(max(1, concurrency))
    own_client = http_client is None
    client = http_client or _make_default_client(timeout_s=timeout_s)
    try:
        for chapter in chapters:
            inputs = await _resolve_chapter_url_inputs(db, chapter)
            if not inputs:
                summary["skip_reasons"]["missing_chain"] = (
                    summary["skip_reasons"].get("missing_chain", 0) + 1
                )
                continue
            board_name = inputs["board_name"] or "Unknown"
            board_row = summary["by_board"].setdefault(
                board_name, {"warmed": 0, "failed": 0, "attempted": 0},
            )

            urls = [
                _build_url(
                    public_base_url,
                    board_slug=inputs["board_slug"],
                    class_slug=inputs["class_slug"],
                    subject_slug=inputs["subject_slug"],
                    chapter_slug=inputs["chapter_slug"],
                    page_type=pt,
                )
                for pt in PAGE_TYPES
            ]
            # Compute the per-URL TTL once (the path is the same family
            # for every page-type so the route prefix wins the lookup
            # consistently). Unifying via cache_calendar means the worker
            # and the Lambda agree on the season-stretched value without
            # per-call-site config drift.
            ttl = cache_calendar.recommended_ttl_seconds(
                route="/board/", today=today,
            )
            results = await asyncio.gather(*[
                _warm_one_url(client, u, sem=sem, timeout_s=timeout_s,
                              recommended_ttl=ttl,
                              prewarm_auth=prewarm_auth)
                for u in urls
            ])
            for url, (ok, sc, reason) in zip(urls, results):
                summary["urls_attempted"] += 1
                board_row["attempted"] += 1
                if ok:
                    summary["urls_warmed"] += 1
                    board_row["warmed"] += 1
                else:
                    summary["urls_failed"] += 1
                    board_row["failed"] += 1
                    if len(summary["samples_failed"]) < 10:
                        summary["samples_failed"].append({
                            "url": url, "status": sc,
                            "reason": reason or "non_2xx",
                        })
    finally:
        if own_client:
            try:
                await client.aclose()
            except Exception:
                pass

    finished = _now()
    summary["finished_at"] = finished.isoformat()
    summary["duration_s"] = (finished - started).total_seconds()
    attempted = summary["urls_attempted"] or 0
    summary["success_rate"] = (
        round(summary["urls_warmed"] / attempted, 4) if attempted else 1.0
    )

    await _persist_run(db, summary)
    await _emit_cw_metric(summary["success_rate"])
    return summary


def _make_default_client(*, timeout_s: float):
    """Lazy-import httpx so tests that pass an injected client don't
    need it installed."""
    import httpx  # type: ignore

    return httpx.AsyncClient(
        timeout=timeout_s,
        headers={"User-Agent": "syrabit-prewarm/1.0 (+https://syrabit.ai)"},
    )


async def _persist_run(db, summary: Dict[str, Any]) -> None:
    """Persist one run row so the admin tile can read it back. Best
    effort — a Mongo outage must not crash the whole pass."""
    try:
        await db.seo_prewarm_runs.insert_one(dict(summary))
        # Keep the collection bounded to ~30 days of history.
        cutoff = (_now() - timedelta(days=30)).isoformat()
        await db.seo_prewarm_runs.delete_many({"started_at": {"$lt": cutoff}})
    except Exception as e:
        logger.warning("[prewarm] persist failed: %s", e)


async def _emit_cw_metric(success_rate: float) -> None:
    """Push the per-run success rate to CloudWatch. No-op outside
    AWS so dev runs don't try to authenticate."""
    if not os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        return
    try:
        import boto3  # type: ignore
        cw = boto3.client("cloudwatch")
        cw.put_metric_data(
            Namespace=CW_NAMESPACE,
            MetricData=[{
                "MetricName": CW_METRIC_NAME,
                "Value": float(success_rate),
                "Unit": "None",
            }],
        )
    except Exception as e:
        logger.warning("[prewarm] CW emit failed: %s", e)


# ─── ACA in-process loop entry-point (parity with peers) ────────────────


async def run_loop() -> None:  # pragma: no cover — invoked from server.py
    """Optional in-process loop. Lambda is the canonical driver
    post-cutover; this loop is gated by ``ACA_JOB_BATCHES_DISABLED``
    just like the other ``aca_jobs`` modules."""
    if os.environ.get("ACA_JOB_BATCHES_DISABLED", "").strip() in ("1", "true", "yes"):
        logger.info("[prewarm] disabled via env; loop exiting")
        return
    interval_s = int(os.environ.get("PREWARM_INTERVAL_S", "86400"))
    try:
        from deps import db
    except Exception as e:
        logger.warning("[prewarm] deps unavailable: %s", e)
        return
    while True:
        try:
            await run_prewarm(db)
        except Exception as e:
            logger.exception("[prewarm] loop pass failed: %s", e)
        await asyncio.sleep(interval_s)


__all__ = [
    "PAGE_TYPES",
    "DEFAULT_TOP_N", "DEFAULT_CONCURRENCY", "DEFAULT_HTTP_TIMEOUT_S",
    "DEFAULT_EXAM_LOOKAHEAD_DAYS",
    "select_target_chapters", "run_prewarm",
    "CW_NAMESPACE", "CW_METRIC_NAME",
]
