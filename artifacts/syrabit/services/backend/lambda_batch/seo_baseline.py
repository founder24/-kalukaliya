"""Task #28 — Lambda handler for ``syrabit-seo-baseline``.

EventBridge cron ``cron(0 2 ? * MON *)`` (weekly, Mondays 02:00 UTC).
Wraps ``aca_jobs.seo_baseline.run_baseline_publish`` so each weekly
invocation does exactly one Lighthouse + structured-data + Rich
Results pass, persists the report to ``db.seo_baseline_runs``, and
emits ``Syrabit/SEO`` CloudWatch datapoints (``MedianSeoScore``,
``PagesWithFailures``, ``MedianSeoScoreWoWDelta``).

Why a separate Lambda image is OK: the existing ``batch_job``
container image already ships every ``aca_jobs/*`` module and the
``scripts/`` directory is mounted next to it (see the Dockerfile —
the same image powers ``prewarm-seo-routes``). The only Lambda-
specific dependency is the ``lighthouse`` Node CLI, which is layered
in via the ``lighthouse-batch`` image flavour the operator picks at
deploy time (set ``LIGHTHOUSE_LAYER_ARN`` on the Lambda or rebuild
the image with ``npm i -g lighthouse@latest``).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from . import _db

logger = logging.getLogger("lambda_batch.seo_baseline")
logger.setLevel(logging.INFO)


def _ensure_scripts_on_path() -> None:
    """Make ``scripts/seo_baseline.py`` importable inside the Lambda.

    The container image copies the repo's ``scripts/`` directory into
    ``/var/task/scripts/`` so ``aca_jobs.seo_baseline``'s lazy
    ``import seo_baseline`` resolves. Outside Lambda this is a no-op
    because ``aca_jobs.seo_baseline`` already prepends the path.
    """
    candidates = [
        Path("/var/task/scripts"),
        Path(__file__).resolve().parents[4] / "scripts",
    ]
    for c in candidates:
        if c.exists() and str(c) not in sys.path:
            sys.path.insert(0, str(c))


async def _run() -> dict:
    _db.bootstrap_env()
    os.environ.setdefault("BATCH_JOB_DRIVER", "lambda")
    _ensure_scripts_on_path()
    from aca_jobs.seo_baseline import (  # type: ignore
        run_baseline_publish,
        DEFAULT_BOARDS,
        DEFAULT_CHAPTERS_PER_BOARD,
        DEFAULT_PAGE_TYPE,
    )
    db = _db.get_db()
    boards_env = (os.environ.get("SEO_BASELINE_BOARDS") or "").strip()
    boards = tuple(b.strip() for b in boards_env.split(",") if b.strip()) or DEFAULT_BOARDS
    return await run_baseline_publish(
        db,
        boards=boards,
        chapters_per_board=int(os.environ.get(
            "SEO_BASELINE_CHAPTERS_PER_BOARD",
            str(DEFAULT_CHAPTERS_PER_BOARD),
        )),
        page_type=os.environ.get("SEO_BASELINE_PAGE_TYPE", DEFAULT_PAGE_TYPE),
    )


def handler(event, context):  # noqa: ARG001
    logger.info(
        "seo_baseline invoked: event=%s",
        json.dumps(event)[:300],
    )
    try:
        summary = asyncio.run(_run())
    except Exception as exc:
        logger.exception("seo_baseline failed: %s", exc)
        raise
    logger.info(
        "seo_baseline summary: %s",
        json.dumps(summary, default=str)[:600],
    )
    return {"ok": True, "summary": summary}
