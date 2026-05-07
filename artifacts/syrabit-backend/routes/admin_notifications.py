"""Syrabit.ai — Admin notifications, push, exam schedule, export, rate policies"""
import re, json, asyncio, uuid, logging, csv, base64
from typing import Optional, Dict, Any
from datetime import datetime, timezone, timedelta
from fastapi import (
    APIRouter, HTTPException, Depends, Query, Body,
    Path,
    File, UploadFile, Response,
)

from deps import db
from auth_deps import (
    get_current_user, get_admin_user,
)
from db_ops import (
    _ADMIN_NOTIF_PREFS_DEFAULTS,
    get_admin_notification_prefs,
    supa_count_users,
    supa_delete_notification,
    supa_get_all_conversations,
    supa_get_notifications,
    supa_insert_notification,
    supa_list_users,
    upsert_admin_notification_prefs,
)

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/admin/notifications")
async def admin_get_notifications(admin: dict = Depends(get_admin_user)):
    notifs = await supa_get_notifications()
    return notifs

@router.post("/admin/notifications")
async def admin_create_notification(data: dict, admin: dict = Depends(get_admin_user)):
    notif = {
        "id": str(uuid.uuid4()),
        "title": data.get("title", ""),
        "message": data.get("message", ""),
        "type": data.get("type", "info"),
        "channel": data.get("channel", "push"),
        "audience": data.get("audience", "all"),
        "status": data.get("status", "draft"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sent_at": datetime.now(timezone.utc).isoformat() if data.get("status") == "sent" else None,
    }
    await supa_insert_notification(notif)
    # Dispatch web-push immediately when status is "sent"
    if notif["status"] == "sent" and notif.get("channel", "push") == "push":
        asyncio.create_task(_dispatch_push_to_all({
            "title": notif["title"],
            "body":  notif["message"],
            "url":   data.get("url", "/"),
        }))
    return notif

@router.delete("/admin/notifications/{notif_id}")
async def admin_delete_notification(notif_id: str, admin: dict = Depends(get_admin_user)):
    await supa_delete_notification(notif_id)
    return {"message": "Deleted"}


# ─────────────────────────────────────────────
# PUSH NOTIFICATIONS — VAPID + Subscriptions
# ─────────────────────────────────────────────

async def _get_or_create_vapid_keys() -> dict:
    """Return the VAPID key pair.

    Source-of-truth precedence (Task #557):
      1. ``config.WEB_PUSH_VAPID_PRIVATE_KEY`` (Azure Key Vault →
         ACA secretRef ``web-push-vapid-private-key``). The matching
         public key is *derived* from the private PEM at call time so
         we never have to keep two halves of the same secret in sync.
      2. Legacy ``db.api_config.push_vapid`` (used by the pre-Task-#557
         deploy that auto-generated and stored both halves in Mongo).
         Treated as a one-shot bootstrap fallback and logged loudly so
         a missed KV rotation surfaces in admin alerts (V4 §12 — no
         silent fallbacks).
      3. Last-resort: generate-and-store in Mongo (local dev / first
         boot before either of the above is configured).
    """
    try:
        import config as _cfg  # local import keeps startup-order safe
    except Exception:
        _cfg = None
    env_private_pem = (getattr(_cfg, "WEB_PUSH_VAPID_PRIVATE_KEY", "") or "").strip()
    if env_private_pem:
        try:
            from cryptography.hazmat.primitives.serialization import (
                Encoding, PublicFormat, load_pem_private_key,
            )
            priv = load_pem_private_key(env_private_pem.encode(), password=None)
            pub_raw = priv.public_key().public_bytes(
                Encoding.X962, PublicFormat.UncompressedPoint
            )
            pub_b64 = base64.urlsafe_b64encode(pub_raw).rstrip(b"=").decode()
            return {"public_key": pub_b64, "private_key_pem": env_private_pem}
        except Exception as e:
            logger.error(
                "WEB_PUSH_VAPID_PRIVATE_KEY is set but unparseable (%s); "
                "refusing to fall back silently per V4 §12", e,
            )
            return {}

    cfg = await db.api_config.find_one({}, {"push_vapid": 1})
    existing = (cfg or {}).get("push_vapid", {})
    if existing.get("public_key") and existing.get("private_key_pem"):
        logger.warning(
            "Using legacy db.api_config.push_vapid keypair — "
            "WEB_PUSH_VAPID_PRIVATE_KEY env var is not configured (Task #557)"
        )
        return existing
    try:
        from py_vapid import Vapid
        from cryptography.hazmat.primitives.serialization import (
            Encoding, PrivateFormat, PublicFormat, NoEncryption
        )
        v = Vapid()
        v.generate_keys()
        private_pem = v.private_key.private_bytes(
            Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption()
        ).decode()
        # Public key as uncompressed EC point, urlsafe-base64 (what browsers expect)
        pub_raw = v.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        pub_b64 = base64.urlsafe_b64encode(pub_raw).rstrip(b"=").decode()
        keys = {"public_key": pub_b64, "private_key_pem": private_pem}
        await db.api_config.update_one({}, {"$set": {"push_vapid": keys}}, upsert=True)
        logger.info("VAPID keys generated and stored in db.api_config (bootstrap)")
        return keys
    except Exception as e:
        logger.error(f"VAPID key generation failed: {e}")
        return {}


def _vapid_claims_sub() -> str:
    """Return the `sub` claim for outbound webpush requests.

    Sourced from `config.WEB_PUSH_CONTACT` (RFC 8292 §2 — must be a
    `mailto:` or `https:` URI identifying the application server).
    Defaults to ``mailto:admin@syrabit.ai`` to preserve the historical
    behaviour for callers that haven't set the env var yet.
    """
    try:
        import config as _cfg
        contact = (getattr(_cfg, "WEB_PUSH_CONTACT", "") or "").strip()
        if contact:
            return contact
    except Exception:
        pass
    return "mailto:admin@syrabit.ai"


async def _dispatch_push_to_all(payload: dict):
    """Send a web-push to every stored subscription. Fire-and-forget."""
    await _dispatch_push(payload, admin_only=False)


async def _dispatch_push_to_admins(payload: dict):
    """Send a web-push only to admin-user subscriptions. Fire-and-forget.
    Respects per-admin notification preferences (push_enabled, push_severities)."""
    await _dispatch_push(payload, admin_only=True)


async def _dispatch_push(payload: dict, admin_only: bool = False):
    """Core push dispatcher. When admin_only=True, sends only to subscriptions
    with role='admin', filtered by per-admin notification prefs
    (push_enabled + push_severities). Uses the admin_id stored directly on
    each subscription document to avoid joining the users collection.
    Persists per-subscription delivery results to db.push_delivery_log."""
    dispatch_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()
    label = "admin-only" if admin_only else "all"

    async def _log_skip(reason: str):
        """Persist a skip/failure entry so per-channel health (Task #427) can
        surface that the dispatch never actually delivered to any subscriber."""
        try:
            await db.push_delivery_log.insert_one({
                "dispatch_id": dispatch_id,
                "dispatched_at": now_iso,
                "target": label,
                "payload_title": payload.get("title", ""),
                "payload_body": payload.get("body", "")[:500],
                "alert_type": payload.get("alert_type", ""),
                "total": 0,
                "sent": 0,
                "failed": 0,
                "expired": 0,
                "results": [],
                "skipped": True,
                "error": reason,
            })
        except Exception as log_exc:
            logger.warning(f"Failed to persist push skip log: {log_exc}")

    try:
        from pywebpush import webpush, WebPushException
        vapid = await _get_or_create_vapid_keys()
        private_pem = vapid.get("private_key_pem", "")
        if not private_pem:
            logger.warning("Push dispatch skipped — VAPID private key missing")
            await _log_skip("VAPID private key missing")
            return
        if admin_only:
            admin_subs = await db.push_subscriptions.find(
                {
                    "$or": [{"role": "admin"}, {"is_admin": True}],
                    "active": {"$ne": False},
                },
                {"_id": 0},
            ).to_list(10000)
            if not admin_subs:
                admin_docs = await db.users.find(
                    {"is_admin": True}, {"_id": 0, "id": 1}
                ).to_list(500)
                legacy_admin_ids = [str(d["id"]) for d in admin_docs if d.get("id")]
                if legacy_admin_ids:
                    admin_subs = await db.push_subscriptions.find(
                        {
                            "user_id": {"$in": legacy_admin_ids},
                            "active": {"$ne": False},
                        },
                        {"_id": 0},
                    ).to_list(10000)
            if not admin_subs:
                logger.info("Push dispatch (admin-only): no admin subscriptions found, skipping")
                await _log_skip("no admin subscriptions registered")
                return

            admin_ids_in_subs = {
                sub.get("admin_id") or sub.get("user_id") for sub in admin_subs
            }
            admin_ids_in_subs.discard("")

            alert_type = payload.get("alert_type", "")
            admin_prefs_map = {}
            try:
                prefs_cursor = db.admin_notification_prefs.find(
                    {"admin_id": {"$in": list(admin_ids_in_subs)}}, {"_id": 0}
                )
                async for pref_doc in prefs_cursor:
                    admin_prefs_map[pref_doc["admin_id"]] = pref_doc
            except Exception as exc:
                logger.debug(f"Failed to load admin notification prefs for push filter: {exc}")

            eligible_admin_ids = set()
            for aid in admin_ids_in_subs:
                prefs = admin_prefs_map.get(aid, {})
                push_enabled = prefs.get("push_enabled", _ADMIN_NOTIF_PREFS_DEFAULTS.get("push_enabled", False))
                push_severities = prefs.get("push_severities", _ADMIN_NOTIF_PREFS_DEFAULTS.get("push_severities", []))
                if not push_enabled:
                    continue
                if alert_type and alert_type not in push_severities:
                    continue
                eligible_admin_ids.add(aid)

            if not eligible_admin_ids:
                logger.info("Push dispatch (admin-only): no admins with push enabled for this alert type, skipping")
                await _log_skip("no admins have push enabled for this alert type")
                return

            subs = [
                sub for sub in admin_subs
                if (sub.get("admin_id") or sub.get("user_id")) in eligible_admin_ids
            ]
        else:
            subs = await db.push_subscriptions.find(
                {"active": {"$ne": False}}, {"_id": 0}
            ).to_list(10000)
        sent = failed = expired = 0
        delivery_results = []
        for sub in subs:
            endpoint = sub.get("endpoint", "")
            user_id = sub.get("user_id", "")
            role = sub.get("role", "unknown")
            try:
                # pywebpush.webpush is a blocking requests.post call.
                # Without to_thread it stalls the entire FastAPI worker
                # for every slow gateway (FCM/autopush) — one bad
                # endpoint blocks every other admin's push.
                await asyncio.to_thread(
                    webpush,
                    subscription_info=sub["subscription_info"],
                    data=json.dumps(payload),
                    vapid_private_key=private_pem,
                    vapid_claims={"sub": _vapid_claims_sub()},
                )
                sent += 1
                delivery_results.append({
                    "endpoint": endpoint,
                    "user_id": user_id,
                    "role": role,
                    "status": "sent",
                    "error": None,
                })
            except WebPushException as e:
                status_code = e.response.status_code if e.response else None
                if status_code in (404, 410):
                    await db.push_subscriptions.delete_one({"endpoint": endpoint})
                    expired += 1
                    delivery_results.append({
                        "endpoint": endpoint,
                        "user_id": user_id,
                        "role": role,
                        "status": "expired",
                        "error": f"HTTP {status_code}",
                    })
                else:
                    failed += 1
                    delivery_results.append({
                        "endpoint": endpoint,
                        "user_id": user_id,
                        "role": role,
                        "status": "failed",
                        "error": str(e)[:200],
                    })
            except Exception as exc:
                failed += 1
                delivery_results.append({
                    "endpoint": endpoint,
                    "user_id": user_id,
                    "role": role,
                    "status": "failed",
                    "error": str(exc)[:200],
                })
        logger.info(f"Push dispatch ({label}): sent={sent} failed={failed} expired={expired} total={len(subs)}")
        try:
            log_doc = {
                "dispatch_id": dispatch_id,
                "dispatched_at": now_iso,
                "target": label,
                "payload_title": payload.get("title", ""),
                "payload_body": payload.get("body", "")[:500],
                "alert_type": payload.get("alert_type", ""),
                "total": len(subs),
                "sent": sent,
                "failed": failed,
                "expired": expired,
                "results": delivery_results,
            }
            await db.push_delivery_log.insert_one(log_doc)
        except Exception as log_exc:
            logger.warning(f"Failed to persist push delivery log: {log_exc}")
    except Exception as e:
        logger.error(f"Push dispatch error: {e}")


@router.get("/push/vapid-public-key")
async def push_vapid_public_key():
    """Return the VAPID public key so the browser can subscribe."""
    keys = await _get_or_create_vapid_keys()
    pub = keys.get("public_key", "")
    if not pub:
        raise HTTPException(503, "Push not configured — VAPID key generation failed")
    return {"public_key": pub}


@router.post("/push/subscribe")
async def push_subscribe(data: dict, user: dict = Depends(get_current_user)):
    """Store a browser push subscription for the authenticated user.
    Stores admin_id alongside user_id so push dispatch can filter
    by per-admin notification preferences without joining the users collection."""
    subscription_info = data.get("subscription")
    if not subscription_info or not subscription_info.get("endpoint"):
        raise HTTPException(400, "Missing subscription object")
    endpoint = subscription_info["endpoint"]
    is_admin = bool(user.get("is_admin"))
    role = "admin" if is_admin else "student"
    admin_id = str(user.get("id", "")) if is_admin else ""
    doc = {
        "user_id":           str(user["id"]),
        "endpoint":          endpoint,
        "subscription_info": subscription_info,
        "role":              role,
        "subscribed_at":     datetime.now(timezone.utc).isoformat(),
        "is_admin":          is_admin,
        "admin_id":          admin_id,
        "active":            True,
    }
    # A re-subscribe always reactivates the endpoint and clears the
    # consecutive-failure counter (Task #435 auto-prune).
    await db.push_subscriptions.update_one(
        {"endpoint": endpoint},
        {"$set": doc, "$unset": {"deactivated_at": "", "deactivation_reason": "",
                                  "consecutive_failures_at_prune": ""}},
        upsert=True,
    )
    return {"ok": True}


@router.delete("/push/subscribe")
async def push_unsubscribe(data: dict, user: dict = Depends(get_current_user)):
    """Remove a push subscription for the authenticated user."""
    endpoint = (data or {}).get("endpoint", "")
    if endpoint:
        await db.push_subscriptions.delete_one({"endpoint": endpoint, "user_id": str(user["id"])})
    return {"ok": True}


@router.post("/admin/push/backfill-roles")
async def admin_backfill_push_roles(admin: dict = Depends(get_admin_user)):
    """One-shot backfill: set role, is_admin, and admin_id on every
    push_subscriptions doc missing any of these fields."""
    subs_needing_backfill = await db.push_subscriptions.find(
        {"$or": [
            {"role": {"$exists": False}},
            {"is_admin": {"$exists": False}},
            {"admin_id": {"$exists": False}},
        ]},
        {"_id": 1, "user_id": 1},
    ).to_list(50000)
    if not subs_needing_backfill:
        return {"backfilled": 0, "message": "All subscriptions already have role/is_admin/admin_id"}

    user_ids = list({s["user_id"] for s in subs_needing_backfill if s.get("user_id")})
    admin_users = await db.users.find(
        {"id": {"$in": user_ids}, "is_admin": True}, {"_id": 0, "id": 1}
    ).to_list(10000)
    admin_id_set = {str(u["id"]) for u in admin_users}

    updated = 0
    for sub in subs_needing_backfill:
        uid = sub.get("user_id", "")
        is_admin = uid in admin_id_set
        update_fields = {
            "role": "admin" if is_admin else "student",
            "is_admin": is_admin,
            "admin_id": uid if is_admin else "",
        }
        await db.push_subscriptions.update_one(
            {"_id": sub["_id"]}, {"$set": update_fields}
        )
        updated += 1
    return {"backfilled": updated}


# ─────────────────────────────────────────────
# PUSH DELIVERY STATUS TRACKING
# ─────────────────────────────────────────────

@router.get("/admin/push/delivery-log")
async def admin_push_delivery_log(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    admin: dict = Depends(get_admin_user),
):
    """Return recent push delivery log entries, newest first."""
    docs = await db.push_delivery_log.find(
        {}, {"_id": 0}
    ).sort("dispatched_at", -1).skip(offset).to_list(limit)
    total = await db.push_delivery_log.count_documents({})
    for d in docs:
        d.pop("results", None)
    return {"logs": docs, "total": total}


@router.get("/admin/push/delivery-log/{dispatch_id}")
async def admin_push_delivery_detail(
    dispatch_id: str = Path(...),
    admin: dict = Depends(get_admin_user),
):
    """Return full delivery detail for a single dispatch, including per-subscription results."""
    doc = await db.push_delivery_log.find_one(
        {"dispatch_id": dispatch_id}, {"_id": 0}
    )
    if not doc:
        raise HTTPException(404, "Delivery log not found")
    return doc


@router.get("/admin/push/subscriptions")
async def admin_push_subscriptions(
    admin: dict = Depends(get_admin_user),
):
    """Return all active push subscriptions with metadata (no keys exposed)."""
    subs = await db.push_subscriptions.find(
        {}, {"_id": 0, "subscription_info.keys": 0}
    ).to_list(10000)
    for s in subs:
        si = s.get("subscription_info", {})
        s["endpoint_domain"] = ""
        try:
            from urllib.parse import urlparse
            s["endpoint_domain"] = urlparse(si.get("endpoint", "")).netloc
        except Exception:
            pass
        s.pop("subscription_info", None)
    return {"subscriptions": subs, "total": len(subs)}


@router.get("/admin/push/delivery-stats")
async def admin_push_delivery_stats(
    days: int = Query(7, ge=1, le=90),
    admin: dict = Depends(get_admin_user),
):
    """Aggregate push delivery stats for the last N days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    docs = await db.push_delivery_log.find(
        {"dispatched_at": {"$gte": cutoff}},
        {"_id": 0, "sent": 1, "failed": 1, "expired": 1, "total": 1, "dispatched_at": 1}
    ).sort("dispatched_at", -1).to_list(5000)
    total_sent = sum(d.get("sent", 0) for d in docs)
    total_failed = sum(d.get("failed", 0) for d in docs)
    total_expired = sum(d.get("expired", 0) for d in docs)
    total_dispatches = len(docs)
    daily = {}
    for d in docs:
        day = d.get("dispatched_at", "")[:10]
        if day not in daily:
            daily[day] = {"date": day, "sent": 0, "failed": 0, "expired": 0, "dispatches": 0}
        daily[day]["sent"] += d.get("sent", 0)
        daily[day]["failed"] += d.get("failed", 0)
        daily[day]["expired"] += d.get("expired", 0)
        daily[day]["dispatches"] += 1
    return {
        "period_days": days,
        "total_dispatches": total_dispatches,
        "total_sent": total_sent,
        "total_failed": total_failed,
        "total_expired": total_expired,
        "daily": sorted(daily.values(), key=lambda x: x["date"]),
    }


# ─────────────────────────────────────────────
# EXAM REMINDER LOOP + ADMIN SCHEDULE
# ─────────────────────────────────────────────

async def _exam_reminder_loop():
    """
    Runs every 6 hours. Queries db.exam_schedule for exams 1 day, 3 days, or
    on the date of the exam (IST), then dispatches push notifications.
    Wakes every 6 hours so it never misses a window even after restart.
    """
    import zoneinfo
    from datetime import timedelta as _td
    if db is None:
        logger.info("Exam reminder loop disabled — MongoDB unavailable.")
        return
    IST = zoneinfo.ZoneInfo("Asia/Kolkata")
    await asyncio.sleep(30)   # let startup settle
    while True:
        try:
            now_ist   = datetime.now(IST)
            today_str = now_ist.date().isoformat()

            targets = {
                "today":      today_str,
                "1_day_away": (now_ist.date() + _td(days=1)).isoformat(),
                "3_day_away": (now_ist.date() + _td(days=3)).isoformat(),
            }

            exams = await db.exam_schedule.find(
                {"exam_date": {"$in": list(targets.values())}, "active": True},
                {"_id": 1, "board": 1, "class_name": 1, "subject": 1, "exam_date": 1, "notified_for": 1}
            ).to_list(200)

            for exam in exams:
                eid      = str(exam["_id"])
                board    = exam.get("board", "")
                subject  = exam.get("subject", "")
                klass    = exam.get("class_name", "")
                edate    = exam.get("exam_date", "")
                notified = set(exam.get("notified_for", []))

                trigger = None
                for label, dstr in targets.items():
                    if dstr == edate and label not in notified:
                        trigger = label
                        break

                if trigger is None:
                    continue

                if trigger == "today":
                    title = f"📋 {subject} exam is TODAY"
                    body  = f"{board} Class {klass} — Best of luck! You've got this."
                elif trigger == "1_day_away":
                    title = f"⏰ {subject} exam tomorrow"
                    body  = f"{board} Class {klass} — Quick revision time!"
                else:
                    title = f"📅 {subject} exam in 3 days"
                    body  = f"{board} Class {klass} — Keep revising!"

                asyncio.create_task(_dispatch_push_to_all({
                    "title": title,
                    "body":  body,
                    "icon":  "/icons/icon-192.png",
                    "url":   "/library",
                    "tag":   f"exam-{eid}-{trigger}",
                }))
                logger.info(f"Exam reminder dispatched: {subject} ({trigger})")

                await db.exam_schedule.update_one(
                    {"_id": exam["_id"]},
                    {"$addToSet": {"notified_for": trigger}}
                )

        except Exception as exc:
            logger.error(f"Exam reminder loop error: {exc}")

        await asyncio.sleep(6 * 3600)   # check every 6 hours


@router.get("/admin/exam-schedule")
async def admin_exam_schedule_list(admin: dict = Depends(get_admin_user)):
    """List all exam dates in the schedule."""
    items = await db.exam_schedule.find(
        {}, {"_id": 1, "board": 1, "class_name": 1, "subject": 1, "exam_date": 1, "active": 1, "notified_for": 1, "created_at": 1}
    ).sort("exam_date", 1).to_list(500)
    for i in items:
        i["id"] = str(i.pop("_id"))
    return {"exams": items}


@router.post("/admin/exam-schedule")
async def admin_exam_schedule_add(data: dict, admin: dict = Depends(get_admin_user)):
    """Add an exam date. Body: { board, class_name, subject, exam_date (YYYY-MM-DD) }"""
    board   = (data.get("board") or "").strip()
    klass   = (data.get("class_name") or "").strip()
    subject = (data.get("subject") or "").strip()
    edate   = (data.get("exam_date") or "").strip()
    if not all([board, klass, subject, edate]):
        raise HTTPException(400, "board, class_name, subject, and exam_date are required")
    try:
        datetime.strptime(edate, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, "exam_date must be YYYY-MM-DD")
    doc = {
        "board":        board,
        "class_name":   klass,
        "subject":      subject,
        "exam_date":    edate,
        "active":       data.get("active", True),
        "notified_for": [],
        "created_at":   datetime.now(timezone.utc).isoformat(),
    }
    result = await db.exam_schedule.insert_one(doc)
    return {"id": str(result.inserted_id), "message": "Exam date added"}


@router.delete("/admin/exam-schedule/{exam_id}")
async def admin_exam_schedule_delete(exam_id: str, admin: dict = Depends(get_admin_user)):
    """Delete an exam date entry."""
    from bson import ObjectId as _ObjId
    try:
        oid = _ObjId(exam_id)
    except Exception:
        raise HTTPException(400, "Invalid exam_id")
    result = await db.exam_schedule.delete_one({"_id": oid})
    if result.deleted_count == 0:
        raise HTTPException(404, "Exam not found")
    return {"ok": True}


@router.patch("/admin/exam-schedule/{exam_id}")
async def admin_exam_schedule_toggle(exam_id: str, data: dict, admin: dict = Depends(get_admin_user)):
    """Toggle active flag or reset notification history for an exam entry."""
    from bson import ObjectId as _ObjId
    try:
        oid = _ObjId(exam_id)
    except Exception:
        raise HTTPException(400, "Invalid exam_id")
    update = {}
    if "active" in data:
        update["active"] = bool(data["active"])
    if data.get("reset_notifications"):
        update["notified_for"] = []
    if not update:
        raise HTTPException(400, "Nothing to update")
    await db.exam_schedule.update_one({"_id": oid}, {"$set": update})
    return {"ok": True}


# ─────────────────────────────────────────────
# ADMIN EXPORT — CSV/JSON
# ─────────────────────────────────────────────
import io as _io

@router.get("/admin/export/users")
async def admin_export_users(format: str = "json", admin: dict = Depends(get_admin_user)):
    users = await supa_list_users()
    if format == "csv":
        if not users:
            return Response(content="", media_type="text/csv")
        output = _io.StringIO()
        writer = csv.DictWriter(output, fieldnames=[k for k in users[0].keys() if k != "password_hash"])
        writer.writeheader()
        for u in users:
            row = {k: v for k, v in u.items() if k != "password_hash"}
            writer.writerow(row)
        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=users_export.csv"},
        )
    return [({k: v for k, v in u.items() if k != "password_hash"}) for u in users]

@router.get("/admin/export/analytics")
async def admin_export_analytics(format: str = "json", days: int = 30, admin: dict = Depends(get_admin_user)):
    start = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    docs = await db.analytics.find({"timestamp": {"$gte": start}}, {"_id": 0}).sort("timestamp", -1).to_list(10000)
    if format == "csv" and docs:
        output = _io.StringIO()
        all_keys = sorted(set().union(*(d.keys() for d in docs)))
        writer = csv.DictWriter(output, fieldnames=all_keys)
        writer.writeheader()
        for d in docs:
            writer.writerow({k: d.get(k, "") for k in all_keys})
        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=analytics_export.csv"},
        )
    return docs

@router.get("/admin/export/conversations")
async def admin_export_conversations(format: str = "json", limit: int = 500, admin: dict = Depends(get_admin_user)):
    convs = await supa_get_all_conversations(limit)
    if format == "csv" and convs:
        output = _io.StringIO()
        keys = ["id", "user_id", "title", "subject_name", "created_at", "updated_at", "preview"]
        writer = csv.DictWriter(output, fieldnames=keys)
        writer.writeheader()
        for c in convs:
            writer.writerow({k: c.get(k, "") for k in keys})
        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=conversations_export.csv"},
        )
    return convs


# ─────────────────────────────────────────────
# BULK SEO GENERATION PROGRESS TRACKING
# ─────────────────────────────────────────────
_seo_generation_progress: Dict[str, dict] = {}

@router.get("/admin/seo/generation-progress")
async def seo_generation_progress(admin: dict = Depends(get_admin_user)):
    return _seo_generation_progress

@router.get("/admin/seo/generation-progress/{job_id}")
async def seo_generation_progress_detail(job_id: str, admin: dict = Depends(get_admin_user)):
    if job_id not in _seo_generation_progress:
        raise HTTPException(status_code=404, detail="Job not found")
    return _seo_generation_progress[job_id]


# ─────────────────────────────────────────────
# RATE LIMIT POLICIES
# ─────────────────────────────────────────────
DEFAULT_RATE_POLICIES = {
    "free":       {"req_per_min": 5,  "credits_per_day": 30,   "max_tokens": 10000,  "req_per_min_ip": 20},
    "starter":    {"req_per_min": 10, "credits_per_day": 500,  "max_tokens": 15000,  "req_per_min_ip": 30},
    "pro":        {"req_per_min": 15, "credits_per_day": 4000, "max_tokens": 20000,  "req_per_min_ip": 40},
    "enterprise": {"req_per_min": 60, "credits_per_day": 99999,"max_tokens": 200000, "req_per_min_ip": 200},
}

@router.get("/admin/rate-policies")
async def admin_get_rate_policies(admin: dict = Depends(get_admin_user)):
    saved = await db.rate_policies.find_one({}, {"_id": 0})
    return saved if saved else DEFAULT_RATE_POLICIES

@router.put("/admin/rate-policies")
async def admin_update_rate_policies(data: dict, admin: dict = Depends(get_admin_user)):
    await db.rate_policies.replace_one({}, data, upsert=True)
    return {"message": "Rate policies updated"}

@router.get("/admin/rate-stats")
async def admin_get_rate_stats(admin: dict = Depends(get_admin_user)):
    total_users = await supa_count_users()
    users = await supa_list_users()
    total_tokens = sum(u.get("credits_used", 0) * 300 for u in users)
    return {
        "active_requests": 0,
        "tokens_today": total_tokens,
        "daily_budget": 2_000_000,
        "cost_degraded": False,
    }


@router.get("/admin/notification-prefs")
async def admin_get_notification_prefs(admin: dict = Depends(get_admin_user)):
    admin_id = admin.get("sub") or admin.get("email") or "default"
    prefs = await get_admin_notification_prefs(admin_id)
    return prefs


@router.put("/admin/notification-prefs")
async def admin_update_notification_prefs(
    data: dict = Body(...),
    admin: dict = Depends(get_admin_user),
):
    admin_id = admin.get("sub") or admin.get("email") or "default"
    prefs = await upsert_admin_notification_prefs(admin_id, data)
    return prefs


@router.get("/admin/seo/daily-summary-dispatches")
async def admin_get_seo_daily_summary_dispatches(
    limit: int = 10,
    admin: dict = Depends(get_admin_user),
):
    """Task #474 — return the most-recent SEO auto-publish summary email
    dispatches so the admin notifications panel can show "Last summary sent
    Xh ago to N admins (M suppressed by quiet hours)" inline. Source rows
    are written by ``seo_engine._maybe_dispatch_seo_daily_summary`` after
    every scheduled auto-publish run. Manual runs are intentionally skipped
    upstream so this never reflects ad-hoc admin button clicks.
    """
    try:
        from seo_engine import get_recent_seo_summary_dispatches
    except Exception as exc:
        logger.debug(f"[admin] seo_engine import failed: {exc}")
        return {"dispatches": []}
    rows = await get_recent_seo_summary_dispatches(limit=limit)
    return {"dispatches": rows}


_CHIME_BUCKET = "study-materials"
_CHIME_PREFIX = "admin-chimes"
_CHIME_MAX_BYTES = 500 * 1024
_CHIME_ALLOWED_MIMES = {"audio/mpeg", "audio/wav", "audio/wave", "audio/x-wav", "audio/mp3"}


def _chime_supabase_upload(raw: bytes, storage_path: str, mime: str) -> str:
    from deps import supa
    supa.storage.from_(_CHIME_BUCKET).upload(
        path=storage_path,
        file=raw,
        file_options={"content-type": mime, "upsert": "true"},
    )
    return supa.storage.from_(_CHIME_BUCKET).get_public_url(storage_path)


def _chime_storage_path_from_url(url: str) -> str | None:
    if not url or url.startswith("data:"):
        return None
    prefix = f"/object/public/{_CHIME_BUCKET}/"
    idx = url.find(prefix)
    if idx == -1:
        return None
    return url[idx + len(prefix):]


def _chime_supabase_delete(storage_path: str):
    from deps import supa
    try:
        supa.storage.from_(_CHIME_BUCKET).remove([storage_path])
    except Exception as e:
        logger.debug(f"Failed to delete old chime from storage ({storage_path}): {e}")


async def _cleanup_old_chime(admin_id: str):
    from deps import supa
    if not supa:
        return
    prefs = await get_admin_notification_prefs(admin_id)
    old_url = prefs.get("custom_chime_url") or ""
    old_path = _chime_storage_path_from_url(old_url)
    if old_path and old_path.startswith(f"{_CHIME_PREFIX}/"):
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: _chime_supabase_delete(old_path)
        )


@router.post("/admin/notification-prefs/upload-chime")
async def admin_upload_custom_chime(
    file: UploadFile = File(...),
    admin: dict = Depends(get_admin_user),
):
    from deps import supa
    raw = await file.read()
    mime = (file.content_type or "").lower()
    if mime not in _CHIME_ALLOWED_MIMES:
        raise HTTPException(400, "Only MP3 and WAV files are supported")
    if len(raw) > _CHIME_MAX_BYTES:
        raise HTTPException(413, f"File exceeds {_CHIME_MAX_BYTES // 1024} KB limit")

    admin_id = admin.get("sub") or admin.get("email") or "default"

    await _cleanup_old_chime(admin_id)

    ext = "mp3" if "mp3" in mime or "mpeg" in mime else "wav"
    safe_id = str(uuid.uuid4())
    storage_path = f"{_CHIME_PREFIX}/{admin_id}/{safe_id}.{ext}"
    original_name = (file.filename or f"chime.{ext}")[:100]

    url = None
    if supa:
        try:
            url = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: _chime_supabase_upload(raw, storage_path, mime),
            )
        except Exception as e:
            logger.warning(f"Custom chime Supabase upload failed: {e}")

    if not url:
        b64 = base64.b64encode(raw).decode()
        url = f"data:{mime};base64,{b64}"

    prefs = await upsert_admin_notification_prefs(admin_id, {
        "chime_tone": "custom",
        "custom_chime_url": url,
        "custom_chime_filename": original_name,
    })
    return prefs


@router.delete("/admin/notification-prefs/custom-chime")
async def admin_delete_custom_chime(
    admin: dict = Depends(get_admin_user),
):
    admin_id = admin.get("sub") or admin.get("email") or "default"

    await _cleanup_old_chime(admin_id)

    prefs = await upsert_admin_notification_prefs(admin_id, {
        "chime_tone": "default",
        "custom_chime_url": None,
        "custom_chime_filename": None,
    })
    return prefs


@router.post("/admin/push/cleanup-orphan-chimes")
async def admin_cleanup_orphan_chimes(admin: dict = Depends(get_admin_user)):
    from deps import supa
    if not supa:
        raise HTTPException(503, "Supabase storage not available")

    all_prefs = await db.admin_notification_prefs.find(
        {"custom_chime_url": {"$exists": True, "$ne": None}},
        {"_id": 0, "custom_chime_url": 1},
    ).to_list(10000)
    referenced_paths = set()
    for p in all_prefs:
        path = _chime_storage_path_from_url(p.get("custom_chime_url", ""))
        if path and path.startswith(f"{_CHIME_PREFIX}/"):
            referenced_paths.add(path)

    try:
        stored_files = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: supa.storage.from_(_CHIME_BUCKET).list(
                _CHIME_PREFIX, {"limit": 10000}
            ),
        )
    except Exception as e:
        logger.warning(f"Failed to list chime storage: {e}")
        stored_files = []

    all_paths = []
    if stored_files:
        for item in stored_files:
            name = item.get("name", "") if isinstance(item, dict) else str(item)
            if not name:
                continue
            full_path = f"{_CHIME_PREFIX}/{name}"
            if name.endswith("/") or item.get("id") is None:
                try:
                    sub_files = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda n=name: supa.storage.from_(_CHIME_BUCKET).list(
                            f"{_CHIME_PREFIX}/{n}", {"limit": 10000}
                        ),
                    )
                    for sf in (sub_files or []):
                        sf_name = sf.get("name", "") if isinstance(sf, dict) else str(sf)
                        if sf_name and sf.get("id") is not None:
                            all_paths.append(f"{_CHIME_PREFIX}/{name}/{sf_name}")
                except Exception as e:
                    logger.debug(f"Failed to list subfolder {_CHIME_PREFIX}/{name}: {e}")
            else:
                all_paths.append(full_path)

    orphaned = [p for p in all_paths if p not in referenced_paths]
    deleted = 0
    for op in orphaned:
        try:
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda path=op: supa.storage.from_(_CHIME_BUCKET).remove([path]),
            )
            deleted += 1
        except Exception as e:
            logger.debug(f"Failed to delete orphan chime {op}: {e}")

    return {
        "total_files": len(all_paths),
        "referenced": len(referenced_paths),
        "orphaned": len(orphaned),
        "deleted": deleted,
    }


@router.get("/admin/alert-settings")
async def admin_get_alert_settings(admin: dict = Depends(get_admin_user)):
    import metrics as _metrics_mod
    try:
        await _metrics_mod._load_alert_settings()
        return {
            "thresholds": {k: _metrics_mod._ALERT_THRESHOLDS.get(k, v) for k, v in _metrics_mod._ALERT_THRESHOLDS_DEFAULT.items()},
            "expiration": {k: _metrics_mod._alert_expiration.get(k, v) for k, v in _metrics_mod._ALERT_EXPIRATION_DEFAULT.items()},
            "notification_channels": {k: _metrics_mod._notification_channels.get(k, v) for k, v in _metrics_mod._NOTIFICATION_CHANNELS_DEFAULT.items()},
            # Task #418: per-channel last-success/last-error so admins can
            # confirm their integrations actually work without waiting for
            # a real incident.
            "channel_status": {k: dict(v) for k, v in _metrics_mod._channel_status.items()},
            "defaults": {
                "thresholds": _metrics_mod._ALERT_THRESHOLDS_DEFAULT,
                "expiration": _metrics_mod._ALERT_EXPIRATION_DEFAULT,
                "notification_channels": _metrics_mod._NOTIFICATION_CHANNELS_DEFAULT,
            },
        }
    except Exception as exc:
        logger.error(f"Failed to get alert settings: {exc}")
        raise HTTPException(status_code=500, detail="Failed to get alert settings")


@router.put("/admin/alert-settings")
async def admin_update_alert_settings(
    data: dict = Body(...),
    admin: dict = Depends(get_admin_user),
):
    import metrics as _metrics_mod
    thresholds = data.get("thresholds", {})
    expiration = data.get("expiration", {})
    notification_channels = data.get("notification_channels", {})
    validated_thresholds = {}
    _ZERO_ALLOWED_THRESHOLDS = {"auto_block_threshold", "auto_block_expiry_hours", "collection_growth_per_day"}
    for k, default_val in _metrics_mod._ALERT_THRESHOLDS_DEFAULT.items():
        if k in thresholds:
            try:
                val = float(thresholds[k])
                if k in _ZERO_ALLOWED_THRESHOLDS:
                    if val < 0:
                        raise ValueError("Must be zero or positive")
                else:
                    if val <= 0:
                        raise ValueError("Must be positive")
                validated_thresholds[k] = val
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail=f"Invalid value for threshold '{k}'")
    validated_expiration = {}
    if "enabled" in expiration:
        if not isinstance(expiration["enabled"], bool):
            raise HTTPException(status_code=400, detail="expiration.enabled must be a boolean")
        validated_expiration["enabled"] = expiration["enabled"]
    if "days" in expiration:
        try:
            days = int(expiration["days"])
            if days < 1 or days > 365:
                raise ValueError("Days must be 1-365")
            validated_expiration["days"] = days
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid value for expiration days (must be 1-365)")
    validated_channels = {}
    if "email" in notification_channels:
        email_val = str(notification_channels["email"]).strip()
        if email_val and "@" not in email_val:
            raise HTTPException(status_code=400, detail="Invalid email address for notification channel")
        validated_channels["email"] = email_val
    if "webhook_url" in notification_channels:
        wh_val = str(notification_channels["webhook_url"]).strip()
        if wh_val and not wh_val.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="Webhook URL must start with http:// or https://")
        validated_channels["webhook_url"] = wh_val
    if "seo_slack_enabled" in notification_channels:
        seo_val = notification_channels["seo_slack_enabled"]
        if not isinstance(seo_val, bool):
            raise HTTPException(status_code=400, detail="seo_slack_enabled must be a boolean")
        validated_channels["seo_slack_enabled"] = seo_val
    # Task #414: hydrate / stale-build alert Slack mute. Same shape as
    # seo_slack_enabled — admins can silence the new hydrate Slack card
    # without affecting email, persisted alerts, or browser push.
    if "hydrate_slack_enabled" in notification_channels:
        hyd_val = notification_channels["hydrate_slack_enabled"]
        if not isinstance(hyd_val, bool):
            raise HTTPException(status_code=400, detail="hydrate_slack_enabled must be a boolean")
        validated_channels["hydrate_slack_enabled"] = hyd_val
    # Task #660: separate recipient list for the Monday review-prompt
    # weekly digest, distinct from the incident-alert ``email`` channel.
    # Accept either an array of strings or a comma-separated string so
    # the admin UI can post whichever is convenient. Empty entries are
    # dropped, addresses are deduped case-insensitively while preserving
    # order, and any obviously-bogus value (no ``@``) is rejected with a
    # specific error so the form can show it inline.
    if "review_prompt_digest_emails" in notification_channels:
        raw_list = notification_channels["review_prompt_digest_emails"]
        if isinstance(raw_list, str):
            parts = [p.strip() for p in raw_list.split(",")]
        elif isinstance(raw_list, list):
            parts = []
            for item in raw_list:
                if item is None:
                    continue
                if not isinstance(item, str):
                    raise HTTPException(
                        status_code=400,
                        detail="review_prompt_digest_emails entries must be strings",
                    )
                parts.append(item.strip())
        elif raw_list is None:
            parts = []
        else:
            raise HTTPException(
                status_code=400,
                detail="review_prompt_digest_emails must be a list or comma-separated string",
            )
        seen_dg: set = set()
        cleaned_dg: list = []
        for entry in parts:
            if not entry:
                continue
            if "@" not in entry:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid review-prompt digest recipient: {entry!r}",
                )
            key = entry.lower()
            if key in seen_dg:
                continue
            seen_dg.add(key)
            cleaned_dg.append(entry)
        validated_channels["review_prompt_digest_emails"] = cleaned_dg
    try:
        existing = await db.api_config.find_one({}, {"_id": 0})
        if existing is None:
            existing = {}
        existing["alert_settings"] = {
            "thresholds": validated_thresholds,
            "expiration": validated_expiration,
            "notification_channels": validated_channels,
        }
        await db.api_config.replace_one({}, existing, upsert=True)
        await _metrics_mod._load_alert_settings()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Failed to update alert settings: {exc}")
        raise HTTPException(status_code=500, detail="Failed to update alert settings")


@router.get("/admin/alerts")
async def admin_get_alerts(
    limit: int = Query(50, ge=1, le=200),
    acknowledged: Optional[bool] = Query(None),
    alert_type: Optional[str] = Query(None, alias="type"),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    include_synthetic: bool = Query(False),
    admin: dict = Depends(get_admin_user),
):
    try:
        query: Dict[str, Any] = {}
        if acknowledged is not None:
            query["acknowledged"] = acknowledged
        if alert_type:
            query["type"] = alert_type
        if date_from or date_to:
            date_filter: Dict[str, str] = {}
            if date_from:
                date_filter["$gte"] = date_from
            if date_to:
                date_filter["$lte"] = date_to
            if date_filter:
                query["fired_at"] = date_filter
        # Task #426: hide synthetic test alerts (from "Test alert delivery"
        # button) from the dashboard feed by default so on-call admins don't
        # confuse them with real incidents. Opt in via ?include_synthetic=true.
        if not include_synthetic:
            query["synthetic"] = {"$ne": True}
        cursor = db.alerts.find(query).sort("fired_at", -1).limit(limit)
        alerts = []
        async for doc in cursor:
            doc["_id"] = str(doc["_id"])
            alerts.append(doc)
        return {"alerts": alerts, "total": len(alerts)}
    except Exception as exc:
        logger.error(f"Failed to fetch alerts: {exc}")
        raise HTTPException(status_code=500, detail="Failed to fetch alerts")


@router.get("/admin/alerts/unacknowledged-count")
async def admin_unacknowledged_alert_count(admin: dict = Depends(get_admin_user)):
    try:
        # Task #426: exclude synthetic test alerts from the bell badge count
        # so the "Test alert delivery" button doesn't inflate the indicator.
        count = await db.alerts.count_documents({
            "acknowledged": False,
            "synthetic": {"$ne": True},
        })
        return {"count": count}
    except Exception as exc:
        logger.debug(f"Failed to count unacknowledged alerts: {exc}")
        return {"count": 0}


@router.patch("/admin/alerts/{alert_id}/acknowledge")
async def admin_acknowledge_alert(
    alert_id: str,
    admin: dict = Depends(get_admin_user),
):
    from bson import ObjectId as _ObjId
    try:
        oid = _ObjId(alert_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid alert ID")
    result = await db.alerts.update_one(
        {"_id": oid},
        {"$set": {"acknowledged": True, "acknowledged_at": datetime.now(timezone.utc).isoformat(), "acknowledged_by": admin.get("email", "admin")}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"ok": True}


@router.patch("/admin/alerts/acknowledge-all")
async def admin_acknowledge_all_alerts(
    admin: dict = Depends(get_admin_user),
):
    result = await db.alerts.update_many(
        {"acknowledged": False},
        {"$set": {"acknowledged": True, "acknowledged_at": datetime.now(timezone.utc).isoformat(), "acknowledged_by": admin.get("email", "admin")}},
    )
    return {"ok": True, "modified": result.modified_count}


# ─────────────────────────────────────────────
# Task #987 — Visibility into suppressed alerts
# ─────────────────────────────────────────────
#
# `metrics._dispatch_alert` writes one row per (alert_type, target) to
# `db.alert_dispatch_log` whenever it claims a dispatch slot. While the
# row's `ts` is within the persistent cooldown window (6h, see
# `metrics._PERSISTENT_ALERT_COOLDOWN_S`) any retry of the same alert is
# silently dropped. Without surfacing that log, "no recent alerts" can
# mean either "everything is healthy" or "alert is being suppressed by
# cooldown" — admins need to be able to tell those two states apart.
#
# These endpoints expose the cooldown holds to the admin Alert History
# UI and let on-call operators release a specific dedup_key when they
# want a fresh alert to fire (e.g. while investigating an incident).
@router.get("/admin/alerts/cooldowns")
async def admin_get_alert_cooldowns(
    limit: int = Query(200, ge=1, le=1000),
    only_active: bool = Query(False),
    admin: dict = Depends(get_admin_user),
):
    """List dispatch-log rows so admins can see which alerts are
    currently being suppressed by the 6h persistent cooldown.

    Each row reports its dedup_key, alert_type, last fired_at, the
    cooldown expiry time, seconds remaining (0 once expired), and
    whether the cooldown is still actively suppressing retries.
    Sorted by `ts` desc so the freshest holds appear first.
    """
    try:
        # Lazy-import the cooldown window so this module doesn't take a
        # hard import dependency on metrics at startup.
        try:
            from metrics import _PERSISTENT_ALERT_COOLDOWN_S as _COOLDOWN_S
        except Exception:
            _COOLDOWN_S = 6 * 3600
        import time as _time_mod
        now_ts = _time_mod.time()
        cutoff = now_ts - _COOLDOWN_S
        query: Dict[str, Any] = {}
        if only_active:
            query["ts"] = {"$gte": cutoff}
        cursor = db.alert_dispatch_log.find(query).sort("ts", -1).limit(limit)
        rows = []
        active_count = 0
        async for doc in cursor:
            ts = doc.get("ts")
            fired_at = doc.get("fired_at")
            fired_at_iso = None
            if isinstance(fired_at, datetime):
                fired_at_iso = fired_at.astimezone(timezone.utc).isoformat()
            elif isinstance(fired_at, str):
                fired_at_iso = fired_at
            cooldown_expires_iso = None
            seconds_until_expires = 0
            active = False
            if isinstance(ts, (int, float)):
                expires_ts = ts + _COOLDOWN_S
                cooldown_expires_iso = datetime.fromtimestamp(
                    expires_ts, tz=timezone.utc
                ).isoformat()
                seconds_until_expires = max(0, int(expires_ts - now_ts))
                active = expires_ts > now_ts
            if active:
                active_count += 1
            rows.append({
                "dedup_key": doc.get("dedup_key"),
                "alert_type": doc.get("alert_type"),
                "fired_at": fired_at_iso,
                "ts": ts,
                "cooldown_expires_at": cooldown_expires_iso,
                "seconds_until_expires": seconds_until_expires,
                "active": active,
            })
        return {
            "cooldowns": rows,
            "total": len(rows),
            "active_count": active_count,
            "cooldown_window_seconds": int(_COOLDOWN_S),
            "now": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        logger.error(f"Failed to fetch alert cooldowns: {exc}")
        raise HTTPException(status_code=500, detail="Failed to fetch alert cooldowns")


@router.delete("/admin/alerts/cooldowns/{dedup_key:path}")
async def admin_release_alert_cooldown(
    dedup_key: str = Path(..., description="dedup_key of the alert_dispatch_log row to release"),
    admin: dict = Depends(get_admin_user),
):
    """Delete a specific alert_dispatch_log row so the next matching
    alert is allowed to fire immediately. Useful when investigating a
    live incident where the cooldown is hiding a recurring alert.

    Also drops the in-memory mirror in metrics so the in-process
    cooldown doesn't immediately re-suppress the next dispatch.
    """
    if not dedup_key:
        raise HTTPException(status_code=400, detail="Missing dedup_key")
    try:
        result = await db.alert_dispatch_log.delete_one({"dedup_key": dedup_key})
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Cooldown row not found")
        # Best-effort: also clear the in-memory cooldown mirror so the
        # next dispatch in this worker isn't blocked by the 30-min
        # _ALERT_COOLDOWN_S backstop. The dedup_key is `alert_type` for
        # generic alerts and `alert_type|<key>=<value>` for per-target
        # alerts, so split on the first `|` to recover the type.
        try:
            from metrics import _alert_last_fired
            alert_type = dedup_key.split("|", 1)[0]
            _alert_last_fired.pop(alert_type, None)
        except Exception as _mirror_exc:
            logger.debug(f"in-memory cooldown clear failed for {dedup_key}: {_mirror_exc}")
        return {"ok": True, "dedup_key": dedup_key, "released_by": admin.get("email", "admin")}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Failed to release alert cooldown {dedup_key}: {exc}")
        raise HTTPException(status_code=500, detail="Failed to release alert cooldown")


_BACKFILL_TYPE_TO_METRIC = {
    "high_error_rate": "error_rate_pct",
    "high_latency": "latency_p95_ms",
    "spoofed_bot_surge": "spoof_rpm",
    "high_fallback_rate": "fallback_rate_pct",
    "endpoint_down": "endpoint_down_minutes",
}

_BACKFILL_BODY_PARSERS = {
    "high_error_rate": re.compile(r"([\d.]+)%\s*errors"),
    "high_latency": re.compile(r"p95\s*=\s*(\d+)\s*ms", re.IGNORECASE),
    "spoofed_bot_surge": re.compile(r"([\d.]+)\s*spoofed\s*requests/min", re.IGNORECASE),
    "high_fallback_rate": re.compile(r"([\d.]+)%\s*of\s*last"),
    "endpoint_down": re.compile(r"failing\s*for\s*(\d+)\s*min", re.IGNORECASE),
}
_BACKFILL_ENDPOINT_RE = re.compile(r"Endpoint\s+(\S+)\s+has\s+been")


# ─────────────────────────────────────────────
# Task #433 — Auto-clean synthetic test alerts
# ─────────────────────────────────────────────
#
# The "Test alert delivery" admin button persists a synthetic alert into
# `db.alerts` (tagged `synthetic: true`) so admins can verify the full
# email + webhook + push + persistence pipeline. These rows are hidden
# from the dashboard feed (Task #426) but still accumulate forever.
#
# We prune them automatically with two complementary mechanisms:
#   1. A partial TTL index on `expires_at` (BSON Date) where
#      `synthetic: true`. New synthetic alerts written by `metrics.py`
#      stamp `expires_at = now + 7d`, so Mongo's TTL monitor cleans
#      them ~once/min after the deadline. Real alerts are unaffected.
#   2. A periodic cleanup loop that also catches legacy synthetic rows
#      written before the `expires_at` field existed (filter on the
#      string `fired_at` ISO timestamp). Loop is idempotent and safe to
#      run on every replica — it's just a `delete_many` against rows
#      that the TTL monitor would have evicted anyway.
_SYNTHETIC_ALERT_TTL_SECONDS = 7 * 24 * 3600
_SYNTHETIC_CLEANUP_INTERVAL_S = 6 * 3600


async def ensure_synthetic_alerts_ttl_index() -> None:
    """Create the partial TTL index on db.alerts so synthetic test
    alerts auto-expire ~7 days after they fire. Idempotent — safe to
    call on every startup."""
    if db is None:
        return
    try:
        await db.alerts.create_index(
            "expires_at",
            expireAfterSeconds=0,
            partialFilterExpression={"synthetic": True},
            name="synthetic_alerts_ttl",
        )
    except Exception as e:
        logger.warning(f"[alerts] synthetic TTL index create failed: {e}")


async def cleanup_synthetic_alerts(ttl_seconds: int = _SYNTHETIC_ALERT_TTL_SECONDS) -> int:
    """Delete synthetic alerts older than `ttl_seconds`. Catches legacy
    rows that don't carry the `expires_at` BSON Date by filtering on the
    string `fired_at` ISO timestamp (lexicographic compare works because
    we always write `datetime.now(tz.utc).isoformat()`). Returns the
    deleted count."""
    if db is None:
        return 0
    cutoff_dt = datetime.now(timezone.utc) - timedelta(seconds=ttl_seconds)
    cutoff_iso = cutoff_dt.isoformat()
    try:
        result = await db.alerts.delete_many({
            "synthetic": True,
            "$or": [
                {"expires_at": {"$lte": datetime.now(timezone.utc)}},
                {"expires_at": {"$exists": False}, "fired_at": {"$lte": cutoff_iso}},
            ],
        })
        return int(getattr(result, "deleted_count", 0) or 0)
    except Exception as e:
        logger.warning(f"[alerts] synthetic cleanup failed: {e}")
        return 0


async def _synthetic_alert_cleanup_loop() -> None:
    """Periodic loop — prunes stale synthetic alerts every 6 hours and
    logs the deleted count for observability. The TTL index does most
    of the work; this loop is a belt-and-suspenders safety net for
    legacy rows and for cases where the TTL monitor is paused."""
    # First sweep happens shortly after startup so a stale heap left
    # over from before this task is cleared promptly.
    await asyncio.sleep(60)
    while True:
        try:
            deleted = await cleanup_synthetic_alerts()
            if deleted:
                logger.info(f"[alerts] pruned {deleted} synthetic test alerts older than 7d")
        except Exception as e:
            logger.warning(f"[alerts] synthetic cleanup loop iteration failed: {e}")
        await asyncio.sleep(_SYNTHETIC_CLEANUP_INTERVAL_S)


@router.get("/admin/alerts/synthetic-cleanup")
async def admin_synthetic_cleanup_status(
    admin: dict = Depends(get_admin_user),
):
    """Inspect the synthetic test-alert backlog: total count, how many
    are past the 7d TTL window, and the oldest `fired_at` in the
    collection. Used by the admin dashboard to surface that auto-prune
    is healthy."""
    try:
        cutoff_iso = (datetime.now(timezone.utc) - timedelta(seconds=_SYNTHETIC_ALERT_TTL_SECONDS)).isoformat()
        total = await db.alerts.count_documents({"synthetic": True})
        expired = await db.alerts.count_documents({
            "synthetic": True,
            "$or": [
                {"expires_at": {"$lte": datetime.now(timezone.utc)}},
                {"expires_at": {"$exists": False}, "fired_at": {"$lte": cutoff_iso}},
            ],
        })
        oldest_doc = await db.alerts.find(
            {"synthetic": True}, {"fired_at": 1, "_id": 0},
        ).sort("fired_at", 1).limit(1).to_list(1)
        oldest_fired_at = oldest_doc[0]["fired_at"] if oldest_doc else None
        return {
            "ok": True,
            "ttl_seconds": _SYNTHETIC_ALERT_TTL_SECONDS,
            "total_synthetic": total,
            "expired": expired,
            "oldest_fired_at": oldest_fired_at,
        }
    except Exception as e:
        logger.warning(f"[alerts] synthetic cleanup status failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to read synthetic cleanup status")


@router.post("/admin/alerts/synthetic-cleanup")
async def admin_synthetic_cleanup_run(
    admin: dict = Depends(get_admin_user),
):
    """Manually trigger the synthetic-alert prune. Idempotent — re-runs
    the same `delete_many` the periodic loop runs."""
    deleted = await cleanup_synthetic_alerts()
    logger.info(f"[alerts] manual synthetic cleanup by {admin.get('email','admin')}: {deleted} deleted")
    return {"ok": True, "deleted": deleted}


# ─────────────────────────────────────────────
# AUTO-PRUNE DEAD PUSH SUBSCRIBERS (Task #435)
# ─────────────────────────────────────────────
#
# Subscriptions that return 404/410 from the push service are deleted
# inline by ``_dispatch_push`` (browser uninstalled / endpoint expired).
# But persistent non-recoverable failures — timeouts, repeated 5xx,
# transport errors — accumulate in db.push_delivery_log and pollute the
# active subscriber list, dragging down the per-channel health signal
# computed by ``metrics._recompute_push_channel_status`` (Task #427).
#
# This module aggregates db.push_delivery_log per endpoint, walks the
# results newest-first, and counts consecutive `failed` outcomes (a
# `sent` resets the counter; `expired` rows refer to subs already
# deleted inline so they're treated as a reset too). Subscriptions with
# >= ``_PUSH_PRUNE_FAIL_THRESHOLD`` consecutive failures are marked
# ``active=False`` so the dispatcher's filter (active != False) skips
# them. They remain in the collection so admins can audit why a
# browser was disabled, and a re-subscribe flips ``active`` back to
# True and clears the deactivation metadata.
_PUSH_PRUNE_FAIL_THRESHOLD = 5
_PUSH_PRUNE_LOOKBACK_DAYS = 7
_PUSH_PRUNE_INTERVAL_S = 6 * 3600


async def prune_dead_push_subscribers(
    fail_threshold: int = _PUSH_PRUNE_FAIL_THRESHOLD,
    lookback_days: int = _PUSH_PRUNE_LOOKBACK_DAYS,
) -> dict:
    """Mark push subscriptions with >= ``fail_threshold`` consecutive
    non-recoverable failures (in the last ``lookback_days``) as
    ``active=False``. Returns a summary dict::

        {
            "scanned_endpoints": int,
            "deactivated": int,
            "endpoints": [{"endpoint": str, "consecutive_failures": int}, ...],
            "fail_threshold": int,
            "lookback_days": int,
        }

    Idempotent: subs that are already inactive are skipped.
    """
    summary = {
        "scanned_endpoints": 0,
        "deactivated": 0,
        "endpoints": [],
        "fail_threshold": fail_threshold,
        "lookback_days": lookback_days,
    }
    try:
        cutoff_iso = (
            datetime.now(timezone.utc) - timedelta(days=lookback_days)
        ).isoformat()
        # Aggregate per-endpoint result statuses, newest-first. We sort
        # before $unwind so ``$push`` preserves order; using $unwind with
        # ``includeArrayIndex`` would also work but this keeps the
        # pipeline straightforward.
        pipeline = [
            {"$match": {
                "dispatched_at": {"$gte": cutoff_iso},
                "results": {"$exists": True, "$ne": []},
            }},
            {"$sort": {"dispatched_at": -1}},
            {"$unwind": "$results"},
            {"$match": {"results.endpoint": {"$nin": [None, ""]}}},
            {"$group": {
                "_id": "$results.endpoint",
                "statuses": {"$push": "$results.status"},
            }},
        ]
        cursor = db.push_delivery_log.aggregate(pipeline)
        endpoints_to_deactivate = []
        async for row in cursor:
            summary["scanned_endpoints"] += 1
            endpoint = row.get("_id")
            statuses = row.get("statuses") or []
            consecutive = 0
            for status in statuses:
                if status == "failed":
                    consecutive += 1
                else:
                    # ``sent`` proves the endpoint still works; ``expired``
                    # rows refer to subs already deleted inline by the
                    # dispatcher so the streak doesn't apply.
                    break
            if consecutive >= fail_threshold and endpoint:
                endpoints_to_deactivate.append((endpoint, consecutive))

        now_iso = datetime.now(timezone.utc).isoformat()
        for endpoint, streak in endpoints_to_deactivate:
            res = await db.push_subscriptions.update_one(
                {"endpoint": endpoint, "active": {"$ne": False}},
                {"$set": {
                    "active": False,
                    "deactivated_at": now_iso,
                    "deactivation_reason": (
                        f"{streak} consecutive push failures "
                        f"(threshold {fail_threshold}) over last {lookback_days}d"
                    ),
                    "consecutive_failures_at_prune": streak,
                }},
            )
            if getattr(res, "modified_count", 0):
                summary["deactivated"] += 1
                summary["endpoints"].append({
                    "endpoint": endpoint,
                    "consecutive_failures": streak,
                })
    except Exception as exc:
        logger.warning(f"[push-prune] aggregation failed: {exc}")
    return summary


async def _push_prune_loop() -> None:
    """Background loop: prune dead push subscribers every 6h."""
    # Defer first sweep so startup work settles.
    await asyncio.sleep(5 * 60)
    while True:
        try:
            summary = await prune_dead_push_subscribers()
            if summary["deactivated"]:
                logger.info(
                    f"[push-prune] deactivated {summary['deactivated']} "
                    f"endpoint(s) (scanned {summary['scanned_endpoints']}, "
                    f"threshold {summary['fail_threshold']} consecutive failures)"
                )
        except Exception as exc:
            logger.warning(f"[push-prune] loop iteration failed: {exc}")
        await asyncio.sleep(_PUSH_PRUNE_INTERVAL_S)


@router.get("/admin/push/prune-dead")
async def admin_push_prune_dead_status(
    admin: dict = Depends(get_admin_user),
):
    """Inspect the auto-prune state: how many subs are currently marked
    inactive and the configured threshold/lookback."""
    try:
        total = await db.push_subscriptions.count_documents({})
        inactive = await db.push_subscriptions.count_documents({"active": False})
        recent_inactive = await db.push_subscriptions.find(
            {"active": False},
            {"_id": 0, "endpoint": 1, "deactivated_at": 1,
             "deactivation_reason": 1, "consecutive_failures_at_prune": 1,
             "user_id": 1, "role": 1},
        ).sort("deactivated_at", -1).limit(50).to_list(50)
        for s in recent_inactive:
            ep = s.get("endpoint", "")
            try:
                from urllib.parse import urlparse
                s["endpoint_domain"] = urlparse(ep).netloc
            except Exception:
                s["endpoint_domain"] = ""
            # Don't leak full endpoint URL in the dashboard.
            s.pop("endpoint", None)
        return {
            "ok": True,
            "fail_threshold": _PUSH_PRUNE_FAIL_THRESHOLD,
            "lookback_days": _PUSH_PRUNE_LOOKBACK_DAYS,
            "interval_seconds": _PUSH_PRUNE_INTERVAL_S,
            "total_subscriptions": total,
            "inactive_subscriptions": inactive,
            "recent_inactive": recent_inactive,
        }
    except Exception as e:
        logger.warning(f"[push-prune] status read failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to read push-prune status")


@router.post("/admin/push/prune-dead")
async def admin_push_prune_dead_run(
    admin: dict = Depends(get_admin_user),
):
    """Manually trigger the dead-push-subscriber prune. Idempotent."""
    summary = await prune_dead_push_subscribers()
    logger.info(
        f"[push-prune] manual run by {admin.get('email','admin')}: "
        f"deactivated={summary['deactivated']} scanned={summary['scanned_endpoints']}"
    )
    return {"ok": True, **summary}


@router.post("/admin/alerts/backfill-thresholds")
async def admin_backfill_thresholds(
    admin: dict = Depends(get_admin_user),
):
    import metrics as _metrics_mod

    configured = dict(_metrics_mod._ALERT_THRESHOLDS_DEFAULT)
    try:
        cfg = await db.api_config.find_one({}, {"_id": 0})
        if cfg and "alert_settings" in cfg:
            saved = cfg["alert_settings"].get("thresholds", {})
            for k in _metrics_mod._ALERT_THRESHOLDS_DEFAULT:
                if k in saved:
                    try:
                        configured[k] = float(saved[k])
                    except (ValueError, TypeError):
                        pass
    except Exception:
        pass

    query = {"threshold_snapshot": {"$exists": False}}
    total = await db.alerts.count_documents(query)
    if total == 0:
        return {"ok": True, "total": 0, "updated": 0, "skipped": 0, "by_type": {}}

    updated = 0
    skipped = 0
    by_type = {}

    async for alert in db.alerts.find(query):
        alert_type = alert.get("type", "")
        body = alert.get("body", "")
        metric = _BACKFILL_TYPE_TO_METRIC.get(alert_type)

        if not metric:
            skipped += 1
            continue

        threshold_value = configured.get(metric, _metrics_mod._ALERT_THRESHOLDS_DEFAULT.get(metric))

        actual = None
        parser = _BACKFILL_BODY_PARSERS.get(alert_type)
        if parser:
            m = parser.search(body or "")
            if m:
                try:
                    val = float(m.group(1))
                    actual = int(val) if alert_type in ("high_latency", "spoofed_bot_surge", "endpoint_down") else round(val, 1)
                except (ValueError, IndexError):
                    pass

        snapshot = {"metric": metric, "value": threshold_value}
        if actual is not None:
            snapshot["actual"] = actual

        if alert_type == "endpoint_down":
            ep_m = _BACKFILL_ENDPOINT_RE.search(body or "")
            if ep_m:
                snapshot["endpoint"] = ep_m.group(1)

        await db.alerts.update_one(
            {"_id": alert["_id"]},
            {"$set": {"threshold_snapshot": snapshot}},
        )
        updated += 1
        by_type[alert_type] = by_type.get(alert_type, 0) + 1

    return {"ok": True, "total": total, "updated": updated, "skipped": skipped, "by_type": by_type}

