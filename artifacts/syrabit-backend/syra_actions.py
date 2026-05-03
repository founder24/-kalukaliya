"""Task #298 — Syra write-action registry.

Mirrors the destructive / mutating buttons that already live in the
admin UI so the orb can request them by id. Every executor is async,
admin-gated by the calling route, and returns a short human-readable
summary that Syra can speak back. Destructive actions MUST be marked
``destructive=True`` so the frontend renders the confirm card.

Keeping the registry centralised (instead of letting Syra freeform
arbitrary HTTP) gives us:
* an explicit allowlist (the LLM can't invent endpoints);
* an audit trail seam (each executor logs to ``activity_log``);
* a single place to add new admin verbs as the panel grows.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# ── Registry types ──────────────────────────────────────────────────────────
ActionParams = dict[str, Any]
ActionExecutor = Callable[[dict, ActionParams], Awaitable[str]]


@dataclass
class SyraAction:
    id: str
    label: str
    destructive: bool
    params: list[str] = field(default_factory=list)
    executor: ActionExecutor | None = None
    summary: str = ""

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "destructive": self.destructive,
            "params": list(self.params),
            "summary": self.summary or self.label,
        }


_REGISTRY: dict[str, SyraAction] = {}


def register(action: SyraAction) -> SyraAction:
    _REGISTRY[action.id] = action
    return action


def list_actions() -> list[dict[str, Any]]:
    return [a.to_public() for a in _REGISTRY.values()]


def get_action(action_id: str) -> SyraAction | None:
    return _REGISTRY.get(action_id)


# ── Audit helper ────────────────────────────────────────────────────────────
async def _audit(admin: dict, action: SyraAction, params: ActionParams, result: str) -> None:
    """Best-effort write to the activity_log Mongo collection. Never
    raises — Syra actions must continue to function even if logging is
    unavailable."""
    try:  # pragma: no cover — exercised through integration paths
        from deps import db  # type: ignore

        await db.activity_log.insert_one({
            "action": f"syra:{action.id}",
            "details": {
                "label": action.label,
                "params": params,
                "result": result[:400],
                "destructive": action.destructive,
            },
            "admin_email": admin.get("email") or admin.get("username"),
            "admin_name": admin.get("name") or admin.get("username") or "Admin",
            "level": "danger" if action.destructive else "info",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "via": "syra",
        })
    except Exception as exc:  # pragma: no cover
        logger.debug("syra audit failed for %s: %s", action.id, exc)


# ── Built-in executors ──────────────────────────────────────────────────────
async def _exec_user_status(admin: dict, params: ActionParams) -> str:
    from deps import supa  # type: ignore

    user_id = str(params.get("user_id") or "").strip()
    status = str(params.get("status") or "").strip().lower()
    if not user_id:
        raise ValueError("user_id is required")
    if status not in ("active", "suspended", "banned"):
        raise ValueError("status must be active|suspended|banned")
    supa.table("users").update({"status": status}).eq("id", user_id).execute()
    return f"User {user_id[:8]}… set to {status}."


async def _exec_user_plan(admin: dict, params: ActionParams) -> str:
    from deps import supa  # type: ignore

    user_id = str(params.get("user_id") or "").strip()
    plan = str(params.get("plan") or "").strip().lower()
    if not user_id:
        raise ValueError("user_id is required")
    if plan not in ("free", "starter", "pro"):
        raise ValueError("plan must be free|starter|pro")
    supa.table("users").update({"plan": plan}).eq("id", user_id).execute()
    return f"User {user_id[:8]}… moved to {plan} plan."


async def _exec_reset_quiz_quota(admin: dict, params: ActionParams) -> str:
    from deps import db  # type: ignore

    user_id = str(params.get("user_id") or "").strip()
    if not user_id:
        raise ValueError("user_id is required")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    res = await db.quiz_quota.delete_many({"user_id": user_id, "date": today})
    cleared = getattr(res, "deleted_count", 0) or 0
    return f"Cleared {cleared} quiz-quota entries for today."


async def _exec_acknowledge_alert(admin: dict, params: ActionParams) -> str:
    """Acknowledge a single alert by id. Mongo stores ``alerts._id`` as
    BSON ObjectId (matching ``routes/admin_notifications.acknowledge``);
    we mirror the same conversion + matched-count check so Syra never
    reports success for an id that doesn't exist."""
    from deps import db  # type: ignore
    from bson import ObjectId  # type: ignore

    alert_id = str(params.get("alert_id") or "").strip()
    if not alert_id:
        raise ValueError("alert_id is required")
    try:
        oid = ObjectId(alert_id)
    except Exception as exc:
        raise ValueError(f"Invalid alert id: {alert_id}") from exc
    res = await db.alerts.update_one(
        {"_id": oid},
        {"$set": {
            "acknowledged": True,
            "acknowledged_at": datetime.now(timezone.utc).isoformat(),
            "acknowledged_by": admin.get("email", "admin"),
        }},
    )
    if not getattr(res, "matched_count", 0):
        raise ValueError(f"Alert {alert_id} not found")
    return f"Alert {alert_id[:8]}… acknowledged."


