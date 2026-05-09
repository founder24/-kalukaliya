"""Task #360 — operational singletons for the credit-burn meters.

This module provides the live wiring layer between the FastAPI chat
handler and the pure-logic ``credit_burn_meter`` classes. The chat
handler imports two functions from here on every request:

  * ``increment_chat_request()`` — bumps Meter A (daily call counter)
    and records a tick into Meter B (RPM-headroom sliding window).
  * ``is_fallback_active()`` — returns ``True`` when the shared
    ``chat:fallback`` hot-flag is currently set (either meter tripped).

Background ingestion of Meter C (cumulative-cost notify-only over a
365-day window) lives in ``cron/credit_burn_meter_c_ingest.py`` and is
called from the daily admin-billing flusher.

The singletons are constructed lazily on first use against
``deps.redis_client``. If Redis is unavailable, the meters fall back to
an in-process state so the import side never raises (the chat handler
must not crash because Redis is down — it just won't share state across
replicas in that case).
"""
from __future__ import annotations

import logging
import threading
import time as _time
from typing import Optional

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_METERS_INIT = False
_METER_A = None
_METER_B = None
_METER_C = None
_METER_D = None  # Task #513 §J — global monthly USD cap (Rule D).
_FLAG = None
_LAST_B_TICK_LOG = 0.0


def _alert_sink(severity: str, message: str, context: dict) -> None:
    """Default alert sink — logs at WARNING for the SRE pager. Replace
    via ``set_alert_sink()`` for SES/PagerDuty integration.

    Signature MUST match ``credit_burn_meter.AlertSink`` exactly:
    ``(severity, message, context)`` — round-5 reviewer caught a
    mismatch that swallowed alerts as TypeErrors.
    """
    log = logger.error if severity == "critical" else logger.warning
    log("[credit-burn-alert] severity=%s message=%s context=%s",
        severity, message, context)


_ALERT_SINK = _alert_sink


def set_alert_sink(sink) -> None:
    global _ALERT_SINK
    _ALERT_SINK = sink


class _InMemoryRedis:
    """Single-process degraded fallback for ``deps.redis_client`` when
    Redis is unavailable. Implements only the subset of the redis-py
    API used by ``credit_burn_meter`` (``get/set/delete/incr/expire``).
    Round-6 reviewer requirement: meter runtime must NOT silently
    no-op when Redis is down — keep a working in-memory store and
    emit a clear health alert so SREs see the degradation."""

    def __init__(self) -> None:
        self._kv: dict = {}

    def get(self, key):
        v = self._kv.get(key)
        if v is None:
            return None
        return v if isinstance(v, (bytes, bytearray)) else str(v).encode("utf-8")

    def set(self, key, value, ex=None):
        self._kv[key] = value
        return True

    def delete(self, key):
        return self._kv.pop(key, None) is not None

    def incr(self, key):
        cur = int(self._kv.get(key) or 0) + 1
        self._kv[key] = cur
        return cur

    def expire(self, key, ttl):
        return key in self._kv


def _ensure_meters() -> None:
    global _METERS_INIT, _METER_A, _METER_B, _METER_C, _METER_D, _FLAG
    if _METERS_INIT:
        return
    with _LOCK:
        if _METERS_INIT:
            return
        try:
            from credit_burn_meter import (
                FallbackFlag, MeterA, MeterB, MeterC, MeterD, MeterDConfig,
            )
            from cost_caps import _monthly_total_usd_cap
            try:
                from deps import redis_client as _r
            except Exception:
                _r = None
            if _r is None:
                logger.warning(
                    "[credit-burn] deps.redis_client unavailable — "
                    "using in-memory degraded store (state will NOT "
                    "be shared across replicas)",
                )
                _ALERT_SINK("warning",
                            "credit_burn_meter degraded: in-memory store",
                            {"reason": "redis_client_none"})
                _r = _InMemoryRedis()
            _FLAG = FallbackFlag(_r)
            _METER_A = MeterA(_r, _FLAG, _ALERT_SINK)
            _METER_B = MeterB(_r, _FLAG, _ALERT_SINK)
            _METER_C = MeterC(_ALERT_SINK)
            # Task #513 §J — Rule D ($500/month default, env-overridable
            # via MONTHLY_TOTAL_USD_CAP). Shares the same Redis client
            # so the chat tier-router (`_select_chat_model`) can read
            # `chat:cheaponly` from any replica without coordination.
            _METER_D = MeterD(
                _r, _ALERT_SINK,
                MeterDConfig(cap_usd=_monthly_total_usd_cap()),
            )
        except Exception as _exc:
            logger.warning(
                "[credit-burn] meter init failed (%s) — chat will run "
                "without meter wiring", _exc,
            )
            _METER_A = None
            _METER_B = None
            _METER_C = None
            _METER_D = None
            _FLAG = None
        _METERS_INIT = True


