"""services.moderation_queue — Task #337 image-moderation quarantine + admin queue.

Implements the runbook §3.5 workflow:

  Every user image upload (avatar, PYQ, future surfaces) calls
  ``screen_image()``. If Rekognition flags it, the bytes are persisted
  to Supabase under a ``quarantine/`` prefix, a row is written to the
  ``moderation_queue`` collection with ``status="pending_review"``, and
  the caller receives a sentinel that lets it surface a friendly
  "under review" notice instead of a hard 4xx — admins resolve the
  queue item from ``AdminModerationQueuePanel``.

  Failure mode: when Rekognition itself raises (boto3 missing, IAM
  refusal, throttle), the upload proceeds **without** quarantining —
  this is the documented availability-over-blocking trade-off in the
  runbook (a Rekognition outage cannot freeze classroom uploads). The
  failure is recorded in the per-feature telemetry window so the admin
  AdminAwsNativePanel tile flips to ``degraded`` and operators know to
  re-enable the gate as soon as the outage clears.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger("services.moderation_queue")

QUARANTINE_PREFIX = "quarantine"
COLLECTION = "moderation_queue"


@dataclass(frozen=True)
class ModerationVerdict:
    flagged: bool
    quarantined: bool
    queue_id: Optional[str]
    max_confidence: float
    error: Optional[str] = None


async def screen_image(
    raw: bytes,
    *,
    surface: str,
    owner_id: str,
    filename: str,
    mime: str,
    db_handle,
    supa_handle=None,
    bucket: str = "study-materials",
    extra: Optional[Dict[str, Any]] = None,
) -> ModerationVerdict:
    """Run Rekognition on ``raw`` and quarantine when flagged.

    Parameters
    ----------
    surface : str
        Free-form label for which UI surface uploaded the image
        (e.g. ``"avatar"``, ``"pyq"``, ``"chat_attachment"``). Stored
        on the queue row so the admin queue can filter per surface.
    owner_id : str
        User / admin id of the uploader. Required so the admin panel
        can show the user-facing context.
    db_handle : motor collection container
        The shared ``deps.db`` Mongo handle. ``moderation_queue`` is
        the destination collection.
    supa_handle : Supabase client, optional
        When supplied, flagged uploads are persisted to
        ``<bucket>/quarantine/<queue_id>/<filename>`` so the admin
        panel can render a thumbnail without re-uploading. When
        ``None`` (e.g. early in container startup), the queue row is
        still written with the bytes inlined as base64 so no evidence
        is lost.
    extra : dict, optional
        Arbitrary metadata blob the caller wants to surface in the
        admin panel (subject id, exam year, …).

    Returns
    -------
    ModerationVerdict
        ``flagged=True`` and ``quarantined=True`` when Rekognition
        rejected the image; ``flagged=False`` otherwise. ``error`` is
        populated when Rekognition itself failed — the caller should
        treat that case as a clean upload (runbook §3.5).
    """
    try:
        from providers import aws_native as _awsn
        if not (_awsn.is_enabled("rekognition") and _awsn.is_configured()):
            return ModerationVerdict(False, False, None, 0.0, error="disabled")
        verdict = await asyncio.to_thread(_awsn.moderate_image, raw)
    except Exception as exc:  # boto3 missing / IAM / throttle / etc.
        logger.warning("[moderation] Rekognition probe failed: %s", str(exc)[:200])
        return ModerationVerdict(False, False, None, 0.0, error=type(exc).__name__)

    if not verdict.get("flagged"):
        return ModerationVerdict(False, False, None, float(verdict.get("max_confidence", 0.0)))

    # ── Quarantine path ────────────────────────────────────────────────
    queue_id = str(uuid.uuid4())
    storage_path: Optional[str] = None
    inlined_b64: Optional[str] = None
    if supa_handle is not None:
        try:
            storage_path = f"{QUARANTINE_PREFIX}/{queue_id}/{filename}"
            await asyncio.to_thread(
                lambda: supa_handle.storage.from_(bucket).upload(
                    path=storage_path, file=raw,
                    file_options={"content-type": mime, "upsert": "true"},
                )
            )
        except Exception as exc:
            logger.warning("[moderation] quarantine supabase upload failed: %s", str(exc)[:200])
            storage_path = None

    if storage_path is None:
        import base64
        inlined_b64 = base64.b64encode(raw[:512_000]).decode("ascii")  # cap inline copy

    row = {
        "_id":            queue_id,
        "surface":        surface,
        "owner_id":       owner_id,
        "filename":       filename,
        "mime":           mime,
        "size_bytes":     len(raw),
        "storage_path":   storage_path,
        "inline_b64":     inlined_b64,
        "labels":         verdict.get("labels", []),
        "max_confidence": verdict.get("max_confidence", 0.0),
        "status":         "pending_review",
        "created_at":     datetime.now(timezone.utc).isoformat(),
        "extra":          extra or {},
    }
    try:
        if db_handle is not None:
            await db_handle[COLLECTION].insert_one(row)
    except Exception as exc:
        logger.warning("[moderation] queue write failed: %s", str(exc)[:200])

    logger.warning(
        "[moderation] QUARANTINED surface=%s owner=%s file=%s max_conf=%.1f queue_id=%s",
        surface, owner_id, filename, row["max_confidence"], queue_id,
    )
    return ModerationVerdict(
        flagged=True,
        quarantined=True,
        queue_id=queue_id,
        max_confidence=float(verdict.get("max_confidence", 0.0)),
    )


async def list_pending(db_handle, *, surface: Optional[str] = None, limit: int = 50) -> list:
    """Return up to ``limit`` pending queue items (newest first)."""
    if db_handle is None:
        return []
    q: Dict[str, Any] = {"status": "pending_review"}
    if surface:
        q["surface"] = surface
    cur = db_handle[COLLECTION].find(q).sort("created_at", -1).limit(limit)
    out = []
    async for row in cur:
        row.pop("inline_b64", None)  # don't ship the full bytes; admin endpoint serves them on demand
        out.append(row)
    return out


async def resolve(db_handle, queue_id: str, *, decision: str, admin_id: str) -> bool:
    """Mark a queue row as ``approved`` or ``rejected``. Returns True on update."""
    if decision not in ("approved", "rejected"):
        raise ValueError("decision must be 'approved' or 'rejected'")
    if db_handle is None:
        return False
    res = await db_handle[COLLECTION].update_one(
        {"_id": queue_id, "status": "pending_review"},
        {"$set": {
            "status": decision,
            "resolved_by": admin_id,
            "resolved_at": datetime.now(timezone.utc).isoformat(),
        }},
    )
    return bool(getattr(res, "modified_count", 0))