async def _exec_acknowledge_all_alerts(admin: dict, params: ActionParams) -> str:
    from deps import db  # type: ignore

    res = await db.alerts.update_many(
        {"acknowledged": False},
        {"$set": {
            "acknowledged": True,
            "acknowledged_at": datetime.now(timezone.utc).isoformat(),
            "acknowledged_by": admin.get("email", "admin"),
        }},
    )
    n = getattr(res, "modified_count", 0) or 0
    return f"Acknowledged {n} open alerts."


async def _exec_flag_conversation(admin: dict, params: ActionParams) -> str:
    """Toggle ``flagged`` on a conversation. Conversations are keyed by
    a string ``id`` field (not ``_id``) — see ``routes/conversations.py``
    and how ``AdminConversations.jsx`` consumes ``c.id``. We refuse the
    op rather than silently no-oping when the id doesn't resolve."""
    from deps import db  # type: ignore

    conv_id = str(params.get("conversation_id") or "").strip()
    if not conv_id:
        raise ValueError("conversation_id is required")
    doc = await db.conversations.find_one({"id": conv_id})
    if not doc:
        raise ValueError(f"Conversation {conv_id} not found")
    new_val = not bool(doc.get("flagged"))
    res = await db.conversations.update_one({"id": conv_id}, {"$set": {"flagged": new_val}})
    if not getattr(res, "matched_count", 0):
        raise ValueError(f"Conversation {conv_id} not found")
    return ("Flagged" if new_val else "Unflagged") + f" conversation {conv_id[:8]}…"


async def _exec_purge_cache(admin: dict, params: ActionParams) -> str:
    try:
        from deps import db  # type: ignore
        await db.kv_cache.delete_many({})
    except Exception:  # pragma: no cover
        pass
    return "Cache purge requested."


async def _exec_user_credits(admin: dict, params: ActionParams) -> str:
    """Adjust a user's credit balance. Mirrors AdminUsers credits modal
    + ``adminUpdateUserCredits``. Positive ``delta`` grants, negative
    revokes; absolute floors at 0."""
    from deps import db  # type: ignore

    uid = str(params.get("user_id") or "").strip()
    delta = int(params.get("delta") or 0)
    if not uid:
        raise ValueError("user_id is required")
    if delta == 0:
        raise ValueError("delta must be non-zero")
    user = await db.users.find_one({"id": uid})
    if not user:
        raise ValueError(f"User {uid} not found")
    new_balance = max(0, int(user.get("credits") or 0) + delta)
    res = await db.users.update_one({"id": uid}, {"$set": {"credits": new_balance}})
    if not getattr(res, "matched_count", 0):
        raise ValueError(f"User {uid} not found")
    sign = "+" if delta > 0 else ""
    return f"Credits {sign}{delta} → {new_balance} for {user.get('email', uid)}."


async def _exec_resolve_alert(admin: dict, params: ActionParams) -> str:
    """Mark an alert resolved (distinct from acknowledged — operator has
    handled the underlying issue, not just seen it)."""
    from deps import db  # type: ignore
    from bson import ObjectId  # type: ignore

    aid = str(params.get("alert_id") or "").strip()
    if not aid:
        raise ValueError("alert_id is required")
    try:
        oid = ObjectId(aid)
    except Exception as exc:
        raise ValueError(f"Invalid alert id: {aid}") from exc
    res = await db.alerts.update_one(
        {"_id": oid},
        {"$set": {
            "acknowledged": True,
            "resolved": True,
            "resolved_at": datetime.now(timezone.utc).isoformat(),
            "resolved_by": admin.get("email", "admin"),
        }},
    )
    if not getattr(res, "matched_count", 0):
        raise ValueError(f"Alert {aid} not found")
    return f"Alert {aid[:8]}… resolved."


