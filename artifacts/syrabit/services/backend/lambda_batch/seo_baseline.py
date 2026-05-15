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
    candidates = [Path("/var/task/scripts")]
    try:
        # In the monorepo (local dev / CI) __file__ is 5+ levels deep.
        # Inside a Lambda container the task root is /var/task/ (only 4
        # parent levels exist), so parents[4] raises IndexError — guard it.
        candidates.append(Path(__file__).resolve().parents[4] / "scripts")
    except IndexError:
        pass
    for c in candidates:
        if c.exists() and str(c) not in sys.path:
            sys.path.insert(0, str(c))


def _load_admin_jwt_secret() -> str:
    """Resolve ADMIN_JWT_SECRET via the same dual-source path that
    ``cache_effectiveness.py`` uses (round-3 reviewer fix):

      1. If ``ADMIN_JWT_SECRET`` is already in the env (local dev /
         pre-hydrated), use it directly.
      2. Otherwise fetch the secret value from Secrets Manager
         using ``ADMIN_JWT_SECRET_ARN`` (the Terraform-injected env
         var on this Lambda — see ``lambda-batch-jobs.tf`` line 428).

    The shared ``_db.bootstrap_env`` helper does NOT map this ARN
    automatically (its ``_SECRET_ENV_MAP`` covers Mongo/origin/
    prewarm secrets only); the cache-effectiveness Lambda hydrates
    on-demand for the same reason, and this handler mirrors it.
    """
    direct = os.environ.get("ADMIN_JWT_SECRET", "").strip()
    if direct:
        return direct
    arn = os.environ.get("ADMIN_JWT_SECRET_ARN", "").strip()
    if not arn:
        raise RuntimeError(
            "ADMIN_JWT_SECRET / ADMIN_JWT_SECRET_ARN not set on Lambda env"
        )
    import boto3  # type: ignore
    sm = boto3.client("secretsmanager")
    raw = (sm.get_secret_value(SecretId=arn).get("SecretString") or "").strip()
    if raw.startswith("{"):
        return json.loads(raw).get("secret", raw)
    return raw


def _mint_admin_jwt() -> str:
    """Mint a 60-second admin JWT for the POST to the admin tile."""
    import time
    import jwt  # type: ignore
    secret = _load_admin_jwt_secret()
    now = int(time.time())
    return jwt.encode(
        {
            "sub":   "lambda-seo-baseline",
            "role":  "admin",
            "iat":   now,
            "exp":   now + 60,
        },
        secret,
        algorithm="HS256",
    )


def _post_to_admin(summary_doc: dict) -> None:
    """POST the persisted-shape summary doc to /api/admin/seo/baseline-publish.

    The Lambda has already done the canonical Mongo write inside
    ``run_baseline_publish``; this POST satisfies the brief's
    "post results to the admin observability tile" contract and
    serves as a deterministic write-through to the same collection
    in case the Lambda's MongoDB egress path differs from the ACA
    primary (different VPC, different replica set node, etc.). The
    backend handler is idempotent on ``report_date``.
    """
    import urllib.request as _ur
    import urllib.error as _ue
    backend = os.environ.get("BACKEND_URL", "").rstrip("/")
    if not backend:
        logger.warning("seo_baseline: BACKEND_URL unset — skipping admin POST")
        return
    try:
        token = _mint_admin_jwt()
    except Exception as exc:
        logger.warning("seo_baseline: admin JWT mint failed (%s) — skipping POST", exc)
        return
    body = json.dumps(summary_doc, default=str).encode("utf-8")
    req = _ur.Request(
        f"{backend}/api/admin/seo/baseline-publish",
        data=body,
        method="POST",
        headers={
            "content-type": "application/json",
            "authorization": f"Bearer {token}",
        },
    )
    try:
        with _ur.urlopen(req, timeout=15) as resp:
            logger.info("seo_baseline: admin POST status=%s", resp.status)
    except _ue.HTTPError as exc:
        # V4 §12: log loud but do not raise — Mongo write was the
        # canonical persistence path. The backend POST is the
        # write-through replica.
        logger.warning("seo_baseline: admin POST failed http=%s body=%s",
                       exc.code, exc.read()[:200])
    except Exception as exc:
        logger.warning("seo_baseline: admin POST errored: %s", exc)


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
    summary = await run_baseline_publish(
        db,
        boards=boards,
        chapters_per_board=int(os.environ.get(
            "SEO_BASELINE_CHAPTERS_PER_BOARD",
            str(DEFAULT_CHAPTERS_PER_BOARD),
        )),
        page_type=os.environ.get("SEO_BASELINE_PAGE_TYPE", DEFAULT_PAGE_TYPE),
    )
    # Reviewer fix (round-2): explicit POST to the admin observability
    # tile in addition to the canonical Mongo write inside
    # ``run_baseline_publish``. The brief asks for both legs.
    _post_to_admin(summary)
    return summary


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