def ingest_daily_cost_usd(usd: float) -> Optional[float]:
    """Task #360 — Meter C ingestion hook. Called by the daily admin
    billing flusher (``cron/daily_credit_cost_ingest.py``) with the
    previous day's total spend in USD across all paid providers
    (Vertex / Azure / Sarvam / Workers AI). Notify-only — never flips
    the chat fallback flag. Returns the new 365-day cumulative cost,
    or None when meters aren't initialised.
    """
    _ensure_meters()
    # Task #513 §J — fan out the SAME dollars to Meter D in the same
    # call so any caller that already feeds C (chat hot path, daily
    # billing cron, admin reconciliation) automatically feeds D too.
    # This satisfies the reviewer's "Integrate ingest_meter_d_usd()
    # into the existing billing ingestion pipeline (same path
    # feeding Meter C)" requirement and ensures Rule D's
    # `chat:cheaponly` lock can fire on real traffic.
    try:
        ingest_meter_d_usd(float(usd))
    except Exception as _d_exc:
        logger.debug("[credit-burn] meter D fan-out failed: %s", _d_exc)
    if _METER_C is None:
        return None
    try:
        return _METER_C.record(float(usd))
    except Exception as _exc:
        logger.debug("[credit-burn] meter C ingest failed: %s", _exc)
        return None


def current_cumulative_cost_usd() -> Optional[float]:
    _ensure_meters()
    if _METER_C is None:
        return None
    try:
        return _METER_C.current()
    except Exception:
        return None


def increment_chat_request(n: int = 1) -> None:
    """Bump Meter A by ``n`` and record one Meter B tick. Safe to call
    on every chat request — both meters are O(1)."""
    _ensure_meters()
    now = _time.time()
    if _METER_A is not None:
        try:
            _METER_A.increment(now=now, n=n)
            # Cheap rollover check — most calls are no-ops because the
            # day key hasn't changed.
            _METER_A.maybe_rollover(now=now)
        except Exception as _exc:
            logger.debug("[credit-burn] meter A increment failed: %s", _exc)
    if _METER_B is not None:
        try:
            _METER_B.record()
            # Only run the rolling evaluator at most once per second to
            # keep the hot path cheap; the meter itself is internally
            # rate-limited but `tick()` does a Redis bucket scan.
            global _LAST_B_TICK_LOG
            if now - _LAST_B_TICK_LOG >= 1.0:
                _METER_B.tick()
                _LAST_B_TICK_LOG = now
        except Exception as _exc:
            logger.debug("[credit-burn] meter B record failed: %s", _exc)


def is_fallback_active() -> bool:
    _ensure_meters()
    if _FLAG is None:
        return False
    try:
        return bool(_FLAG.is_set())
    except Exception:
        return False


def get_flag():
    """Test/admin helper — returns the singleton FallbackFlag (or
    None if init failed)."""
    _ensure_meters()
    return _FLAG


# ── Task #513 §J — Rule D (global monthly USD cap) runtime wiring ────────
def ingest_meter_d_usd(usd: float) -> None:
    """Record `usd` against the current calendar month for Rule D.

    Called from the same daily billing flush that feeds Meter C; we
    count the same dollars on a calendar-month window. When the
    cumulative spend crosses the configured cap (`MONTHLY_TOTAL_USD_CAP`,
    default $500), MeterD flips `chat:cheaponly=1` in Redis and
    `_select_chat_model` clamps every English chat turn to the
    Workers-AI Mistral-7B cheap tier. Never raises.
    """
    _ensure_meters()
    if _METER_D is None:
        return
    try:
        _METER_D.record_usd(float(usd))
    except Exception as _exc:
        logger.debug("[credit-burn] meter D ingest failed: %s", _exc)


