"""Backend shim for the ChatSession + RateLimiter Durable Objects.
Dispatches to the edge worker when ``DO_CHAT_ON`` is set; otherwise
serves the same API from an in-process dict + token bucket.

Public API: ``get_session``, ``put_session``, ``delete_session``,
``rate_check(key, limit, window_s) -> (allowed, remaining)``,
``snapshot()``."""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def is_enabled() -> bool:
    from config import DO_CHAT_ON
    return bool(DO_CHAT_ON)


# ── Counters surfaced to /admin/cf-health ───────────────────────────────────
_counters_lock = threading.Lock()
_counters: dict[str, int] = {
    "session_get_total": 0,
    "session_put_total": 0,
    "session_delete_total": 0,
    "rate_check_total": 0,
    "rate_check_blocked": 0,
    "do_requests_total": 0,
    "do_requests_failed": 0,
    "fallback_requests_total": 0,
}

# Task #430 — per-prefix breakdown of rate_check traffic. Keys match
# the prefix segment of the rate-check key (the part before the first
# ``:``), so callers using ``"signup:ip:..."`` flow into the
# ``"signup"`` bucket and chat callers using ``"chat:..."`` flow into
# ``"chat"``. Surfaced separately in ``snapshot()`` so on-call can
# tell a bot-signup wave apart from chat throttling on the same
# aggregate counter.
_counters_by_prefix_total: dict[str, int] = {}
_counters_by_prefix_blocked: dict[str, int] = {}

# Task #462 — rolling 1-hour window of blocked rate-checks per prefix.
# The lifetime ``_counters_by_prefix_blocked`` map is a process-uptime
# counter that resets every time an ACA revision rolls, so the admin
# panel can't tell "spike right now" apart from "pod restarted an hour
# ago". Ring buffer of 60 per-minute buckets keyed by prefix, each
# entry ``(minute_index, blocked_count)``. Stale buckets (older than
# the 60-minute window) are skipped on read and pruned on write so
# memory stays bounded at ≤60 entries × #prefixes.
_BLOCKED_WINDOW_MIN = 60
_blocked_buckets_by_prefix: dict[str, list[tuple[int, int]]] = {}


