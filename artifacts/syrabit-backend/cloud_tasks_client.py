"""Google Cloud Tasks client (SA-OAuth).

Wraps https://cloudtasks.googleapis.com/v2 — list queues/tasks, enqueue
HTTP-target tasks. Used to defer slow work (PageSpeed batch audits, bulk
fact-checks, cohort enrichment) off the API workers without losing
durability on restart.

Auth: requires GOOGLE_APPLICATION_CREDENTIALS_JSON.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx

import gcp_auth

logger = logging.getLogger(__name__)

BASE = "https://cloudtasks.googleapis.com/v2"
_HTTP_TIMEOUT_S = 10.0


def _location() -> str:
    return (os.environ.get("GCP_TASKS_LOCATION") or "us-central1").strip()


def is_configured() -> bool:
    return gcp_auth.is_configured()


def _parent(project: Optional[str] = None, location: Optional[str] = None) -> Optional[str]:
    p = (project or gcp_auth.project_id() or "").strip()
    if not p:
        return None
    return f"projects/{p}/locations/{(location or _location()).strip()}"


async def _request(method: str, path: str, *, json_body: Any = None,
                   timeout_s: float = _HTTP_TIMEOUT_S) -> Dict[str, Any]:
    headers = gcp_auth.auth_header()
    if not headers:
        return gcp_auth.disabled_payload()
    url = f"{BASE}/{path}"
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.request(method, url, headers=headers, json=json_body)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        if r.status_code >= 400:
            return {"status": "error", "elapsed_ms": elapsed_ms,
                    "error": f"HTTP {r.status_code}: {r.text[:300]}"}
        return {"status": "ok", "elapsed_ms": elapsed_ms,
                "data": r.json() if r.text else {}}
    except httpx.TimeoutException:
        return {"status": "error",
                "elapsed_ms": (time.perf_counter() - t0) * 1000.0,
                "error": "timeout"}
    except Exception as exc:
        return {"status": "error",
                "elapsed_ms": (time.perf_counter() - t0) * 1000.0,
                "error": f"{type(exc).__name__}: {str(exc)[:200]}"}


async def list_queues(*, project: Optional[str] = None,
                      location: Optional[str] = None,
                      page_size: int = 100) -> Dict[str, Any]:
    parent = _parent(project, location)
    if not parent:
        return gcp_auth.disabled_payload({"reason": "no project_id"})
    out = await _request("GET", f"{parent}/queues?pageSize={int(page_size)}")
    if out.get("status") != "ok":
        return out
    qs = (out["data"] or {}).get("queues") or []
    queues = [{
        "name": q.get("name"),
        "state": q.get("state"),
        "rate_limits": q.get("rateLimits"),
        "retry_config": q.get("retryConfig"),
    } for q in qs]
    return {"status": "ok", "elapsed_ms": out["elapsed_ms"],
            "parent": parent, "count": len(queues), "queues": queues}


async def list_tasks(queue_name: str, *, page_size: int = 50) -> Dict[str, Any]:
    """List tasks currently enqueued. `queue_name` is full resource path."""
    out = await _request("GET", f"{queue_name}/tasks?pageSize={int(page_size)}")
    if out.get("status") != "ok":
        return out
    ts = (out["data"] or {}).get("tasks") or []
    tasks = [{
        "name": t.get("name"),
        "schedule_time": t.get("scheduleTime"),
        "create_time": t.get("createTime"),
        "dispatch_count": t.get("dispatchCount"),
        "response_count": t.get("responseCount"),
    } for t in ts]
    return {"status": "ok", "elapsed_ms": out["elapsed_ms"],
            "queue": queue_name, "count": len(tasks), "tasks": tasks}


async def enqueue_http_task(
    queue_name: str,
    *,
    url: str,
    payload: Optional[Dict[str, Any]] = None,
    method: str = "POST",
    headers: Optional[Dict[str, str]] = None,
    schedule_time: Optional[str] = None,
    oidc_service_account_email: Optional[str] = None,
) -> Dict[str, Any]:
    """Enqueue an HTTP-target task that calls `url` with `payload`."""
    body_bytes = json.dumps(payload).encode("utf-8") if payload is not None else b""
    http_request: Dict[str, Any] = {
        "httpMethod": method.upper(),
        "url": url,
        "headers": dict(headers or {}),
    }
    if body_bytes:
        http_request["body"] = base64.b64encode(body_bytes).decode("ascii")
        http_request["headers"].setdefault("Content-Type", "application/json")
    if oidc_service_account_email:
        http_request["oidcToken"] = {
            "serviceAccountEmail": oidc_service_account_email,
        }
    task: Dict[str, Any] = {"httpRequest": http_request}
    if schedule_time:
        task["scheduleTime"] = schedule_time
    return await _request("POST", f"{queue_name}/tasks",
                          json_body={"task": task})


async def delete_task(task_name: str) -> Dict[str, Any]:
    return await _request("DELETE", task_name)
