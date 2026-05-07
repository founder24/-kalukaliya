"""Task #485 — Page on-call when one model trips a sustained guardrail
block-ratio spike.

Task #448 added the ``guardrail_by_model`` per-model breakdown to the
admin CF Health panel so on-call can *see* which model is being
throttled by Cloudflare AI Gateway's Llama-Guard / AI Content Safety
guardrails. That tile only helps when somebody happens to look — a
sustained spike on a single model (a regression in the model's
safety behaviour, or a prompt-injection wave hitting one provider)
still surfaces only on manual inspection.

This module wires three things together:

* :func:`_check_and_alert_aig_guardrail` — one alert iteration that
  scans :func:`ai_gateway_observability.snapshot` ``["guardrail_by_model"]``
  and pages on-call once per offending model when its ``block_ratio``
  stays above a configurable threshold (default 30%) over a minimum
  sample floor (default 20).
* :func:`_aig_guardrail_alert_loop` — periodic background poll
  (default 5 min) that drives the iteration. Cross-replica safety +
  spam debounce both use atomic CAS on ``db.job_locks`` (the same
  pattern Task #893 / #831 / #751 alerters use), so the loop is safe
  to run on every replica even though ``server.py`` only spawns it
  once.
* :func:`admin_aig_guardrail_alert_state` — admin-protected snapshot
  of every per-model lock doc + the alerter's tunables, surfaced
  inline beside the guardrail-by-model tile so on-call can see
  "last paged Xh ago" beside each row.

Each model gets its own lock doc with ``_id =
``aig_guardrail_alert_state__<provider>__<model>``. The alerter pages
once per offending model, then debounces re-pages to once per 24h
while the model remains over the threshold (mirroring the cron
silence-alerter pattern). A model returning below the threshold for
a full sample window fires a recovery notification on the next tick.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends

from auth_deps import get_admin_user

logger = logging.getLogger(__name__)
router = APIRouter()


# ─── Tunables ───────────────────────────────────────────────────────────────

# Block-ratio floor that counts as "sustained guardrail spike". 0.30
# matches the task spec; tunable via env so on-call can tighten/loosen
# without a deploy.
_AIG_BLOCK_RATIO_THRESHOLD = float(
    os.environ.get("AIG_GUARDRAIL_BLOCK_RATIO_THRESHOLD") or 0.30
)
# Minimum guardrail-classified samples (allow + rewrite + block) for a
# model in the rolling window before we'll page on its block_ratio.
# Without this floor a single ``block`` event on a quiet model would
# read as 100% blocked and page immediately.
_AIG_MIN_SAMPLES = int(
    os.environ.get("AIG_GUARDRAIL_MIN_SAMPLES") or 20
)
# Re-page cadence while a model remains over the threshold.
_AIG_REALERT_INTERVAL_S = int(
    os.environ.get("AIG_GUARDRAIL_REALERT_INTERVAL_S") or 24 * 3600
)
# Background poll cadence + warmup delay.
_AIG_LOOP_SLEEP_S = int(os.environ.get("AIG_GUARDRAIL_LOOP_SLEEP_S") or 300)
_AIG_WARMUP_S = int(os.environ.get("AIG_GUARDRAIL_WARMUP_S") or 600)

_LOCK_ID_PREFIX = "aig_guardrail_alert_state__"
# Conservative upper bound on the safe portion of provider/model pair
# inside the lock id. Mongo's _id has a 1024-byte ceiling; we cap at a
# much smaller number so a pathological model name can never break
# CAS lookups.
_LOCK_KEY_MAX_LEN = 96


def _safe_lock_key(value: str) -> str:
    """Coerce a provider/model name into a Mongo-safe lock-id chunk.

    Replaces any non-``[A-Za-z0-9._-]`` character with ``_`` so the
    composed ``_id`` stays readable in admin dashboards but cannot
    smuggle ``$`` operators or regex meta-characters into the lookup.
    """
    if not value:
        return "unknown"
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_") or "unknown"
    return cleaned[:_LOCK_KEY_MAX_LEN]


def _lock_id_for(provider: str, model: str) -> str:
    return (
        f"{_LOCK_ID_PREFIX}{_safe_lock_key(provider)}__"
        f"{_safe_lock_key(model)}"
    )


# ─── Classification ────────────────────────────────────────────────────────

def _classify_row(row: dict[str, Any]) -> str:
    """Reduce one ``guardrail_by_model`` bucket to ``spike`` /
    ``healthy`` / ``unknown``.

    * ``unknown``: not enough guardrail-classified samples in the
      window yet — we refuse to page on a one-off block from a
      low-volume model.
    * ``spike``: at or above the configured block-ratio threshold AND
      past the minimum-sample floor.
    * ``healthy``: enough samples, ratio under the threshold.
    """
    total = int(row.get("guardrail_total") or 0)
    if total < _AIG_MIN_SAMPLES:
        return "unknown"
    ratio = row.get("block_ratio")
    if ratio is None:
        return "unknown"
    return "spike" if float(ratio) >= _AIG_BLOCK_RATIO_THRESHOLD else "healthy"


# ─── Cross-replica CAS ─────────────────────────────────────────────────────

async def _claim_aig_alert_slot(
    db, lock_id: str, kind: str, now_utc: datetime,
    row: dict[str, Any],
) -> bool:
    """Atomic single-winner CAS so a multi-replica deployment cannot
    page admins twice for the same spike or recovery transition (or
    the same 24h re-page cycle while a model stays over the threshold).

    Mirrors :func:`routes.admin_trustpilot_alerts._claim_trustpilot_alert_slot`.
    """
    set_payload: dict[str, Any] = {
        "last_state": "spike" if kind == "spike" else "healthy",
        "last_alert_at": now_utc.isoformat(),
        "provider": row.get("provider"),
        "model": row.get("model"),
        "last_block_ratio": row.get("block_ratio"),
        "last_blocks": row.get("blocks"),
        "last_rewrites": row.get("rewrites"),
        "last_allows": row.get("allows"),
        "last_guardrail_total": row.get("guardrail_total"),
        "last_samples": row.get("samples"),
        "updated_at": now_utc.isoformat(),
    }
    if kind == "spike":
        cutoff_iso = (
            now_utc - timedelta(seconds=_AIG_REALERT_INTERVAL_S)
        ).isoformat()
        guard = {
            "_id": lock_id,
            "$or": [
                {"last_state": {"$ne": "spike"}},
                {"last_alert_at": {"$lt": cutoff_iso}},
                {"last_alert_at": {"$exists": False}},
            ],
        }
    else:
        guard = {"_id": lock_id, "last_state": "spike"}
    try:
        res = await db.job_locks.find_one_and_update(
            guard, {"$set": set_payload}, upsert=False,
        )
        if res is not None:
            return True
    except Exception as exc:
        logger.debug(f"[aig-guardrail-alerts] CAS failed for {lock_id}: {exc}")
        return False
    if kind != "spike":
        # Recovery has no bootstrap path: there must be a prior spike row.
        return False
    try:
        from pymongo.errors import DuplicateKeyError
        await db.job_locks.insert_one({"_id": lock_id, **set_payload})
        return True
    except DuplicateKeyError:
        return False
    except Exception as exc:
        logger.debug(
            f"[aig-guardrail-alerts] bootstrap insert failed for {lock_id}: {exc}"
        )
        return False


# ─── Notification fan-out ──────────────────────────────────────────────────

async def _email_admins_about_aig_guardrail(
    title: str, message: str, kind: str,
) -> None:
    """Email every admin (best-effort). Mirrors the helper shape used
    by the Trustpilot feed alerter (Task #728) and CI alerter (Task
    #484) so all admin alert channels look consistent in the inbox.
    """
    try:
        from email_templates import _send  # internal helper, intentional
    except Exception as exc:
        logger.debug(f"[aig-guardrail-alerts] email helper unavailable: {exc}")
        return
    admins: list[str] = []
    try:
        from deps import db as _mongo_db  # type: ignore
        if _mongo_db is not None:
            cursor = _mongo_db.users.find(
                {"is_admin": True}, {"_id": 0, "email": 1}
            )
            async for u in cursor:
                e = (u.get("email") or "").strip()
                if e:
                    admins.append(e)
    except Exception as exc:
        logger.debug(f"[aig-guardrail-alerts] admin lookup failed: {exc}")
    color = "#16a34a" if kind == "recovered" else "#dc2626"
    html = (
        f"<h2 style='color:{color};margin:0 0 8px;'>{title}</h2>"
        f"<p style='font-size:14px;line-height:1.6;color:#374151;"
        f"white-space:pre-line;'>{message}</p>"
        f"<p style='font-size:12px;color:#6b7280;'>This is an automated "
        f"alert from the Syrabit AI Gateway guardrail-spike monitor "
        f"(Task #485).</p>"
    )
    for email in admins:
        try:
            await _send(email, title, html)
        except Exception as exc:
            logger.debug(
                f"[aig-guardrail-alerts] email send failed for {email}: {exc}"
            )


async def _send_aig_guardrail_alert(
    db, kind: str, row: dict[str, Any], now_utc: datetime,
) -> None:
    """Email admins + record an in-app notification. ``kind`` is
    ``"spike"`` or ``"recovered"``. Best-effort: never raises."""
    provider = row.get("provider") or "unknown"
    model = row.get("model") or "unknown"
    ratio = row.get("block_ratio")
    ratio_pct = (
        f"{float(ratio) * 100:.1f}%"
        if isinstance(ratio, (int, float)) else "—"
    )
    blocks = int(row.get("blocks") or 0)
    total = int(row.get("guardrail_total") or 0)

    if kind == "recovered":
        title = (
            f"AI Gateway guardrail spike recovered: {model} ({provider})"
        )
        msg = (
            f"Model `{model}` (provider `{provider}`) is no longer being "
            f"blocked by the Cloudflare AI Gateway guardrails layer at a "
            f"sustained rate. Current block ratio is {ratio_pct} over "
            f"{total} guardrail-classified samples. No further action "
            f"required."
        )
        notif_type = "info"
    else:
        title = (
            f"AI Gateway guardrail spike: {model} ({provider}) "
            f"at {ratio_pct} blocked"
        )
        msg = (
            f"Model `{model}` (provider `{provider}`) is being blocked by "
            f"the Cloudflare AI Gateway guardrails layer (Llama-Guard / "
            f"AI Content Safety) at {ratio_pct} of requests "
            f"({blocks}/{total} guardrail-classified samples), past the "
            f"{int(_AIG_BLOCK_RATIO_THRESHOLD * 100)}% page threshold.\n\n"
            f"Likely causes: a regression in this model's safety "
            f"behaviour, a prompt-injection wave hitting this provider, "
            f"or a mis-tuned guardrail policy. Check "
            f"/admin/cf-health → AI Gateway · models by guardrail block "
            f"ratio for the live tile."
        )
        notif_type = "error"

    try:
        from db_ops import supa_insert_notification
        await supa_insert_notification({
            "id": str(uuid.uuid4()),
            "title": title,
            "message": msg,
            "type": notif_type,
            "channel": "in_app",
            "audience": "admins",
            "status": "sent",
            "created_at": now_utc.isoformat(),
            "sent_at": now_utc.isoformat(),
            "meta": {
                "kind": "aig_guardrail_alert",
                "state": kind,
                "provider": provider,
                "model": model,
                "block_ratio": ratio,
                "blocks": blocks,
                "guardrail_total": total,
                "threshold": _AIG_BLOCK_RATIO_THRESHOLD,
            },
        })
    except Exception as exc:
        logger.debug(
            f"[aig-guardrail-alerts] notification persist failed: {exc}"
        )

    asyncio.create_task(_email_admins_about_aig_guardrail(title, msg, kind))


# ─── One iteration ─────────────────────────────────────────────────────────

async def _check_and_alert_aig_guardrail_for_row(
    db, row: dict[str, Any], now_utc: datetime,
) -> dict[str, Any]:
    """One iteration of the alerter for a single model row. Returns a
    small report dict for tests / observability."""
    provider = row.get("provider") or "unknown"
    model = row.get("model") or "unknown"
    lock_id = _lock_id_for(provider, model)
    state = _classify_row(row)
    if state == "unknown":
        # Not enough samples → never page, never touch the lock doc.
        return {"action": "skip", "reason": "inconclusive",
                "model": model, "provider": provider, "state": state}

    prior: dict = {}
    try:
        prior = await db.job_locks.find_one({"_id": lock_id}) or {}
    except Exception as exc:
        logger.debug(
            f"[aig-guardrail-alerts] prior load failed for {lock_id}: {exc}"
        )
        prior = {}
    prior_state = prior.get("last_state")

    last_alert_dt = None
    if prior.get("last_alert_at"):
        try:
            s = str(prior["last_alert_at"])
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            last_alert_dt = datetime.fromisoformat(s)
            if last_alert_dt.tzinfo is None:
                last_alert_dt = last_alert_dt.replace(tzinfo=timezone.utc)
        except Exception:
            last_alert_dt = None

    if state == "spike":
        # Fast-path debounce — avoid the CAS round-trip when we just paged.
        if prior_state == "spike" and last_alert_dt is not None:
            elapsed_s = (now_utc - last_alert_dt).total_seconds()
            if elapsed_s < _AIG_REALERT_INTERVAL_S:
                return {"action": "skip", "reason": "debounced",
                        "model": model, "provider": provider,
                        "elapsed_s": elapsed_s}
        if not await _claim_aig_alert_slot(db, lock_id, "spike", now_utc, row):
            return {"action": "skip", "reason": "lost_race",
                    "model": model, "provider": provider}
        await _send_aig_guardrail_alert(db, "spike", row, now_utc)
        return {"action": "alerted", "kind": "spike",
                "model": model, "provider": provider}

    # state == "healthy"
    if prior_state == "spike":
        if not await _claim_aig_alert_slot(
            db, lock_id, "recovered", now_utc, row,
        ):
            return {"action": "skip", "reason": "lost_race",
                    "model": model, "provider": provider}
        await _send_aig_guardrail_alert(db, "recovered", row, now_utc)
        return {"action": "alerted", "kind": "recovered",
                "model": model, "provider": provider}

    # healthy → healthy: never bootstrap a state doc here. An
    # unconditional upsert from this replica could race a peer that
    # just claimed `spike`, silently overwriting the lock and bypassing
    # the 24h debounce — same race-avoidance reasoning the Trustpilot
    # and CI alerters carry.
    return {"action": "skip", "reason": "healthy",
            "model": model, "provider": provider}


async def _check_and_alert_aig_guardrail(
    db, now_utc: Optional[datetime] = None,
    snapshot: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """One full iteration covering every model row in the snapshot.

    Returns a per-model report. ``snapshot`` defaults to the live
    :func:`ai_gateway_observability.snapshot` (kept overridable so
    tests can pin a deterministic window).
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    if snapshot is None:
        from ai_gateway_observability import snapshot as _aig_snap
        snapshot = _aig_snap()
    if not snapshot.get("enabled"):
        return {"action": "skip", "reason": "obs_disabled", "results": {}}
    rows = snapshot.get("guardrail_by_model") or []
    results: dict[str, dict[str, Any]] = {}
    for row in rows:
        provider = row.get("provider") or "unknown"
        model = row.get("model") or "unknown"
        key = f"{provider}::{model}"
        try:
            results[key] = await _check_and_alert_aig_guardrail_for_row(
                db, row, now_utc,
            )
        except Exception as exc:
            logger.warning(
                f"[aig-guardrail-alerts] iteration error for {key}: {exc}"
            )
            results[key] = {"action": "skip", "reason": "exception",
                            "model": model, "provider": provider,
                            "error": str(exc)[:200]}
    return {"action": "checked", "results": results}


# ─── Background loop ───────────────────────────────────────────────────────

async def _aig_guardrail_alert_loop():
    """Background poll loop. Cross-replica dedup is handled by the CAS
    inside :func:`_check_and_alert_aig_guardrail_for_row`, so this
    loop is safe to run on every replica even though ``server.py``
    only spawns it once.
    """
    from deps import db, is_mongo_available  # type: ignore
    await asyncio.sleep(_AIG_WARMUP_S)
    while True:
        try:
            if await is_mongo_available():
                await _check_and_alert_aig_guardrail(db)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug(f"[aig-guardrail-alerts] loop iteration error: {exc}")
        await asyncio.sleep(_AIG_LOOP_SLEEP_S)


# ─── Admin alert-state endpoint ────────────────────────────────────────────

def _shape_lock_doc(doc: dict[str, Any], now_utc: datetime) -> dict[str, Any]:
    """Project one alerter lock doc into the camelCase JSON the
    AdminHealth tile consumes. Mirrors the per-key shaping that
    ``routes.admin_health._build_alert_state_response`` does for the
    cron silence-alerter pills."""
    out: dict[str, Any] = {
        "lockId": doc.get("_id"),
        "provider": doc.get("provider"),
        "model": doc.get("model"),
        "lastState": doc.get("last_state"),
        "lastAlertAt": doc.get("last_alert_at"),
        "lastBlockRatio": doc.get("last_block_ratio"),
        "lastBlocks": doc.get("last_blocks"),
        "lastRewrites": doc.get("last_rewrites"),
        "lastAllows": doc.get("last_allows"),
        "lastGuardrailTotal": doc.get("last_guardrail_total"),
        "lastSamples": doc.get("last_samples"),
        "updatedAt": doc.get("updated_at"),
        "lastAlertAgeSeconds": None,
        "inDebounce": False,
        "debounceRemainingSeconds": None,
    }
    last_alert_at = doc.get("last_alert_at")
    if last_alert_at:
        try:
            s = str(last_alert_at)
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age = int((now_utc - dt).total_seconds())
            out["lastAlertAgeSeconds"] = max(0, age)
            if (
                doc.get("last_state") == "spike"
                and age < _AIG_REALERT_INTERVAL_S
            ):
                out["inDebounce"] = True
                out["debounceRemainingSeconds"] = max(
                    0, _AIG_REALERT_INTERVAL_S - age,
                )
        except Exception as exc:
            logger.debug(
                f"[aig-guardrail-alerts] alert-state ts parse failed "
                f"for {doc.get('_id')}: {exc}"
            )
    return out


@router.get("/admin/health/ai-gateway/guardrail-alerts/state")
async def admin_aig_guardrail_alert_state(
    admin: dict = Depends(get_admin_user),
) -> dict[str, Any]:
    """Lock-doc snapshot for every model the guardrail-spike alerter
    has paged on. Always 200; surfaces an empty ``models`` list when
    the alerter has not fired yet or Mongo is unavailable.

    The AdminHealth ``AiGatewayGuardrailByModelTile`` reads this and
    decorates each row with a "last paged Xh ago" caption so on-call
    can distinguish "this model is bad and we already paged" from
    "this model is bad and the page is still pending".
    """
    now_utc = datetime.now(timezone.utc)
    base: dict[str, Any] = {
        "alerter": {
            "blockRatioThreshold": _AIG_BLOCK_RATIO_THRESHOLD,
            "minSamples": _AIG_MIN_SAMPLES,
            "realertIntervalSeconds": _AIG_REALERT_INTERVAL_S,
            "loopSleepSeconds": _AIG_LOOP_SLEEP_S,
        },
        "models": [],
    }
    try:
        from deps import db, is_mongo_available  # type: ignore
        if not await is_mongo_available():
            return base
        # ``$regex`` with a literal ``^prefix`` is a covered-prefix
        # scan when ``_id`` is indexed (which it always is on Mongo);
        # the prefix is constructed from a hard-coded module constant
        # so user input cannot smuggle regex meta-characters in.
        cursor = db.job_locks.find(
            {"_id": {"$regex": f"^{re.escape(_LOCK_ID_PREFIX)}"}}
        )
        rows: list[dict[str, Any]] = []
        async for doc in cursor:
            rows.append(_shape_lock_doc(doc, now_utc))
    except Exception as exc:
        logger.debug(f"[aig-guardrail-alerts] alert-state read failed: {exc}")
        return base
    # Newest pages first so the dashboard's truncated view shows the
    # most recent activity instead of arbitrary _id order.
    rows.sort(
        key=lambda r: r.get("lastAlertAgeSeconds")
        if r.get("lastAlertAgeSeconds") is not None else 10**9
    )
    base["models"] = rows
    return base


__all__ = [
    "router",
    "_check_and_alert_aig_guardrail",
    "_check_and_alert_aig_guardrail_for_row",
    "_aig_guardrail_alert_loop",
    "_classify_row",
    "_lock_id_for",
    "_LOCK_ID_PREFIX",
    "_AIG_BLOCK_RATIO_THRESHOLD",
    "_AIG_MIN_SAMPLES",
    "_AIG_REALERT_INTERVAL_S",
]
