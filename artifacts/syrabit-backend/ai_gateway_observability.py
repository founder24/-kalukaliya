"""Task #383 — Cloudflare AI Gateway response-header observability.

Cloudflare's AI Gateway (when fronting OpenAI / Workers AI / Vertex /
Anthropic / Azure OpenAI traffic) annotates every response with a
small set of ``cf-aig-*`` headers that tell the origin whether the
request was a cache hit, which upstream model handled it, and whether
the gateway's guardrails layer rewrote or blocked the request. None
of these headers are observed in production today — the only mention
of ``cf-aig-cache-status`` lives in a one-off diagnostic script.

This module gives the rest of the codebase one place to:

  * **Parse** the headers from any ``httpx.Response`` (or ``aiohttp``,
    or a plain dict — anything ``__getitem__``-able).
  * **Count** the outcomes so the admin ``/admin/cf-health`` panel
    can show "cache hit ratio over the last hour" without re-reading
    request logs.
  * **Snapshot** the running counters for the unified health route.

Everything runs behind ``CF_AIGW_OBS_ON`` so a regression in the
parser cannot affect chat traffic — when the flag is off the helpers
become no-ops and counters stay frozen at zero.

Header reference (Cloudflare AI Gateway, 2025-Q4 docs):

  ``cf-aig-cache-status``       — ``HIT`` / ``MISS`` / ``BYPASS``
  ``cf-aig-cache-ttl``          — seconds the response is cacheable
  ``cf-aig-step``               — fallback step that produced response
  ``cf-aig-log-id``             — gateway log id for cross-referencing
  ``cf-aig-event-id``           — per-request event id
  ``cf-aig-guardrail-action``   — ``allow`` / ``rewrite`` / ``block``
  ``cf-aig-guardrail-category`` — ``pii`` / ``profanity`` / ``code`` …
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from typing import Any, Mapping, Optional

from config import CF_AIGW_OBS_ON

logger = logging.getLogger(__name__)


# ── Redis-backed shared sample store (Task #449) ─────────────────────────────
# The local ``_SAMPLES`` deque resets on every container restart and is
# private to one ACA replica. The admin "cache by model" tile therefore
# loses its rolling view on every deploy / scale-to-zero, and shows a
# different slice depending on which pod the request lands on. We mirror
# every recorded sample into a shared Redis list so ``snapshot()`` can
# return the union across replicas and survive restarts.
#
# Layout: a single capped list (LPUSH + LTRIM) under one key with a 1h
# TTL — same pattern ``chat_speedup_metrics`` uses for warm-run history.
# Failures are swallowed; the local deque is the fallback so a Redis
# outage degrades the tile to single-replica behaviour but never breaks
# the chat hot path that calls ``record_aig_response()``.
_REDIS_SAMPLES_KEY = "aig_obs:samples"
_REDIS_SAMPLES_MAX = 1024
_REDIS_SAMPLES_TTL_S = 3600  # 1h rolling window


def _get_redis():
    """Resolve the Upstash REST client lazily so this module imports
    cleanly even when ``deps`` (and therefore Redis init) is not ready
    yet — e.g. in unit tests that import us before ``server.py``."""
    try:
        from deps import redis_client  # type: ignore
        return redis_client
    except Exception:
        return None


def _push_sample_to_shared_store(sample: dict[str, Any]) -> None:
    rc = _get_redis()
    if rc is None:
        return
    try:
        payload = json.dumps(sample, default=str)
        rc.lpush(_REDIS_SAMPLES_KEY, payload)
        rc.ltrim(_REDIS_SAMPLES_KEY, 0, _REDIS_SAMPLES_MAX - 1)
        rc.expire(_REDIS_SAMPLES_KEY, _REDIS_SAMPLES_TTL_S)
    except Exception as exc:  # best-effort — local deque still holds it
        logger.debug("[ai-gateway] shared sample push failed: %s", exc)


def _read_shared_samples() -> Optional[list[dict[str, Any]]]:
    """Return the union of all replicas' recorded samples in chronological
    order, or ``None`` if the shared store is unavailable so the caller
    can fall back to the local deque."""
    rc = _get_redis()
    if rc is None:
        return None
    try:
        raw = rc.lrange(_REDIS_SAMPLES_KEY, 0, _REDIS_SAMPLES_MAX - 1)
    except Exception as exc:
        logger.debug("[ai-gateway] shared sample read failed: %s", exc)
        return None
    if not raw:
        return []
    out: list[dict[str, Any]] = []
    for entry in raw:
        try:
            out.append(json.loads(entry))
        except Exception:
            continue
    # Upstash LPUSH puts newest at head; flip so the snapshot's
    # ``samples[-32:]`` slice returns the *most recent* 32 entries
    # like the local-deque path does.
    out.reverse()
    return out


def _reset_shared_store() -> None:
    rc = _get_redis()
    if rc is None:
        return
    try:
        rc.delete(_REDIS_SAMPLES_KEY)
    except Exception:
        pass


# ── In-memory counters (reset on process restart; admin route reads them) ────
_LOCK = threading.Lock()

# Outcome counters — running totals since process start.
_COUNTERS: dict[str, int] = {
    "aig_cache_hits": 0,
    "aig_cache_misses": 0,
    "aig_cache_bypass": 0,
    "aig_responses_total": 0,
    "aig_guardrails_allowed": 0,
    "aig_guardrails_rewrote": 0,
    "aig_guardrails_blocked": 0,
    "aig_parse_errors": 0,
}

# Last-N samples (so the admin panel can show "in the last hour" without
# pulling the gateway's analytics API). 1024 samples covers ~30 min of
# steady chat traffic at typical throughput; oldest entries roll off.
_SAMPLES: deque = deque(maxlen=1024)


def _normalise_headers(headers: Any) -> dict[str, str]:
    """Coerce any header-like object into a ``{lowercase: str}`` dict.

    httpx / aiohttp ``Response.headers`` are case-insensitive multi-dicts,
    Python dicts are case-sensitive. We always lowercase the keys so the
    lookups below are uniform regardless of the caller's transport.
    """
    if headers is None:
        return {}
    if hasattr(headers, "items"):
        return {str(k).lower(): str(v) for k, v in headers.items()}
    try:
        return {str(k).lower(): str(v) for k, v in dict(headers).items()}
    except Exception:
        return {}


def parse_aig_response_headers(headers: Any) -> dict[str, Any]:
    """Return a structured summary of the AI Gateway headers on one
    response. Always returns a dict — missing headers become ``None``.

    Shape:

        {
          "present":      bool,    # True iff any cf-aig-* header was seen
          "cache_status": "hit" | "miss" | "bypass" | None,
          "cache_ttl_s":  int | None,
          "log_id":       str | None,
          "event_id":     str | None,
          "step":         str | None,
          "guardrail":    {"action": "allow"|"rewrite"|"block"|None,
                           "category": str | None},
        }
    """
    h = _normalise_headers(headers)
    raw_status = (h.get("cf-aig-cache-status") or "").strip().lower() or None
    cache_status = raw_status if raw_status in ("hit", "miss", "bypass") else (
        # Treat unknown but non-empty values as "bypass" so we don't
        # silently drop telemetry when CF adds a new status name.
        "bypass" if raw_status else None
    )
    try:
        ttl = int(h.get("cf-aig-cache-ttl") or 0) or None
    except ValueError:
        ttl = None
    action = (h.get("cf-aig-guardrail-action") or "").strip().lower() or None
    if action not in ("allow", "rewrite", "block"):
        action = None
    return {
        "present": any(k.startswith("cf-aig-") for k in h),
        "cache_status": cache_status,
        "cache_ttl_s": ttl,
        "log_id": h.get("cf-aig-log-id") or None,
        "event_id": h.get("cf-aig-event-id") or None,
        "step": h.get("cf-aig-step") or None,
        "guardrail": {
            "action": action,
            "category": (h.get("cf-aig-guardrail-category") or "").strip()
            or None,
        },
    }


def record_aig_response(headers: Any, *, provider: str = "",
                        model: str = "") -> dict[str, Any]:
    """Parse + tally one response. Returns the parsed summary so callers
    can also log it. No-op (and returns the parsed shape with all counters
    untouched) when ``CF_AIGW_OBS_ON`` is false."""
    summary = parse_aig_response_headers(headers)
    if not CF_AIGW_OBS_ON or not summary["present"]:
        return summary
    with _LOCK:
        _COUNTERS["aig_responses_total"] += 1
        cs = summary["cache_status"]
        if cs == "hit":
            _COUNTERS["aig_cache_hits"] += 1
        elif cs == "miss":
            _COUNTERS["aig_cache_misses"] += 1
        elif cs == "bypass":
            _COUNTERS["aig_cache_bypass"] += 1
        action = (summary["guardrail"] or {}).get("action")
        if action == "allow":
            _COUNTERS["aig_guardrails_allowed"] += 1
        elif action == "rewrite":
            _COUNTERS["aig_guardrails_rewrote"] += 1
        elif action == "block":
            _COUNTERS["aig_guardrails_blocked"] += 1
        sample = {
            "ts": time.time(),
            "provider": provider or None,
            "model": model or None,
            "cache_status": cs,
            "guardrail_action": action,
            "log_id": summary.get("log_id"),
        }
        _SAMPLES.append(sample)
    # Mirror to the shared store *outside* the local lock — Upstash REST
    # calls are sync HTTP and we don't want to serialise every recorder
    # behind one in-process lock just to talk to Redis.
    _push_sample_to_shared_store(sample)
    # Structured guardrail-block log so on-call sees blocks in real time
    # (counters are eventually-consistent with logs, but a single block
    # event in production deserves to surface immediately, not when the
    # admin happens to refresh the dashboard).
    if action in ("block", "rewrite"):
        guard = summary.get("guardrail") or {}
        log_fn = logger.warning if action == "block" else logger.info
        log_fn(
            "[ai-gateway] guardrail %s provider=%s model=%s category=%s log_id=%s event_id=%s",
            action, provider or "?", model or "?",
            guard.get("category") or "-",
            summary.get("log_id") or "-",
            summary.get("event_id") or "-",
        )
    return summary


def _aggregate_cache_by_model(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Task #419 — bucket the rolling sample window by ``(provider, model)``
    so the admin CF Health tile can show on-call which model is most
    often served from cache.

    A bucket whose samples carried no ``cache_status`` at all (e.g. every
    sample was a guardrail-only event with no ``cf-aig-cache-status``
    header) reports ``hit_ratio = None``. The frontend renders that as
    "—" rather than 0% so the tile does not paint a model that simply
    has no cache telemetry as a "100% miss" outlier.
    """
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for s in samples:
        provider = s.get("provider") or "unknown"
        model = s.get("model") or "unknown"
        bucket = by_key.setdefault((provider, model), {
            "provider": provider,
            "model": model,
            "samples": 0,
            "hits": 0,
            "misses": 0,
            "bypass": 0,
        })
        bucket["samples"] += 1
        cs = s.get("cache_status")
        if cs == "hit":
            bucket["hits"] += 1
        elif cs == "miss":
            bucket["misses"] += 1
        elif cs == "bypass":
            bucket["bypass"] += 1
    out: list[dict[str, Any]] = []
    for bucket in by_key.values():
        cache_total = bucket["hits"] + bucket["misses"] + bucket["bypass"]
        bucket["cache_status_total"] = cache_total
        bucket["hit_ratio"] = (
            round(bucket["hits"] / cache_total, 4) if cache_total else None
        )
        out.append(bucket)
    # Sort: rows with a ratio first (highest hit_ratio, then most hits),
    # rows with no cache telemetry (ratio is None) last so on-call's eye
    # lands on the high-volume cached models first.
    out.sort(key=lambda b: (
        0 if b["hit_ratio"] is not None else 1,
        -(b["hit_ratio"] or 0.0),
        -b["hits"],
        b["model"],
    ))
    return out