async def _exec_retry_failed_jobs(admin: dict, params: ActionParams) -> str:
    """Re-queue jobs that ended in ``failed`` state. Best-effort; the
    actual retry runner is owned by the worker process — we only flip
    the status so the next sweep picks them up."""
    from deps import db  # type: ignore

    job_type = str(params.get("job_type") or "").strip() or None
    query: dict[str, Any] = {"status": "failed"}
    if job_type:
        query["type"] = job_type
    res = await db.jobs.update_many(
        query,
        {"$set": {"status": "pending", "retry_requested_at": datetime.now(timezone.utc).isoformat()}},
    )
    n = getattr(res, "modified_count", 0) or 0
    scope = f" of type {job_type}" if job_type else ""
    return f"Re-queued {n} failed jobs{scope}."


async def _exec_toggle_maintenance(admin: dict, params: ActionParams) -> str:
    from deps import db  # type: ignore

    enable = bool(params.get("enable", True))
    await db.settings.update_one(
        {"_id": "global"},
        {"$set": {"maintenance_mode": enable}},
        upsert=True,
    )
    return f"Maintenance mode {'enabled' if enable else 'disabled'}."


# ── Registry seeding ────────────────────────────────────────────────────────
register(SyraAction(
    id="user.set_status",
    label="Change user status",
    destructive=True,
    params=["user_id", "status"],
    executor=_exec_user_status,
    summary="Ban, suspend or reactivate a user.",
))
register(SyraAction(
    id="user.set_plan",
    label="Change user plan",
    destructive=True,
    params=["user_id", "plan"],
    executor=_exec_user_plan,
    summary="Move a user between free/starter/pro.",
))
register(SyraAction(
    id="user.reset_quiz_quota",
    label="Reset today's quiz quota",
    destructive=False,
    params=["user_id"],
    executor=_exec_reset_quiz_quota,
    summary="Clear today's quiz-quota counter for a user.",
))
register(SyraAction(
    id="alert.acknowledge",
    label="Acknowledge alert",
    destructive=False,
    params=["alert_id"],
    executor=_exec_acknowledge_alert,
))
register(SyraAction(
    id="alert.acknowledge_all",
    label="Acknowledge all open alerts",
    destructive=True,
    params=[],
    executor=_exec_acknowledge_all_alerts,
))
register(SyraAction(
    id="conversation.flag",
    label="Flag / unflag conversation",
    destructive=False,
    params=["conversation_id"],
    executor=_exec_flag_conversation,
))
register(SyraAction(
    id="cache.purge_all",
    label="Purge all server caches",
    destructive=True,
    params=[],
    executor=_exec_purge_cache,
))
register(SyraAction(
    id="user.adjust_credits",
    label="Adjust user credits",
    destructive=True,
    params=["user_id", "delta"],
    executor=_exec_user_credits,
    summary="Grant or revoke credits on a user account.",
))
register(SyraAction(
    id="alert.resolve",
    label="Resolve alert",
    destructive=False,
    params=["alert_id"],
    executor=_exec_resolve_alert,
    summary="Mark an alert as resolved (handled), not just acknowledged.",
))
register(SyraAction(
    id="jobs.retry_failed",
    label="Retry failed jobs",
    destructive=True,
    params=["job_type"],
    executor=_exec_retry_failed_jobs,
    summary="Re-queue jobs in failed status (optionally filtered by type).",
))
register(SyraAction(
    id="settings.toggle_maintenance",
    label="Toggle maintenance mode",
    destructive=True,
    params=["enable"],
    executor=_exec_toggle_maintenance,
))


# ── Public dispatcher ───────────────────────────────────────────────────────
class SyraActionError(Exception):
    """Raised for client-facing validation problems (bad params, unknown id,
    missing confirmation on a destructive action). Routes translate to 400."""


async def execute(action_id: str, params: ActionParams, admin: dict, *, confirmed: bool = False) -> dict[str, Any]:
    """Run a registered action. Destructive actions require ``confirmed=True``
    so the route can defer dispatch until the operator has answered the
    confirm card. Returns ``{ok, summary, action_id}`` on success."""
    action = get_action(action_id)
    if action is None:
        raise SyraActionError(f"Unknown Syra action: {action_id}")
    if action.destructive and not confirmed:
        raise SyraActionError(f"Action '{action.label}' requires confirmation")
    if action.executor is None:  # pragma: no cover — guard for half-defined entries
        raise SyraActionError(f"Action '{action.id}' has no executor wired up")
    try:
        result = await action.executor(admin, params or {})
    except ValueError as exc:
        raise SyraActionError(str(exc))
    summary = result if isinstance(result, str) and result else action.label
    await _audit(admin, action, params or {}, summary)
    return {"ok": True, "action_id": action.id, "summary": summary}
