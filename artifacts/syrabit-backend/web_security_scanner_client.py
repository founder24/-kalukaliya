"""Google Web Security Scanner client (SA-OAuth).

Wraps https://websecurityscanner.googleapis.com/v1 — list scan configs,
list/start scan runs, list findings. Long-running scans against the
public Syrabit web app to surface XSS / outdated libraries / mixed
content / clear-text auth issues.

Auth: requires GOOGLE_APPLICATION_CREDENTIALS_JSON.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx

import gcp_auth

logger = logging.getLogger(__name__)

BASE = "https://websecurityscanner.googleapis.com/v1"
_HTTP_TIMEOUT_S = 10.0


def is_configured() -> bool:
    return gcp_auth.is_configured()


def _project_path(project: Optional[str] = None) -> Optional[str]:
    p = (project or gcp_auth.project_id() or "").strip()
    if not p:
        return None
    return f"projects/{p}"


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


async def list_scan_configs(*, project: Optional[str] = None,
                            page_size: int = 100) -> Dict[str, Any]:
    parent = _project_path(project)
    if not parent:
        return gcp_auth.disabled_payload({"reason": "no project_id"})
    out = await _request("GET", f"{parent}/scanConfigs?pageSize={int(page_size)}")
    if out.get("status") != "ok":
        return out
    configs = (out["data"] or {}).get("scanConfigs") or []
    return {"status": "ok", "elapsed_ms": out["elapsed_ms"],
            "parent": parent, "count": len(configs),
            "scan_configs": [{
                "name": c.get("name"),
                "display_name": c.get("displayName"),
                "starting_urls": c.get("startingUrls"),
                "user_agent": c.get("userAgent"),
                "schedule": c.get("schedule"),
                "max_qps": c.get("maxQps"),
            } for c in configs]}


async def start_scan_run(scan_config_name: str) -> Dict[str, Any]:
    """Start a new scan run for the given config. Returns the operation."""
    return await _request("POST", f"{scan_config_name}:start", json_body={})


async def list_scan_runs(scan_config_name: str, *, page_size: int = 25) -> Dict[str, Any]:
    out = await _request(
        "GET", f"{scan_config_name}/scanRuns?pageSize={int(page_size)}",
    )
    if out.get("status") != "ok":
        return out
    runs = (out["data"] or {}).get("scanRuns") or []
    return {"status": "ok", "elapsed_ms": out["elapsed_ms"],
            "scan_config": scan_config_name, "count": len(runs),
            "scan_runs": [{
                "name": r.get("name"),
                "execution_state": r.get("executionState"),
                "result_state": r.get("resultState"),
                "start_time": r.get("startTime"),
                "end_time": r.get("endTime"),
                "urls_crawled_count": r.get("urlsCrawledCount"),
                "urls_tested_count": r.get("urlsTestedCount"),
                "has_vulnerabilities": r.get("hasVulnerabilities"),
                "progress_percent": r.get("progressPercent"),
            } for r in runs]}


async def list_findings(scan_run_name: str, *, page_size: int = 100) -> Dict[str, Any]:
    out = await _request(
        "GET", f"{scan_run_name}/findings?pageSize={int(page_size)}",
    )
    if out.get("status") != "ok":
        return out
    findings = (out["data"] or {}).get("findings") or []
    return {"status": "ok", "elapsed_ms": out["elapsed_ms"],
            "scan_run": scan_run_name, "count": len(findings),
            "findings": [{
                "name": f.get("name"),
                "finding_type": f.get("findingType"),
                "severity": f.get("severity"),
                "http_method": f.get("httpMethod"),
                "fuzzed_url": f.get("fuzzedUrl"),
                "body": f.get("body"),
                "description": f.get("description"),
                "reproduction_url": f.get("reproductionUrl"),
                "frame_url": f.get("frameUrl"),
                "final_url": f.get("finalUrl"),
                "tracking_id": f.get("trackingId"),
            } for f in findings]}
