"""OIDC-verified internal job endpoints (Phase 3 migration target).

Exposes the existing in-process nightly loops as POST endpoints so they
can be driven by Cloud Scheduler (with attached OIDC token) instead of
being asyncio.create_task'd at FastAPI startup.

Mounted under /api/internal/jobs/*. Auth: Google OIDC bearer token from
an allow-listed service account (see oidc_auth.py).

Endpoints:
    POST /api/internal/jobs/grounded-recall
    POST /api/internal/jobs/internal-linker
    POST /api/internal/jobs/seo-remediation-flush
    POST /api/internal/jobs/wss-poll          (drains WSS findings → Slack)
    POST /api/internal/jobs/discovery-ingest  (Cloud Tasks fan-out target)

The body is a JSON dict; each handler ignores fields it doesn't need so
Cloud Scheduler can send a uniform `{"trigger":"scheduler"}` payload.

Migration policy: these endpoints run *in addition to* the in-process
loops until the env flag GCP_SCHEDULER_TAKEOVER=1 is set. That keeps
the system safe during cutover — operators flip the flag once the
Scheduler jobs are confirmed running.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from oidc_auth import require_google_oidc
import slack_notifier
import web_security_scanner_client
import discovery_engine_ingest

logger = logging.getLogger(__name__)
router = APIRouter()


def scheduler_takeover_enabled() -> bool:
    """Read at request time so a runtime env-flip activates without restart."""
    return (os.environ.get("GCP_SCHEDULER_TAKEOVER") or "").strip() in {"1", "true", "yes"}


# ── Grounded-recall benchmark ─────────────────────────────────────────
@router.post("/internal/jobs/grounded-recall")
async def job_grounded_recall(
    payload: Dict[str, Any] = Body(default_factory=dict),
    claims: dict = Depends(require_google_oidc()),
):
    t0 = time.perf_counter()
    try:
        # Late import — server.py defines the loop body but the actual
        # benchmark function lives in retrievers/benchmarks.
        from grounded_recall import run_grounded_recall_once  # type: ignore
        result = await run_grounded_recall_once()
        return {"status": "ok",
                "elapsed_ms": (time.perf_counter() - t0) * 1000.0,
                "caller": claims.get("email"),
                "result": result}
    except ImportError:
        return {"status": "error", "error": "grounded_recall module not found"}
    except Exception as exc:
        logger.exception("grounded-recall job failed")
        return {"status": "error",
                "elapsed_ms": (time.perf_counter() - t0) * 1000.0,
                "error": f"{type(exc).__name__}: {str(exc)[:200]}"}


# ── Internal-linker rebuild ────────────────────────────────────────────
@router.post("/internal/jobs/internal-linker")
async def job_internal_linker(
    payload: Dict[str, Any] = Body(default_factory=dict),
    claims: dict = Depends(require_google_oidc()),
):
    t0 = time.perf_counter()
    try:
        from internal_linker import rebuild_internal_links  # type: ignore
        result = await rebuild_internal_links()
        return {"status": "ok",
                "elapsed_ms": (time.perf_counter() - t0) * 1000.0,
                "caller": claims.get("email"),
                "result": result}
    except ImportError:
        return {"status": "error", "error": "internal_linker module not found"}
    except Exception as exc:
        logger.exception("internal-linker job failed")
        return {"status": "error",
                "elapsed_ms": (time.perf_counter() - t0) * 1000.0,
                "error": f"{type(exc).__name__}: {str(exc)[:200]}"}


# ── SEO remediation flush ──────────────────────────────────────────────
@router.post("/internal/jobs/seo-remediation-flush")
async def job_seo_remediation_flush(
    payload: Dict[str, Any] = Body(default_factory=dict),
    claims: dict = Depends(require_google_oidc()),
):
    t0 = time.perf_counter()
    try:
        from seo_remediation import drain_remediation_signals  # type: ignore
        max_items = int(payload.get("max_items") or 50)
        result = await drain_remediation_signals(max_items=max_items)
        return {"status": "ok",
                "elapsed_ms": (time.perf_counter() - t0) * 1000.0,
                "caller": claims.get("email"),
                "result": result}
    except ImportError:
        return {"status": "error", "error": "seo_remediation module not found"}
    except Exception as exc:
        logger.exception("seo-remediation-flush job failed")
        return {"status": "error",
                "elapsed_ms": (time.perf_counter() - t0) * 1000.0,
                "error": f"{type(exc).__name__}: {str(exc)[:200]}"}


# ── Web Security Scanner poll → Slack ──────────────────────────────────
@router.post("/internal/jobs/wss-poll")
async def job_wss_poll(
    payload: Dict[str, Any] = Body(default_factory=dict),
    claims: dict = Depends(require_google_oidc()),
):
    """Fetch the latest scan run for each config and push HIGH+ findings to Slack."""
    min_sev = (payload.get("min_severity") or "HIGH").upper()
    cfgs = await web_security_scanner_client.list_scan_configs()
    if cfgs.get("status") != "ok":
        return {"status": "error", "stage": "list_scan_configs", "detail": cfgs}

    summary: List[Dict[str, Any]] = []
    for cfg in cfgs.get("scan_configs") or []:
        cfg_name = cfg.get("name")
        if not cfg_name:
            continue
        runs = await web_security_scanner_client.list_scan_runs(cfg_name, page_size=1)
        if runs.get("status") != "ok" or not (runs.get("scan_runs") or []):
            summary.append({"config": cfg_name, "skipped": True})
            continue
        latest = runs["scan_runs"][0]
        run_name = latest.get("name")
        if not run_name:
            continue
        findings = await web_security_scanner_client.list_findings(run_name)
        if findings.get("status") != "ok":
            summary.append({"config": cfg_name, "findings_error": findings.get("error")})
            continue
        notif = await slack_notifier.post_wss_findings(
            findings.get("findings") or [],
            min_severity=min_sev,
            scan_run_name=run_name,
        )
        summary.append({
            "config": cfg_name,
            "run": run_name,
            "findings": findings.get("count"),
            "slack": notif.get("status"),
            "skipped": notif.get("skipped", False),
        })
    return {"status": "ok", "min_severity": min_sev,
            "caller": claims.get("email"), "configs": summary}


# ── Discovery Engine ingest (Cloud Tasks fan-out target) ───────────────
@router.post("/internal/jobs/discovery-ingest")
async def job_discovery_ingest(
    payload: Dict[str, Any] = Body(default_factory=dict),
    claims: dict = Depends(require_google_oidc()),
):
    """Upsert one or more topics into the Discovery Engine data store.

    Body shape (one of):
      {"topic": {...}}                       single doc
      {"topics": [{...}, {...}]}             batch
      {"topic_ids": ["abc","def"]}           pull from Mongo and upsert
    """
    if payload.get("topic"):
        return await discovery_engine_ingest.upsert_topic(payload["topic"])
    if payload.get("topics"):
        docs = []
        errs = []
        for t in payload["topics"]:
            try:
                docs.append(discovery_engine_ingest.topic_to_document(t))
            except Exception as exc:
                errs.append({"topic": t.get("slug") or t.get("_id"), "error": repr(exc)})
        out = await discovery_engine_ingest.upsert_documents(docs)
        if errs:
            out["transform_errors"] = errs
        return out
    if payload.get("topic_ids"):
        try:
            from db_ops import get_topics_by_ids  # type: ignore
        except ImportError:
            try:
                from deps import db  # type: ignore
                ids = payload["topic_ids"]
                cursor = db.topics.find({"_id": {"$in": ids}})
                topics = [t async for t in cursor]
            except Exception as exc:
                return {"status": "error",
                        "error": f"cannot load topics from Mongo: {exc!r}"}
        else:
            topics = await get_topics_by_ids(payload["topic_ids"])
        docs = []
        for t in topics:
            try:
                docs.append(discovery_engine_ingest.topic_to_document(t))
            except Exception:
                pass
        return await discovery_engine_ingest.upsert_documents(docs)
    return {"status": "error",
            "error": "provide one of: topic, topics, topic_ids"}


# ── Cutover status (helper for ops) ────────────────────────────────────
@router.get("/internal/jobs/status")
async def job_status(claims: dict = Depends(require_google_oidc())):
    """Lightweight health probe Cloud Scheduler hits to verify the SA can reach us."""
    return {
        "status": "ok",
        "scheduler_takeover": scheduler_takeover_enabled(),
        "caller": claims.get("email"),
        "instance_pid": os.getpid(),
    }
