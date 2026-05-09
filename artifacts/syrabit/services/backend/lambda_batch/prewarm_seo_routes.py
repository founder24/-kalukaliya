"""Task #13 — Lambda handler for ``syrabit-prewarm-seo-routes``.

EventBridge cron ``cron(0 1 * * ? *)`` (daily 01:00 UTC, ahead of
the 02:00 ``materialize-chapter-faqs`` job so warmed pages already
have FAQ data when the morning crawl arrives). Wraps
``aca_jobs.prewarm_seo_routes.run_prewarm`` so each Lambda
invocation does exactly one prewarm pass and returns the per-pass
summary suitable for log inspection.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

from . import _db

logger = logging.getLogger("lambda_batch.prewarm_seo_routes")
logger.setLevel(logging.INFO)


async def _run() -> dict:
    _db.bootstrap_env()
    os.environ.setdefault("BATCH_JOB_DRIVER", "lambda")
    from aca_jobs.prewarm_seo_routes import (  # type: ignore
        run_prewarm,
        DEFAULT_TOP_N,
        DEFAULT_CONCURRENCY,
        DEFAULT_HTTP_TIMEOUT_S,
        DEFAULT_EXAM_LOOKAHEAD_DAYS,
    )
    db = _db.get_db()
    # ``PREWARM_AUTH_TOKEN`` MUST equal the worker's
    # ``BACKEND_ORIGIN_SECRET`` binding for the worker to honor the
    # ``X-Prewarm-Recommended-TTL`` cache TTL override; without it the
    # header is simply ignored (worker falls back to its default TTL).
    return await run_prewarm(
        db,
        top_n=int(os.environ.get("PREWARM_TOP_N", str(DEFAULT_TOP_N))),
        concurrency=int(os.environ.get("PREWARM_CONCURRENCY", str(DEFAULT_CONCURRENCY))),
        timeout_s=float(os.environ.get("PREWARM_HTTP_TIMEOUT_S", str(DEFAULT_HTTP_TIMEOUT_S))),
        exam_lookahead_days=int(os.environ.get(
            "PREWARM_EXAM_LOOKAHEAD_DAYS", str(DEFAULT_EXAM_LOOKAHEAD_DAYS))),
        prewarm_auth=(os.environ.get("PREWARM_AUTH_TOKEN") or None),
    )


def handler(event, context):  # noqa: ARG001
    logger.info(
        "prewarm_seo_routes invoked: event=%s",
        json.dumps(event)[:300],
    )
    try:
        summary = asyncio.run(_run())
    except Exception as exc:
        logger.exception("prewarm_seo_routes failed: %s", exc)
        raise
    logger.info(
        "prewarm_seo_routes summary: %s",
        json.dumps(summary, default=str)[:600],
    )
    return {"ok": True, "summary": summary}
