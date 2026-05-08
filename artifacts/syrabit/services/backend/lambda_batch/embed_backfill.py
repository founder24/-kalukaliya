"""Task #551 §B — Lambda handler for `syrabit-embed-backfill`.

EventBridge cron `cron(0 */6 * * ? *)` (every 6h). Drains up to
`MAX_DOCS_PER_RUN` legacy chunks per invocation through the existing
Workers-AI Gemma+Qwen3 embed worker → Pinecone upsert pipeline by
re-using `aca_jobs.embed_backfill.run_backfill`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

from . import _db

logger = logging.getLogger("lambda_batch.embed_backfill")
logger.setLevel(logging.INFO)

MAX_DOCS_PER_RUN = int(os.environ.get("MAX_DOCS_PER_RUN", "500"))


async def _run() -> dict:
    _db.bootstrap_env()
    # Task #560 — driver discriminator for shadow reconciliation.
    os.environ.setdefault("BATCH_JOB_DRIVER", "lambda")
    from aca_jobs.embed_backfill import run_backfill  # type: ignore
    db = _db.get_db()
    summary = await run_backfill(db, max_chunks=MAX_DOCS_PER_RUN)
    return summary


def handler(event, context):  # noqa: ARG001
    logger.info("embed_backfill invoked: event=%s", json.dumps(event)[:300])
    try:
        summary = asyncio.run(_run())
    except Exception as exc:
        logger.exception("embed_backfill failed: %s", exc)
        raise
    logger.info("embed_backfill summary: %s", json.dumps(summary, default=str)[:600])
    return {"ok": True, "summary": summary}