def _aggregate_guardrail_by_model(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Task #448 — bucket the rolling sample window by ``(provider, model)``
    so the admin CF Health tile can show on-call which model is most
    often tripped by the AI Gateway guardrails layer (Llama-Guard /
    AI Content Safety).

    Mirrors the shape produced by :func:`_aggregate_cache_by_model` so
    the sibling tile on the frontend stays trivially symmetric. A
    bucket whose samples carried no ``guardrail_action`` at all (e.g.
    every sample was a cache-only event with no
    ``cf-aig-guardrail-action`` header) reports ``block_ratio = None``.
    The frontend renders that as "—" rather than 0% so a model with no
    guardrail telemetry is not painted as a "0% blocked" outlier.
    """
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for s in samples:
        provider = s.get("provider") or "unknown"
        model = s.get("model") or "unknown"
        bucket = by_key.setdefault((provider, model), {
            "provider": provider,
            "model": model,
            "samples": 0,
            "allows": 0,
            "rewrites": 0,
            "blocks": 0,
        })
        bucket["samples"] += 1
        action = s.get("guardrail_action")
        if action == "allow":
            bucket["allows"] += 1
        elif action == "rewrite":
            bucket["rewrites"] += 1
        elif action == "block":
            bucket["blocks"] += 1
    out: list[dict[str, Any]] = []
    for bucket in by_key.values():
        guardrail_total = bucket["allows"] + bucket["rewrites"] + bucket["blocks"]
        bucket["guardrail_total"] = guardrail_total
        bucket["block_ratio"] = (
            round(bucket["blocks"] / guardrail_total, 4)
            if guardrail_total else None
        )
        out.append(bucket)
    # Sort: rows with a ratio first (highest block_ratio, then most
    # blocks), rows with no guardrail telemetry (ratio is None) last
    # so on-call's eye lands on the worst offender first.
    out.sort(key=lambda b: (
        0 if b["block_ratio"] is not None else 1,
        -(b["block_ratio"] or 0.0),
        -b["blocks"],
        b["model"],
    ))
    return out


def snapshot() -> dict[str, Any]:
    """Return a JSON-serialisable snapshot for the admin health route."""
    with _LOCK:
        counters = dict(_COUNTERS)
        local_samples = list(_SAMPLES)
    # Task #449 — prefer the shared Redis store so the admin tile shows
    # the union across replicas and survives container restarts. Falls
    # back to this replica's local deque when Redis is unavailable.
    shared_samples = _read_shared_samples()
    samples = shared_samples if shared_samples is not None else local_samples
    total = counters["aig_responses_total"] or 0
    cache_total = (counters["aig_cache_hits"] + counters["aig_cache_misses"]
                   + counters["aig_cache_bypass"])
    hit_ratio = (counters["aig_cache_hits"] / cache_total) if cache_total else 0.0
    block_ratio = (counters["aig_guardrails_blocked"] / total) if total else 0.0
    recent_samples = samples[-32:]  # cap to keep payload small
    return {
        "enabled": bool(CF_AIGW_OBS_ON),
        "counters": counters,
        "cache_hit_ratio": round(hit_ratio, 4),
        "guardrail_block_ratio": round(block_ratio, 4),
        "recent_samples": recent_samples,
        # Task #419 — per-model breakdown built from the *same*
        # ``recent_samples`` window the admin payload exposes, so the
        # tile and the raw sample list cannot disagree about what
        # "in the current window" means.
        "cache_by_model": _aggregate_cache_by_model(recent_samples),
        # Task #448 — sibling per-model breakdown for guardrail blocks,
        # built from the same ``recent_samples`` window so the cache and
        # guardrail tiles cannot disagree about what "in the current
        # window" means.
        "guardrail_by_model": _aggregate_guardrail_by_model(recent_samples),
    }


def reset_for_tests() -> None:
    """Test-only helper — resets counters + samples to empty."""
    with _LOCK:
        for k in _COUNTERS:
            _COUNTERS[k] = 0
        _SAMPLES.clear()
    _reset_shared_store()


__all__ = [
    "parse_aig_response_headers",
    "record_aig_response",
    "snapshot",
    "reset_for_tests",
]
