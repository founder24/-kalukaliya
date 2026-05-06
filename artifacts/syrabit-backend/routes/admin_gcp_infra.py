"""Admin endpoints for SA-gated GCP infra APIs (Phase 3 — post-Task #489).

Cloud Scheduler + Cloud Tasks endpoints were REMOVED by Task #489
(four-cloud delegation lock-in): GCP must not host queueing or
scheduling per V4 §0. The deleted client modules
(`cloud_scheduler_client`, `cloud_tasks_client`) and the related
`/admin/gcp/scheduler/*` + `/admin/gcp/tasks/*` endpoints are gone.
Producer-side enqueueing now goes through `sqs_fanout.enqueue` (AWS
SQS); periodic ticks go through AWS EventBridge schedules.

Surviving endpoints:

  Web Security Scanner:
    GET  /api/admin/gcp/wss/configs
    POST /api/admin/gcp/wss/configs/start         body: {name}
    GET  /api/admin/gcp/wss/runs?config=...
    GET  /api/admin/gcp/wss/findings?run=...
    POST /api/admin/gcp/wss/notify-slack          body: {run, min_severity?}

  Discovery Engine:
    POST /api/admin/discovery/engine/ingest       body: {topic|topics|topic_ids}

All endpoints return status="disabled" cleanly when
GOOGLE_APPLICATION_CREDENTIALS_JSON is missing, so the dashboard renders
gracefully without crashing.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Body, Depends, Query

from auth_deps import get_admin_user
import web_security_scanner_client
import slack_notifier
import discovery_engine_ingest

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Web Security Scanner ───────────────────────────────────────────────────
@router.get("/admin/gcp/wss/configs")
async def admin_wss_configs(
    page_size: int = Query(100, ge=1, le=500),
    admin: dict = Depends(get_admin_user),
):
    return await web_security_scanner_client.list_scan_configs(page_size=page_size)


@router.post("/admin/gcp/wss/configs/start")
async def admin_wss_start(
    payload: dict = Body(...),
    admin: dict = Depends(get_admin_user),
):
    name = (payload.get("name") or "").strip()
    if not name:
        return {"status": "error", "error": "name required"}
    return await web_security_scanner_client.start_scan_run(name)


@router.get("/admin/gcp/wss/runs")
async def admin_wss_runs(
    config: str = Query(..., description="Full scan-config resource name."),
    page_size: int = Query(25, ge=1, le=200),
    admin: dict = Depends(get_admin_user),
):
    return await web_security_scanner_client.list_scan_runs(config, page_size=page_size)


@router.get("/admin/gcp/wss/findings")
async def admin_wss_findings(
    run: str = Query(..., description="Full scan-run resource name."),
    page_size: int = Query(100, ge=1, le=500),
    admin: dict = Depends(get_admin_user),
):
    return await web_security_scanner_client.list_findings(run, page_size=page_size)


# ── Slack alerts on demand ─────────────────────────────────────────────
@router.post("/admin/gcp/wss/notify-slack")
async def admin_wss_notify_slack(
    payload: dict = Body(...),
    admin: dict = Depends(get_admin_user),
):
    """Fetch findings for a scan run and post HIGH+ ones to Slack."""
    run = (payload.get("run") or "").strip()
    if not run:
        return {"status": "error", "error": "run required"}
    min_sev = (payload.get("min_severity") or "HIGH").upper()
    findings = await web_security_scanner_client.list_findings(run)
    if findings.get("status") != "ok":
        return {"status": "error", "stage": "list_findings", "detail": findings}
    return await slack_notifier.post_wss_findings(
        findings.get("findings") or [],
        min_severity=min_sev,
        scan_run_name=run,
    )


# ── Discovery Engine document ingest ───────────────────────────────────
@router.post("/admin/discovery/engine/ingest")
async def admin_discovery_ingest(
    payload: dict = Body(...),
    admin: dict = Depends(get_admin_user),
):
    """Upsert structured documents into the Discovery Engine data store.

    Body: {"topics": [{...}]} or {"topic_ids": ["..."]} or {"topic": {...}}
    """
    if payload.get("topic"):
        return await discovery_engine_ingest.upsert_topic(payload["topic"])
    if payload.get("topics"):
        docs = []
        for t in payload["topics"]:
            try:
                docs.append(discovery_engine_ingest.topic_to_document(t))
            except Exception:
                pass
        return await discovery_engine_ingest.upsert_documents(docs)
    return {"status": "error",
            "error": "provide one of: topic, topics"}
