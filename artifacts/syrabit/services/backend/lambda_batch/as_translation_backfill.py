"""Task #551 §B — Lambda handler for `syrabit-as-translation-backfill`.

Daily 03:00 UTC EventBridge cron. Wraps `aca_jobs.as_translation_backfill.run_backfill`
with a Lambda-friendly handler signature so we can reuse the existing
resumable IndicTrans2 → Vertex polish driver verbatim.

The function image bundles the FastAPI backend code (same image used by
`lambda-workers.tf`), so the import below resolves to the in-tree
`artifacts/syrabit-backend/aca_jobs/` package.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

from . import _db

logger = logging.getLogger("lambda_batch.as_translation_backfill")
logger.setLevel(logging.INFO)

MAX_DOCS_PER_RUN = int(os.environ.get("MAX_DOCS_PER_RUN", "1000"))


async def _run() -> dict:
    _db.bootstrap_env()
    from aca_jobs.as_translation_backfill import run_backfill  # type: ignore
    db = _db.get_db()
    summary = await run_backfill(
        db,
        max_docs=MAX_DOCS_PER_RUN,
        batch_size=int(os.environ.get("AS_BACKFILL_BATCH_SIZE", "5")),
    )
    return summary


def handler(event, context):  # noqa: ARG001
    """EventBridge invocation entry point."""
    logger.info("as_translation_backfill invoked: event=%s", json.dumps(event)[:300])
    try:
        summary = asyncio.run(_run())
    except Exception as exc:
        logger.exception("as_translation_backfill failed: %s", exc)
        raise
    logger.info("as_translation_backfill summary: %s", json.dumps(summary, default=str)[:600])
    return {"ok": True, "summary": summary}
