"""Admin proxy for the Azure Container Apps Jobs cron tier (Task #332).

The React `AdminCronJobsCard` polls `GET /admin/azure/cron/health`.
This module backs that endpoint by reading:

  1. Azure Resource Manager — most-recent execution per Container
     Apps Job in the `syrabit-cron-obs-rg` resource group. Auth is
     via the federated GitHub OIDC managed identity already
     configured in `infra/azure/iam-github-oidc.tf`.

  2. The local `cron_heartbeats` Mongo collection — written by
     `services/cron-jobs/run.py` at the END of every run with
     `{job_name, status, duration_ms, error}`. This is the source
     used when the ARM control plane is degraded, so the admin card
     never blanks out on a transient Azure outage.

The route is hardened so the ARM call is best-effort: if the ARM
side fails we still return the heartbeat snapshot with
`composite="degraded"` and a `partial=true` flag so the card can
surface the degradation rather than show nothing.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException

from auth_deps import get_admin_user as require_admin

try:
    from azure.identity.aio import DefaultAzureCredential  # type: ignore
except ImportError:  # pragma: no cover
    DefaultAzureCredential = None  # type: ignore

logger = logging.getLogger(__name__)
router = APIRouter()

_SUBSCRIPTION_ID = os.environ.get("AZURE_SUBSCRIPTION_ID", "")
_RESOURCE_GROUP  = os.environ.get("AZURE_CRON_RG", "syrabit-cron-obs-rg")
_ARM_API_VERSION = "2024-03-01"
_ARM_TIMEOUT_S   = 5.0


async def _arm_token() -> str | None:
    if DefaultAzureCredential is None:
        return None
    cred = DefaultAzureCredential()
    try:
        tok = await cred.get_token("https://management.azure.com/.default")
        return tok.token
    finally:
        await cred.close()


async def _list_jobs(token: str) -> list[dict[str, Any]]:
    url = (
        f"https://management.azure.com/subscriptions/{_SUBSCRIPTION_ID}"
        f"/resourceGroups/{_RESOURCE_GROUP}/providers/Microsoft.App/jobs"
        f"?api-version={_ARM_API_VERSION}"
    )
    async with httpx.AsyncClient(timeout=_ARM_TIMEOUT_S) as client:
        r = await client.get(url, headers={"Authorization": f"Bearer {token}"})
    r.raise_for_status()
    return (r.json() or {}).get("value", [])


async def _latest_execution(token: str, job_name: str) -> dict[str, Any] | None:
    url = (
        f"https://management.azure.com/subscriptions/{_SUBSCRIPTION_ID}"
        f"/resourceGroups/{_RESOURCE_GROUP}/providers/Microsoft.App/jobs/{job_name}"
        f"/executions?api-version={_ARM_API_VERSION}&$top=1"
    )
    async with httpx.AsyncClient(timeout=_ARM_TIMEOUT_S) as client:
        r = await client.get(url, headers={"Authorization": f"Bearer {token}"})
    if r.status_code != 200:
        return None
    items = (r.json() or {}).get("value", [])
    return items[0] if items else None


def _kind_from(job: dict[str, Any]) -> str:
    tags = (job.get("tags") or {}) if isinstance(job, dict) else {}
    return str(tags.get("job-kind") or tags.get("job_kind") or "loop")


def _key_from(job_name: str) -> str:
    # `aca-job-<key>` per container-apps-jobs.tf; strip the prefix
    # so the React card key matches DISPATCH/cron_jobs.
    return job_name[len("aca-job-"):] if job_name.startswith("aca-job-") else job_name


async def _heartbeat_snapshot(db) -> dict[str, dict[str, Any]]:
    """Best-effort fallback when ARM is degraded.

    Returns a {job_name: {status, duration_ms, ts}} map keyed by the
    `aca-job-*` resource name so callers can join against the ARM
    listing.
    """
    if db is None:
        return {}
    try:
        # Latest row per job_name. Mongo aggregation kept tiny — the
        # cron_heartbeats collection has at most ~50 rows × N runs/day.
        cursor = db.cron_heartbeats.aggregate([
            {"$sort": {"ts": -1}},
            {"$group": {
                "_id": "$job_name",
                "status": {"$first": "$status"},
                "duration_ms": {"$first": "$duration_ms"},
                "error": {"$first": "$error"},
                "ts": {"$first": "$ts"},
            }},
        ])
        out: dict[str, dict[str, Any]] = {}
        async for row in cursor:
            out[row["_id"]] = {
                "status":      row.get("status"),
                "duration_ms": row.get("duration_ms"),
                "error":       row.get("error"),
                "ts":          row.get("ts"),
            }
        return out
    except Exception:
        logger.exception("cron_heartbeats read failed")
        return {}


def _composite_from(jobs: list[dict[str, Any]]) -> str:
    if not jobs:
        return "unknown"
    if any(j.get("lastRunStatus") == "Failed" for j in jobs):
        return "failed"
    if any((j.get("consecutiveFailures") or 0) > 0 for j in jobs):
        return "degraded"
    return "ok"


@router.get("/admin/azure/cron/health")
async def cron_health(_admin: dict = Depends(require_admin)) -> dict[str, Any]:
    if not _SUBSCRIPTION_ID:
        raise HTTPException(status_code=503, detail="AZURE_SUBSCRIPTION_ID not configured")

    # Heartbeat fallback first — cheap Mongo read, used as the floor
    # if ARM is slow / 5xx.
    from server import db as _server_db  # circular-safe at request time
    heartbeats = await _heartbeat_snapshot(_server_db)

    token = await _arm_token()
    partial = False
    job_rows: list[dict[str, Any]] = []
    arm_job_keys: set[str] = set()

    if not token:
        partial = True
    else:
        try:
            jobs = await _list_jobs(token)
        except Exception as e:
            logger.warning("ARM list_jobs failed: %s", e)
            jobs = []
            partial = True

        # ARM doesn't expose "last execution" inline; fan out per-job.
        async def _row(job: dict[str, Any]) -> dict[str, Any]:
            name = job.get("name") or ""
            try:
                exe = await _latest_execution(token, name)
            except Exception:
                exe = None
            props = (exe or {}).get("properties", {}) or {}
            status_raw = (props.get("status") or "").strip() or None
            run_status = {
                "Succeeded": "Succeeded",
                "Failed":    "Failed",
                "Running":   "Running",
                "Stopped":   "Aborted",
            }.get(status_raw or "", status_raw or "Unknown")

            hb = heartbeats.get(name) or heartbeats.get(_key_from(name)) or {}
            last_run_at = props.get("startTime") or (hb.get("ts").isoformat() if isinstance(hb.get("ts"), datetime) else None)

            return {
                "key":                 _key_from(name),
                "jobName":             name,
                "kind":                _kind_from(job),
                "cron":                ((job.get("properties") or {}).get("configuration") or {}).get("scheduleTriggerConfig", {}).get("cronExpression"),
                "lastRunAt":           last_run_at,
                "lastRunStatus":       run_status,
                "consecutiveFailures": 1 if (run_status == "Failed") else 0,
                "nextRunAt":           None,  # ARM does not surface "next fire time"; left for client estimation
                "alertState":          "OK",  # populated by Azure Monitor merge in Phase 5
            }

        job_rows = await asyncio.gather(*[_row(j) for j in jobs])
        arm_job_keys = {r["key"] for r in job_rows}

    # Heartbeat backfill — synthesize a row from the most recent
    # heartbeat for any job key the ARM listing did not return. This
    # keeps the admin card populated when the ARM control plane is
    # 5xx-ing, and surfaces stale jobs (no recent heartbeat AND no
    # ARM record) as `Unknown` instead of dropping them silently.
    for job_name, hb in heartbeats.items():
        key = _key_from(job_name)
        if key in arm_job_keys:
            continue
        ts = hb.get("ts")
        last_run_at = ts.isoformat() if isinstance(ts, datetime) else None
        status_map = {
            "ok":           "Succeeded",
            "error":        "Failed",
            "aborted":      "Aborted",
            "config_error": "Failed",
        }
        run_status = status_map.get(str(hb.get("status") or "").lower(), "Unknown")
        job_rows.append({
            "key":                 key,
            "jobName":             job_name if job_name.startswith("aca-job-") else f"aca-job-{key}",
            "kind":                "loop",
            "cron":                None,
            "lastRunAt":           last_run_at,
            "lastRunStatus":       run_status,
            "consecutiveFailures": 1 if run_status == "Failed" else 0,
            "nextRunAt":           None,
            "alertState":          "OK",
            "source":              "heartbeat",  # disambiguates ARM-vs-fallback rows in the UI
        })

    composite = _composite_from(job_rows)
    return {
        "asOf":      datetime.now(timezone.utc).isoformat(),
        "composite": "degraded" if (partial and composite == "ok") else composite,
        "partial":   partial,
        "jobs":      job_rows,
    }
