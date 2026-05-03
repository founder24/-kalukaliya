"""Google Cloud Scheduler client (SA-OAuth).

Wraps https://cloudscheduler.googleapis.com/v1 — list/get/run jobs in a
region. Used to migrate in-process nightly loops (grounded-recall,
internal-linker, remediation worker) onto a managed cron service.

Auth: requires GOOGLE_APPLICATION_CREDENTIALS_JSON. Returns
status="disabled" cleanly when absent.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx

import gcp_auth

logger = logging.getLogger(__name__)

BASE = "https://cloudscheduler.googleapis.com/v1"
_HTTP_TIMEOUT_S = 10.0


def _location() -> str:
    return (os.environ.get("GCP_SCHEDULER_LOCATION") or "us-central1").strip()


def is_configured() -> bool:
    return gcp_auth.is_configured()


def _parent(project: Optional[str] = None, location: Optional[str] = None) -> Optional[str]:
    p = (project or gcp_auth.project_id() or "").strip()
    if not p:
        return None
    loc = (location or _location()).strip()
    return f"projects/{p}/locations/{loc}"


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


async def list_jobs(*, project: Optional[str] = None,
                    location: Optional[str] = None,
                    page_size: int = 100) -> Dict[str, Any]:
    """List Cloud Scheduler jobs in a region."""
    parent = _parent(project, location)
    if not parent:
        return gcp_auth.disabled_payload({"reason": "no project_id"})
    out = await _request("GET", f"{parent}/jobs?pageSize={int(page_size)}")
    if out.get("status") != "ok":
        return out
    jobs_raw = (out["data"] or {}).get("jobs") or []
    jobs = [{
        "name": j.get("name"),
        "description": j.get("description"),
        "schedule": j.get("schedule"),
        "time_zone": j.get("timeZone"),
        "state": j.get("state"),
        "last_attempt_time": j.get("lastAttemptTime"),
        "user_update_time": j.get("userUpdateTime"),
        "target": (
            "http" if j.get("httpTarget") else
            "pubsub" if j.get("pubsubTarget") else
            "appengine" if j.get("appEngineHttpTarget") else "unknown"
        ),
    } for j in jobs_raw]
    return {"status": "ok", "elapsed_ms": out["elapsed_ms"],
            "parent": parent, "count": len(jobs), "jobs": jobs}


async def get_job(name: str) -> Dict[str, Any]:
    """Get a single job by full resource name (`projects/.../jobs/X`)."""
    return await _request("GET", name)


async def run_job(name: str) -> Dict[str, Any]:
    """Force-run a scheduled job immediately (out of cycle)."""
    return await _request("POST", f"{name}:run", json_body={})


async def pause_job(name: str) -> Dict[str, Any]:
    return await _request("POST", f"{name}:pause", json_body={})


async def resume_job(name: str) -> Dict[str, Any]:
    return await _request("POST", f"{name}:resume", json_body={})