def is_chat_cheaponly_active() -> bool:
    """True when Rule D has locked the chat tier-router into cheap-only
    mode. Read by `routes/ai_chat.py` on every English-chat dispatch
    and passed into `cost_caps._select_chat_model(cheaponly_active=...)`.

    Defensive: any Redis read failure returns False so a Redis outage
    does not silently degrade every English chat turn to Mistral-7B.
    """
    _ensure_meters()
    if _METER_D is None:
        return False
    try:
        return bool(_METER_D.is_cheaponly_active())
    except Exception:
        return False


# ── Task #27 — Indic-route Bedrock-Cohere sub-cap helpers ───────────────
def ingest_meter_d_usd_indic(usd: float) -> None:
    """Record `usd` against BOTH the global Rule D bucket and the
    dedicated Indic sub-bucket (Task #27).

    Called from `llm.call_embed_with_dispatch` on every successful
    Bedrock-Cohere Indic-embed call. The sub-cap default is sourced
    from `cost_caps.INDIC_EMBED_MONTHLY_USD_SUBCAP` (currently $5/mo)
    so a runaway Indic embed volume shuts down ONLY the Bedrock route
    via `is_indic_embed_paused()` without touching English chat or
    triggering the global cheaponly lock prematurely. Never raises.
    """
    _ensure_meters()
    if _METER_D is None:
        return
    try:
        from cost_caps import INDIC_EMBED_MONTHLY_USD_SUBCAP as _SUBCAP
    except Exception:
        _SUBCAP = 5.0
    try:
        _METER_D.record_usd_indic_bedrock(float(usd), subcap_usd=float(_SUBCAP))
    except Exception as _exc:
        logger.debug("[credit-burn] meter D indic ingest failed: %s", _exc)


def is_indic_embed_paused() -> bool:
    """True when the Indic embed sub-cap (Task #27) has tripped this
    month. The dispatcher reads this via the standard import in
    `llm.call_embed_with_dispatch` so an outage routes Indic queries
    to Workers AI for the rest of the calendar month.
    """
    _ensure_meters()
    if _METER_D is None:
        return False
    try:
        return bool(_METER_D.is_indic_embed_paused())
    except Exception:
        return False


def indic_monthly_usd() -> float:
    """Current-month Indic embed spend in USD (0.0 on errors)."""
    _ensure_meters()
    if _METER_D is None:
        return 0.0
    try:
        return float(_METER_D.indic_monthly_usd())
    except Exception:
        return 0.0


def get_meter_d():
    """Test/admin helper — returns the singleton MeterD (or None)."""
    _ensure_meters()
    return _METER_D


# ── Task #581 §L10 — month-to-date spend fraction for the free-tier ladder ─
def monthly_spend_fraction() -> float:
    """Return ``current_month_usd / monthly_cap_usd`` in [0.0, ~1.0].

    Reads MeterD's monthly Redis bucket (``rule_d:usd:YYYY-MM``) and
    divides by the configured cap. Returns 0.0 on any failure (Redis
    down, meter not initialised, cap == 0) so the chat dispatcher
    never accidentally collapses free-tier traffic to paywall when
    telemetry is unavailable. Caller passes this into
    ``cost_caps._select_chat_model(monthly_spend_fraction=...)`` and
    ``cost_caps.free_tier_dispatch_state(...)``.
    """
    _ensure_meters()
    if _METER_D is None:
        return 0.0
    try:
        from credit_burn_meter import MONTHLY_USD_KEY_PREFIX as _PFX
        from datetime import datetime, timezone
        cap = float(_METER_D.cfg.cap_usd or 0.0)
        if cap <= 0:
            return 0.0
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        raw = _METER_D.redis.get(f"{_PFX}:{month}")
        if raw is None:
            return 0.0
        cur = float(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
        return max(0.0, cur / cap)
    except Exception:
        return 0.0
