"""Task #460 — Page on-call when the D1 mirror lag exceeds the threshold.

Task #427 added an in-process nightly safety net so ``sync_full`` +
``sync_extended`` always run even when the external Cloud Scheduler ping
fails. The flip side of that contract is a brand-new failure mode: if
BOTH the external scheduler AND the in-process loop fail (a stuck Mongo
lease, a wedged sync coroutine, a Cloudflare auth rotation that quietly
401s every fan-out), ``/admin/cf-health.d1_mirror.lag_seconds`` simply
climbs without anyone noticing — the safety net itself has no watchdog.

This module is that watchdog. It mirrors the established silence-alerter
pattern (Task #951 / #893 / #831) so the inbox / dedup / debounce
semantics line up across every cron alert channel:

  * a 1h background loop reads ``d1_mirror.lag_snapshot()`` (per the
    task spec) and the cross-replica nightly lease's ``last_fired_at``
    timestamp from Mongo (so a freshly-promoted leader replica whose
    in-process state is empty still has a global view);
  * the larger of the two ages drives the breach decision — using the
    Mongo lease as the floor means a wedged in-process state can't
    paper over a globally-stale mirror, while the in-process snapshot
    contributes when a sync error has bumped ``consecutive_failures``;
  * a configurable streak counter (``D1_MIRROR_LAG_REQUIRED_STREAK``,
    default 2) requires the breach to be observed on N consecutive
    polls before paging — that is the "configured grace window" called
    out in the task spec, defending against a single transient mongo
    blip during a leader hand-off;
  * email + in-app notification go out on the breach transition,
    debounced to one page per 24h while still over-threshold;
  * a one-shot recovery alert fires on breached → healthy as soon as
    a fresh sync lands;
  * cross-replica dedup uses :mod:`background_lease` plus an atomic
    Mongo CAS on ``job_locks`` so two replicas waking up on the same
    tick can't both page on-call.

Why we cross-reference the Mongo lease
--------------------------------------
``d1_mirror._state`` is a per-process in-process dict. The nightly loop
is leader-gated, so ONLY the current leader replica ever stamps
``last_sync_ts``. A freshly-promoted leader (after the prior leader
crashed) has ``last_sync_ts = None`` until it runs its first cycle —
classifying that as "lag exceeded" would falsely page on every leader
hand-off. Reading the Mongo lease's ``last_fired_at`` gives us the
global "when did the last sync land anywhere" anchor; the in-process
``lag_snapshot()`` then layers on the diagnostic detail (last error,
consecutive_failures, last row counts) the page body needs for triage.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends

from auth_deps import get_admin_user

logger = logging.getLogger(__name__)
router = APIRouter()


# ─── Tunables ───────────────────────────────────────────────────────────────

# Lag threshold. The nightly loop runs every 24h by default, so a
# healthy mirror's lag oscillates between ~0 and ~24h. 36h gives a
# 12h grace beyond the natural cycle so we don't page on a benign
# leader hand-off that happened to push the next sync a few hours
# late. Operators can tune via ``D1_MIRROR_LAG_THRESHOLD_S``.
def _lag_threshold_s() -> int:
    raw = os.environ.get("D1_MIRROR_LAG_THRESHOLD_S", "").strip()
    if raw:
        try:
            return max(60, int(raw))
        except ValueError:
            pass
    return 36 * 3600


# Required consecutive-breach count before paging. The task spec calls
# for a "grace window" expressed as N consecutive checks — defaulting
# to 2 means a single transient mongo blip during a leader hand-off
# (which would otherwise read ``last_fired_at = None``) can't
# false-positive on its own.
def _required_streak() -> int:
    raw = os.environ.get("D1_MIRROR_LAG_REQUIRED_STREAK", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return 2


# Re-page cadence while the lag is still over threshold. 24h matches
# every sibling alerter (cf-waf-drift, edge-proxy-deploy, cf-pull
# silence) so on-call sees a uniform page cadence across the admin
# surface.
_REALERT_INTERVAL_S = int(
    os.environ.get("D1_MIRROR_LAG_REALERT_INTERVAL_S") or 24 * 3600
)
# Background poll cadence + warmup. 1h poll because the entire check
# is a single Mongo find_one + an in-process dict read; warmup keeps
# a bouncing replica from spamming on the first 60s after boot when
# the nightly leader hasn't yet had a chance to run.
_LOOP_SLEEP_S = int(
    os.environ.get("D1_MIRROR_LAG_LOOP_SLEEP_S") or 3600
)
_WARMUP_S = int(
    os.environ.get("D1_MIRROR_LAG_WARMUP_S") or 900
)

_LOCK_ID = "d1_mirror_lag_alert_state"
_NIGHTLY_LEASE_ID = "d1_sync_nightly_lease"
_NIGHTLY_LAST_FIRED_FIELD = "last_fired_at"
_HEALTH_URL = "/admin/cf-health"


# ─── Health snapshot ───────────────────────────────────────────────────────

def _parse_iso_utc(s: Any) -> Optional[datetime]:
    if isinstance(s, datetime):
        return s if s.tzinfo else s.replace(tzinfo=timezone.utc)
    if not isinstance(s, str) or not s:
        return None
    try:
        out = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return out if out.tzinfo else out.replace(tzinfo=timezone.utc)
    except Exception:
        return None


async def _last_global_sync_ts(db) -> Optional[float]:
    """Read the cross-replica nightly lease's ``last_fired_at`` and
    return it as a unix timestamp. ``None`` when the lease doc is
    missing (no sync has ever landed) or Mongo is unreachable.
    """
    if db is None:
        return None
    try:
        doc = await db.job_locks.find_one({"_id": _NIGHTLY_LEASE_ID})
    except Exception as exc:
        logger.debug(f"[d1-mirror-lag] lease read failed: {exc}")
        return None
    if not doc:
        return None
    parsed = _parse_iso_utc(doc.get(_NIGHTLY_LAST_FIRED_FIELD))
    return parsed.timestamp() if parsed else None


async def get_d1_mirror_lag_health(db) -> dict[str, Any]:
    """Synthesize the health snapshot the alerter classifies on.

    Combines the two D1 mirror "last sync" signals so the alerter
    works regardless of which replica it's running on:

    * ``inProcessLastSyncTs`` — from ``d1_mirror.lag_snapshot()``;
      only populated on the leader replica that ran the most recent
      sync, and resets to ``None`` on restart;
    * ``leaseLastFiredTs`` — from ``job_locks[d1_sync_nightly_lease].
      last_fired_at``, which is globally consistent across replicas
      and survives restarts (it's the canonical "the nightly safety
      net actually ran" signal).

    ``lagSeconds`` is the age of the more recent of the two — using
    the more-recent timestamp means we don't false-positive when one
    of the two is stale-but-the-other-is-fresh (typical leader
    hand-off scenario). When both are ``None`` (no sync has ever
    landed AND the in-process state is empty), ``lagSeconds`` is
    ``None`` and the classifier returns ``unknown``.
    """
    try:
        from d1_mirror import lag_snapshot, is_enabled
    except Exception as exc:
        logger.debug(f"[d1-mirror-lag] d1_mirror import failed: {exc}")
        return {
            "enabled": False,
            "inProcessLastSyncTs": None,
            "leaseLastFiredTs": None,
            "lagSeconds": None,
            "lastSyncOk": None,
            "lastSyncError": None,
            "consecutiveFailures": 0,
            "rowCounts": {},
        }
    snap = lag_snapshot()
    in_proc_ts = snap.get("last_sync_ts")
    lease_ts = await _last_global_sync_ts(db)
    candidates = [t for t in (in_proc_ts, lease_ts) if t]
    now_ts = datetime.now(timezone.utc).timestamp()
    if candidates:
        lag_seconds = max(0.0, now_ts - max(candidates))
    else:
        lag_seconds = None
    return {
        "enabled": bool(is_enabled()),
        "inProcessLastSyncTs": in_proc_ts,
        "leaseLastFiredTs": lease_ts,
        "lagSeconds": lag_seconds,
        "lastSyncOk": snap.get("last_sync_ok"),
        "lastSyncError": snap.get("last_sync_error"),
        "consecutiveFailures": int(snap.get("consecutive_failures") or 0),
        "rowCounts": dict(snap.get("row_counts") or {}),
    }


# ─── Admin health endpoint ─────────────────────────────────────────────────

async def _read_alert_state(db) -> dict[str, Any]:
    """Project the lock-doc onto the alert-state shape the dashboard
    pills consume (mirrors ``_build_alert_state_response`` for the
    sibling cron alerters in ``routes/admin_health.py``).

    Always returns a dict — when the lock doc is missing or Mongo is
    unreachable, ``present: False`` is returned so the pill can still
    render a meaningful "no page recorded yet" state. Task #508.
    """
    base: dict[str, Any] = {
        "present": False,
        "lastState": None,
        "lastAlertAt": None,
        "lastAlertAgeSeconds": None,
        "consecutiveBreachCount": 0,
        "inDebounce": False,
        "debounceRemainingSeconds": None,
        "realertIntervalSeconds": _REALERT_INTERVAL_S,
    }
    if db is None:
        return base
    try:
        doc = await db.job_locks.find_one({"_id": _LOCK_ID})
    except Exception as exc:
        logger.debug(f"[d1-mirror-lag] lock-doc read failed: {exc}")
        return base
    # Defensive: in unit tests the global `deps.db` may be a mock
    # whose `find_one` returns a non-dict sentinel, in which case
    # `doc.get(...)` would return another mock and produce coroutine
    # warnings further down. Only proceed when we have a real mapping.
    if not isinstance(doc, dict):
        return base
    last_state = doc.get("last_state")
    last_alert_dt = _parse_iso_utc(doc.get("last_alert_at"))
    now_utc = datetime.now(timezone.utc)
    last_alert_age = None
    if last_alert_dt is not None:
        last_alert_age = max(
            0, int((now_utc - last_alert_dt).total_seconds()),
        )
    in_debounce = (
        last_state == "breached"
        and last_alert_age is not None
        and last_alert_age < _REALERT_INTERVAL_S
    )
    debounce_remaining = None
    if in_debounce and last_alert_age is not None:
        debounce_remaining = max(0, _REALERT_INTERVAL_S - last_alert_age)
    return {
        "present": True,
        "lastState": last_state,
        "lastAlertAt": doc.get("last_alert_at"),
        "lastAlertAgeSeconds": last_alert_age,
        "consecutiveBreachCount": int(
            doc.get("consecutive_breach_count") or 0,
        ),
        "inDebounce": bool(in_debounce),
        "debounceRemainingSeconds": debounce_remaining,
        "realertIntervalSeconds": _REALERT_INTERVAL_S,
    }


@router.get("/admin/health/d1-mirror/lag")
async def admin_d1_mirror_lag_health(
    admin: dict = Depends(get_admin_user),
) -> dict[str, Any]:
    """Pill snapshot for the D1 mirror lag alerter.

    Always 200; the dashboard branches on ``status``:

      * ``not_enabled`` — ``D1_MIRROR_ON`` is unset; the mirror is
        a no-op so a stale lag is expected.
      * ``never_observed`` — no sync timestamp anywhere yet.
      * ``breached`` — lag exceeded threshold; the alerter is paging
        (or about to, once the streak counter trips).
      * ``healthy`` — lag within threshold.

    Task #508 — the response also carries the lock-doc projection
    (``alertState`` + the flat ``consecutiveBreachCount`` /
    ``lastAlertAt`` fields) so the AdminHealth pill can render the
    "current streak / required streak" + "last paged Xh ago"
    captions without a second round-trip.
    """
    try:
        from deps import db  # type: ignore
    except Exception:
        db = None
    health = await get_d1_mirror_lag_health(db)
    threshold = _lag_threshold_s()
    lag = health.get("lagSeconds")
    if not health.get("enabled"):
        status = "not_enabled"
    elif lag is None:
        status = "never_observed"
    elif lag >= threshold:
        status = "breached"
    else:
        status = "healthy"
    alert_state = await _read_alert_state(db)
    return {
        **health,
        "status": status,
        "lagThresholdSeconds": threshold,
        "requiredStreak": _required_streak(),
        "healthUrl": _HEALTH_URL,
        "alertState": alert_state,
        "consecutiveBreachCount": alert_state["consecutiveBreachCount"],
        "lastAlertAt": alert_state["lastAlertAt"],
        "lastAlertAgeSeconds": alert_state["lastAlertAgeSeconds"],
        "lastAlertState": alert_state["lastState"],
        "inDebounce": alert_state["inDebounce"],
        "debounceRemainingSeconds": alert_state["debounceRemainingSeconds"],
        "realertIntervalSeconds": alert_state["realertIntervalSeconds"],
    }


@router.get("/admin/health/d1-mirror/lag/alert-history")
async def admin_d1_mirror_lag_alert_history(
    limit: int = 20,
    admin: dict = Depends(get_admin_user),
) -> dict[str, Any]:
    """Audit-log of pages issued by the D1 mirror lag alerter, most
    recent first. Task #508 — surfaces the events
    ``record_cron_alert_event`` already writes against
    ``_LOCK_ID`` so the AdminHealth pill can render a "Show paged
    history" disclosure inline like its sibling cron pills.

    Always 200; returns ``events: []`` when the alerter has never
    fired or when Mongo is unavailable.
    """
    from routes.admin_health import _build_alert_history_response
    return await _build_alert_history_response(_LOCK_ID, limit=limit)


# ─── Classification ────────────────────────────────────────────────────────

def _classify(health: dict[str, Any]) -> str:
    """Reduce to ``breached`` / ``healthy`` / ``unknown``.

    * ``unknown`` — the mirror flag is off (so the lag is meaningless)
      or no sync has ever landed (no anchor for a lag computation).
      Either branch is "we don't know yet" — never page on inconclusive
      signal.
    * ``breached`` — ``lagSeconds`` is past the configured threshold.
    * ``healthy`` — ``lagSeconds`` is under the threshold.
    """
    if not health.get("enabled"):
        return "unknown"
    lag = health.get("lagSeconds")
    if lag is None:
        return "unknown"
    return "breached" if lag >= _lag_threshold_s() else "healthy"


# ─── CAS dedup ─────────────────────────────────────────────────────────────

async def _claim_alert_slot(
    db, kind: str, now_utc: datetime, health: dict[str, Any],
    streak: int,
) -> bool:
    """Atomic single-winner CAS — same shape as the cf-waf-drift alerter.

    ``kind`` is ``"breached"`` (over-threshold) or ``"recovered"``.
    """
    set_payload = {
        "last_state": "breached" if kind == "breached" else "healthy",
        "last_alert_at": now_utc.isoformat(),
        "last_lag_seconds": health.get("lagSeconds"),
        "last_in_process_ts": health.get("inProcessLastSyncTs"),
        "last_lease_ts": health.get("leaseLastFiredTs"),
        "last_sync_ok": health.get("lastSyncOk"),
        "last_sync_error": health.get("lastSyncError"),
        "consecutive_failures": health.get("consecutiveFailures"),
        "consecutive_breach_count": streak,
        "updated_at": now_utc.isoformat(),
    }
    if kind == "breached":
        cutoff_iso = (
            now_utc - timedelta(seconds=_REALERT_INTERVAL_S)
        ).isoformat()
        cur_lease_ts = health.get("leaseLastFiredTs")
        cur_in_proc_ts = health.get("inProcessLastSyncTs")
        # Re-page when:
        #  - prior state isn't breached (first detection / recovery
        #    flipped back to breached), OR
        #  - the 24h debounce has elapsed AND a fresh sync DID land in
        #    between (lease or in-process timestamp rolled forward) so
        #    this is a genuinely new breach episode.
        guard = {
            "_id": _LOCK_ID,
            "$or": [
                {"last_state": {"$ne": "breached"}},
                {"$and": [
                    {"$or": [
                        {"last_alert_at": {"$lt": cutoff_iso}},
                        {"last_alert_at": {"$exists": False}},
                    ]},
                    {"$or": [
                        {"last_lease_ts": {"$ne": cur_lease_ts}},
                        {"last_in_process_ts": {"$ne": cur_in_proc_ts}},
                    ]},
                ]},
            ],
        }
    else:
        guard = {"_id": _LOCK_ID, "last_state": "breached"}
    try:
        res = await db.job_locks.find_one_and_update(
            guard, {"$set": set_payload}, upsert=False,
        )
        if res is not None:
            return True
    except Exception as exc:
        logger.debug(f"[d1-mirror-lag] CAS failed: {exc}")
        return False
    if kind != "breached":
        return False
    # Bootstrap insert for the first-ever breach detection on a fresh
    # deployment (the doc didn't exist yet so the CAS above missed).
    try:
        from pymongo.errors import DuplicateKeyError
        await db.job_locks.insert_one({"_id": _LOCK_ID, **set_payload})
        return True
    except DuplicateKeyError:
        try:
            res = await db.job_locks.find_one_and_update(
                guard, {"$set": set_payload}, upsert=False,
            )
            return res is not None
        except Exception:
            return False
    except Exception as exc:
        logger.debug(f"[d1-mirror-lag] bootstrap insert failed: {exc}")
        return False


async def _bump_streak_only(db, streak: int, now_utc: datetime) -> None:
    """Persist the streak counter without crossing into "alerted" state.

    Used when the breach has been classified but the configured streak
    threshold (``D1_MIRROR_LAG_REQUIRED_STREAK``) hasn't been reached
    yet. Best-effort — never raises. Stamps ``updated_at`` so an
    operator scrolling the lock-doc snapshot can see the alerter is
    actually running.
    """
    try:
        await db.job_locks.update_one(
            {"_id": _LOCK_ID},
            {"$set": {
                "consecutive_breach_count": streak,
                "updated_at": now_utc.isoformat(),
            },
             "$setOnInsert": {"_id": _LOCK_ID}},
            upsert=True,
        )
    except Exception as exc:
        logger.debug(f"[d1-mirror-lag] streak bump failed: {exc}")


async def _reset_streak(db, now_utc: datetime) -> None:
    """Zero the streak counter on a healthy classification. Best-effort."""
    try:
        await db.job_locks.update_one(
            {"_id": _LOCK_ID},
            {"$set": {
                "consecutive_breach_count": 0,
                "updated_at": now_utc.isoformat(),
            }},
        )
    except Exception as exc:
        logger.debug(f"[d1-mirror-lag] streak reset failed: {exc}")


# ─── Channels: email + in-app ──────────────────────────────────────────────

async def _email_admins(title: str, message: str, kind: str) -> None:
    """Best-effort email blast to every admin."""
    try:
        from email_templates import _send  # internal helper, intentional
    except Exception as exc:
        logger.debug(f"[d1-mirror-lag] email helper unavailable: {exc}")
        return
    admins: list[str] = []
    try:
        from deps import db as _mongo_db  # type: ignore
        if _mongo_db is not None:
            cursor = _mongo_db.users.find(
                {"is_admin": True}, {"_id": 0, "email": 1},
            )
            async for u in cursor:
                e = (u.get("email") or "").strip()
                if e:
                    admins.append(e)
    except Exception as exc:
        logger.debug(f"[d1-mirror-lag] admin lookup failed: {exc}")
    color = "#16a34a" if kind == "recovered" else "#dc2626"
    html = (
        f"<h2 style='color:{color};margin:0 0 8px;'>{title}</h2>"
        f"<p style='font-size:14px;line-height:1.6;color:#374151;"
        f"white-space:pre-line;'>{message}</p>"
        f"<p style='font-size:12px;color:#6b7280;'>This is an automated "
        f"alert from the Syrabit D1 mirror lag watchdog (Task #460).</p>"
    )
    for email in admins:
        try:
            await _send(email, title, html)
        except Exception as exc:
            logger.debug(
                f"[d1-mirror-lag] email send failed for {email}: {exc}"
            )


def _format_age(seconds: Optional[float]) -> str:
    if not isinstance(seconds, (int, float)):
        return "never"
    return f"{seconds / 3600:.1f}h"


async def _send_lag_alert(
    db, kind: str, health: dict[str, Any], now_utc: datetime,
) -> None:
    """Email + in-app notification + paged-on-call audit append.

    ``kind`` is ``"breached"`` or ``"recovered"``. Best-effort — never
    raises. The in-app notification persist is the canonical "we paged"
    signal; email and history-record fan-outs run as background tasks
    so a slow Mongo or email provider can't stall the alert loop or
    undo a notification that already succeeded.
    """
    lag_s = health.get("lagSeconds")
    age_h = _format_age(lag_s)
    threshold = _lag_threshold_s()
    threshold_h = threshold / 3600.0
    last_err = health.get("lastSyncError") or "<none>"
    consec_fail = health.get("consecutiveFailures") or 0

    if kind == "recovered":
        title = "D1 mirror lag recovered: a fresh sync has landed"
        msg = (
            "The D1 mirror's lag has dropped back under the configured "
            f"threshold of {threshold_h:.1f}h. The cross-replica "
            "nightly lease has stamped a fresh `last_fired_at`, "
            "meaning either the in-process safety net or the external "
            "Cloud Scheduler ping resumed advancing the mirror.\n\n"
            f"Current lag: {age_h}\n"
            f"Health endpoint: {_HEALTH_URL}\n\n"
            "No further action required."
        )
        notif_type = "info"
    else:
        title = (
            f"D1 mirror lag breached: no sync in {age_h} "
            f"(threshold {threshold_h:.1f}h)"
        )
        msg = (
            "The D1 mirror has not had a successful sync in "
            f"{age_h}, which is past the configured threshold of "
            f"{threshold_h:.1f}h. Both the external Cloud Scheduler "
            "ping and the in-process nightly safety net (Task #427) "
            "appear to be wedged — until one of them recovers, the "
            "Cloudflare Pages SSR layer will keep serving stale "
            "seo_meta / audit_log / syllabus_map rows, and the "
            "edge-cached content catalog will silently drift from "
            "Mongo.\n\n"
            f"Current lag: {age_h} (threshold: {threshold_h:.1f}h)\n"
            f"Last in-process sync ok: {health.get('lastSyncOk')}\n"
            f"Last in-process sync error: {last_err}\n"
            f"Consecutive in-process failures: {consec_fail}\n\n"
            "Likely causes: every backend replica is unhealthy "
            "(check /api/admin/health), the d1_sync_nightly_lease "
            "lease doc is owned by a zombie process, the external "
            "Cloud Scheduler job is paused, the D1_SYNC_SECRET has "
            "been rotated without redeploy, or every fan-out target "
            "(prod + preview) is 5xx-ing. Open the health endpoint "
            "below for the lock-doc snapshot, then trigger a manual "
            "POST /admin/d1-sync as a one-shot diagnostic.\n\n"
            f"Health endpoint: {_HEALTH_URL}"
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
                "kind": "d1_mirror_lag_alert",
                "state": kind,
                "lag_seconds": lag_s,
                "lag_threshold_seconds": threshold,
                "in_process_last_sync_ts": health.get("inProcessLastSyncTs"),
                "lease_last_fired_ts": health.get("leaseLastFiredTs"),
                "last_sync_ok": health.get("lastSyncOk"),
                "last_sync_error": health.get("lastSyncError"),
                "consecutive_failures": consec_fail,
            },
        })
    except Exception as exc:
        logger.debug(f"[d1-mirror-lag] notification persist failed: {exc}")

    asyncio.create_task(_email_admins(title, msg, kind))
    # Append to the paged-on-call audit log so the AdminHealth
    # dashboard's "show paged history" panel can render this event
    # next to the pill (Task #918 shared helper).
    try:
        from routes.admin_health import record_cron_alert_event
        asyncio.create_task(record_cron_alert_event(
            db,
            lock_id=_LOCK_ID,
            kind=kind,
            sub_kind=None,
            health={
                **health,
                "ageSeconds": lag_s,
                "conclusion": (
                    "breached" if kind == "breached" else "recovered"
                ),
                "status": kind,
            },
            now_utc=now_utc,
        ))
    except Exception as exc:
        logger.debug(
            f"[d1-mirror-lag] history record schedule failed: {exc}"
        )


# ─── Main alert iteration ─────────────────────────────────────────────────

async def _check_and_alert_d1_mirror_lag(
    db, now_utc: Optional[datetime] = None,
    health: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """One alert iteration. Returns a small report dict for tests."""
    now_utc = now_utc or datetime.now(timezone.utc)
    if health is None:
        health = await get_d1_mirror_lag_health(db)

    state = _classify(health)
    if state == "unknown":
        return {"action": "skip", "reason": "inconclusive", "state": state}

    prior: dict = {}
    try:
        prior = await db.job_locks.find_one({"_id": _LOCK_ID}) or {}
    except Exception as exc:
        logger.debug(f"[d1-mirror-lag] prior load failed: {exc}")
        prior = {}
    prior_state = prior.get("last_state")
    prior_streak = int(prior.get("consecutive_breach_count") or 0)

    last_alert_dt = _parse_iso_utc(prior.get("last_alert_at"))

    if state == "breached":
        new_streak = prior_streak + 1
        required = _required_streak()
        if new_streak < required and prior_state != "breached":
            # Inside the "grace window" — N consecutive checks haven't
            # been observed yet. Persist the bumped streak so the next
            # poll knows where we are, but don't page on-call yet.
            await _bump_streak_only(db, new_streak, now_utc)
            return {
                "action": "skip",
                "reason": "streak_pending",
                "streak": new_streak,
                "required": required,
            }
        if prior_state == "breached" and last_alert_dt is not None:
            elapsed_s = (now_utc - last_alert_dt).total_seconds()
            if elapsed_s < _REALERT_INTERVAL_S:
                # Still inside the 24h debounce — keep counting the
                # streak so the lock-doc snapshot stays fresh, but
                # don't re-page.
                await _bump_streak_only(db, new_streak, now_utc)
                return {
                    "action": "skip",
                    "reason": "debounced",
                    "elapsed_s": elapsed_s,
                }
            # Past the debounce: only re-page when a fresh sync DID
            # land in between (in-process or lease ts rolled forward).
            # Otherwise we'd double-page on the same already-acknowledged
            # breach episode.
            prior_lease = prior.get("last_lease_ts")
            prior_in_proc = prior.get("last_in_process_ts")
            cur_lease = health.get("leaseLastFiredTs")
            cur_in_proc = health.get("inProcessLastSyncTs")
            if prior_lease == cur_lease and prior_in_proc == cur_in_proc:
                await _bump_streak_only(db, new_streak, now_utc)
                return {
                    "action": "skip",
                    "reason": "same_run",
                    "elapsed_s": elapsed_s,
                }
        if not await _claim_alert_slot(
            db, "breached", now_utc, health, new_streak,
        ):
            return {"action": "skip", "reason": "lost_race"}
        await _send_lag_alert(db, "breached", health, now_utc)
        return {"action": "alerted", "kind": "breached", "streak": new_streak}

    # state == "healthy"
    if prior_state == "breached":
        if not await _claim_alert_slot(
            db, "recovered", now_utc, health, 0,
        ):
            return {"action": "skip", "reason": "lost_race"}
        await _send_lag_alert(db, "recovered", health, now_utc)
        return {"action": "alerted", "kind": "recovered"}

    # Healthy → healthy. Reset the streak counter if a previous
    # near-miss had bumped it without ever crossing into "alerted".
    if prior_streak:
        await _reset_streak(db, now_utc)
    return {"action": "skip", "reason": "healthy"}


async def _d1_mirror_lag_alert_loop():
    """Background poll loop.

    Cross-replica dedup: the per-state CAS above already prevents
    N×-paging across replicas, but the loop also acquires a Mongo-
    backed lease so only one replica reads the lock doc on each tick.
    Followers stand down on each tick, mirroring the cf-waf-drift /
    cf-pull silence alert loops.
    """
    from deps import db, is_mongo_available  # type: ignore
    import background_lease as _bglease
    owner_id = _bglease.make_owner_id("d1-mirror-lag-alert")
    lock_id = "d1_mirror_lag_alert_lease"
    ttl_s = max(900, _LOOP_SLEEP_S * 3)
    follower_s = max(60, min(600, _LOOP_SLEEP_S // 2))
    await asyncio.sleep(_WARMUP_S)
    try:
        while True:
            try:
                if not await is_mongo_available():
                    await asyncio.sleep(follower_s)
                    continue
                if not await _bglease.try_acquire_lease(
                    db, lock_id, owner_id, ttl_s,
                ):
                    await asyncio.sleep(follower_s)
                    continue
                await _check_and_alert_d1_mirror_lag(db)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug(
                    f"[d1-mirror-lag] loop iteration error: {exc}"
                )
            await asyncio.sleep(_LOOP_SLEEP_S)
    finally:
        try:
            await asyncio.shield(_bglease.release_lease(
                db, lock_id, owner_id,
            ))
        except Exception:
            pass
