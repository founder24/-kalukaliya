"""Task #551 §A — admin Glacier-Deep-Archive restore endpoint.

`POST /admin/archive/restore` — admin-only, audit-logged. Accepts a
list of `{bucket, key}` entries and issues an S3 `restore_object`
Standard-tier request (12 h SLA, ~$0.02/GB egress) for each.

The actual download / re-publish step is intentionally NOT done here:
the operator runbook (`docs/infra/glacier-restore-runbook.md`)
describes how to fetch the restored object after the 12 h window via
`aws s3 cp` once `s3:HeadObject` shows `x-amz-restore: ongoing-request="false"`.
This endpoint only initiates the thaw — the restore itself is async on
the AWS side and we deliberately do not block the FastAPI worker for
12 h.
"""
from __future__ import annotations

import datetime as _dt
import logging
import os
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth_deps import get_admin_user as require_admin

logger = logging.getLogger(__name__)

router = APIRouter()

_db = None
_allowed_buckets: Optional[set] = None


def init_admin_archive(db) -> None:
    """Wire the Mongo handle so we can audit-log restore requests."""
    global _db
    _db = db


def _resolve_allowed_buckets() -> set:
    """The three Glacier compliance buckets are the only allowed targets.

    Sourced from env (`GLACIER_ARCHIVE_BUCKETS`, comma-separated) so
    operators can override during DR drills without a code change.
    """
    global _allowed_buckets
    if _allowed_buckets is not None:
        return _allowed_buckets
    raw = os.environ.get("GLACIER_ARCHIVE_BUCKETS", "").strip()
    if raw:
        _allowed_buckets = {b.strip() for b in raw.split(",") if b.strip()}
    else:
        # Defaults match the Terraform output `glacier_archive_buckets`
        # PLUS the live `s3_finals_bucket` (S3 → R2 sync source) which
        # `glacier-archive.tf` also tags with a Deep Archive lifecycle
        # under the `finals/` prefix. The bucket name is taken from
        # `S3_FINALS_BUCKET` env when set (matches the value already
        # injected into the s3-to-r2-sync Lambda) so DR drills against
        # the finals tail don't need a `GLACIER_ARCHIVE_BUCKETS` override.
        _allowed_buckets = {
            "syrabit-razorpay-receipts-prod",
            "syrabit-content-snapshots-prod",
            "syrabit-cw-logs-archive-prod",
        }
        finals_bucket = os.environ.get("S3_FINALS_BUCKET", "").strip()
        if finals_bucket:
            _allowed_buckets.add(finals_bucket)
    return _allowed_buckets


class _RestoreItem(BaseModel):
    bucket: str = Field(..., min_length=3, max_length=128)
    key: str = Field(..., min_length=1, max_length=1024)


class _RestoreRequest(BaseModel):
    items: List[_RestoreItem] = Field(..., min_length=1, max_length=200)
    days_available: int = Field(7, ge=1, le=30, description="How many days the restored copy stays in S3 standard before re-archiving.")
    tier: str = Field("Standard", description="S3 RestoreObject tier — Standard (12h, ~$0.02/GB) or Bulk (48h, ~$0.0025/GB).")


@router.post("/admin/archive/restore")
async def restore_from_glacier(
    body: _RestoreRequest,
    admin: dict = Depends(require_admin),
):
    """Initiate a Glacier Deep Archive restore for the listed objects.

    Returns a per-item status; failures are captured per-item rather
    than aborting the whole batch.
    """
    if body.tier not in {"Standard", "Bulk", "Expedited"}:
        raise HTTPException(status_code=400, detail="tier must be one of Standard, Bulk, Expedited")
    # Expedited is not supported for Deep Archive.
    if body.tier == "Expedited":
        raise HTTPException(status_code=400, detail="Expedited restore is not supported for DEEP_ARCHIVE storage class")

    allowed = _resolve_allowed_buckets()
    bad_buckets = sorted({i.bucket for i in body.items if i.bucket not in allowed})
    if bad_buckets:
        raise HTTPException(
            status_code=400,
            detail=f"buckets not in archive allowlist: {bad_buckets}",
        )

    try:
        import boto3  # type: ignore
    except Exception as exc:
        logger.error("boto3 import failed: %s", exc)
        raise HTTPException(status_code=503, detail="boto3 not installed in runtime")

    region = os.environ.get("AWS_GLACIER_REGION", "").strip() or os.environ.get("AWS_REGION", "ap-south-1")
    s3 = boto3.client("s3", region_name=region)

    results: list[dict] = []
    requested_at = _dt.datetime.utcnow().isoformat() + "Z"
    for item in body.items:
        try:
            s3.restore_object(
                Bucket=item.bucket,
                Key=item.key,
                RestoreRequest={
                    "Days": body.days_available,
                    "GlacierJobParameters": {"Tier": body.tier},
                },
            )
            results.append({
                "bucket": item.bucket,
                "key":    item.key,
                "status": "restore_initiated",
                "tier":   body.tier,
                "available_for_days": body.days_available,
            })
        except Exception as exc:
            code = ""
            try:
                code = exc.response["Error"]["Code"]  # type: ignore[index]
            except Exception:
                code = type(exc).__name__
            logger.warning("restore_object failed for %s/%s: %s (%s)", item.bucket, item.key, exc, code)
            results.append({
                "bucket": item.bucket,
                "key":    item.key,
                "status": "error",
                "error":  code,
                "detail": str(exc)[:200],
            })

    # Audit log every restore request — admin email + items + outcome.
    if _db is not None:
        try:
            await _db["admin_archive_restore_log"].insert_one({
                "admin_email":   admin.get("email", ""),
                "admin_id":      admin.get("id", ""),
                "requested_at":  requested_at,
                "tier":          body.tier,
                "days_available": body.days_available,
                "items":         [i.model_dump() for i in body.items],
                "results":       results,
            })
        except Exception as exc:
            logger.warning("admin_archive_restore_log insert failed: %s", exc)

    initiated = sum(1 for r in results if r["status"] == "restore_initiated")
    return {
        "ok":          initiated == len(results),
        "initiated":   initiated,
        "failed":      len(results) - initiated,
        "results":     results,
        "sla_hours":   12 if body.tier == "Standard" else 48,
        "next_step":   (
            "Poll s3:HeadObject for x-amz-restore=ongoing-request=\"false\"; "
            "see artifacts/syrabit/docs/infra/glacier-restore-runbook.md "
            "for the full procedure. Endpoint mount is /api/admin/archive/*."
        ),
    }


@router.get("/admin/archive/restore/log")
async def restore_log(
    limit: int = 50,
    admin: dict = Depends(require_admin),  # noqa: ARG001
):
    """Recent restore requests, newest first. Admin-only audit feed."""
    if _db is None:
        raise HTTPException(status_code=503, detail="archive log not initialised")
    limit = max(1, min(int(limit), 200))
    try:
        cursor = _db["admin_archive_restore_log"].find({}).sort("requested_at", -1).limit(limit)
        rows = await cursor.to_list(length=limit)
    except Exception as exc:
        logger.warning("restore_log read failed: %s", exc)
        raise HTTPException(status_code=503, detail="audit log read failed")
    for r in rows:
        r["_id"] = str(r.get("_id", ""))
    return {"count": len(rows), "rows": rows}
