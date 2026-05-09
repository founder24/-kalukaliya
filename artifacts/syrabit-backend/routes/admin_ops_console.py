"""Task #2 — 2026 blueprint: admin Ops Console.

`GET /api/admin/ops/console` returns three operator-facing tiles in a
single round-trip so `AdminOpsConsole.jsx` can render without a fan-out
of GETs:

  1. **SLA ledger** — rolling 24h *and* 7d success rate + p50 / p95
     latency per canonical-specialist chain (English chat, Assamese
     chat, TTS, STT, content formatter), plus a per-feature **breach
     count** vs the locked latency target. Sourced from the
     `_LLM_PROVIDER_METRICS` ring already populated by every
     `_record_llm_call` site in `llm.py` (Redis-backed when available,
     in-process ring otherwise).

  2. **Outage map** — current circuit-breaker state per provider
     (`open` / `closed`, consecutive failures, last-failure timestamp +
     error class). Sourced from the in-process breaker registry
     maintained by `llm._BREAKER_STATE`.

  3. **Toggles** — read-only listing of the founder-locked operator
     knobs (`CHAT_PRIMARY_OVERRIDE`, `EMBED_DEGRADED_MODE`,
     `RAG_EMBEDDING_PROVIDER_FORCE`, `MONTHLY_TOTAL_USD_CAP`, ...)
     plus the three runtime degradation thresholds. Mutating these
     still goes through the existing `/api/admin/settings` route — the
     Ops Console is a single-pane-of-glass *viewer*, not a write
     surface.

Auth: `get_admin_user` — same dependency as the rest of the admin
control plane. Every read is best-effort: missing modules / Redis
hiccups never raise (we surface a `available=False` row instead).
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Iterable

from fastapi import APIRouter, Depends

from auth_deps import get_admin_user

logger = logging.getLogger(__name__)
router = APIRouter()


# ── SLA ledger ───────────────────────────────────────────────────────
# Per-feature locked latency targets (milliseconds). Breaches are
# counted against the rolling 24h window and surfaced verbatim in the
# panel so operators can spot a chain that's degrading even while still
# returning 200s.
_FEATURE_LATENCY_TARGET_MS: dict[str, int] = {
    "english_rag_chat":   2000,
    "assamese_rag_chat":  2500,
    "tts":                3000,
    "stt":                3000,
    "content_format":     5000,
}
_SLA_FEATURES = tuple(_FEATURE_LATENCY_TARGET_MS.keys())


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((pct / 100.0) * (len(s) - 1)))))
    return round(s[k], 1)


def _aggregate_window(events: Iterable[dict], target_ms: int) -> dict[str, Any]:
    successes = 0
    failures = 0
    latencies: list[float] = []
    breaches = 0
    for ev in events:
        if ev.get("success"):
            successes += 1
        else:
            failures += 1
        lat = float(ev.get("latency_ms") or ev.get("duration_ms") or 0.0)
        latencies.append(lat)
        if lat > target_ms:
            breaches += 1
    total = successes + failures
    return {
        "calls":        total,
        "success":      successes,
        "failure":      failures,
        "success_rate": (round(successes / total, 4) if total else None),
        "p50_ms":       _percentile(latencies, 50),
        "p95_ms":       _percentile(latencies, 95),
        "target_ms":    target_ms,
        "breaches":     breaches,
    }


def _events_in_window(window_seconds: int) -> list[dict]:
    """Pull all `_record_llm_call` events from the last `window_seconds`.
    Prefers the Redis-backed sorted set (cross-pod, survives restarts);
    falls back to the in-process ring."""
    cutoff = time.time() - window_seconds
    events: list[dict] = []
    redis_used = False
    try:
        from deps import redis_client as _rc
        import json as _j
        import llm as _llm
        if _rc is not None:
            raw = _rc.zrangebyscore(
                getattr(_llm, "_LLM_ROUTING_HISTORY_REDIS_KEY",
                        "llm:routing_history"),
                cutoff, "+inf",
            ) or []
            for item in raw:
                try:
                    events.append(_j.loads(item))
                except Exception:
                    pass
            redis_used = True
    except Exception as e:
        logger.debug("[ops_console] redis history unavailable: %s", e)
    if not redis_used:
        try:
            import llm as _llm
            ring = getattr(_llm, "_LLM_PROVIDER_METRICS", []) or []
            events = [m for m in ring if float(m.get("ts", 0)) >= cutoff]
        except Exception as e:
            logger.debug("[ops_console] in-proc ring unavailable: %s", e)
    return events


def _sla_ledger() -> dict[str, Any]:
    """Per-feature 24h + 7d success rate, p50/p95 latency, target, and
    breach count rolled out of `_LLM_PROVIDER_METRICS` events tagged
    with `feature_key`."""
    out: dict[str, Any] = {
        "windows": ["24h", "7d"],
        "rows": [],
    }
    try:
        events_24h = _events_in_window(24 * 3600)
        events_7d = _events_in_window(7 * 24 * 3600)
    except Exception as e:
        logger.debug("[ops_console] SLA ledger unavailable: %s", e)
        return {**out, "available": False}
    by_feature_24h: dict[str, list[dict]] = {f: [] for f in _SLA_FEATURES}
    by_feature_7d: dict[str, list[dict]] = {f: [] for f in _SLA_FEATURES}
    for ev in events_24h:
        f = str(ev.get("feature_key") or "")
        if f in by_feature_24h:
            by_feature_24h[f].append(ev)
    for ev in events_7d:
        f = str(ev.get("feature_key") or "")
        if f in by_feature_7d:
            by_feature_7d[f].append(ev)
    rows = []
    for feature in _SLA_FEATURES:
        target = _FEATURE_LATENCY_TARGET_MS[feature]
        rows.append({
            "feature": feature,
            "target_ms": target,
            "h24": _aggregate_window(by_feature_24h[feature], target),
            "d7":  _aggregate_window(by_feature_7d[feature], target),
        })
    out["rows"] = rows
    return out


# ── Outage map ───────────────────────────────────────────────────────
def _status_pill(success_rate_pct: float | None, breaker_open: bool) -> str:
    """Map (1h success rate, breaker state) → a single status pill the
    UI can colour without repeating thresholds in JS."""
    if breaker_open:
        return "open"
    if success_rate_pct is None:
        return "unknown"
    if success_rate_pct >= 95.0:
        return "healthy"
    if success_rate_pct >= 80.0:
        return "degraded"
    return "down"


def _outage_map() -> dict[str, Any]:
    """Outage map per Task #2 spec: combines circuit-breaker state with
    the rolling 1h **5xx / timeout rate per provider** sourced from the
    same provider-health counters that back ``/admin/llm-provider-stats``.

    Returns one row per provider with:
      - breaker `open` + consecutive failures + last_error
      - 1h `calls`, `success_rate_pct`, `failure_rate_pct`,
        `avg_latency_ms` from `llm.get_llm_provider_stats(3600)`
      - `status` pill ("healthy" / "degraded" / "down" / "open" /
        "unknown") derived from the two signals.
    """
    breakers: dict = {}
    try:
        import llm as _llm
        breakers = dict(getattr(_llm, "_BREAKER_STATE", {}) or {})
    except Exception as e:
        logger.debug("[ops_console] breaker registry unavailable: %s", e)
    stats: dict = {}
    try:
        import llm as _llm
        stats = (_llm.get_llm_provider_stats(window_seconds=3600) or {}).get(
            "providers", {}
        )
    except Exception as e:
        logger.debug("[ops_console] provider stats unavailable: %s", e)
    providers = sorted(set(breakers.keys()) | set(stats.keys()))
    rows: list[dict[str, Any]] = []
    for provider in providers:
        bk = breakers.get(provider) or {}
        st = stats.get(provider) or {}
        success_pct = st.get("success_rate")
        calls = int(st.get("calls", 0))
        failure_pct = (
            round(100.0 - success_pct, 1) if success_pct is not None else None
        )
        rows.append({
            "provider": provider,
            "open": bool(bk.get("open", False)),
            "consecutive_failures": int(bk.get("failures", 0)),
            "last_failure_ts": bk.get("last_failure_ts"),
            "last_error": bk.get("last_error"),
            "calls_1h": calls,
            "success_rate_pct_1h": success_pct,
            "failure_rate_pct_1h": failure_pct,
            "avg_latency_ms_1h": st.get("avg_latency_ms"),
            "status": _status_pill(success_pct, bool(bk.get("open", False))),
        })
    rows.sort(key=lambda r: (
        r["status"] != "open",
        r["status"] != "down",
        r["status"] != "degraded",
        -int(r.get("consecutive_failures", 0)),
    ))
    return {"rows": rows, "window_seconds": 3600}


# ── Toggles (read-only viewer) ───────────────────────────────────────
_OPERATOR_KNOBS = (
    "CHAT_PRIMARY_OVERRIDE",
    "EMBED_DEGRADED_MODE",
    "RAG_EMBEDDING_PROVIDER_FORCE",
    "EMBED_PROVIDER_PRIMARY",
    "MONTHLY_TOTAL_USD_CAP",
    "CHAT_CREDIT_RUNWAY_DAYS",
    "GCP_CREDITS_REMAINING_USD",
)


def _toggles() -> dict[str, Any]:
    """Aggregated read-only toggle viewer.

    Combines three sources into one payload so the Ops Console doesn't
    need to fan out to three separate admin GETs:
      1. Operator env knobs (`CHAT_PRIMARY_OVERRIDE` family).
      2. Founder-locked `cost_caps` thresholds.
      3. The per-feature routing-pool snapshot from
         `routes/admin_routing_config._build_pool` (same math as
         `GET /admin/routing-config`) so an operator can see which
         provider is actually serving each canonical-specialist chain
         without leaving the Ops Console.
    """
    rows = []
    for k in _OPERATOR_KNOBS:
        v = os.environ.get(k, "")
        rows.append({"name": k, "value": v, "set": bool(v)})
    thresholds: dict[str, Any] = {}
    try:
        import cost_caps as _cc
        for name in (
            "_DEFAULT_MONTHLY_TOTAL_USD_CAP",
            "DEGRADATION_PCT_PAUSE_BATCH",
            "DEGRADATION_PCT_VOICE_OFF",
            "DEGRADATION_PCT_FREE_503",
        ):
            if hasattr(_cc, name):
                thresholds[name] = getattr(_cc, name)
    except Exception as e:
        logger.debug("[ops_console] cost_caps thresholds unavailable: %s", e)
    routing_pools: list[dict[str, Any]] = []
    try:
        from routes.admin_routing_config import _build_pool as _bp
        from config import PROVIDER_PRIORITY as _pp
        for feature, providers in _pp.items():
            try:
                routing_pools.append(_bp(feature, list(providers)))
            except Exception as _be:
                logger.debug(
                    "[ops_console] _build_pool(%s) failed: %s", feature, _be,
                )
    except Exception as e:
        logger.debug("[ops_console] admin_routing_config unavailable: %s", e)
    return {
        "env_knobs": rows,
        "founder_locked_thresholds": thresholds,
        "routing_pools": routing_pools,
    }


@router.get("/api/admin/ops/console")
async def admin_ops_console(_admin: dict = Depends(get_admin_user)) -> dict[str, Any]:
    """Single-pane-of-glass dashboard for the canonical-specialist
    rollout. Renders the SLA ledger (24h + 7d windows with p50/p95 +
    breach counts), outage map, and operator-toggle viewer in one
    round-trip."""
    return {
        "generated_at": int(time.time()),
        "sla_ledger": _sla_ledger(),
        "outage_map": _outage_map(),
        "toggles": _toggles(),
    }
