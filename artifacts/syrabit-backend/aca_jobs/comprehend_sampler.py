"""aca_jobs.comprehend_sampler — Task #337 sampled background analytics job.

The runbook (§3.6) wires Amazon Comprehend as a *sampled* PII +
sentiment overlay so we never call it on every chat turn (it would
swamp the cost cap). This loop wakes once an hour, samples up to
``SAMPLE_SIZE`` documents from the ``chapters`` collection that have
not been scored in the last 7 days, and writes the result into a
dedicated ``content_analytics`` collection so the admin
``AdminContentQuality`` dashboard can render aggregate PII / sentiment
trends without hitting Comprehend at request time.

Failure mode: any exception in the loop is logged and the loop
sleeps the same interval before retrying — Comprehend outage is
*never* allowed to wedge the worker.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger("aca_jobs.comprehend_sampler")

SAMPLE_INTERVAL_S = int(os.environ.get("COMPREHEND_SAMPLE_INTERVAL_S", "3600"))
SAMPLE_SIZE = int(os.environ.get("COMPREHEND_SAMPLE_SIZE", "25"))
RESCORE_AFTER_DAYS = int(os.environ.get("COMPREHEND_RESCORE_AFTER_DAYS", "7"))
ANALYTICS_COLLECTION = "content_analytics"


async def _sample_once(db_handle) -> dict:
    """Run one sampling pass. Returns a summary dict for logging."""
    from providers import aws_native as _awsn
    if not (_awsn.is_enabled("comprehend") and _awsn.is_configured()):
        return {"skipped": "disabled", "scored": 0}

    cutoff = (datetime.now(timezone.utc) - timedelta(days=RESCORE_AFTER_DAYS)).isoformat()
    # Pull a sample of chapters without recent comprehend scoring.
    pipeline = [
        {"$match": {"$or": [
            {"comprehend_scored_at": {"$exists": False}},
            {"comprehend_scored_at": {"$lt": cutoff}},
        ]}},
        {"$sample": {"size": SAMPLE_SIZE}},
        {"$project": {"id": 1, "title": 1, "content": 1}},
    ]
    candidates = []
    try:
        async for d in db_handle["chapters"].aggregate(pipeline):
            candidates.append(d)
    except Exception as exc:
        logger.warning("comprehend sampler aggregate failed: %s", str(exc)[:200])
        return {"error": type(exc).__name__, "scored": 0}

    scored = 0
    failed = 0
    for ch in candidates:
        text = (ch.get("content") or "")[:4500]  # Comprehend sync hard limit ~5KB
        if not text.strip():
            continue
        try:
            sentiment = await asyncio.to_thread(_awsn.detect_sentiment, text)
            pii = await asyncio.to_thread(_awsn.detect_pii, text)
        except Exception as exc:
            failed += 1
            logger.debug("comprehend score failed for %s: %s", ch.get("id"), str(exc)[:120])
            continue
        now = datetime.now(timezone.utc).isoformat()
        try:
            await db_handle[ANALYTICS_COLLECTION].update_one(
                {"chapter_id": ch.get("id")},
                {"$set": {
                    "chapter_id":   ch.get("id"),
                    "title":        ch.get("title"),
                    "sentiment":    sentiment.get("sentiment"),
                    "scores":       sentiment.get("scores", {}),
                    "pii_count":    len(pii),
                    "pii_types":    sorted({(p.get("Type") or "") for p in pii if p.get("Type")}),
                    "scored_at":    now,
                    "source":       "comprehend",
                }},
                upsert=True,
            )
            await db_handle["chapters"].update_one(
                {"id": ch.get("id")},
                {"$set": {"comprehend_scored_at": now}},
            )
            scored += 1
        except Exception as exc:
            failed += 1
            logger.debug("comprehend persist failed for %s: %s", ch.get("id"), str(exc)[:120])

    return {"scored": scored, "failed": failed, "candidates": len(candidates)}


async def run_loop(db_handle) -> None:
    """Forever loop. Designed to be wrapped by ``asyncio.create_task`` at
    container startup. Never raises — every iteration is wrapped."""
    logger.info(
        "comprehend sampler started (interval=%ds, sample=%d, rescore_after=%dd)",
        SAMPLE_INTERVAL_S, SAMPLE_SIZE, RESCORE_AFTER_DAYS,
    )
    # Tiny initial jitter so multiple workers don't all wake at the same instant.
    await asyncio.sleep(min(SAMPLE_INTERVAL_S, 60))
    while True:
        try:
            summary = await _sample_once(db_handle)
            logger.info("comprehend sampler pass: %s", summary)
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("comprehend sampler iteration crashed: %s", str(exc)[:200])
        await asyncio.sleep(SAMPLE_INTERVAL_S)


def start(db_handle) -> Optional[asyncio.Task]:
    """Kick off the loop. Returns the task handle for tests."""
    if db_handle is None:
        logger.info("comprehend sampler not started (db unavailable)")
        return None
    return asyncio.create_task(run_loop(db_handle))
