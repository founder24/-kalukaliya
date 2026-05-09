"""Task #12 — Lambda handler for ``syrabit-materialize-chapter-faqs``.

EventBridge cron ``cron(0 2 * * ? *)`` (daily 02:00 UTC, ahead of the
03:00 translation backfill so the freshly-materialised English FAQ
text is available when the bilingual mirror runs). Wraps
``aca_jobs.materialize_chapter_faqs.run_materialization`` so the
Lambda invocation runs exactly one materialization pass and returns
the per-pass summary.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

from . import _db

logger = logging.getLogger("lambda_batch.materialize_chapter_faqs")
logger.setLevel(logging.INFO)

MAX_DOCS_PER_RUN = int(os.environ.get("MAX_DOCS_PER_RUN", "0"))


async def _run() -> dict:
    _db.bootstrap_env()
    os.environ.setdefault("BATCH_JOB_DRIVER", "lambda")
    from aca_jobs.materialize_chapter_faqs import run_materialization  # type: ignore
    db = _db.get_db()
    return await run_materialization(
        db, max_chapters=MAX_DOCS_PER_RUN or None,
    )


def handler(event, context):  # noqa: ARG001
    logger.info(
        "materialize_chapter_faqs invoked: event=%s",
        json.dumps(event)[:300],
    )
    try:
        summary = asyncio.run(_run())
    except Exception as exc:
        logger.exception("materialize_chapter_faqs failed: %s", exc)
        raise
    logger.info(
        "materialize_chapter_faqs summary: %s",
        json.dumps(summary, default=str)[:600],
    )
    return {"ok": True, "summary": summary}
