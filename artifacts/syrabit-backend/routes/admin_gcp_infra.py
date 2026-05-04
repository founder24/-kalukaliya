"""Admin endpoints for SA-gated GCP infra APIs (Phase 3).

  Cloud Scheduler:
    GET  /api/admin/gcp/scheduler/jobs
    POST /api/admin/gcp/scheduler/jobs/run        body: {name}
    POST /api/admin/gcp/scheduler/jobs/pause      body: {name}
    POST /api/admin/gcp/scheduler/jobs/resume     body: {name}

  Cloud Tasks:
    GET  /api/admin/gcp/tasks/queues
    GET  /api/admin/gcp/tasks/queue?name=...
    POST /api/admin/gcp/tasks/enqueue             body: {queue,url,payload?,method?,headers?,schedule_time?,oidc_sa?}

  Web Security Scanner:
    GET  /api/admin/gcp/wss/configs
    POST /api/admin/gcp/wss/configs/start         body: {name}
    GET  /api/admin/gcp/wss/runs?config=...
    GET  /api/admin/gcp/wss/findings?run=...

All endpoints return status="disabled" cleanly when GOOGLE_APPLICATION_CREDENTIALS_JSON
is missing, so the dashboard renders gracefully without crashing.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import APIRouter, Body, Depends, Query

from auth_deps import get_admin_user
import cloud_scheduler_client
import cloud_tasks_client
import web_security_scanner_client
import slack_notifier
import discovery_engine_ingest

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Cloud Scheduler ────────────────────────────────────────────────────────
@router.get("/admin/gcp/scheduler/jobs")
async def admin_scheduler_jobs(
    location: Optional[str] = Query(None),
    page_size: int = Query(100, ge=1, le=500),
    admin: dict = Depends(get_admin_user),
):
    return await cloud_scheduler_client.list_jobs(
        location=location, page_size=page_size,
    )


@router.post("/admin/gcp/scheduler/jobs/run")
async def admin_scheduler_run(
    payload: dict = Body(...),
    admin: dict = Depends(get_admin_user),
):
    name = (payload.get("name") or "").strip()
    if not name:
        return {"status": "error", "error": "name required"}
    return await cloud_scheduler_client.run_job(name)


@router.post("/admin/gcp/scheduler/jobs/pause")
async def admin_scheduler_pause(
    payload: dict = Body(...),
    admin: dict = Depends(get_admin_user),
):
    name = (payload.get("name") or "").strip()
    if not name:
        return {"status": "error", "error": "name required"}
    return await cloud_scheduler_client.pause_job(name)


@router.post("/admin/gcp/scheduler/jobs/resume")
async def admin_scheduler_resume(
    payload: dict = Body(...),
    admin: dict = Depends(get_admin_user),
):
    name = (payload.get("name") or "").strip()
    if not name:
        return {"status": "error", "error": "name required"}
    return await cloud_scheduler_client.resume_job(name)


# ── Cloud Tasks ────────────────────────────────────────────────────────────
@router.get("/admin/gcp/tasks/queues")
async def admin_tasks_queues(
    location: Optional[str] = Query(None),
    admin: dict = Depends(get_admin_user),
):
    return await cloud_tasks_client.list_queues(location=location)


@router.get("/admin/gcp/tasks/queue")
async def admin_tasks_queue(
    name: str = Query(..., description="Full queue resource name."),
    page_size: int = Query(50, ge=1, le=500),
    admin: dict = Depends(get_admin_user),
):
    return await cloud_tasks_client.list_tasks(name, page_size=page_size)


@router.post("/admin/gcp/tasks/enqueue")
async def admin_tasks_enqueue(
    payload: dict = Body(...),
    admin: dict = Depends(get_admin_user),
):
    """Producer endpoint — dual-target during the AWS cutover.

    Task #332: when ``WORKERS_BACKEND=aws`` (the cutover flag) we
    publish to the matching SQS queue via ``sqs_fanout.enqueue``
    instead of Cloud Tasks. The legacy ``queue`` param doubles as
    the cloud-tasks.json key (e.g. ``seo-indexnow``) so producer
    payload shape is unchanged. Rolling back is "unset env, restart
    API" — no producer-side code change.
    """
    queue = (payload.get("queue") or "").strip()
    url = (payload.get("url") or "").strip()
    if not queue:
        return {"status": "error", "error": "queue required"}

    backend = (os.environ.get("WORKERS_BACKEND") or "gcp").strip().lower()
    if backend == "aws":
        try:
            from sqs_fanout import enqueue as _sqs_enqueue  # type: ignore
        except ImportError:
            return {"status": "error", "error": "sqs_fanout not deployed"}
        body = payload.get("payload") if payload.get("payload") is not None else {}
        try:
            msg_id = await _sqs_enqueue(queue, body)
            return {"status": "ok", "backend": "aws", "queue": queue, "message_id": msg_id}
        except Exception as e:
            return {"status": "error", "backend": "aws", "queue": queue, "error": f"{type(e).__name__}: {e}"}

    if not url:
        return {"status": "error", "error": "url required for gcp backend"}
    return await cloud_tasks_client.enqueue_http_task(
        queue,
        url=url,
        payload=payload.get("payload"),
        method=payload.get("method") or "POST",
        headers=payload.get("headers") or None,
        schedule_time=payload.get("schedule_time") or None,
        oidc_service_account_email=payload.get("oidc_sa") or None,
    )


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