def _current_minute() -> int:
    return int(time.time() // 60)


def _prune_blocked_buckets_locked(prefix: str, now_min: int) -> list[tuple[int, int]]:
    """Drop buckets older than the rolling window. Caller holds ``_counters_lock``."""
    cutoff = now_min - _BLOCKED_WINDOW_MIN + 1
    buckets = [b for b in _blocked_buckets_by_prefix.get(prefix, []) if b[0] >= cutoff]
    if buckets:
        _blocked_buckets_by_prefix[prefix] = buckets
    else:
        _blocked_buckets_by_prefix.pop(prefix, None)
    return buckets


def _bump(name: str, n: int = 1) -> None:
    with _counters_lock:
        _counters[name] = _counters.get(name, 0) + n


def _prefix_of(key: str) -> str:
    """Extract the leading scope segment from a rate-check key.

    ``"signup:ip:1.2.3.4"`` → ``"signup"``;
    ``"chat:user:abc"``     → ``"chat"``;
    a bare key with no ``:`` separator becomes itself, so an
    accidentally-unscoped call still shows up rather than silently
    landing in a generic bucket.
    """
    if not key:
        return ""
    head, _sep, _rest = key.partition(":")
    return head or key


def _bump_prefix(key: str, *, blocked: bool) -> None:
    prefix = _prefix_of(key)
    if not prefix:
        return
    now_min = _current_minute()
    with _counters_lock:
        _counters_by_prefix_total[prefix] = (
            _counters_by_prefix_total.get(prefix, 0) + 1
        )
        if blocked:
            _counters_by_prefix_blocked[prefix] = (
                _counters_by_prefix_blocked.get(prefix, 0) + 1
            )
            buckets = _prune_blocked_buckets_locked(prefix, now_min)
            if buckets and buckets[-1][0] == now_min:
                buckets[-1] = (now_min, buckets[-1][1] + 1)
            else:
                buckets.append((now_min, 1))
            _blocked_buckets_by_prefix[prefix] = buckets


def snapshot() -> dict[str, Any]:
    """Counter snapshot for the cf-health route."""
    now_min = _current_minute()
    cutoff = now_min - _BLOCKED_WINDOW_MIN + 1
    with _counters_lock:
        out = {"enabled": is_enabled(), **_counters}
        out["rate_check_total_by_prefix"] = dict(_counters_by_prefix_total)
        out["rate_check_blocked_by_prefix"] = dict(_counters_by_prefix_blocked)
        last_hour: dict[str, int] = {}
        for prefix, buckets in _blocked_buckets_by_prefix.items():
            total = sum(count for minute, count in buckets if minute >= cutoff)
            if total:
                last_hour[prefix] = total
        out["rate_check_blocked_by_prefix_last_hour"] = last_hour
    out["block_ratio"] = (
        out["rate_check_blocked"] / out["rate_check_total"]
        if out["rate_check_total"] else 0.0
    )
    out["do_failure_ratio"] = (
        out["do_requests_failed"] / out["do_requests_total"]
        if out["do_requests_total"] else 0.0
    )
    return out


def reset() -> None:
    with _counters_lock:
        for k in list(_counters.keys()):
            _counters[k] = 0
        _counters_by_prefix_total.clear()
        _counters_by_prefix_blocked.clear()
        _blocked_buckets_by_prefix.clear()
    _local_sessions.clear()
    _local_buckets.clear()


# ── In-process fallback ─────────────────────────────────────────────────────
_local_lock = threading.Lock()
_local_sessions: dict[str, tuple[float, dict[str, Any]]] = {}
_local_buckets: dict[str, tuple[float, int]] = {}


def _local_get(session_id: str) -> dict[str, Any] | None:
    with _local_lock:
        ent = _local_sessions.get(session_id)
        if not ent:
            return None
        expires_at, payload = ent
        if expires_at and expires_at < time.time():
            _local_sessions.pop(session_id, None)
            return None
        return dict(payload)


def _local_put(session_id: str, payload: dict[str, Any], ttl: int) -> None:
    expires_at = (time.time() + ttl) if ttl > 0 else 0.0
    with _local_lock:
        _local_sessions[session_id] = (expires_at, dict(payload))


def _local_delete(session_id: str) -> bool:
    with _local_lock:
        return _local_sessions.pop(session_id, None) is not None


def _local_rate_check(key: str, limit: int, window_s: int) -> tuple[bool, int]:
    """Token-bucket-ish counter scoped to ``window_s``. Resets when the
    window rolls over rather than tracking per-request expiry — cheaper
    and matches the semantics of the DO implementation."""
    now = time.time()
    with _local_lock:
        ent = _local_buckets.get(key)
        if not ent or ent[0] < now:
            # New window
            _local_buckets[key] = (now + window_s, 1)
            return True, max(0, limit - 1)
        window_end, count = ent
        if count >= limit:
            return False, 0
        _local_buckets[key] = (window_end, count + 1)
        return True, max(0, limit - (count + 1))


# ── Edge dispatch (DO via edge-proxy) ───────────────────────────────────────
_DO_BASE = (
    os.environ.get("DO_CHAT_BASE_URL", "").strip().rstrip("/")
    or os.environ.get("EDGE_WORKER_URL", "").strip().rstrip("/")
)
_DO_SECRET = (
    os.environ.get("DO_CHAT_SHARED_SECRET", "").strip()
    or os.environ.get("DISPATCH_SHARED_SECRET", "").strip()
)
_DO_TIMEOUT_S = float(os.environ.get("DO_CHAT_TIMEOUT_S", "3.0") or "3.0")

_http: httpx.AsyncClient | None = None


def _client() -> httpx.AsyncClient:
    global _http
    if _http is None or _http.is_closed:
        _http = httpx.AsyncClient(
            timeout=_DO_TIMEOUT_S,
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
        )
    return _http


def _do_configured() -> bool:
    return bool(_DO_BASE and _DO_SECRET)


async def _do_request(method: str, path: str, *, json: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if not _do_configured():
        return None
    url = f"{_DO_BASE}{path}"
    headers = {
        "Authorization": f"Bearer {_DO_SECRET}",
        "Content-Type": "application/json",
    }
    _bump("do_requests_total")
    try:
        resp = await _client().request(method, url, headers=headers, json=json)
        if resp.status_code >= 400:
            _bump("do_requests_failed")
            logger.warning("do_chat: %s %s → %s", method, path, resp.status_code)
            return None
        return resp.json()
    except Exception as exc:
        _bump("do_requests_failed")
        logger.warning("do_chat: %s %s failed — %s", method, path, exc)
        return None


# ── Public API ──────────────────────────────────────────────────────────────
async def get_session(session_id: str) -> dict[str, Any] | None:
    if not session_id:
        return None
    _bump("session_get_total")
    if is_enabled() and _do_configured():
        data = await _do_request("GET", f"/do/chat-session/{session_id}")
        if data is not None:
            return data.get("session") if isinstance(data, dict) else None
        # Fall through to local on edge failure — don't lose chat state
        # because of a transient DO error.
    _bump("fallback_requests_total")
    return _local_get(session_id)


async def put_session(session_id: str, payload: dict[str, Any], ttl: int = 1800) -> bool:
    if not session_id:
        return False
    _bump("session_put_total")
    if is_enabled() and _do_configured():
        data = await _do_request(
            "PUT",
            f"/do/chat-session/{session_id}",
            json={"session": payload, "ttl": ttl},
        )
        if data is not None:
            # Mirror locally as a hot-cache so a subsequent get on the
            # same pod is free.
            _local_put(session_id, payload, ttl)
            return bool(data.get("ok", True))
    _bump("fallback_requests_total")
    _local_put(session_id, payload, ttl)
    return True


async def delete_session(session_id: str) -> bool:
    if not session_id:
        return False
    _bump("session_delete_total")
    if is_enabled() and _do_configured():
        data = await _do_request("DELETE", f"/do/chat-session/{session_id}")
        if data is not None:
            _local_delete(session_id)
            return bool(data.get("ok", True))
    _bump("fallback_requests_total")
    return _local_delete(session_id)


# Typing-indicator channel — backed by the ChatSession DO when
# DO_CHAT_ON, in-process dict otherwise. Poll-based so it works
# behind every CDN/proxy.
_local_typing: dict[str, tuple[float, dict[str, Any]]] = {}


async def get_typing(session_id: str) -> dict[str, Any]:
    if not session_id:
        return {"typing": False, "actor": "", "expires_at": 0}
    if is_enabled() and _do_configured():
        data = await _do_request("GET", f"/do/chat-session/{session_id}/typing")
        if isinstance(data, dict) and "typing" in data:
            return data
    _bump("fallback_requests_total")
    with _local_lock:
        ent = _local_typing.get(session_id)
    if not ent:
        return {"typing": False, "actor": "", "expires_at": 0}
    expires_at, payload = ent
    if expires_at and expires_at < time.time() * 1000:
        return {"typing": False, "actor": "", "expires_at": 0}
    return dict(payload)


async def put_typing(session_id: str, typing: bool, actor: str = "assistant", ttl_ms: int = 5000) -> bool:
    if not session_id:
        return False
    if is_enabled() and _do_configured():
        data = await _do_request(
            "PUT",
            f"/do/chat-session/{session_id}/typing",
            json={"typing": bool(typing), "actor": actor, "ttl_ms": int(ttl_ms)},
        )
        if isinstance(data, dict):
            with _local_lock:
                _local_typing[session_id] = (
                    (time.time() * 1000 + ttl_ms) if typing else 0.0,
                    {"typing": bool(typing), "actor": actor,
                     "expires_at": (int(time.time() * 1000) + int(ttl_ms)) if typing else 0},
                )
            return bool(data.get("ok", True))
    _bump("fallback_requests_total")
    expires_at_ms = (time.time() * 1000 + ttl_ms) if typing else 0.0
    with _local_lock:
        _local_typing[session_id] = (
            expires_at_ms,
            {"typing": bool(typing), "actor": actor,
             "expires_at": int(expires_at_ms)},
        )
    return True


async def rate_check(key: str, limit: int, window_s: int = 60) -> tuple[bool, int]:
    """Atomic increment+check — returns ``(allowed, remaining)``.

    ``key`` is typically ``"<feature>:<ip>"`` or ``"<feature>:<user_id>"``.
    """
    _bump("rate_check_total")
    if is_enabled() and _do_configured():
        data = await _do_request(
            "POST",
            "/do/rate-limiter/check",
            json={"key": key, "limit": limit, "window_s": window_s},
        )
        if isinstance(data, dict) and "allowed" in data:
            allowed = bool(data["allowed"])
            remaining = int(data.get("remaining") or 0)
            if not allowed:
                _bump("rate_check_blocked")
            _bump_prefix(key, blocked=not allowed)
            return allowed, remaining
    _bump("fallback_requests_total")
    allowed, remaining = _local_rate_check(key, limit, window_s)
    if not allowed:
        _bump("rate_check_blocked")
    _bump_prefix(key, blocked=not allowed)
    return allowed, remaining
