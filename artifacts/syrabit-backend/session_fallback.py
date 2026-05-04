"""Task #362 §3 — Per-session sticky model fallback.

When a single chat session sees K consecutive turns with TTFB above a
threshold, the session is "stuck" — usually because the upstream model
(typically Mistral via Workers AI, or a degraded paid path) is in a slow
window for that user's traffic shape. Flipping that *one* session to
Azure GPT-4.1-mini for the remainder of its lifetime recovers the user
within the next turn without affecting any other session, and is
distinct from the global ``chat:fallback`` Redis hot-flag (which is a
fleet-wide kill switch driven by the credit-burn meters).

Defaults (all overridable via env / runbook):

* ``SESSION_FALLBACK_K``                 — consecutive slow turns to trip (3)
* ``SESSION_FALLBACK_TTFB_MS``           — per-turn slow threshold ms (2400)
* ``SESSION_FALLBACK_PROVIDER``          — provider to swap into ("azure_openai")
* ``SESSION_FALLBACK_TTL_S``             — sticky-key TTL seconds (7200 = 2h)
* ``SESSION_FALLBACK_HERD_PCT``          — fraction of active sessions
                                            that may be swapped before the
                                            anti-thundering-herd guard
                                            disables further swaps (0.05)

Redis keys:

* ``session:ttfb:{session_id}``           — Redis list, capped at K, TTL 24h
* ``session:fallback:{session_id}``       — string = provider name; TTL = TTL_S
* ``session:fallback:disabled``           — kill-switch (set by herd guard or
                                            on-call); when "1" the dispatcher
                                            ignores all per-session swaps and
                                            no new swaps are written
* ``session:active``                      — sliding-window counter of unique
                                            active sessions in the last 5 min
                                            (used by the herd guard)

The module is **soft-fail**: any Redis exception logs a warning and the
caller continues with the normal weighted dispatch. We never block a
chat turn because the fallback bookkeeping failed.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────
SESSION_FALLBACK_K = int(os.environ.get("SESSION_FALLBACK_K", "3"))
SESSION_FALLBACK_TTFB_MS = int(os.environ.get("SESSION_FALLBACK_TTFB_MS", "2400"))
SESSION_FALLBACK_PROVIDER = os.environ.get("SESSION_FALLBACK_PROVIDER", "azure_openai").strip()
SESSION_FALLBACK_TTL_S = int(os.environ.get("SESSION_FALLBACK_TTL_S", "7200"))
SESSION_FALLBACK_HERD_PCT = float(os.environ.get("SESSION_FALLBACK_HERD_PCT", "0.05"))
SESSION_TTFB_KEY_TTL_S = int(os.environ.get("SESSION_TTFB_KEY_TTL_S", str(24 * 3600)))

_TTFB_KEY = "session:ttfb:{sid}"
_SWAP_KEY = "session:fallback:{sid}"
_DISABLED_KEY = "session:fallback:disabled"
_ACTIVE_KEY = "session:active"


def _r():
    """Return the shared Upstash Redis client, or None when unavailable."""
    try:
        from deps import redis_client
        return redis_client
    except Exception:
        return None


def _swap_disabled() -> bool:
    r = _r()
    if not r:
        return False
    try:
        return r.get(_DISABLED_KEY) == "1"
    except Exception as exc:  # pragma: no cover — soft-fail path
        logger.warning("session_fallback: disabled-flag read failed: %s", exc)
        return False


def get_session_swap(session_id: str) -> Optional[str]:
    """Return the provider this session has been swapped to, or None."""
    if not session_id:
        return None
    if _swap_disabled():
        return None
    r = _r()
    if not r:
        return None
    try:
        val = r.get(_SWAP_KEY.format(sid=session_id))
        return val or None
    except Exception as exc:  # pragma: no cover
        logger.warning("session_fallback: swap-read failed sid=%s: %s", session_id, exc)
        return None


def _set_session_swap(session_id: str, provider: str) -> bool:
    r = _r()
    if not r:
        return False
    try:
        r.set(_SWAP_KEY.format(sid=session_id), provider, ex=SESSION_FALLBACK_TTL_S)
        logger.warning(
            "session_fallback: SWAP sid=%s → %s (ttl=%ss; K=%d slow turns at >%dms)",
            session_id, provider, SESSION_FALLBACK_TTL_S,
            SESSION_FALLBACK_K, SESSION_FALLBACK_TTFB_MS,
        )
        return True
    except Exception as exc:  # pragma: no cover
        logger.warning("session_fallback: swap-write failed sid=%s: %s", session_id, exc)
        return False


def _record_active_session(session_id: str) -> None:
    """Track this session as active in the last 5-min window (herd-guard input)."""
    r = _r()
    if not r:
        return
    try:
        now_minute = int(time.time() // 60)
        bucket = f"{_ACTIVE_KEY}:{now_minute}"
        r.sadd(bucket, session_id)
        r.expire(bucket, 360)  # 6 min — covers the 5-min sliding window
    except Exception:  # pragma: no cover
        pass


def _count_active_sessions() -> int:
    """Approximate count of sessions seen in the last 5 minutes."""
    r = _r()
    if not r:
        return 0
    try:
        now_minute = int(time.time() // 60)
        total = 0
        seen: set = set()
        for offset in range(5):
            bucket = f"{_ACTIVE_KEY}:{now_minute - offset}"
            members = r.smembers(bucket) or []
            for m in members:
                if m not in seen:
                    seen.add(m)
                    total += 1
        return total
    except Exception:  # pragma: no cover
        return 0


def _count_active_swaps() -> int:
    """Count outstanding session swaps (used by the herd guard)."""
    r = _r()
    if not r:
        return 0
    try:
        n = 0
        cursor = 0
        # upstash_redis exposes scan as `scan(cursor, match=..., count=...)`
        while True:
            res = r.scan(cursor, match="session:fallback:*", count=200)
            # upstash_redis returns [next_cursor, [keys]]; tolerate both shapes.
            try:
                cursor, keys = int(res[0]), res[1]
            except Exception:
                cursor, keys = 0, res or []
            for k in keys:
                if k == _DISABLED_KEY:
                    continue
                n += 1
            if not cursor:
                break
        return n
    except Exception:  # pragma: no cover
        return 0


def _herd_guard_should_block(active_sessions: int) -> bool:
    """Return True when too many sessions have already swapped (anti-thunder).

    Triggered when active swaps exceed ``SESSION_FALLBACK_HERD_PCT`` of the
    sessions seen in the last 5 minutes. When tripped, sets the
    ``session:fallback:disabled`` kill-switch so subsequent dispatches
    bypass per-session fallbacks until on-call clears it.
    """
    if active_sessions < 20:
        # Too few sessions for the percentage to be meaningful — never trip
        # the guard on a tiny absolute number (would block the very first
        # legitimate stuck-session swap on a quiet day).
        return False
    swaps = _count_active_swaps()
    if active_sessions <= 0:
        return False
    pct = swaps / active_sessions
    if pct >= SESSION_FALLBACK_HERD_PCT:
        r = _r()
        if r:
            try:
                r.set(_DISABLED_KEY, "1", ex=900)  # 15 min cool-down
            except Exception:  # pragma: no cover
                pass
        logger.error(
            "session_fallback: HERD GUARD TRIPPED — %d swaps across %d active sessions "
            "(%.1f%% ≥ %.1f%%). Auto-disabled per-session fallbacks for 15 min. "
            "Likely upstream degradation; investigate before re-enabling.",
            swaps, active_sessions, pct * 100, SESSION_FALLBACK_HERD_PCT * 100,
        )
        return True
    return False


def record_turn_ttfb(session_id: str, ttfb_ms: float) -> None:
    """Append this turn's TTFB to the rolling per-session list and trip the
    swap if K consecutive turns all exceed the threshold.

    Called once per chat turn from the dispatcher's post-call path.
    """
    if not session_id or ttfb_ms <= 0:
        return
    if _swap_disabled():
        return
    r = _r()
    if not r:
        return
    _record_active_session(session_id)
    # If already swapped, nothing more to do — sticky for the session.
    if get_session_swap(session_id):
        return
    key = _TTFB_KEY.format(sid=session_id)
    try:
        r.rpush(key, str(int(ttfb_ms)))
        r.ltrim(key, -SESSION_FALLBACK_K, -1)
        r.expire(key, SESSION_TTFB_KEY_TTL_S)
        last = r.lrange(key, 0, -1) or []
    except Exception as exc:  # pragma: no cover
        logger.warning("session_fallback: ttfb-record failed sid=%s: %s", session_id, exc)
        return
    if len(last) < SESSION_FALLBACK_K:
        return
    try:
        ints = [int(x) for x in last]
    except (TypeError, ValueError):
        return
    if not all(v >= SESSION_FALLBACK_TTFB_MS for v in ints):
        return
    # All K turns slow — check herd guard then swap.
    active = _count_active_sessions()
    if _herd_guard_should_block(active):
        return
    _set_session_swap(session_id, SESSION_FALLBACK_PROVIDER)


__all__ = [
    "SESSION_FALLBACK_K",
    "SESSION_FALLBACK_TTFB_MS",
    "SESSION_FALLBACK_PROVIDER",
    "SESSION_FALLBACK_TTL_S",
    "SESSION_FALLBACK_HERD_PCT",
    "get_session_swap",
    "record_turn_ttfb",
]
