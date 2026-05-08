"""Task #551 §B — Lambda handler for `syrabit-comprehend-sampler`.

EventBridge cron `cron(0 4 ? * SUN *)` (weekly Sunday 04:00 UTC). Wraps
`aca_jobs.comprehend_sampler._sample_once` so the Lambda invocation
runs exactly one sampling pass and returns the per-pass summary.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

from . import _db

logger = logging.getLogger("lambda_batch.comprehend_sampler")
logger.setLevel(logging.INFO)

MAX_DOCS_PER_RUN = int(os.environ.get("MAX_DOCS_PER_RUN", "25"))


async def _run() -> dict:
    _db.bootstrap_env()
    # Allow the per-run sample size to be tuned via the Lambda env so
    # we do not need a code re-deploy to throttle Comprehend spend.
    os.environ.setdefault("COMPREHEND_SAMPLE_SIZE", str(MAX_DOCS_PER_RUN))
    # Task #560 — driver discriminator for shadow reconciliation. The
    # sampler stamps every `content_analytics` row it writes with
    # `scored_by=os.environ["BATCH_JOB_DRIVER"]`.
    os.environ.setdefault("BATCH_JOB_DRIVER", "lambda")
    from aca_jobs.comprehend_sampler import _sample_once  # type: ignore
    db = _db.get_db()
    return await _sample_once(db)


def handler(event, context):  # noqa: ARG001
    logger.info("comprehend_sampler invoked: event=%s", json.dumps(event)[:300])
    try:
        summary = asyncio.run(_run())
    except Exception as exc:
        logger.exception("comprehend_sampler failed: %s", exc)
        raise
    logger.info("comprehend_sampler summary: %s", json.dumps(summary, default=str)[:600])
    return {"ok": True, "summary": summary}
