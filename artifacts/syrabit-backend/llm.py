"""Syrabit.ai — LLM infrastructure: batching, smart key pool, streaming."""
import os, re, json, asyncio, uuid, time, logging, httpx, hashlib
import openai as _oai  # noqa: legacy — Task #347 transport reuse: AsyncOpenAI SDK is the HTTP transport for Azure OpenAI / Workers AI / CF AI Gateway only; no api.openai.com traffic. Removed-provider audit (#360 ci_grep_gate) excludes this line via the noqa marker.

_INDIC_LANG_CODES = frozenset({"as", "hi", "bn", "hi-in", "bn-in", "as-in"})

def _is_indic_lang(lang: str | None) -> bool:
    return bool(lang and lang.lower().strip() in _INDIC_LANG_CODES)

_SARVAM_INDIC_MODEL_PREFERENCE = ["sarvam-m", "sarvam-105b"]


class LlmResult(str):
    """String subclass that carries the provider that produced the result.

    `provider` is the canonical name (e.g. "gemini", "workers-ai").
    `fallback_reason` is set ONLY when this result came from a fallback tier
    (Task #636) — it's the short label returned by
    `providers.workers_ai.classify_primary_error` ("timeout", "http_503",
    "network", etc) so traces and admin dashboards can attribute the cost
    to the upstream failure that triggered the fallback.
    """
    def __new__(cls, text, provider="unknown", fallback_reason: str = ""):
        obj = str.__new__(cls, text)
        obj.provider = provider
        obj.fallback_reason = fallback_reason
        return obj

_MODEL_MAX_OUTPUT_TOKENS = {
    "llama-3.1-8b-instant": 8192,
    "gemini-2.5-flash": 65536,
}

# Deprecated / renamed models — resolved before the provider call so we
# never send a stale model name to the upstream API.
_MODEL_ALIASES: dict[str, str] = {
    # gemini-2.0-flash, gemini-2.0-flash-lite-001, gemini-1.5-flash,
    # gemini-1.5-pro, gemini-flash-latest, gemini-pro-latest are NOT enabled
    # in the BYOK Vertex project (verified 2026-05-03 — only the 2.5 family
    # is provisioned). Any historical references are normalised to 2.5-flash.
    "gemini-2.0-flash":          "gemini-2.5-flash",
    "gemini-2.0-flash-001":      "gemini-2.5-flash",
    "gemini-2.0-flash-lite-001": "gemini-2.5-flash",
    "gemini-1.5-flash":          "gemini-2.5-flash",
    "gemini-1.5-pro":            "gemini-2.5-flash",
    "gemini-flash-latest":       "gemini-2.5-flash",
    "gemini-pro-latest":         "gemini-2.5-flash",
}

def _clamp_max_tokens(model: str, max_tokens: int) -> int:
    cap = _MODEL_MAX_OUTPUT_TOKENS.get(model)
    return min(max_tokens, cap) if cap else max_tokens
from typing import Any, Dict, Optional
from fastapi import HTTPException
from emergentintegrations.llm.chat import LlmChat, UserMessage
from config import (
    LLM_PROVIDER, LLM_MODEL, OPENAI_API_KEY, SARVAM_THINK_BUFFER,
    _OPENAI_KEY,
    _SARVAM_LLM_KEY, _SARVAM_LLM_KEY_2, _SARVAM_LLM_KEY_3, _AWS_ACCESS_KEY, _AWS_SECRET_KEY, _AWS_REGION,
    is_cf_gateway_up, mark_cf_gateway_down, get_provider_base_url,
    byok_headers, BYOK_PLACEHOLDER,
    AZURE_OPENAI_DEPLOYMENT,
    ENABLE_PARALLEL_LLM_RACE, PARALLEL_RACE_TIMEOUT, MIN_PROVIDERS_TO_RACE, MAX_CONCURRENT_RACE_PROVIDERS,
)
# Task #490 — the `vertex_chat` (CF-shim streaming wrapper) and the
# in-llm SA-OAuth chat helper were removed when Vertex was scoped to
# `content_format` only. The remaining Vertex surface is
# `vertex_format.format_with_vertex` (NotebookLM-style polish), which
# `polish_notes_with_vertex` below delegates to directly.
from deps import sarvam_llm_client
from cache import _cache_key

logger = logging.getLogger(__name__)

_oai_client_cache: Dict[str, _oai.AsyncOpenAI] = {}

# Shared HTTP/2 transport reused by every provider client.
# HTTP/2 multiplexes multiple requests over a single TCP connection, eliminating
# per-request TLS handshake overhead for the CF AI Gateway.  Connection limits
# are sized to cover the worst-case concurrency:
#   chat pool: 5 WAI slots × 24 + Groq×4 + Cerebras×4 = 128
#   content pool: 2 WAI slots × 16 = 32
#   total: ~160 — so 256 max_connections gives comfortable headroom.
_OAI_HTTP_TRANSPORT = httpx.AsyncHTTPTransport(
    http2=True,
    limits=httpx.Limits(
        max_connections=256,
        max_keepalive_connections=128,
        keepalive_expiry=60.0,
    ),
)

def _get_oai_client(api_key: str, base_url: str) -> _oai.AsyncOpenAI:
    key_hash = hashlib.sha256(api_key.encode()).hexdigest()[:16]
    ck = f"{base_url}|{key_hash}"
    client = _oai_client_cache.get(ck)
    if client is None:
        # Custom httpx client shares the high-limit HTTP/2 transport above.
        # Each unique base_url gets its own AsyncClient so cookies / headers
        # don't bleed between providers, but the underlying TCP pool is shared.
        http_client = httpx.AsyncClient(
            transport=_OAI_HTTP_TRANSPORT,
            timeout=httpx.Timeout(connect=5.0, read=90.0, write=15.0, pool=10.0),
        )
        client = _oai.AsyncOpenAI(api_key=api_key, base_url=base_url,
                                   http_client=http_client)
        _oai_client_cache[ck] = client
    return client

# Global outer semaphores.  Sized to the total slot capacity so they never
# become the bottleneck — the per-slot SmartKeyPool semaphores (max_con) are
# the real rate-control layer.
#   chat pool:    5×24 + 4 + 4 = 136  → round up to 200
#   admin pool:   2×16           = 32  → keep at 30 (admin is low-traffic)
_LLM_SEMAPHORE = asyncio.Semaphore(int(os.environ.get("LLM_MAX_CONCURRENT", 200)))
_ADMIN_LLM_SEMAPHORE = asyncio.Semaphore(int(os.environ.get("ADMIN_LLM_MAX_CONCURRENT", 30)))

_LLM_PROVIDER_METRICS: list = []
_LLM_PROVIDER_METRICS_MAX = 20_000

# ── Persistent routing history (Task #271) ────────────────────────────────────
# Redis sorted set — score=unix_timestamp, member=JSON event.
# Survives restarts; enables cost allocation auditing across Azure, Bedrock,
# and Workers AI credit pools.  In-memory list above is kept as a fast local
# fallback when Redis is unavailable.
_LLM_ROUTING_HISTORY_REDIS_KEY = "llm:routing_history"
_LLM_ROUTING_HISTORY_RETENTION_S = 90 * 86_400   # 90 days
_LLM_ROUTING_HISTORY_MAX_ENTRIES = 100_000

# ── Per-provider 429 burst tracking (Task #70 + Task #75) ───────────────────
# Unified sliding-window counter for every LLM provider.
# Each provider gets its own in-memory list and its own Redis key so the
# alerting loop can fire a targeted alert for each independently.
#
# Window is 180s — deliberately larger than the 120s _alerting_loop interval
# so a burst that starts near a tick boundary is never silently missed.
# Redis TTL is refreshed on every 429 hit so an ongoing outage never
# silently auto-expires mid-burst.  The counter resets on the next
# successful call (mark_ok → _reset_provider_429).
#
# Providers included: workers-ai, gemini, azure_openai, deepgram.  Others silently no-op.
_PROVIDER_429_BURST_WINDOW_S = 180   # shared lookback / Redis TTL for all providers
_PROVIDER_429_WINDOWS: dict = {       # provider → list[float epoch timestamps]
    "workers-ai":   [],
    # groq removed in Task #347 / V4 §0
    "gemini":       [],
    "azure_openai": [],
    # bedrock removed in Task #347
    "deepgram":     [],
}
_PROVIDER_429_REDIS_KEYS: dict = {
    "workers-ai":   "wai_429_burst",
    # groq removed in Task #347 / V4 §0
    "gemini":       "gemini_429_burst",
    "azure_openai": "azure_429_burst",
    # bedrock removed in Task #347
    "deepgram":     "deepgram_429_burst",
    # Task #491 — legacy SLM provider retired (see V4 changelog).
}

# Backwards-compat module-level aliases for code that references these directly
# (server.py, vertex_health_cache.py, tests).  They are aliases to the list
# inside _PROVIDER_429_WINDOWS["workers-ai"] — never reassign the list object,
# use .clear() to reset so the aliases stay valid.
_WORKERS_AI_429_WINDOW   = _PROVIDER_429_WINDOWS["workers-ai"]
_WORKERS_AI_429_WINDOW_S = _PROVIDER_429_BURST_WINDOW_S
_WORKERS_AI_429_REDIS_KEY = _PROVIDER_429_REDIS_KEYS["workers-ai"]


def _track_provider_429(provider: str) -> None:
    """Record one 429 hit for *provider* (in-memory + Redis).

    No-ops silently for providers not in ``_PROVIDER_429_WINDOWS``.
    """
    window = _PROVIDER_429_WINDOWS.get(provider)
    if window is None:
        return
    window.append(time.time())
    redis_key = _PROVIDER_429_REDIS_KEYS.get(provider)
    if not redis_key:
        return
    try:
        from deps import redis_client as _rc
        if _rc:
            _rc.incr(redis_key)
            _rc.expire(redis_key, _PROVIDER_429_BURST_WINDOW_S)
    except Exception:
        pass


def _reset_provider_429(provider: str) -> None:
    """Reset 429 counter for *provider* after a successful call.

    Uses list.clear() (not reassignment) so module-level aliases stay valid.
    No-ops silently for providers not in ``_PROVIDER_429_WINDOWS``.
    """
    window = _PROVIDER_429_WINDOWS.get(provider)
    if window is None:
        return
    window.clear()
    redis_key = _PROVIDER_429_REDIS_KEYS.get(provider)
    if not redis_key:
        return
    try:
        from deps import redis_client as _rc
        if _rc:
            _rc.delete(redis_key)
    except Exception:
        pass


# Task #347: ``_BEDROCK_HTTP_STATUS_RE`` and ``_bedrock_track_outcome`` were
# deleted alongside ``providers/bedrock.py``. AWS Bedrock is no longer in
# PROVIDER_PRIORITY for any feature pool, so the 429-burst lifecycle has
# no bedrock outcomes to record.


# ── Per-provider real RPM sliding window (chat soft-shed, 2026-05-05) ─────────
# When ``azure_openai`` / ``sarvam`` (the strict primaries for the
# ``english_rag_chat`` / ``assamese_rag_chat`` pools after the 2026-05-05
# vertex purge) accumulate >= 70 % of their configured RPM cap inside a
# 60-second window, ``select_provider`` excludes them so the dispatcher
# preemptively shifts traffic to the ``workers_ai_*`` fallback BEFORE a
# single 429 is observed.
#
# This is a soft-shed; the 429-burst counter above is still the final
# safety net for any traffic that slipped through (e.g. a sudden burst
# that crossed the cap before the next select_provider draw saw it).
#
# Window width = 60 s (matches the per-minute semantics of "RPM").  Limits
# come from ``_POOL_RPM_LIMITS`` (PROVIDER_MAX_CONCURRENT × 60, env-
# overridable via AZURE_OPENAI_RPM_LIMIT / SARVAM_RPM_LIMIT, see
# ``_build_pool_rpm_limits`` further down in this file).
_PAID_PROVIDER_RPM_WINDOW_S = 60
_PAID_PROVIDER_RPM_WINDOWS: dict = {   # provider → list[float] epoch timestamps
    "azure_openai": [],
    "sarvam":       [],
}

# Redis key prefix for cross-worker RPM accounting.  The shed mechanism
# MUST see global traffic (gunicorn runs 3 workers by default — see
# gunicorn.conf.py).  Without Redis aggregation each worker only counts
# its own ~1/3 of traffic, so 70 % of the global cap is reached when
# each worker has only seen ~23 %, and the shed never fires.
#
# Storage uses 2-bucket rolling-window counters (one INCR per dispatched
# request, two GETs per saturation check):
#   key = "paid_rpm:{provider}:{epoch_minute}"
#   value = INCR-tracked count of requests inside that minute bucket
#   TTL = 90 s (1.5 × bucket size) so the previous bucket is still
#         readable while we are inside the current one.
# Reads compute  cur + prev * (1 - frac_into_cur_bucket)  — standard
# Cloudflare-style rolling window approximation, ~95 % accurate at the
# bucket boundary and exact in steady state.
_PAID_RPM_REDIS_KEY_PREFIX = "paid_rpm:"
_PAID_RPM_REDIS_BUCKET_S   = 60        # one bucket per minute
_PAID_RPM_REDIS_TTL_S      = 90        # keep the previous bucket alive


def _record_paid_provider_request(provider: str) -> None:
    """Record one dispatched request for *provider*'s 60-second RPM window.

    Called from ``_dispatch_llm_for_feature`` for the ``azure_openai`` and
    ``sarvam`` branches BEFORE the upstream call so that timed-out / 429ed
    attempts still consume against the cap (matches what the provider's
    own quota meter sees).

    Records to BOTH:
      • a per-process in-memory list (``_PAID_PROVIDER_RPM_WINDOWS``) for
        zero-latency local trim/read, and
      • an Upstash Redis bucket counter for cross-worker aggregation
        (gunicorn runs 3 workers — without this the per-worker count
        is ~1/3 of global traffic and the 70 % shed fires too late).

    No-ops silently for providers not in ``_PAID_PROVIDER_RPM_WINDOWS``.
    The list is trimmed eagerly so it never grows unbounded under
    sustained load.  Mirrors the ``_PROVIDER_429_WINDOWS`` pattern.
    """
    window = _PAID_PROVIDER_RPM_WINDOWS.get(provider)
    if window is None:
        return
    now = time.time()
    window.append(now)
    cutoff = now - _PAID_PROVIDER_RPM_WINDOW_S
    while window and window[0] < cutoff:
        window.pop(0)
    # Cross-worker counter — best-effort, never blocks the dispatch path.
    try:
        from deps import redis_client as _rc
        if _rc:
            bucket = int(now // _PAID_RPM_REDIS_BUCKET_S)
            key = f"{_PAID_RPM_REDIS_KEY_PREFIX}{provider}:{bucket}"
            _rc.incr(key)
            _rc.expire(key, _PAID_RPM_REDIS_TTL_S)
    except Exception:
        pass


def _get_paid_provider_rpm_count(
    provider: str,
    window_seconds: int = _PAID_PROVIDER_RPM_WINDOW_S,
) -> int:
    """Return number of dispatched requests for *provider* in the last
    *window_seconds* seconds (in-process only)."""
    window = _PAID_PROVIDER_RPM_WINDOWS.get(provider, [])
    cutoff = time.time() - window_seconds
    return sum(1 for t in window if t > cutoff)


def _get_paid_provider_rpm_count_global(provider: str) -> int:
    """Return the cross-worker dispatched-request count for *provider*
    in the last 60 s (Cloudflare-style rolling window approximation).

    Reads the current-minute and previous-minute bucket counters from
    Redis and weights the previous bucket by how far into the current
    bucket we are.  Returns 0 when Redis is unavailable so the caller
    falls back gracefully to the per-process count.
    """
    try:
        from deps import redis_client as _rc
        if not _rc:
            return 0
        now = time.time()
        cur_bucket = int(now // _PAID_RPM_REDIS_BUCKET_S)
        prev_bucket = cur_bucket - 1
        cur_key  = f"{_PAID_RPM_REDIS_KEY_PREFIX}{provider}:{cur_bucket}"
        prev_key = f"{_PAID_RPM_REDIS_KEY_PREFIX}{provider}:{prev_bucket}"
        cur_val_raw  = _rc.get(cur_key)
        prev_val_raw = _rc.get(prev_key)
        cur_val  = int(cur_val_raw  or 0)
        prev_val = int(prev_val_raw or 0)
        frac_into_cur = (now % _PAID_RPM_REDIS_BUCKET_S) / float(_PAID_RPM_REDIS_BUCKET_S)
        # Previous bucket contributes  (1 - frac_into_cur)  of its count
        # to the trailing 60-second window.
        return int(cur_val + prev_val * (1.0 - frac_into_cur))
    except Exception:
        return 0


def _get_paid_provider_rpm_ratio(provider: str) -> float:
    """Return real RPM-utilisation ratio (0.0 – 1.0+) for *provider*.

    Takes ``max(per_process_count, cross_worker_count)`` so the shed
    fires whichever signal crosses the cap first — the cross-worker
    Redis aggregate is the production signal (gunicorn runs 3 workers),
    while the per-process list keeps the unit tests deterministic and
    serves as a fallback when Upstash is unreachable.

    Reads the configured RPM cap from ``_POOL_RPM_LIMITS`` (populated at
    module import from ``PROVIDER_MAX_CONCURRENT × 60`` with env overrides
    from ``_build_pool_rpm_limits``).  Returns ``0.0`` when the provider
    isn't tracked or has no positive cap.

    The lookup is intentionally inside the function body so the call is
    resolved against the module dict at *call* time — ``_POOL_RPM_LIMITS``
    is defined further down in this file.
    """
    if provider not in _PAID_PROVIDER_RPM_WINDOWS:
        return 0.0
    limit = _POOL_RPM_LIMITS.get(provider, 0)
    if limit <= 0:
        return 0.0
    local = _get_paid_provider_rpm_count(provider)
    global_count = _get_paid_provider_rpm_count_global(provider)
    return max(local, global_count) / float(limit)


def _reset_paid_provider_rpm(provider: str | None = None) -> None:
    """Test helper: clear the RPM window for one or all paid providers.

    Production code never calls this — the 60-second sliding trim above
    expires entries naturally and the Redis buckets self-expire via TTL.
    Tests use this between cases so each test starts from a clean slate.
    Uses ``list.clear()`` (not reassignment) so any module-level alias
    keeps pointing at the same list object.

    Does NOT touch Redis — tests that need a clean cross-worker counter
    monkeypatch ``deps.redis_client`` to ``None`` (or a fake) instead.
    """
    if provider is None:
        for w in _PAID_PROVIDER_RPM_WINDOWS.values():
            w.clear()
        return
    window = _PAID_PROVIDER_RPM_WINDOWS.get(provider)
    if window is not None:
        window.clear()


# ── Assamese chat "both rails red" burst tracking (Task #374) ───────────────
# When the strict Assamese chain (Sarvam → Vertex/Gemini, Task #291) and the
# Workers-AI Phase-2 fallback are BOTH exhausted we surface either:
#   • SSE chunk with ``error_kind: 'assamese_unavailable'`` (streaming path), or
#   • HTTPException(503) with detail starting with "Assamese chat" (non-stream).
# Either event represents a P0 user-visible Assamese outage. We record it on
# a 180s rolling window so ``metrics._alerting_loop`` can fire a targeted
# ``assamese_unavailable_burst`` alert that pages on-call instead of waiting
# for a user complaint. Mirrors the Workers-AI / Groq / Gemini 429 burst
# lifecycle (in-memory list + Redis TTL counter) so the admin health panel
# and the alerting loop can read the same value cross-worker.
_ASSAMESE_UNAVAILABLE_BURST_WINDOW_S = 180
_ASSAMESE_UNAVAILABLE_WINDOW: list = []   # list[float] epoch timestamps
_ASSAMESE_UNAVAILABLE_REDIS_KEY = "assamese_unavailable_burst"

# Task #379 — capped recent-events log so the admin "Assamese Chat (both rails)"
# tile can render which conversations were affected, which leg failed first,
# and the underlying provider error. In-memory list (most recent last) +
# Redis list (LPUSH/LTRIM, most recent first) for cross-worker visibility.
# 20 entries is plenty: the alerting threshold is 3 events / 180s, and the
# UI only shows the most recent 5 by default — but keeping a small buffer
# means an operator who arrives a minute late can still see the ramp-up.
_ASSAMESE_RECENT_OUTAGES_MAX = 20
_ASSAMESE_RECENT_OUTAGES: list = []   # list[dict] in insertion order
_ASSAMESE_RECENT_OUTAGES_REDIS_KEY = "assamese_unavailable_events"
# Same TTL as the burst counter — recent-event context is only useful while
# the outage is still "live". After 180 s of silence the panel can be empty.
_ASSAMESE_RECENT_OUTAGES_TTL_S = _ASSAMESE_UNAVAILABLE_BURST_WINDOW_S
_ASSAMESE_ERROR_SUMMARY_MAX_LEN = 200


def _hash_conversation_id(conversation_id: str | None) -> str:
    """Return a short, irreversible hash of a conversation id for the
    recent-outages log. We never store the raw id — operators only need
    a stable opaque token to spot "is this the same conversation as the
    last event?". Empty / falsy id → "" (rendered as "—" in the UI).
    """
    if not conversation_id:
        return ""
    try:
        return hashlib.sha256(str(conversation_id).encode("utf-8")).hexdigest()[:12]
    except Exception:
        return ""


def record_assamese_unavailable(
    failing_leg: str = "",
    error_summary: str = "",
    conversation_id: str | None = None,
) -> None:
    """Record one "both Assamese rails red" event (in-memory + Redis).

    Called from the three error sites in this module that surface an
    ``assamese_unavailable`` outage to the user:
      • non-stream ``call_llm_api_chat`` 503 (strict chain exhausted)
      • streaming Phase-2 unavailable (Workers AI not configured)
      • streaming Phase-2 failed before first token

    The Redis key uses TTL-INCR semantics identical to the per-provider
    429 counters so the alerting loop and the dashboard tile read a
    cross-worker value. In-memory list survives Redis outages.

    Task #379 — also persists a capped recent-events document so the admin
    health panel can show which leg failed and a short error excerpt for
    each of the last events. ``failing_leg`` is one of:
      • ``sarvam_workers_indic_chain`` — non-stream 503 (Assamese strict
        chain exhausted). Renamed from ``sarvam_vertex_chain`` by Task
        #492 (V4 §15 Sarvam scope-down) in coordination with the admin
        health panel + 7 dependent test assertions + prod alert query;
        all consumers were migrated together.
      • ``workers_ai_unavailable`` — Phase-2 fallback not configured
      • ``workers_ai_phase2``      — Phase-2 fallback errored before token
    ``error_summary`` is truncated to ``_ASSAMESE_ERROR_SUMMARY_MAX_LEN``
    characters; ``conversation_id`` (if provided) is hashed before storage.
    All three kwargs are optional so legacy callers keep working.
    """
    now_ts = time.time()
    _ASSAMESE_UNAVAILABLE_WINDOW.append(now_ts)
    # Trim eagerly so the in-memory list never grows unbounded in long runs.
    cutoff = now_ts - _ASSAMESE_UNAVAILABLE_BURST_WINDOW_S * 2
    while _ASSAMESE_UNAVAILABLE_WINDOW and _ASSAMESE_UNAVAILABLE_WINDOW[0] < cutoff:
        _ASSAMESE_UNAVAILABLE_WINDOW.pop(0)

    # Build the recent-event document (Task #379). Truncate error summary so
    # a noisy stack trace from a buggy provider can't blow up Redis memory.
    _err_short = (error_summary or "").strip()
    if len(_err_short) > _ASSAMESE_ERROR_SUMMARY_MAX_LEN:
        _err_short = _err_short[: _ASSAMESE_ERROR_SUMMARY_MAX_LEN - 1] + "…"
    event = {
        "ts": now_ts,
        "failing_leg": (failing_leg or "unknown").strip() or "unknown",
        "error_summary": _err_short,
        "conversation_id_hash": _hash_conversation_id(conversation_id),
    }
    _ASSAMESE_RECENT_OUTAGES.append(event)
    # Cap the in-memory buffer.
    while len(_ASSAMESE_RECENT_OUTAGES) > _ASSAMESE_RECENT_OUTAGES_MAX:
        _ASSAMESE_RECENT_OUTAGES.pop(0)

    try:
        from deps import redis_client as _rc
        if _rc:
            _rc.incr(_ASSAMESE_UNAVAILABLE_REDIS_KEY)
            _rc.expire(_ASSAMESE_UNAVAILABLE_REDIS_KEY,
                       _ASSAMESE_UNAVAILABLE_BURST_WINDOW_S)
            # Capped event log — LPUSH (newest first) + LTRIM to cap size.
            try:
                _rc.lpush(_ASSAMESE_RECENT_OUTAGES_REDIS_KEY,
                          json.dumps(event, separators=(",", ":")))
                _rc.ltrim(_ASSAMESE_RECENT_OUTAGES_REDIS_KEY,
                          0, _ASSAMESE_RECENT_OUTAGES_MAX - 1)
                _rc.expire(_ASSAMESE_RECENT_OUTAGES_REDIS_KEY,
                           _ASSAMESE_RECENT_OUTAGES_TTL_S)
            except Exception:
                pass
    except Exception:
        pass


def get_assamese_recent_outages(limit: int = 5) -> list[dict]:
    """Return up to ``limit`` recent Assamese-unavailable events, newest first.

    Redis is the primary source (cross-worker, TTL-backed list). Falls back
    to the in-process ``_ASSAMESE_RECENT_OUTAGES`` buffer when Redis is
    unavailable or returns nothing. Each entry is a dict with keys
    ``ts`` (epoch seconds), ``failing_leg``, ``error_summary``,
    ``conversation_id_hash``.

    Defensive: malformed Redis entries are skipped, and a Redis outage
    never causes the caller to fail — the in-process buffer is always
    consulted as a last resort.
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 5
    if limit <= 0:
        return []

    out: list[dict] = []
    try:
        from deps import redis_client as _rc
        if _rc:
            raw_items = _rc.lrange(_ASSAMESE_RECENT_OUTAGES_REDIS_KEY, 0, limit - 1)
            for raw in raw_items or []:
                try:
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", errors="replace")
                    doc = json.loads(raw)
                    if isinstance(doc, dict):
                        out.append(doc)
                except Exception:
                    continue
            if out:
                return out[:limit]
    except Exception:
        pass

    # Fallback: in-process buffer (newest last → reverse).
    return list(reversed(_ASSAMESE_RECENT_OUTAGES))[:limit]


def get_assamese_unavailable_burst_inprocess(window_seconds: int = 60) -> int:
    """Return the number of assamese_unavailable events in the last
    ``window_seconds`` from the in-process timestamp list.
    """
    cutoff = time.time() - window_seconds
    return sum(1 for t in _ASSAMESE_UNAVAILABLE_WINDOW if t > cutoff)


def get_assamese_unavailable_burst(
    window_seconds: int = _ASSAMESE_UNAVAILABLE_BURST_WINDOW_S,
) -> int:
    """Return the number of assamese_unavailable events in the last
    ``window_seconds``.

    Redis is the primary source (cross-worker, TTL-backed). Falls back to
    the in-process sliding window when Redis is unavailable.

    NOTE: when Redis is available the ``window_seconds`` parameter is
    ignored — Redis stores a cumulative counter with a fixed TTL. Use
    ``get_assamese_unavailable_burst_inprocess()`` for an accurate short
    window.
    """
    try:
        from deps import redis_client as _rc
        if _rc:
            val = _rc.get(_ASSAMESE_UNAVAILABLE_REDIS_KEY)
            if val is not None:
                return int(val)
    except Exception:
        pass
    return get_assamese_unavailable_burst_inprocess(window_seconds)


def get_provider_429_burst(provider: str,
                           window_seconds: int = _PROVIDER_429_BURST_WINDOW_S) -> int:
    """Return the number of 429s for *provider* in the last *window_seconds*.

    Redis is the primary source (cross-worker, TTL-backed). Falls back to the
    in-process sliding window when Redis is unavailable.

    NOTE: when Redis is available the *window_seconds* parameter is ignored —
    Redis stores a cumulative counter with a fixed TTL.  Use
    ``get_provider_429_burst_inprocess()`` when you need an accurate short
    window that is always timestamp-filtered.
    """
    redis_key = _PROVIDER_429_REDIS_KEYS.get(provider)
    if redis_key:
        try:
            from deps import redis_client as _rc
            if _rc:
                val = _rc.get(redis_key)
                if val is not None:
                    return int(val)
        except Exception:
            pass
    return get_provider_429_burst_inprocess(provider, window_seconds)


def get_provider_429_burst_inprocess(provider: str,
                                     window_seconds: int = 60) -> int:
    """Return the number of 429s for *provider* in the last *window_seconds*
    using only the in-process timestamp list (no Redis).

    Use this when you need an accurate short window (e.g. 60 s) or when
    Redis is unavailable.
    """
    window = _PROVIDER_429_WINDOWS.get(provider, [])
    cutoff = time.time() - window_seconds
    return sum(1 for t in window if t > cutoff)


# ── Backwards-compat wrappers — Workers AI specific public API ────────────────
# Existing callers (server.py, vertex_health_cache.py, metrics.py, tests)
# import these by name.  They delegate to the generic helpers above.

def _track_workers_ai_429() -> None:
    """Record one Workers AI 429 hit (in-memory + Redis)."""
    _track_provider_429("workers-ai")


def _reset_workers_ai_429() -> None:
    """Reset Workers AI 429 counter after a successful call."""
    _reset_provider_429("workers-ai")


def get_workers_ai_429_burst(window_seconds: int = _WORKERS_AI_429_WINDOW_S) -> int:
    """Return the number of Workers AI 429s in the last *window_seconds*.

    Redis is the primary source. Falls back to the in-process sliding window.
    See ``get_provider_429_burst`` for the generic version.
    """
    return get_provider_429_burst("workers-ai", window_seconds)


def get_workers_ai_429_burst_inprocess(window_seconds: int = 60) -> int:
    """Return Workers AI 429 burst using only the in-process timestamp list.

    See ``get_provider_429_burst_inprocess`` for the generic version.
    """
    return get_provider_429_burst_inprocess("workers-ai", window_seconds)


def _record_llm_call(provider: str, model: str, duration_ms: float, success: bool, tokens_approx: int = 0, fallback: bool = False, error_type: str = "", feature_key: str = ""):
    ts = time.time()
    dm = round(duration_ms, 1)
    event = {
        # Canonical audit fields (Task #271 schema)
        "timestamp":     int(ts),
        "latency_ms":    dm,
        "provider":      provider,
        "model":         model,
        "success":       success,
        "feature_key":   feature_key,
        # Extended fields for internal consumers
        "ts":            ts,
        "duration_ms":   dm,
        "tokens_approx": tokens_approx,
        "fallback":      fallback,
        "error_type":    error_type,
    }
    _LLM_PROVIDER_METRICS.append(event)
    if len(_LLM_PROVIDER_METRICS) > _LLM_PROVIDER_METRICS_MAX:
        del _LLM_PROVIDER_METRICS[:1000]
    # Persist to Redis sorted set (best-effort — never blocks the LLM call path).
    # Score = unix timestamp so ZRANGEBYSCORE gives events in any time window.
    try:
        from deps import redis_client as _rc
        if _rc:
            import json as _json_r, random as _rnd_r
            _rc.zadd(_LLM_ROUTING_HISTORY_REDIS_KEY, {_json_r.dumps(event): ts})
            # Probabilistic pruning (~1 % of calls) — keeps the sorted set bounded
            # to _LLM_ROUTING_HISTORY_RETENTION_S and _LLM_ROUTING_HISTORY_MAX_ENTRIES.
            if _rnd_r.random() < 0.01:
                _rc.zremrangebyscore(
                    _LLM_ROUTING_HISTORY_REDIS_KEY,
                    "-inf",
                    ts - _LLM_ROUTING_HISTORY_RETENTION_S,
                )
                # Also enforce max-entry cap: trim oldest if over the limit.
                sz = _rc.zcard(_LLM_ROUTING_HISTORY_REDIS_KEY)
                if sz and sz > _LLM_ROUTING_HISTORY_MAX_ENTRIES:
                    excess = sz - _LLM_ROUTING_HISTORY_MAX_ENTRIES
                    _rc.zpopmin(_LLM_ROUTING_HISTORY_REDIS_KEY, excess)
    except Exception:
        pass


def get_llm_provider_stats(window_seconds: int = 3600) -> dict:
    cutoff = time.time() - window_seconds
    # Primary: Redis sorted set (persists across restarts, cross-worker).
    recent: list = []
    _redis_available = False
    try:
        from deps import redis_client as _rc
        if _rc:
            import json as _json_s
            raw = _rc.zrangebyscore(_LLM_ROUTING_HISTORY_REDIS_KEY, cutoff, "+inf")
            if raw is not None:
                for item in raw:
                    try:
                        recent.append(_json_s.loads(item))
                    except Exception:
                        pass
                _redis_available = True
    except Exception:
        pass
    # Fallback: in-memory sliding window (single-process, lost on restart).
    if not _redis_available:
        recent = [m for m in _LLM_PROVIDER_METRICS if m["ts"] >= cutoff]

    by_provider: dict = {}
    for m in recent:
        p = m["provider"]
        if p not in by_provider:
            by_provider[p] = {"calls": 0, "successes": 0, "failures": 0, "total_ms": 0, "tokens": 0, "models": set()}
        by_provider[p]["calls"] += 1
        by_provider[p]["tokens"] += m.get("tokens_approx", 0)
        by_provider[p]["total_ms"] += m["duration_ms"]
        by_provider[p]["models"].add(m["model"])
        if m["success"]:
            by_provider[p]["successes"] += 1
        else:
            by_provider[p]["failures"] += 1
    result = {}
    for p, s in by_provider.items():
        result[p] = {
            "calls": s["calls"],
            "success_rate": round(s["successes"] / max(s["calls"], 1) * 100, 1),
            "failures": s["failures"],
            "avg_latency_ms": round(s["total_ms"] / max(s["calls"], 1), 1),
            "tokens_approx": s["tokens"],
            "models": list(s["models"]),
        }
    total_calls = sum(s["calls"] for s in by_provider.values())
    total_success = sum(s["successes"] for s in by_provider.values())
    fallback_calls = sum(1 for m in recent if m.get("fallback"))
    return {
        "providers": result,
        "total_calls": total_calls,
        "overall_success_rate": round(total_success / max(total_calls, 1) * 100, 1),
        "fallback_rate": round(fallback_calls / max(total_calls, 1) * 100, 1),
        "window_seconds": window_seconds,
        "data_source": "redis" if _redis_available else "in-memory",
    }
_LLM_BATCH_WINDOW_MS = int(os.environ.get("LLM_BATCH_WINDOW_MS", 5))
_CONTENT_BATCH_WINDOW_MS = int(os.environ.get("CONTENT_BATCH_WINDOW_MS", 300))

_CONTENT_RETRY_MAX = 3
_CONTENT_RETRY_BACKOFF = [2.0, 4.0, 8.0]
_CONTENT_RPM_MAX_WAIT = float(os.environ.get("CONTENT_RPM_MAX_WAIT", 30))

class _LlmBatcher:
    """
    Smart LLM Batching: deduplicates identical questions arriving within a
    short window so only one API call is made per unique question.
    """
    def __init__(self, batch_window_ms: int = None):
        self._pending: Dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()
        self._stats = {"batched": 0, "deduped": 0, "solo": 0, "errors": 0}
        self._batch_window_ms = batch_window_ms if batch_window_ms is not None else _LLM_BATCH_WINDOW_MS

    async def call(self, messages: list, model: str = None, max_tokens: int = 1024, provider_list=None, use_admin_sem: bool = False) -> str:
        if provider_list is _LLM_PROVIDERS_CHAT:
            provider_tag = "chat"
        elif provider_list is _LLM_PROVIDERS_CONTENT:
            provider_tag = "admin"
        else:
            provider_tag = "all"
        batch_key = _cache_key(
            provider_tag + ":" + "".join(m.get("content", "") for m in messages if m.get("role") in ("user", "system"))
        )

        async with self._lock:
            if batch_key in self._pending:
                self._stats["deduped"] += 1
                logger.info(f"LLM batch DEDUP: {batch_key} — piggy-backing on in-flight request")
                future = self._pending[batch_key]
        
            else:
                future = asyncio.get_event_loop().create_future()
                self._pending[batch_key] = future
                self._stats["batched"] += 1
                asyncio.ensure_future(self._execute(batch_key, messages, model, max_tokens, future, provider_list, use_admin_sem))

        try:
            return await asyncio.wait_for(future, timeout=120)
        except asyncio.TimeoutError:
            logger.error(f"LLM batch TIMEOUT: {batch_key}")
            raise HTTPException(status_code=504, detail="AI response timed out. Please try again.")

    async def _execute(self, batch_key: str, messages: list, model: str, max_tokens: int, future: asyncio.Future, provider_list=None, use_admin_sem: bool = False):
        await asyncio.sleep(self._batch_window_ms / 1000.0)

        sem = _ADMIN_LLM_SEMAPHORE if use_admin_sem else _LLM_SEMAPHORE
        try:
            async with sem:
                result = await _call_llm_raw(messages, model, max_tokens, provider_list=provider_list)
            future.set_result(result)
        except Exception as e:
            self._stats["errors"] += 1
            if not future.done():
                future.set_exception(e)
        finally:
            async with self._lock:
                self._pending.pop(batch_key, None)

    @property
    def stats(self):
        return {**self._stats, "pending": len(self._pending)}

_llm_batcher = _LlmBatcher(batch_window_ms=_LLM_BATCH_WINDOW_MS)
_content_batcher = _LlmBatcher(batch_window_ms=_CONTENT_BATCH_WINDOW_MS)

# ── Sarvam provider list — Assamese-only ─────────────────────────────────────
# Sarvam is intentionally segregated into its own provider list and is NEVER
# added to `_LLM_PROVIDERS`, `_LLM_PROVIDERS_CHAT`, `_LLM_PROVIDERS_CONTENT`,
# `_SLM_SLOT_CANDIDATES`, or `_CONTENT_SLOT_CANDIDATES`. Sarvam billing /
# quota is reserved for the two Assamese paths that benefit from its native
# Indic grounding:
#
#   1. Assamese chat response generation — the hedged Sarvam-key race in
#      `call_llm_api_stream` (gated on `_indic_mode == _is_indic_lang(lang)`,
#      where `_INDIC_LANG_CODES = {"as"}`). Indic resolution reads from
#      `_SARVAM_PROVIDERS` to find a Sarvam key.
#   2. Assamese translation — `routes/ai_chat.py` calls Sarvam's `/translate`
#      endpoint only when `_SARVAM_LANG_MAP[lang]` is set, and that map only
#      contains `{"as": "as-IN"}`.
#
# Any other request (English, Hindi, content-generation pools, admin notes,
# PYQ, important questions, etc.) MUST NOT touch Sarvam — even if Sarvam's
# key was working, it would drift to the wrong script for non-Assamese.
_SARVAM_PROVIDERS: list[dict] = []
if _SARVAM_LLM_KEY_3:
    _SARVAM_PROVIDERS.append({"provider": "sarvam", "key": _SARVAM_LLM_KEY_3, "default_model": "sarvam-m"})
if _SARVAM_LLM_KEY_2 and _SARVAM_LLM_KEY_2 != _SARVAM_LLM_KEY_3:
    _SARVAM_PROVIDERS.append({"provider": "sarvam", "key": _SARVAM_LLM_KEY_2, "default_model": "sarvam-m"})
if _SARVAM_LLM_KEY and _SARVAM_LLM_KEY not in (_SARVAM_LLM_KEY_3, _SARVAM_LLM_KEY_2):
    _SARVAM_PROVIDERS.append({"provider": "sarvam", "key": _SARVAM_LLM_KEY, "default_model": "sarvam-m"})

# ── Cloudflare Workers AI — PRIMARY provider (2026-04-29 upgrade) ──────────────
# Workers AI is now Tier 1. With $5k Cloudflare startup credits and the
# account on Enterprise, Workers AI is cheaper and lower-latency than
# Groq/Cerebras/OpenRouter for our Assam-region user base.
#
# Provider key: "workers-ai" — uses providers/cloudflare_ai.py which calls
# the CF REST API (or AI Gateway) directly without an edge worker round-trip.
# The CLOUDFLARE_API_TOKEN env var (already set) is the credential; no new key.
#
# Models in priority order:
#   chat  → llama-3.3-70b-instruct-fp8-fast (70B, fp8 quantised, 16k context)
#   admin → gpt-oss-120b (admin content gen, long-form notes, MCQ batches)
# Gemini/Groq/Cerebras remain as secondary fallbacks below.
_CF_AI_ACCOUNT_ID = os.environ.get("CF_AI_GATEWAY_ACCOUNT_ID", "").strip()
_CF_API_TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
_CF_AI_ENABLED = bool(_CF_AI_ACCOUNT_ID and _CF_API_TOKEN)

_LLM_PROVIDERS = []
if _CF_AI_ENABLED:
    _LLM_PROVIDERS.append({"provider": "workers-ai", "key": _CF_API_TOKEN, "default_model": "@cf/meta/llama-3.3-70b-instruct-fp8-fast"})
# NOTE: legacy "gemini" provider entry removed (vertex-only Gemini auth,
# 2026-05-03). Task #490 then scoped Vertex to `content_format` only —
# chat/vision/translate/embed Vertex branches are gone; only the
# NotebookLM-style polish path via `vertex_format.format_with_vertex`
# remains.

_LLM_PROVIDERS_CHAT: list[dict] = []
if _CF_AI_ENABLED:
    _LLM_PROVIDERS_CHAT.append({"provider": "workers-ai", "key": _CF_API_TOKEN, "default_model": "@cf/meta/llama-3.3-70b-instruct-fp8-fast"})

# Workers-AI-only slice of _LLM_PROVIDERS_CHAT — used as the ``workers_ai``
# fallback inside _dispatch_llm_for_feature so that PROVIDER_PRIORITY routing
# never spills into Groq / Cerebras / Gemini on the fall-through path.
_LLM_PROVIDERS_WORKERS_ONLY: list[dict] = [
    p for p in _LLM_PROVIDERS_CHAT if p["provider"] == "workers-ai"
]

_MODEL_PROVIDER_MAP = {
    "sarvam-m": "sarvam",
    "sarvam-30b": "sarvam",
    "sarvam-30b-16k": "sarvam",
    "sarvam-105b": "sarvam",
    "sarvam-105b-32k": "sarvam",
    # Task #490: `"gemini-2.5-flash" -> "gemini"` mapping removed; the
    # in-llm gemini chat dispatch branch was deleted along with the
    # Vertex chat hot path. Gemini 2.5 Flash is now reachable ONLY via
    # `vertex_format.format_with_vertex` (formatter polish).
    "gpt-4o-mini": "azure_openai",
    "gpt-4.1-mini": "azure_openai",
}

_MODEL_ALIAS_MAP = {
    # Workers AI model aliases — redirect old names to current Workers AI equivalents
    "openai/gpt-oss-20b":  "@cf/openai/gpt-oss-20b",
    "openai/gpt-oss-120b": "@cf/openai/gpt-oss-120b",
    "llama-3.3-70b-versatile": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
    "llama-3.3-70b": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
    # Task #490: legacy Gemini-version aliases removed. The "gemini"
    # chat provider was deleted along with the Vertex chat hot path,
    # so a `"gemini-2.0-flash" -> "gemini-2.5-flash"` rewrite would
    # only resolve to a model id no chat dispatch branch can serve.
    # Any caller that still passes a `gemini-*` model id will now
    # fail loud with "no provider configured" instead of silently
    # routing to a deleted backend. The formatter consumes
    # `VERTEX_GEMINI_MODEL` directly via `vertex_format`.
}

# ── SLM slot table ────────────────────────────────────────────────────────────
# Each entry: (provider, model, max_concurrent, speed_tier)
# speed_tier: lower = faster provider, used by pick() to prefer fast slots.
# Slots in the same tier are load-balanced by in-flight count.
#
# Concurrency caps — Cloudflare Workers AI unified billing (Standard plan).
# Each model carries its own independent 3 000 RPM quota; the 5 chat models
# give a combined ~15 000 RPM headroom.  The shared SmartKeyPool rpm_window
# tracks them under a single 10 000 RPM budget (_POOL_RPM_LIMITS["workers-ai"])
# to avoid over-counting while still letting the pool fill each model's quota.
#
# Per-model concurrency math (rpm / 60 × avg_resp_s):
#   70B FP8 (~2.0s):  3 000/60 × 2.0  = 100 concurrent  → cap at 64 (headroom)
#   20B     (~0.8s):  3 000/60 × 0.8  =  40 concurrent  → cap at 64 (safe)
#   72B     (~2.0s):  3 000/60 × 2.0  = 100 concurrent  → cap at 64
#   3B      (~0.3s):  3 000/60 × 0.3  =  15 concurrent  → cap at 128 (burst)
#   8B FP8  (~0.6s):  3 000/60 × 0.6  =  30 concurrent  → cap at 64
#
# Total chat caps: 64+64+64+128+64 = 384 concurrent (within combined RPM budget).
_SLM_SLOT_CANDIDATES = [
    # Tier 0: Workers AI llama-3.3-70b-fp8 — primary chat provider (70B, FP8).
    ("workers-ai",  "@cf/meta/llama-3.3-70b-instruct-fp8-fast",        64, 0),
    # Tier 1: Workers AI GPT-OSS-20B — fast 20B model, own 3 000 RPM quota.
    ("workers-ai",  "@cf/openai/gpt-oss-20b",                          64, 1),
    # Tier 2: Workers AI Qwen 2.5-72B — high-quality 72B on separate quota.
    ("workers-ai",  "@cf/qwen/qwen2.5-72b-instruct",                   64, 2),
    # Tier 3: Workers AI llama-3.2-3b — ultrafast 3B for burst traffic.
    ("workers-ai",  "@cf/meta/llama-3.2-3b-instruct",                 128, 3),
    # Tier 4: Workers AI llama-3.1-8b — fast 8B fallback.
    ("workers-ai",  "@cf/meta/llama-3.1-8b-instruct-fp8",              64, 4),
    # Task #490: Tier 5 (`("gemini", "gemini-2.5-flash", 4, 5)`) removed —
    # the gemini chat provider was deleted along with the Vertex chat
    # hot path. Workers-AI tiers 0–4 above are now the full chat pool
    # (Azure OpenAI gpt-4.1-nano sits ahead of the SLM pool entirely
    # in `PROVIDER_PRIORITY['english_rag_chat']`). Vertex Gemini 2.5
    # Flash is reachable ONLY via `vertex_format.format_with_vertex`.
]

# Content SmartKeyPool — serves `_CONTENT_INTENTS` (notes, important_questions,
# pyq) for ALL languages.
#
# Tier order:
#   0 — Workers AI gpt-oss-120b: 120B model, best for long-form content.
#   1 — Workers AI llama-3.3-70b: fallback for content generation.
#
# Content calls are longer (300-600 tokens output, ~4-8s) but lower volume.
# Cap at 48 per slot: 3 000/60 × 6s = 300 theoretical → 48 leaves headroom
# for the shared 10 000 RPM combined budget.
_CONTENT_SLOT_CANDIDATES = [
    ("workers-ai",  "@cf/openai/gpt-oss-120b",                         48, 0),
    ("workers-ai",  "@cf/meta/llama-3.3-70b-instruct-fp8-fast",        48, 1),
]

_CONTENT_INTENTS = {"notes", "important_questions", "pyq"}


def _parse_rpm_limit(env_var: str, default: int) -> int:
    """Safely parse an RPM-limit env var, falling back to *default* on bad input.

    Logs a warning (never raises) so a misconfigured value never prevents
    the service from starting.
    """
    raw = os.environ.get(env_var, "")
    if raw:
        try:
            val = int(raw)
            if val > 0:
                return val
            logger.warning(
                "RPM env var %s=%r is not a positive integer — using default %d",
                env_var, raw, default,
            )
        except ValueError:
            logger.warning(
                "RPM env var %s=%r is not an integer — using default %d",
                env_var, raw, default,
            )
    return default


# Per-provider RPM caps used by _SmartKeyPool._PROVIDER_RPM_LIMITS.
# All values are env-overridable so ops can tune them without a deploy.
#
# Workers AI — Cloudflare Standard plan (unified billing enabled).
# Each model has its own independent 3 000 RPM quota (not shared across models).
# The SmartKeyPool tracks all Workers AI slots via a single shared rpm_window
# (same API token / key_idx), so we set the combined budget to 10 000 RPM —
# safely below the ~15 000 total (5 models × 3 000) but high enough that the
# pool never soft-deprioritises Workers AI under normal load.
# Override with WORKERS_AI_RPM_LIMIT env var if the account tier changes.
#
# NOTE: Workers AI embedding (@cf/baai/bge-large-en-v1.5) is NOT rate-limited
#   by this pool — it goes through vertex_services._workers_ai_primary_embed()
#   which has its own burst-cooldown path (vertex_services._track_embed_429).
#   On the Standard plan, the embedding model also has 3 000 RPM (same as LLMs);
#   the old ~50 RPM free-tier limit no longer applies.  See _EMBED_429_THRESHOLD
#   in vertex_services.py — threshold raised to 10 hits accordingly.
# 2026-05-05 — Per-provider RPM is derived from PROVIDER_MAX_CONCURRENT × 60
# (formula chosen by the user: assumes ~1 second per request). The legacy
# per-provider env-var overrides (WORKERS_AI_RPM_LIMIT, SARVAM_RPM_LIMIT, …)
# still take precedence so ops can tune any single provider without a deploy.
# Task #347 — bare ``openai`` RPM entry retained because the AsyncOpenAI SDK
# is still the transport for Azure OpenAI / Workers AI / CF AI Gateway calls
# (no real api.openai.com traffic).
def _build_pool_rpm_limits() -> dict:
    from config import PROVIDER_MAX_CONCURRENT
    env_var_for = {
        "workers-ai":   "WORKERS_AI_RPM_LIMIT",
        "sarvam":       "SARVAM_RPM_LIMIT",
        "gemini":       "GEMINI_RPM_LIMIT",
        "azure_openai": "AZURE_OPENAI_RPM_LIMIT",
        "openai":       "OPENAI_RPM_LIMIT",
    }
    out: dict = {}
    for provider, max_concurrent in PROVIDER_MAX_CONCURRENT.items():
        derived = max_concurrent * 60
        env_var = env_var_for.get(provider, f"{provider.upper().replace('-', '_')}_RPM_LIMIT")
        out[provider] = _parse_rpm_limit(env_var, derived)
    return out

_POOL_RPM_LIMITS = _build_pool_rpm_limits()
logger.info(
    "SLM RPM limits (max_concurrent × 60, env-overridable): %s",
    _POOL_RPM_LIMITS,
)


class _SmartKeyPool:
    """Concurrent smart pool — maximises RPS across all providers.

    Each slot has:
      sem            asyncio.Semaphore(max_concurrent) — caps parallel in-flight requests
      priority       int — list-order index; lower = faster provider, always preferred
      last_used      float timestamp — for mark_ok tracking
      cooldown_until float timestamp — set after 429 / errors
      errors         int            — error count for exponential back-off
      rpm_window     list[float]    — timestamps of requests in the current minute
      rpm_limit      int            — max requests per minute for this provider

    pick() uses RPM-aware scoring: when a slot hits 70% of its RPM limit,
    it gets deprioritized so traffic shifts to the next provider BEFORE hitting 429.
    """
    _RL_COOLDOWN  = 20.0
    _ERR_COOLDOWN = 7.0
    # With unified billing and a combined 10 000 RPM budget, keep Workers AI
    # as primary until 85% (8 500 RPM) before soft-shifting to fallbacks,
    # and hard-deprioritize only at 95% (9 500 RPM). Per-model quota is
    # independent so a single model saturating does not affect others.
    # Groq removed in Task #347 / V4 §0; Cerebras removed in Task #491.
    _RPM_SOFT_THRESHOLD = 0.85
    _RPM_HARD_THRESHOLD = 0.95

    # RPM limits per provider — see _parse_rpm_limit() for the env-var safe parser.
    # Workers AI: CF Standard plan with unified billing — 3 000 RPM per model.
    # Override with WORKERS_AI_RPM_LIMIT env var if the account tier differs.
    _PROVIDER_RPM_LIMITS = _POOL_RPM_LIMITS  # module-level dict, populated just above

    def __init__(self, candidates: list):
        pmap: dict = {}
        for p in _LLM_PROVIDERS:
            pname = p["provider"]
            if pname not in pmap:
                pmap[pname] = []
            pmap[pname].append(p["key"])
        self._slots = []
        shared_rpm: dict = {}
        for pname, model_id, max_con, tier in candidates:
            real_provider = pname.split(":")[0]
            key_idx = int(pname.split(":")[1]) - 1 if ":" in pname else 0
            keys = pmap.get(real_provider, [])
            key = keys[key_idx] if key_idx < len(keys) else ""
            # Task #347: ``bedrock`` removed from the keyless-eligible set;
            # only ``sarvam`` is allowed to claim a slot without a key
            # (uses platform-managed creds).
            if key or real_provider == "sarvam":
                rpm = self._PROVIDER_RPM_LIMITS.get(real_provider, 30)
                rpm_key = f"{real_provider}:{key_idx}"
                if rpm_key not in shared_rpm:
                    shared_rpm[rpm_key] = []
                self._slots.append({
                    "provider": real_provider, "key": key, "model": model_id,
                    "sem": asyncio.Semaphore(max_con), "max_con": max_con,
                    "last_used": 0.0, "cooldown_until": 0.0, "errors": 0,
                    "priority": tier,
                    "rpm_window": shared_rpm[rpm_key], "rpm_limit": rpm,
                    "base_priority": tier,
                    "rpm_warn_until": 0.0,  # suppresses repeated soft-threshold warnings
                })
        logger.info(
            f"SLM SmartKeyPool active slots: "
            f"{[(s['provider'], s['model'], s['max_con'], s['rpm_limit']) for s in self._slots]}"
        )

    def _rpm_count(self, slot):
        now = time.time()
        cutoff = now - 60.0
        slot["rpm_window"] = [t for t in slot["rpm_window"] if t > cutoff]
        return len(slot["rpm_window"])

    def _rpm_ratio(self, slot):
        count = self._rpm_count(slot)
        return count / slot["rpm_limit"] if slot["rpm_limit"] > 0 else 0.0

    def _record_request(self, slot):
        slot["rpm_window"].append(time.time())

    # Task #247: Workers AI aggregate load threshold below which Gemini is
    # heavily penalized so it NEVER pre-empts Workers AI. Only when Workers
    # AI is above this utilization does Gemini become a live fallback.
    _GEMINI_WAI_LOAD_THRESHOLD = 0.80
    # Penalty added to Gemini priority when Workers AI load < threshold.
    # 50 >> any Workers AI slot (0–4) so Workers AI always wins below 80%.
    _GEMINI_WAI_PENALTY = 50

    def _workers_ai_aggregate_load(self) -> float:
        """Return fractional aggregate load (0.0–1.0+) across all Workers AI slots."""
        wai_slots = [s for s in self._slots if s["provider"] == "workers-ai"]
        if not wai_slots:
            return 0.0
        total_ratio = sum(self._rpm_ratio(s) for s in wai_slots)
        return total_ratio / len(wai_slots)

    def _effective_priority(self, slot):
        ratio = self._rpm_ratio(slot)
        base = slot["base_priority"]
        if ratio >= self._RPM_HARD_THRESHOLD:
            return base + 100
        if ratio >= self._RPM_SOFT_THRESHOLD:
            return base + 10
        # Task #490: dead `slot["provider"] == "gemini"` priority-penalty
        # branch removed — no gemini slot is registered in
        # `_SLM_SLOT_CANDIDATES` anymore. The `_GEMINI_WAI_*` constants
        # are retained as inert config (no live consumer) so admin
        # health diagnostics that probe their existence don't NameError.
        return base

    def pick(self, exclude_ids: set = None):
        now = time.time()
        available = [s for s in self._slots if now >= s["cooldown_until"]]
        if exclude_ids:
            available = [s for s in available if id(s) not in exclude_ids]
        if not available:
            return None

        for s in available:
            ratio = self._rpm_ratio(s)
            # Soft-threshold warning: log once per 60s per slot so the team
            # can see RPM pressure building before a 429 actually fires.
            if ratio >= self._RPM_SOFT_THRESHOLD and now >= s.get("rpm_warn_until", 0.0):
                remaining = self._seconds_until_rpm_drop(s)
                level = "WARNING" if ratio >= self._RPM_HARD_THRESHOLD else "INFO"
                msg = (
                    f"SLM pool: {s['provider']}/{s['model']} at {ratio*100:.0f}% RPM "
                    f"({self._rpm_count(s)}/{s['rpm_limit']}) — "
                    f"{'near limit, strongly deprioritizing' if ratio >= self._RPM_HARD_THRESHOLD else 'soft threshold crossed, shifting traffic'}"
                    + (f" (~{remaining:.0f}s to free)" if remaining > 0 else "")
                )
                if level == "WARNING":
                    logger.warning(msg)
                else:
                    logger.info(msg)
                s["rpm_warn_until"] = now + 60.0  # suppress for 60 s

        with_capacity = [s for s in available if s["sem"]._value > 0]
        pool = with_capacity if with_capacity else available
        return min(pool, key=lambda s: (self._effective_priority(s), s["max_con"] - s["sem"]._value))

    def _seconds_until_rpm_drop(self, slot):
        if not slot["rpm_window"]:
            return 0
        cutoff = time.time() - 60.0
        future_exits = [t - cutoff for t in slot["rpm_window"] if t > cutoff]
        if not future_exits:
            return 0
        return min(future_exits)

    def mark_ok(self, slot):
        slot["last_used"] = time.time()
        slot["errors"] = 0
        self._record_request(slot)
        _reset_provider_429(slot["provider"])   # no-op for untracked providers

    def mark_429(self, slot):
        slot["cooldown_until"] = time.time() + self._RL_COOLDOWN
        self._record_request(slot)
        logger.warning(
            f"SLM pool: {slot['provider']}/{slot['model']} → 429 rate-limit "
            f"(RPM {self._rpm_count(slot)}/{slot['rpm_limit']}), cooling {self._RL_COOLDOWN}s"
        )
        _track_provider_429(slot["provider"])   # no-op for untracked providers

    def mark_403(self, slot):
        slot["cooldown_until"] = float("inf")
        logger.error(
            f"SLM pool: {slot['provider']}/{slot['model']} → 403 Forbidden (auth/permission error). "
            f"Slot permanently disabled. Check the API key for '{slot['provider']}'."
        )

    def mark_err(self, slot):
        slot["errors"] += 1
        cd = min(self._ERR_COOLDOWN * slot["errors"], 120.0)
        slot["cooldown_until"] = time.time() + cd
        logger.warning(
            f"SLM pool: {slot['provider']}/{slot['model']} → error #{slot['errors']}, "
            f"cooling {cd:.0f}s"
        )

    def rpm_status(self):
        return [
            {
                "provider": s["provider"], "model": s["model"],
                "rpm_used": self._rpm_count(s), "rpm_limit": s["rpm_limit"],
                "rpm_pct": round(self._rpm_ratio(s) * 100, 1),
                "effective_priority": self._effective_priority(s),
                "cooldown": s["cooldown_until"] > time.time(),
            }
            for s in self._slots
        ]

    @property
    def all_slots(self):
        return self._slots

_slm_pool = _SmartKeyPool(_SLM_SLOT_CANDIDATES)


class _ContentSmartKeyPool(_SmartKeyPool):
    _RPM_SOFT_THRESHOLD = 0.90
    _RPM_HARD_THRESHOLD = 0.95

    async def pick_or_wait(self, max_wait: float = None, exclude_ids: set = None):
        if max_wait is None:
            max_wait = _CONTENT_RPM_MAX_WAIT
        slot = self.pick(exclude_ids=exclude_ids)
        if slot is not None:
            return slot

        deadline = time.time() + max_wait
        while time.time() < deadline:
            wait_time = min(2.0, deadline - time.time())
            if wait_time <= 0:
                break
            best_wait = None
            for s in self._slots:
                if exclude_ids and id(s) in exclude_ids:
                    continue
                if s["cooldown_until"] > time.time():
                    cd_remaining = s["cooldown_until"] - time.time()
                    if cd_remaining <= max_wait:
                        best_wait = min(best_wait or cd_remaining, cd_remaining)
                secs = self._seconds_until_rpm_drop(s)
                if secs > 0:
                    best_wait = min(best_wait or secs, secs)

            actual_wait = min(best_wait or 2.0, wait_time)
            logger.info(
                f"Content pool: all providers at capacity, waiting {actual_wait:.1f}s for RPM to free up "
                f"(max wait remaining: {deadline - time.time():.1f}s)"
            )
            await asyncio.sleep(actual_wait)
            slot = self.pick(exclude_ids=exclude_ids)
            if slot is not None:
                return slot
        logger.warning("Content pool: max wait exceeded, no provider available")
        return None


_content_pool = _ContentSmartKeyPool(_CONTENT_SLOT_CANDIDATES)

def _resolve_provider_for_model(model: str, provider_list=None):
    plist = _LLM_PROVIDERS if provider_list is None else provider_list
    preferred = _MODEL_PROVIDER_MAP.get(model)
    if preferred:
        for p in plist:
            if p["provider"] == preferred:
                return p["provider"], p["key"]
    if plist:
        return plist[0]["provider"], plist[0]["key"]
    return LLM_PROVIDER, OPENAI_API_KEY


def _safe_model_for_provider(model: str, provider: str, provider_list=None) -> str:
    """Return a model name that the given provider actually supports.
    If the requested model is already mapped to this provider, use it as-is.
    For Sarvam, always use sarvam-m unless the model already starts with 'sarvam-'.
    Otherwise fall back to the provider's configured default_model."""
    if provider == "sarvam" and not model.startswith("sarvam-"):
        return "sarvam-m"
    # Task #347 / V4 §0: groq branch removed — provider no longer dispatchable.
    mapped_provider = _MODEL_PROVIDER_MAP.get(model)
    if mapped_provider == provider:
        return model
    plist = _LLM_PROVIDERS if provider_list is None else provider_list
    matched = next((p for p in plist if p["provider"] == provider), None)
    if matched:
        return matched["default_model"]
    return model

def _pick_sarvam_client():
    # Task #492 (V4 §15) collapsed the Sarvam chat surface to a single
    # client. The CF-Gateway-bypass twin (`sarvam_llm_client_direct`) was
    # removed per the task's acceptance gate; CF Gateway outages now
    # surface as a loud failure so the assamese_rag_chat dispatcher
    # advances to the Workers-AI IndicTrans2 leg instead of silently
    # bypassing the gateway.
    return sarvam_llm_client

async def _call_sarvam_llm(messages: list, api_key: str, model: str, max_tokens: int) -> str:
    """Non-streaming call to Sarvam LLM — reuses persistent sarvam_llm_client (zero TCP overhead).
    Adds SARVAM_THINK_BUFFER so the <think> block never consumes the user's answer budget.
    Per Task #492 there is no direct-client fallback; gateway/auth errors propagate
    so dispatch can advance to the next provider."""
    api_max = max_tokens + SARVAM_THINK_BUFFER
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": api_max,
        "temperature": 0.1,
        "stream": False,
    }
    client = _pick_sarvam_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Sarvam LLM client not initialised")
    resp = await client.post("/v1/chat/completions", json=payload)
    resp.raise_for_status()
    data = resp.json()
    choice = data["choices"][0]["message"]
    content = choice.get("content") or ""
    reasoning = choice.get("reasoning_content") or ""
    result = content if content else reasoning
    result = re.sub(r'<think>.*?</think>', '', result, flags=re.DOTALL).strip()
    result = re.sub(r'<think>.*$', '', result, flags=re.DOTALL).strip()
    return result

def _cf_cache_headers(api_key: Optional[str] = None, *, clear_upstream_auth: Optional[bool] = None) -> dict:
    # Delegates to config.byok_headers() which returns:
    #   cf-aig-byok-key:true      — CF may substitute the stored BYOK key upstream
    #   cf-aig-cache-ttl:<N>      — cache TTL hint
    #   cf-aig-authorization:…    — only when Authenticated Gateway mode is on
    # Returns {} when the gateway is down — callers should raise or continue
    # without the caching hint.
    #
    # Auth-header behaviour (FIXED 2026-04-26 after architect review):
    # The decision of whether to clear the SDK's auto-attached
    # ``Authorization: Bearer <api_key>`` is per-call, derived from the
    # api_key the caller is about to send:
    #   • api_key == BYOK_PLACEHOLDER ("x")  → BYOK mode, CF must
    #     substitute the stored key upstream → CLEAR Authorization
    #     so CF doesn't forward "Bearer x" (which 401s upstream).
    #   • api_key is a REAL provider key      → keep Authorization so
    #     CF forwards it to the upstream provider. The original bug
    #     (default cleared) produced 400 "Missing or invalid
    #     Authorization header" from Google Gemini whenever the CF
    #     dashboard's BYOK binding was missing or stale.
    # Callers can still force a value via ``clear_upstream_auth=...``
    # for tests or special bypass paths; otherwise pass ``api_key``.
    if clear_upstream_auth is None:
        clear_upstream_auth = (api_key == BYOK_PLACEHOLDER)
    return byok_headers(clear_upstream_auth=clear_upstream_auth)

def _is_cf_connection_error(exc: Exception) -> bool:
    err = str(exc).lower()
    return "connect" in err or "timeout" in err or "unreachable" in err or "dns" in err

def _handle_cf_connection_error(exc: Exception) -> None:
    if _is_cf_connection_error(exc):
        mark_cf_gateway_down()
        logger.warning(f"Cloudflare AI Gateway connection error — falling back to direct URLs for 5 min: {type(exc).__name__}")

def _handle_cf_gateway_auth_error(exc: Exception) -> None:
    mark_cf_gateway_down()
    logger.warning(f"Cloudflare AI Gateway 401 auth error — falling back to direct URLs for 5 min: {type(exc).__name__}: {str(exc)[:200]}")

# ── Vertex AI native chat — REMOVED (Task #490) ──────────────────────────────
# The SA-OAuth Vertex chat helper was removed. Vertex is
# no longer a chat / vision / translate / embed provider. The only remaining
# Vertex surface is `vertex_format.format_with_vertex` (NotebookLM-style
# polish) which `polish_notes_with_vertex` delegates to.
def _is_cf_gateway_base(base: str) -> bool:
    """True iff ``base`` is a Cloudflare AI Gateway URL (so cf-aig-* response
    headers are expected). When the gateway is down we fall back to
    direct provider URLs and must NOT record AI Gateway telemetry."""
    from config import CF_GATEWAY_BASE as _CF_BASE
    return bool(_CF_BASE) and base.startswith(_CF_BASE)


def _record_aig_from_raw(raw: Any, *, base: str, provider: str, model: str) -> None:
    """Pure-observation: forward cf-aig-* response headers to the AI
    Gateway counters when the request actually went through CF. Never
    raises — telemetry must not be able to break a chat response."""
    if not _is_cf_gateway_base(base):
        return
    try:
        from ai_gateway_observability import record_aig_response
        record_aig_response(getattr(raw, "headers", {}) or {},
                            provider=provider, model=model)
    except Exception:
        pass


async def _call_openai_compat(messages: list, api_key: str, model: str, max_tokens: int, provider: str, fallback_base: str) -> str:
    """Non-streaming call via an OpenAI-compatible provider (OpenAI, xAI, Fireworks)."""
    base = get_provider_base_url(provider) or fallback_base
    client = _get_oai_client(api_key, base)
    raw = None
    try:
        # Task #420 — use ``with_raw_response`` so the underlying httpx
        # Response (and its cf-aig-* headers) is reachable. The parsed
        # body is identical to the regular client.chat.completions.create
        # return value, just one ``.parse()`` call away.
        raw = await client.chat.completions.with_raw_response.create(
            model=model, messages=messages, max_tokens=max_tokens, temperature=0.1,
            # Pass api_key so BYOK
            # placeholders correctly trigger the clear-Authorization branch.
            extra_headers=_cf_cache_headers(api_key=api_key) or None,
        )
        resp = raw.parse()
    except _oai.APIConnectionError as e:
        if base != fallback_base and _is_cf_connection_error(e):
            _handle_cf_connection_error(e)
            client = _get_oai_client(api_key, fallback_base)
            base = fallback_base
            raw = await client.chat.completions.with_raw_response.create(
                model=model, messages=messages, max_tokens=max_tokens, temperature=0.1,
            )
            resp = raw.parse()
        else:
            raise
    except _oai.AuthenticationError as e:
        if base != fallback_base:
            _handle_cf_gateway_auth_error(e)
            client = _get_oai_client(api_key, fallback_base)
            base = fallback_base
            raw = await client.chat.completions.with_raw_response.create(
                model=model, messages=messages, max_tokens=max_tokens, temperature=0.1,
            )
            resp = raw.parse()
        else:
            raise
    _record_aig_from_raw(raw, base=base, provider=provider, model=model)
    content = resp.choices[0].message.content or ""
    return re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()

# Task #491 — legacy SLM single-provider call helper removed.

async def _call_single_provider(messages: list, provider: str, api_key: str, model: str, max_tokens: int) -> str:
    model = _MODEL_ALIASES.get(model, model)
    max_tokens = _clamp_max_tokens(model, max_tokens)
    if provider == "workers-ai":
        from providers.cloudflare_ai import chat as _cf_chat, MODELS as _CF_MODELS
        if model.startswith("@cf/"):
            model_key = model
        else:
            model_key = "chat"
            if "120b" in model or "gpt-oss" in model:
                model_key = "chat_long"
            elif "coder" in model:
                model_key = "chat_code"
            elif "8b" in model or "fast" in model.lower():
                model_key = "chat_fast"
        text = await _cf_chat(messages, model_key=model_key, max_tokens=max_tokens)
        return re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
    if provider == "sarvam":
        return await _call_sarvam_llm(messages, api_key, model, max_tokens)
    # Task #490 — gemini→vertex dispatch removed. Vertex is now scoped to
    # `content_format` (vertex_format.format_with_vertex) only.
    # Task #491 — legacy SLM dispatch branch removed.
    # Task #347 / V4 §0: groq dispatch branch removed — provider no longer
    # in PROVIDER_PRIORITY; CF AI Gateway slug `groq/v1` is not configured.
    # Task #347: xAI/Grok dispatch branch removed — provider is no longer
    # in PROVIDER_PRIORITY and the SDK is uninstalled.
    if provider == "openrouter":
        return await _call_openai_compat(messages, api_key, model, max_tokens, "openrouter", "https://openrouter.ai/api/v1")
    if provider == "azure_openai":
        # Task #290 — route through providers.azure_openai so we get the full
        # candidate chain (CF BYOK → direct KEY_1 → direct KEY_2) and
        # consistent failover across every dispatch path.
        from providers.azure_openai import call_chat as _az_chat
        from config import AZURE_OPENAI_DEPLOYMENT as _AZ_DEPL
        return await _az_chat(messages, model=model or _AZ_DEPL, max_tokens=max_tokens)

    system_msg = ""
    user_msg = ""
    for m in messages:
        if m["role"] == "system":
            system_msg = m["content"]
        elif m["role"] == "user":
            user_msg = m["content"]

    chat = LlmChat(
        api_key=api_key,
        session_id=str(uuid.uuid4()),
        system_message=system_msg or "You are a helpful AI tutor.",
    ).with_model(provider, model)

    response = await chat.send_message(UserMessage(text=user_msg))
    response = re.sub(r'<think>.*?</think>', '', response, flags=re.DOTALL).strip()
    return response

async def _call_llm_raw(messages: list, model: str = None, max_tokens: int = 1024, provider_list=None, feature_key: str = "") -> str:
    # Task #513 §B — token-budget clamp. Resolve a coarse call_type from
    # `feature_key` (chat hot path → chat_turn; everything else falls
    # back to chat_turn's conservative ceiling). The clamp runs BEFORE
    # provider dispatch so an over-budget message never reaches a paid
    # endpoint. See artifacts/syrabit-backend/cost_caps.py for the
    # locked TOKEN_BUDGETS table.
    from cost_caps import clamp_messages, max_output_tokens_for
    _ct = "chat_turn"
    if feature_key in ("content", "content_generation"):
        _ct = "content_generation"
    elif feature_key in ("translate",):
        _ct = "translate"
    elif feature_key in ("vision", "vision_ocr"):
        _ct = "vision_ocr"
    messages = clamp_messages(messages, call_type=_ct)
    max_tokens = max_output_tokens_for(_ct, max_tokens)
    import time as _t
    # Wall-clock start of the whole primary-rotation loop. Used so the
    # Workers AI fallback path (Task #636) can attribute the *real*
    # cumulative primary latency (instead of 0) when we eventually give
    # up and call the edge.
    _loop_t0 = _t.perf_counter()
    providers = _LLM_PROVIDERS if provider_list is None else provider_list
    use_model = _MODEL_ALIAS_MAP.get(model or LLM_MODEL, model or LLM_MODEL)
    primary_provider, primary_key = _resolve_provider_for_model(use_model, providers)

    if not primary_key and not providers:
        raise HTTPException(status_code=503, detail="LLM API key not configured")

    tried: set = set()
    last_err = None

    _is_content = provider_list is _LLM_PROVIDERS_CONTENT
    _is_chat = provider_list is _LLM_PROVIDERS_CHAT
    _is_rag = provider_list is _RAG_PROVIDERS
    # Content: 30s (long generation). RAG: 12s (Gemini thinking can take 8-10s).
    # Chat: 4s (streaming latency budget). General: 6s.
    _PROVIDER_TIMEOUT = 30.0 if _is_content else (12.0 if _is_rag else (4.0 if _is_chat else 6.0))

    # Task #LLM-PARALLEL-FALLBACK: Race multiple providers in parallel to reduce worst-case latency
    # Sequential fallback caused 90-120s worst-case; parallel reduces to ~8s (90%+ improvement)
    
    async def _call_with_tracking(provider_cfg, key, try_model, is_fallback=False):
        """Call single provider with timeout and metrics tracking. Returns (success, result, error)."""
        fb_key_id = id(key) if key else 0
        tried.add((provider_cfg["provider"], try_model, fb_key_id))
        try:
            _t0 = _t.perf_counter()
            result = await asyncio.wait_for(
                _call_single_provider(messages, provider_cfg["provider"], key, try_model, max_tokens),
                timeout=_PROVIDER_TIMEOUT,
            )
            _dur = int((_t.perf_counter() - _t0) * 1000)
            tok = len(result.split())
            _record_llm_call(provider_cfg["provider"], try_model, _dur, True, tok, is_fallback, feature_key=feature_key)
            log_prefix = "llm_call provider=" + provider_cfg["provider"] + f" model={try_model} duration_ms={_dur} tokens_approx={tok}"
            if is_fallback:
                logger.info(log_prefix + " fallback=true")
            else:
                logger.info(log_prefix)
            return (True, LlmResult(result, provider=provider_cfg["provider"]), None)
        except asyncio.TimeoutError:
            _dur = int((_t.perf_counter() - _t0) * 1000)
            _record_llm_call(provider_cfg["provider"], try_model, _dur, False, 0, is_fallback, "Timeout", feature_key=feature_key)
            err = TimeoutError(f"{provider_cfg['provider']}/{try_model} timed out after {_PROVIDER_TIMEOUT}s")
            logger.warning(f"LLM {'fallback' if is_fallback else 'primary'} TIMEOUT ({provider_cfg['provider']}/{try_model}): {_dur}ms > {_PROVIDER_TIMEOUT}s limit")
            return (False, None, err)
        except Exception as e:
            _dur = int((_t.perf_counter() - _t0) * 1000)
            _record_llm_call(provider_cfg["provider"], try_model, _dur, False, 0, is_fallback, type(e).__name__, feature_key=feature_key)
            logger.warning(f"LLM {'fallback' if is_fallback else 'primary'} failed ({provider_cfg['provider']}/{try_model}): {type(e).__name__}: {str(e)[:150]}")
            return (False, None, e)

    # Primary attempt
    provider, key = primary_provider, primary_key
    try_model = _safe_model_for_provider(use_model, provider, providers)
    if try_model != use_model:
        logger.info(f"Model '{use_model}' not compatible with {provider} → using '{try_model}'")
    
    success, result, err = await _call_with_tracking({"provider": provider, "default_model": try_model}, key, try_model, is_fallback=False)
    if success:
        return result
    last_err = err

    # Parallel fallback: race remaining providers concurrently instead of sequentially
    # This reduces worst-case from N*30s to ~PARALLEL_RACE_TIMEOUT where N is number of providers
    
    # Build list of healthy fallback providers to race
    fallback_candidates = []
    for fallback in providers:
        fb_model = fallback["default_model"]
        fb_key_id = id(fallback["key"]) if fallback.get("key") else 0
        if (fallback["provider"], fb_model, fb_key_id) in tried:
            continue
        # Skip providers with high recent error rates (SmartKeyPool health check)
        if fallback.get("_error_rate", 0) > 0.5:  # >50% error rate in recent window
            logger.debug(f"Skipping unhealthy provider {fallback['provider']} (error_rate={fallback.get('_error_rate', 0):.2f})")
            continue
        fallback_candidates.append(fallback)
    
    # Limit concurrent providers in race to avoid overwhelming API quotas
    fallback_to_race = fallback_candidates[:MAX_CONCURRENT_RACE_PROVIDERS]

    # Only run parallel race when:
    #   a) feature flag is on
    #   b) at least MIN_PROVIDERS_TO_RACE healthy candidates exist (with 1
    #      candidate a race is just a sequential call with extra overhead)
    should_race = (
        ENABLE_PARALLEL_LLM_RACE
        and len(fallback_to_race) >= MIN_PROVIDERS_TO_RACE
    )

    if should_race:
        # Race providers concurrently — first valid response wins; the rest
        # are cancelled to avoid wasting quota on slow/unhealthy endpoints.
        race_semaphore = asyncio.Semaphore(MAX_CONCURRENT_RACE_PROVIDERS)

        async def _race_task(fallback):
            async with race_semaphore:
                fb_model = fallback["default_model"]
                fb_key = fallback["key"]
                return await _call_with_tracking(fallback, fb_key, fb_model, is_fallback=True)

        fallback_tasks = [asyncio.create_task(_race_task(fb)) for fb in fallback_to_race]
        try:
            for completed in asyncio.as_completed(fallback_tasks, timeout=PARALLEL_RACE_TIMEOUT):
                success, result, err = await completed
                if success and result:
                    # Cancel losers and clean up
                    for task in fallback_tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*fallback_tasks, return_exceptions=True)
                    return result
                elif err:
                    last_err = err
        except asyncio.TimeoutError:
            logger.warning(
                f"Parallel LLM race timed out after {PARALLEL_RACE_TIMEOUT}s "
                f"({len(fallback_tasks)} providers), cancelling"
            )
            for task in fallback_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*fallback_tasks, return_exceptions=True)
            last_err = TimeoutError(
                f"All {len(fallback_tasks)} providers timed out after "
                f"{PARALLEL_RACE_TIMEOUT}s race window"
            )
        # All parallel tasks failed — fall through to Workers AI last-resort below

    else:
        # Sequential fallback: parallel disabled, or not enough healthy providers
        # to justify the concurrency overhead (< MIN_PROVIDERS_TO_RACE).
        for fallback in fallback_to_race:
            fb_model = fallback["default_model"]
            success, result, err = await _call_with_tracking(
                fallback, fallback["key"], fb_model, is_fallback=True
            )
            if success and result:
                return result
            elif err:
                last_err = err
    
    # Task #636 — last-resort Workers AI fallback. Only reached after every
    # configured primary+fallback Cerebras/Gemini/etc provider has failed.
    # Policy is strict (timeout/5xx/429/quota only) so 4xx bad-input bugs
    # still surface as 503 instead of being silently masked by a different
    # model's looser parser.
    try:
        from providers import workers_ai as _wai
        if _wai.is_enabled("chat") and last_err is not None and _wai.should_fallback(last_err):
            # Real cumulative primary-loop latency (all rotations combined),
            # so the admin panel and structured logs attribute the actual
            # wait the user incurred before we gave up on the primaries.
            _primary_total_ms = int((_t.perf_counter() - _loop_t0) * 1000)
            _t0 = _t.perf_counter()
            ok, value, label = await _wai.attempt_fallback(
                "chat", last_err, _primary_total_ms,
                lambda: _wai.call_chat(messages, max_tokens=max_tokens, temperature=0.3),
            )
            _dur = int((_t.perf_counter() - _t0) * 1000)
            if ok and isinstance(value, str) and value:
                reason = _wai.classify_primary_error(last_err)
                _record_llm_call("workers-ai", "llama-3.1-8b-instruct", _dur, True,
                                 len(value.split()), True, feature_key=feature_key)
                logger.info(
                    f"llm_call provider=workers-ai model=llama-3.1-8b-instruct "
                    f"duration_ms={_dur} fallback=true reason={reason}"
                )
                return LlmResult(value, provider="workers-ai", fallback_reason=reason)
    except Exception as _wai_err:  # noqa: BLE001
        logger.warning(f"[workers-ai] chat fallback skipped: {type(_wai_err).__name__}: {str(_wai_err)[:150]}")

    logger.error(f"All LLM providers exhausted. Last error: {last_err}")
    raise HTTPException(status_code=503, detail="AI service temporarily unavailable. Please try again.")

# ── Task-based dynamic router ──────────────────────────────────────────────────
# Maps abstract task types to (provider_list, model) so callers never
# hard-code provider names. Add new task types here; never in route handlers.
#
# Task taxonomy:
#   fast / classify / routing  → Workers AI 70B  (fastest, free under CF credits)
#   chat                       → _LLM_PROVIDERS_CHAT pool (Workers AI → Cerebras qwen-3 → Groq)
#   rag_answer / synthesis     → Gemini 2.5 Flash primary (best multi-doc reasoning)
#   content / notes / pyq      → _LLM_PROVIDERS_CONTENT pool (Workers AI 120B → Gemini → Cerebras)
#   embed                      → Workers AI BGE-large-en-v1.5 via vertex_services.embed_text()

_TASK_ROUTE: dict[str, tuple] = {
    # ── Speed-optimised (low latency, simple output) ──────────────────────────
    "fast":          ("workers-ai", "@cf/meta/llama-3.3-70b-instruct-fp8-fast"),
    "classify":      ("workers-ai", "@cf/meta/llama-3.3-70b-instruct-fp8-fast"),
    "routing":       ("workers-ai", "@cf/meta/llama-3.3-70b-instruct-fp8-fast"),
    "rewrite":       ("workers-ai", "@cf/meta/llama-3.3-70b-instruct-fp8-fast"),
    # ── RAG quality (factual, multi-doc, citation-heavy) ─────────────────────
    "rag_answer":    ("workers-ai", "@cf/openai/gpt-oss-120b"),
    "synthesis":     ("workers-ai", "@cf/openai/gpt-oss-120b"),
    "pyq_solve":     ("workers-ai", "@cf/openai/gpt-oss-120b"),
    # ── Long-form content (notes, MCQs, PYQs) ────────────────────────────────
    "content":       ("workers-ai", "@cf/openai/gpt-oss-120b"),
    "notes":         ("workers-ai", "@cf/openai/gpt-oss-120b"),
    "mcq":           ("workers-ai", "@cf/openai/gpt-oss-120b"),
    # ── Deep reasoning ───────────────────────────────────────────────────────
    "reasoning":     ("workers-ai", "@cf/openai/gpt-oss-120b"),
}


def route_for_task(task: str, lang: str = "") -> tuple[str, str]:
    """Return (provider, model) for the given abstract task type.

    For the 15 Task #250 feature keys (``english_rag_chat``, ``assamese_rag_chat``,
    ``content``, ``assamese_content``, ``tts``, ``stt``, ``voice``, ``embed``,
    ``rerank``, ``vector_search``, ``translate``, ``vision``, ``safety``,
    ``search_rag``, ``live_search``) the selection is done via ``select_provider``
    so traffic is distributed according to ``PROVIDER_CREDITS`` weights.

    Legacy task keys (``fast``, ``rag_answer``, ``content``, etc.) continue to
    work via the static ``_TASK_ROUTE`` dict for backward compatibility with
    existing call sites that have not yet been migrated to feature-key strings.

    Falls back to Workers AI 70B for unknown task names.

    Usage::

        provider, model = route_for_task("english_rag_chat", lang="en")
        provider, model = route_for_task("rag_answer")   # legacy static route
    """
    from config import PROVIDER_PRIORITY
    if task in PROVIDER_PRIORITY:
        return route_for_feature(task, lang=lang)
    return _TASK_ROUTE.get(task, ("workers-ai", "@cf/meta/llama-3.3-70b-instruct-fp8-fast"))


# ── PROVIDER_PRIORITY weighted round-robin dispatch (Task #250) ───────────────
# Maps provider names (as used in PROVIDER_PRIORITY) to their default LLM
# model identifiers.  For non-LLM providers (assemblyai,
# pinecone_ai, exa_ai, tavily) the model string is a descriptive tag only —
# the actual API call goes through the provider's own client module.
_PROVIDER_DEFAULT_MODELS: dict[str, str] = {
    # Task #490: `vertex` removed from chat/embed/vector pools entirely.
    # The remaining Vertex surface (`content_format` formatter) goes
    # through `vertex_format.format_with_vertex`, which uses its own
    # model-id constant (`VERTEX_GEMINI_MODEL`) — not this dispatch
    # default-model map. Re-adding `vertex` here would silently put
    # Vertex back into the round-robin chat pool.
    # bedrock removed in Task #347
    "azure_openai":     AZURE_OPENAI_DEPLOYMENT,                     # Azure OpenAI deployment from config (Task #290 — env-driven, no hard-coded model drift)
    "sarvam":           "sarvam-m",                                  # Sarvam LLM (Indic) — primary for assamese_rag_chat
    "elevenlabs":       "eleven_multilingual_v2",                    # ElevenLabs TTS — primary TTS
    "assemblyai":       "best",                                      # AssemblyAI STT
    "deepgram":         "nova-3",                                    # Deepgram STT + Aura-2 TTS — primary STT
    "pinecone_ai":      "llama-text-embed-v2",                       # Pinecone embed/rerank — primary rerank
    "exa_ai":           "exa",                                       # Exa neural search
    "tavily":           "tavily-search",                             # Tavily search
    "mongodb_atlas":    "vector-search",                             # Atlas $vectorSearch (fallback)
    "workers_ai":            "@cf/openai/gpt-oss-20b",                  # CF Workers AI gpt-oss-20b — Task #291 last-resort fallback for content + english_rag_chat (no quota lock-up like llama-3.3-70b)
    "workers_ai_indic":      "@cf/ai4bharat/indictrans2-en-indic-1b",   # CF Workers AI IndicTrans2 English→Assamese; primary translate
    # Task #347 — named Workers AI promotions used at small tail weights
    # in POOL_WEIGHTS so they fire only after every paid provider is
    # excluded. Each is a thin alias that routes through the canonical
    # ``workers-ai`` dispatch with a model override.
    "workers_ai_mistral_7b": "@cf/mistral/mistral-7b-instruct-v0.3",   # balanced English fallback
    "workers_ai_llama32_3b": "@cf/meta/llama-3.2-3b-instruct",         # ultrafast 3B for burst / fast-mode
    "workers_ai_llama31_8b": "@cf/meta/llama-3.1-8b-instruct-fp8",     # Indic chat fallback tail
}

# Maps provider names to the canonical provider string used by _call_single_provider.
# This bridges Task #250's semantic provider names to llm.py's internal strings.
_PROVIDER_CANONICAL: dict[str, str] = {
    # Task #490: `vertex` → `gemini` mapping removed; the chat/vision
    # Gemini dispatch branch was deleted along with the Vertex chat
    # hot-path. The formatter goes through `vertex_format` directly.
    # bedrock removed in Task #347
    "azure_openai":     "azure_openai",     # Task #290 — own branch w/ failover chain
    "sarvam":           "sarvam",
    "elevenlabs":       "elevenlabs",
    "assemblyai":       "assemblyai",
    "deepgram":         "deepgram",
    "pinecone_ai":      "pinecone_ai",
    "exa_ai":           "exa_ai",
    "tavily":           "tavily",
    "mongodb_atlas":    "mongodb_atlas",
    "workers_ai":            "workers-ai",
    "workers_ai_indic":      "workers-ai-indic",  # CF IndicTrans2 — primary for translate + assamese pools
    # Task #347 — Workers AI promotions all canonicalize to "workers-ai"
    # so the standard CF AI dispatch handles them; the per-alias model
    # comes from _PROVIDER_DEFAULT_MODELS above.
    "workers_ai_mistral_7b": "workers-ai",
    "workers_ai_llama32_3b": "workers-ai",
    "workers_ai_llama31_8b": "workers-ai",
}

# CF AI Gateway saturation threshold — providers above this RPM ratio are
# temporarily deprioritized in the weighted draw (same threshold as SLM pool).
_SELECT_SATURATION_THRESHOLD = 0.80

# Chat-pool soft-shed threshold (2026-05-05 user instruction): when the
# strict primary for a chat pool (``azure_openai`` for english_rag_chat,
# ``sarvam`` for assamese_rag_chat) reaches this fraction of its
# configured RPM cap inside a 60-second window, ``select_provider``
# excludes it and the dispatcher walks down to the ``workers_ai_*``
# fallback BEFORE the upstream actually starts 429ing.  Default 0.70
# per user spec; override with ``CHAT_RPM_SOFT_SHED_THRESHOLD``.
_CHAT_RPM_SOFT_SHED_THRESHOLD = float(
    os.environ.get("CHAT_RPM_SOFT_SHED_THRESHOLD", "0.70") or "0.70"
)
_CHAT_POOLS_FOR_RPM_SHED: frozenset = frozenset({
    "english_rag_chat", "assamese_rag_chat",
})


def _get_provider_saturation(provider_name: str) -> float:
    """Return current RPM saturation ratio (0.0–1.0) for a provider.

    Resolution order:
      1. ``workers-ai`` → aggregate ``_SmartKeyPool._rpm_ratio`` across slots.
      2. Paid chat primary (``azure_openai`` / ``sarvam``) → real
         dispatched-request count over the last 60 s divided by the
         configured ``_POOL_RPM_LIMITS`` cap. This drives the new 70 %
         soft-shed (2026-05-05) so the dispatcher pre-emptively shifts
         traffic to the ``workers_ai_*`` fallback BEFORE 429s start.
      3. Otherwise → in-process 429-burst counter as a proxy
         (≥ 5 bursts → 0.90, ≥ 2 → 0.70).
    """
    canonical = _PROVIDER_CANONICAL.get(provider_name, provider_name)
    if canonical == "workers-ai":
        # Use the SLM pool's aggregate RPM ratio across all Workers AI slots.
        ratios = [
            _slm_pool._rpm_ratio(s)
            for s in _slm_pool.all_slots
            if s.get("provider") == "workers-ai"
        ]
        return max(ratios) if ratios else 0.0

    # 2026-05-05 — Real per-provider RPM tracking for the paid chat
    # primaries.  When the 60-second sliding window crosses the configured
    # cap we surface the real ratio so select_provider's threshold check
    # can shed traffic to the workers_ai_* fallback BEFORE 429s start.
    # Falls through to the 429-burst proxy below when not yet saturated
    # (so a transient 429-burst can still raise saturation independently).
    if provider_name in _PAID_PROVIDER_RPM_WINDOWS:
        ratio = _get_paid_provider_rpm_ratio(provider_name)
        if ratio > 0.0:
            return ratio

    # For other providers, use the 429 burst counter as a proxy:
    # ≥ 5 bursts in 60s → treat as saturated (0.85+).
    # NOTE: look up by the original provider_name first (azure_openai, bedrock, gemini, groq)
    # because _PROVIDER_429_WINDOWS keys use provider_name, not the canonical string.
    burst_key = provider_name if provider_name in _PROVIDER_429_WINDOWS else canonical
    burst = get_provider_429_burst_inprocess(burst_key, window_seconds=60)
    if burst >= 5:
        return 0.90
    if burst >= 2:
        return 0.70
    return 0.0


def select_provider(feature: str, lang: str = "", exclude: frozenset = frozenset()) -> str:
    """Round-robin / load-balanced provider selection for *feature*.

    2026-05-05 — converted from strict primary→fallback to round-robin
    per user instruction. Every active provider in a pool gets the same
    weight in POOL_WEIGHTS, so `random.choices(pool, weights)` is a
    uniform draw across all healthy providers. Load is shared equally;
    there is no "primary" any more.

    Algorithm:
    1. Build candidate pool from ``PROVIDER_PRIORITY[feature]``.
    2. Filter out:
       - providers with ``PROVIDER_CREDITS == 0`` (last-resort only).
       - providers in *exclude* (already failed in this request).
       - providers exceeding ``_SELECT_SATURATION_THRESHOLD`` RPM saturation.
    3. For ``assamese_rag_chat`` / ``assamese_content``: only include
       ``sarvam`` when ``lang == "as"``.
    4. Draw one provider with ``random.choices(pool, weights)``.
    5. If pool is empty, fall back to weight-0 providers in fallback order
       (``mongodb_atlas`` for ``vector_search``, then ``workers_ai`` for all).

    Returns the selected provider *name* (e.g. ``"vertex"``, ``"sarvam"``).
    Returns ``"workers_ai"`` when all else fails, or ``None`` for the
    Assamese strict-chain when all 3 legs are exhausted (no silent
    downgrade to wrong-language output).
    """
    import random as _random
    from config import PROVIDER_PRIORITY, PROVIDER_CREDITS, POOL_WEIGHTS

    candidates = PROVIDER_PRIORITY.get(feature, ["workers_ai"])
    _is_assamese_feature = feature in ("assamese_rag_chat", "assamese_content")

    # 2-leg allowlist for assamese_rag_chat (Task #490 — Vertex was removed
    # from the chat hot path and dropped from this third leg): sarvam →
    # workers_ai_indic. workers_ai_llama31_8b is still excluded because it
    # produces non-Assamese (English/Hindi) output for Assamese prompts;
    # only the IndicTrans2 neural-MT path is whitelisted as a fallback.
    if feature == "assamese_rag_chat":
        candidates = [p for p in candidates if p in ("sarvam", "workers_ai_indic")]

    # Per-pool weight overrides take precedence over global PROVIDER_CREDITS.
    # Providers not listed in the override fall back to PROVIDER_CREDITS as usual.
    _pool_override: dict = POOL_WEIGHTS.get(feature, {})

    pool: list[str] = []
    weights: list[int] = []

    for p in candidates:
        credit = _pool_override.get(p, PROVIDER_CREDITS.get(p, 0))
        if credit == 0:
            continue                                   # weight-0 → fallback only
        if p in exclude:
            continue                                   # already failed
        if _is_assamese_feature and p == "sarvam" and lang.lower().strip() != "as":
            continue                                   # Sarvam reserved for Assamese only
        saturation = _get_provider_saturation(p)
        # Chat pools (english_rag_chat / assamese_rag_chat) use the
        # tighter 70 % soft-shed threshold so azure_openai / sarvam
        # primaries hand off to the workers_ai_* fallback BEFORE 429s
        # start.  All other features keep the 80 % default.
        threshold = (
            _CHAT_RPM_SOFT_SHED_THRESHOLD
            if feature in _CHAT_POOLS_FOR_RPM_SHED
            else _SELECT_SATURATION_THRESHOLD
        )
        if saturation >= threshold:
            logger.info(
                "select_provider: skipping %s for feature=%s — at %.0f%% RPM "
                "(threshold %.0f%%, shedding to fallback)",
                p, feature, saturation * 100, threshold * 100,
            )
            continue
        pool.append(p)
        weights.append(credit)

    if pool:
        # 2026-05-05 — Round-robin / load-balanced draw. POOL_WEIGHTS is now
        # equalized across all active providers in every pool, so this
        # uniform-random selection effectively rotates traffic evenly.
        # The strict primary→fallback short-circuit (Task #291) was
        # removed per user instruction — every healthy provider gets a
        # share of every batch instead of one dominant primary.
        chosen = _random.choices(pool, weights=weights, k=1)[0]
        logger.debug("select_provider: feature=%s lang=%s → %s (pool=%s)", feature, lang, chosen, pool)
        return chosen

    # Pool exhausted — try weight-0 fallbacks in list order.
    for p in candidates:
        credit = PROVIDER_CREDITS.get(p, 0)
        if credit != 0:
            continue                                   # already tried
        if p in exclude:
            continue
        if p == "mongodb_atlas" and feature != "vector_search":
            continue                                   # Atlas only for vector_search
        logger.info("select_provider: feature=%s — all weighted providers exhausted, using fallback %s", feature, p)
        return p

    # Task #291 — strict-chain features must NOT silently downgrade to a
    # global workers_ai default. assamese_rag_chat is reasoning-only
    # (Sarvam → IndicTrans2 fallback after Task #490); falling through
    # to llama/gpt-oss for an Assamese answer would produce
    # English/garbled output. Return None so the caller errors out
    # cleanly instead of serving a wrong-language response.
    _STRICT_CHAIN_FEATURES = ("assamese_rag_chat",)
    if feature in _STRICT_CHAIN_FEATURES:
        logger.warning("select_provider: feature=%s — strict chain exhausted, returning None (no silent downgrade)", feature)
        return None
    logger.warning("select_provider: feature=%s — no provider available, defaulting to workers_ai", feature)
    return "workers_ai"


def route_for_feature(feature: str, lang: str = "") -> tuple[str, str]:
    """Return (canonical_provider, model) for a Task #250 feature key via select_provider.

    Bridges the semantic feature-key system into the existing (provider, model)
    tuple used by _call_single_provider and route_for_task callers.

    Example::

        provider, model = route_for_feature("english_rag_chat")
        # → ("azure_openai", AZURE_OPENAI_DEPLOYMENT) when azure is selected, or
        #   ("workers-ai", "@cf/meta/llama-3.3-70b-instruct-fp8-fast") as fallback.
        # (Task #490: the prior ("gemini", "gemini-2.5-flash") branch is gone.)
    """
    provider_name = select_provider(feature, lang=lang)
    canonical = _PROVIDER_CANONICAL.get(provider_name, provider_name)
    model = _PROVIDER_DEFAULT_MODELS.get(provider_name, "@cf/meta/llama-3.3-70b-instruct-fp8-fast")
    return (canonical, model)


_INDICTRANS_VALID_FEATURES: frozenset = frozenset({
    "assamese_content", "translate", "assamese_rag_chat",
})
"""Features where workers_ai_indic (IndicTrans2) is a valid provider.

`assamese_rag_chat` was re-added 2026-05-05 per user instruction — IndicTrans2
is now the second (and final) leg of the post-Task-#490 Assamese chat chain
(sarvam → workers_ai_indic), since Vertex was removed from the chat hot path
entirely. A Sarvam outage hands off to the in-house Cloudflare neural MT
instead of silently downgrading. Note: IndicTrans2 is a translation model
(en-indic), not a chat model, so when it dispatches for `assamese_rag_chat`
it returns a translated form of the user's prompt rather than a true
conversational answer; this is
an explicit operator trade-off (cheaper than Vertex, slower-quality than
Sarvam) chosen by the user.

Chat / safety / english features are NOT in this set. When workers_ai_indic
is drawn for a pool outside this set it raises RuntimeError immediately so
call_with_provider_fallback excludes it and moves to the next provider.
"""


# Task #360 — features that always run inside an async-batch entrypoint
# (PDF summarizer, model-paper generator, content/notes/MCQ batches).
# These are allowed to dispatch to forbidden live-chat models like
# `@cf/openai/gpt-oss-120b` because the dispatch happens off the live
# user-facing chat hot path.
_ASYNC_BATCH_FEATURES: frozenset = frozenset({
    "content", "assamese_content", "notes", "mcq", "synthesis",
    "pyq_solve", "exam_model_paper", "rag_answer", "reasoning",
})


async def _dispatch_llm_for_feature(
    messages: list,
    provider: str,
    max_tokens: int,
    *,
    feature: str = "",
) -> str:
    """Dispatch a chat LLM call to the named Task #250 provider.

    Used as the ``attempt_fn`` argument to ``call_with_provider_fallback`` so
    that chat/content/RAG entrypoints route through the weighted pool.

    ``feature`` is the PROVIDER_PRIORITY key being served (e.g. ``"content"``,
    ``"assamese_rag_chat"``).  It is used to guard ``workers_ai_indic``:
    IndicTrans2 is a translation model — it must not be called for chat/safety
    pools where the caller expects a generated natural-language answer.

    Raises ``RuntimeError`` for Phase-2 providers (bedrock, azure_openai) that
    are not yet wired as full client modules (see Task #256).

    Falls back to the Workers AI SmartKeyPool batcher for ``workers_ai`` and
    any unrecognised provider name.

    Task #360 — wraps the underlying call with the async-only guard for
    ``@cf/openai/gpt-oss-120b`` (and friends) and the per-turn memory-
    brain enforcement check. Both are no-ops outside a chat turn.
    """
    import time as _dp_t

    # Task #360 — async-only / memory-brain enforcement.
    try:
        from chat_turn_context import (
            assert_live_chat_model_allowed as _assert_model_ok,
            assert_mongo_read_or_raise as _assert_mongo_ok,
            async_batch_scope as _async_batch_scope,
        )
    except Exception:
        _assert_model_ok = None  # type: ignore[assignment]
        _assert_mongo_ok = None  # type: ignore[assignment]
        _async_batch_scope = None  # type: ignore[assignment]

    if _assert_mongo_ok is not None:
        # No-op outside a ChatTurnContext; raises in dev/test if the
        # chat handler skipped the Mongo history+profile load.
        try:
            _assert_mongo_ok(dispatcher_name="_dispatch_llm_for_feature")
        except Exception:
            raise

    # Resolve the model that will actually be called so the guard sees
    # the real model id, not just the provider slug. If we can't tell
    # yet (provider == "workers_ai"), the guard inside the model-level
    # call wins; this branch covers the common explicit cases.
    _resolved_model = None
    # Task #490: the `provider == "vertex"` branch was removed (Vertex
    # is no longer a chat-pool dispatch target). Add new providers here.
    if provider == "azure_openai":
        try:
            from config import AZURE_OPENAI_DEPLOYMENT as _AZ_DEPL_PEEK
            _resolved_model = _AZ_DEPL_PEEK
        except Exception:
            _resolved_model = "gpt-4.1-mini"

    # For workers_ai we resolve the concrete model id via _TASK_ROUTE so
    # the guard can catch a chat-feature dispatch to gpt-oss-120b
    # *before* the SmartKeyPool batch is created.
    if _resolved_model is None and provider in ("workers_ai", "workers-ai", ""):
        _wa_route = _TASK_ROUTE.get(feature)
        if _wa_route:
            _resolved_model = _wa_route[1]

    if _assert_model_ok is not None and _resolved_model:
        if feature in _ASYNC_BATCH_FEATURES and _async_batch_scope is not None:
            with _async_batch_scope():
                _assert_model_ok(_resolved_model)
        else:
            _assert_model_ok(_resolved_model)

    # Task #490 — `vertex` chat dispatch branch removed. Vertex is now
    # scoped to `content_format` only. Use vertex_format.format_with_vertex
    # for polish; do not route hot-path chat through Vertex.

    if provider == "sarvam":
        sarvam_slot = _SARVAM_PROVIDERS[0] if _SARVAM_PROVIDERS else None
        if not sarvam_slot:
            raise RuntimeError("sarvam: no Sarvam LLM key available")
        # 2026-05-05 — record against the 60-second RPM soft-shed window
        # BEFORE issuing the call so timed-out / 429ed attempts still
        # consume against the cap (matches Sarvam's own quota meter).
        _record_paid_provider_request("sarvam")
        _t0 = _dp_t.perf_counter()
        try:
            result = await _call_sarvam_llm(messages, sarvam_slot["key"], "sarvam-m", max_tokens)
            _record_llm_call("sarvam", "sarvam-m", int((_dp_t.perf_counter() - _t0) * 1000), True, len(result.split()), feature_key=feature)
            return result
        except Exception as _exc:
            _record_llm_call("sarvam", "sarvam-m", int((_dp_t.perf_counter() - _t0) * 1000), False, 0, error_type=type(_exc).__name__, feature_key=feature)
            raise

    # Task #347: bedrock dispatch branch deleted — providers/bedrock.py is gone
    # and no PROVIDER_PRIORITY pool routes to "bedrock" any more.

    if provider == "azure_openai":
        # Azure OpenAI chat/completions — providers/azure_openai handles the
        # candidate chain (CF AI Gateway BYOK → direct KEY_1 → direct KEY_2).
        # Deployment name comes from AZURE_OPENAI_DEPLOYMENT (Task #290; default
        # set via config.py, falls back to legacy AZURE_OPENAI_MODEL alias).
        # Task #338: gated by the azure.openai.enabled admin toggle so ops can
        # drop the Azure path without a redeploy when MI auth/quotas misbehave.
        from azure_ai_runtime import is_enabled as _az_enabled
        if not await _az_enabled("openai"):
            raise RuntimeError(
                "azure openai disabled via admin toggle "
                "(azure.openai.enabled=false) — routing to next provider"
            )
        from providers.azure_openai import call_chat as _az_chat
        from config import AZURE_OPENAI_DEPLOYMENT as _AZ_DEPL
        # 2026-05-05 — record against the 60-second RPM soft-shed window
        # BEFORE issuing the call so timed-out / 429ed attempts still
        # consume against the cap (matches Azure's own quota meter).
        _record_paid_provider_request("azure_openai")
        _t0 = _dp_t.perf_counter()
        try:
            result = await _az_chat(messages, model=_AZ_DEPL, max_tokens=max_tokens)
            _record_llm_call("azure_openai", _AZ_DEPL, int((_dp_t.perf_counter() - _t0) * 1000), True, len(result.split()), feature_key=feature)
            try:
                from azure_ai_metrics import record_latency as _rl
                _rl("openai", (_dp_t.perf_counter() - _t0) * 1000)
            except Exception:
                pass
            return result
        except Exception as _exc:
            _record_llm_call("azure_openai", _AZ_DEPL, int((_dp_t.perf_counter() - _t0) * 1000), False, 0, error_type=type(_exc).__name__, feature_key=feature)
            try:
                from azure_ai_metrics import record_error as _re
                _re("openai", f"{type(_exc).__name__}: {_exc}")
            except Exception:
                pass
            raise

    if provider == "workers_ai_indic":
        # CF Workers AI IndicTrans2 — translation-only provider (Task #267).
        # Valid only for assamese_content and translate pools where the caller
        # needs English→Assamese text translation, NOT a conversational answer.
        # Any other feature (chat, safety, content…) gets a RuntimeError so that
        # call_with_provider_fallback excludes IndicTrans2 and moves to workers_ai.
        if feature not in _INDICTRANS_VALID_FEATURES:
            raise RuntimeError(
                f"workers_ai_indic: translation model not valid for feature={feature!r}; "
                "routing to workers_ai instead"
            )
        from providers.workers_indic import call_indic_trans as _indic_trans
        # Extract the last user message as the English source text to translate.
        src_text = ""
        for m in reversed(messages):
            if m.get("role") == "user" and m.get("content"):
                src_text = str(m["content"])
                break
        if not src_text:
            raise RuntimeError("workers_ai_indic: no user message to translate; route to workers_ai")
        _t0 = _dp_t.perf_counter()
        try:
            result = await _indic_trans(src_text, direction="en-indic")
            _record_llm_call("workers_ai_indic", "indictrans2", int((_dp_t.perf_counter() - _t0) * 1000), True, len(result.split()), feature_key=feature)
            return result
        except Exception as _exc:
            _record_llm_call("workers_ai_indic", "indictrans2", int((_dp_t.perf_counter() - _t0) * 1000), False, 0, error_type=type(_exc).__name__, feature_key=feature)
            raise

    # ── Task #513 §C round-4 — explicit tier-router aliases ───────────
    # `cost_caps._select_chat_model` emits provider names like
    # `workers_ai_mistral_7b` / `workers_ai_llama32_3b` along with the
    # exact model id we must invoke. Without these branches the call
    # would fall through to the generic `workers_ai` arm below and pick
    # the default Workers-AI chat model (gpt-oss-20b), which means the
    # "free turns 1-2 → Mistral-7B" rule and the Rule-D cheaponly lock
    # would be advertised in tags but not enforced on the wire. We
    # therefore route each alias through `_call_llm_raw` with its
    # canonical model id pinned, while still using the Workers-AI-only
    # provider pool so deprecated providers cannot sneak back in.
    _TIER_ALIAS_TO_MODEL = {
        "workers_ai_mistral_7b": "@cf/mistral/mistral-7b-instruct-v0.3",
        "workers_ai_llama32_3b": "@cf/meta/llama-3.2-3b-instruct",
        "workers_ai_llama31_8b": "@cf/meta/llama-3.1-8b-instruct-fp8",
    }
    if provider in _TIER_ALIAS_TO_MODEL:
        if not _LLM_PROVIDERS_WORKERS_ONLY:
            raise RuntimeError(
                f"{provider}: Cloudflare AI (CF_AI_ENABLED) is not configured"
            )
        _pinned_model = _TIER_ALIAS_TO_MODEL[provider]
        _t0 = _dp_t.perf_counter()
        try:
            result = await _call_llm_raw(
                messages,
                model=_pinned_model,
                max_tokens=max_tokens,
                provider_list=_LLM_PROVIDERS_WORKERS_ONLY,
                feature_key=feature,
            )
            _record_llm_call(
                provider, _pinned_model,
                int((_dp_t.perf_counter() - _t0) * 1000),
                True, len(result.split()), feature_key=feature,
            )
            return result
        except Exception as _exc:
            _record_llm_call(
                provider, _pinned_model,
                int((_dp_t.perf_counter() - _t0) * 1000),
                False, 0, error_type=type(_exc).__name__, feature_key=feature,
            )
            raise

    # workers_ai or any unknown provider → Workers-AI-only dispatch.
    # Use _LLM_PROVIDERS_WORKERS_ONLY so deprecated providers (Groq, Cerebras,
    # Gemini) cannot re-enter routing via this fallback path — they are absent
    # from PROVIDER_PRIORITY and must stay out of the weighted dispatch chain.
    if not _LLM_PROVIDERS_WORKERS_ONLY:
        raise RuntimeError("workers_ai: Cloudflare AI (CF_AI_ENABLED) is not configured")
    return await _call_llm_raw(messages, max_tokens=max_tokens, provider_list=_LLM_PROVIDERS_WORKERS_ONLY, feature_key=feature)


async def call_with_provider_fallback(
    feature: str,
    lang: str,
    attempt_fn,
    max_attempts: int = 6,
):
    """Weighted fallback-without-replacement execution for *feature*.

    Draws a provider via ``select_provider``, calls ``attempt_fn(provider_name)``,
    and on 429 / connection error removes that provider from the pool and retries
    with the next weighted draw.  This implements the "exclude" loop described in
    the Task #250 spec.

    Args:
        feature:     Feature key (e.g. ``"english_rag_chat"``).
        lang:        BCP-47 language code (used by select_provider for sarvam guard).
        attempt_fn:  ``async def fn(provider: str) -> result`` — may raise any exception.
        max_attempts: Maximum draws before raising the last exception.

    Returns:
        The return value of the first successful ``attempt_fn`` call.

    Raises:
        RuntimeError: If all attempts fail.

    Example::

        result = await call_with_provider_fallback(
            "english_rag_chat", "en",
            lambda p: _call_single_provider(msgs, _PROVIDER_CANONICAL[p], key, _PROVIDER_DEFAULT_MODELS[p], 512),
        )
    """
    import httpx as _httpx
    exclude: frozenset = frozenset()
    last_exc: Exception = RuntimeError(f"No providers available for feature={feature}")

    # Task #360 round-7 — CONSUME the credit-burn fallback flag with
    # the CORRECT v3 semantics. The shared `chat:fallback` flag is
    # written by both Meter A (cost ceiling) and Meter B (Workers AI
    # RPM headroom). When set, live-chat dispatch must move OFF the
    # most-expensive paid providers (vertex/gemini/sarvam) AND off
    # constrained Workers AI, BUT MUST PRESERVE Azure GPT-4.1-mini —
    # Azure is explicitly the v3 fallback target for Meter B (RPM
    # relief) and a much cheaper paid path for Meter A than
    # vertex/sarvam. Excluding Azure here would defeat the meter's
    # purpose. See `infra/credit-burn-runbook.md` §4.2.
    _PAID_FOR_FALLBACK: frozenset = frozenset({
        "vertex", "gemini", "sarvam", "anthropic", "openai",
    })
    # Round-8: removed os.environ fallback path — the chat handler no
    # longer mutates process-global env per request (race-prone). The
    # runtime singleton is the single source of truth.
    try:
        from credit_burn_meter_runtime import is_fallback_active as _fb_active
        _is_in_fallback = bool(_fb_active())
    except Exception:
        _is_in_fallback = False
    if _is_in_fallback:
        exclude = exclude | _PAID_FOR_FALLBACK
        logger.warning(
            "call_with_provider_fallback: chat:fallback ACTIVE — "
            "excluding paid providers (%s) for feature=%s",
            sorted(_PAID_FOR_FALLBACK), feature,
        )

    # Task #362 §3 — per-session sticky swap. If this session was previously
    # marked stuck (K consecutive slow turns), force-route every turn for
    # the rest of the session lifetime to the swap provider. This is
    # distinct from the global chat:fallback flag above and only affects
    # one user. Soft-fail: any error in the lookup falls through to the
    # normal weighted dispatch.
    _session_swap_provider: str = ""
    _session_id_for_ttfb: str = ""
    try:
        from chat_turn_context import get_current_session_id
        from session_fallback import get_session_swap as _get_swap
        _session_id_for_ttfb = get_current_session_id() or ""
        if _session_id_for_ttfb:
            _session_swap_provider = _get_swap(_session_id_for_ttfb) or ""
    except Exception:
        _session_swap_provider = ""

    _t_start = time.time()

    for attempt in range(max_attempts):
        if _session_swap_provider and _session_swap_provider not in exclude:
            provider = _session_swap_provider
            logger.info(
                "call_with_provider_fallback: per-session swap sid=%s → %s "
                "(feature=%s, attempt=%d)",
                _session_id_for_ttfb, provider, feature, attempt,
            )
            # Only honor the swap on the first attempt; if it fails, fall
            # through to the normal weighted pool so we don't pin a
            # broken provider for the whole retry chain.
            _session_swap_provider = ""
        else:
            provider = select_provider(feature, lang=lang, exclude=exclude)
        # Strict-chain guard: select_provider returns None for locked
        # features (e.g. assamese_rag_chat) once every leg is excluded.
        # Without this guard attempt_fn(None) would fall through to the
        # generic workers_ai branch in _dispatch_llm_for_feature and emit
        # wrong-language output. Surface exhaustion immediately so the
        # caller raises a clean 503.
        if provider in (None, ""):
            logger.warning(
                "call_with_provider_fallback: feature=%s strict-chain exhausted "
                "(select_provider returned %r) — surfacing exhaustion instead of "
                "falling through to workers_ai default",
                feature, provider,
            )
            raise RuntimeError(
                f"All providers exhausted for feature={feature}: strict chain "
                f"returned no provider"
            ) from last_exc
        try:
            _result = await attempt_fn(provider)
            # Record per-turn TTFB for the per-session stuck-detection
            # heuristic. Soft-fail: any error here must NOT take down a
            # successful chat turn.
            try:
                if _session_id_for_ttfb:
                    from session_fallback import record_turn_ttfb as _rec_ttfb
                    _rec_ttfb(_session_id_for_ttfb, (time.time() - _t_start) * 1000.0)
            except Exception:
                pass
            return _result
        except _httpx.HTTPStatusError as exc:
            if exc.response.status_code in (429, 502, 503, 504):
                logger.warning(
                    "call_with_provider_fallback: feature=%s provider=%s HTTP %d — removing from pool",
                    feature, provider, exc.response.status_code,
                )
                exclude = exclude | {provider}
                last_exc = exc
                continue
            raise
        except Exception as exc:
            logger.warning(
                "call_with_provider_fallback: feature=%s provider=%s error=%s — removing from pool",
                feature, provider, exc,
            )
            exclude = exclude | {provider}
            last_exc = exc

    raise RuntimeError(f"All providers exhausted for feature={feature}: {last_exc}") from last_exc


# ── RAG-quality call path ───────────────────────────────────────────────────────
# V4 §4 (user-locked 2026-05-06 via B3): chat/RAG dispatch is Azure
# gpt-4.1-nano SOLE primary → Workers-AI Mistral-7B → Workers-AI
# Llama-3.2-3B → generic Workers-AI. The actual order is enforced by
# PROVIDER_PRIORITY['english_rag_chat'] in config.py — this list is only
# the *terminal hard-fallback* pool used by call_llm_for_rag() when the
# weighted dispatch above exhausts. Workers-AI 120B / 70B kept here as a
# last-ditch quality option (off the V4 spec'd chain but still safer than
# returning RuntimeError to the student). Gemini removed from this hard
# fallback (founder rejected Vertex in chat hot path; reachable only via
# the `content` pool).
_RAG_PROVIDERS: list[dict] = []
if _CF_AI_ENABLED:
    _RAG_PROVIDERS.append({"provider": "workers-ai", "key": _CF_API_TOKEN, "default_model": "@cf/openai/gpt-oss-120b"})
    _RAG_PROVIDERS.append({"provider": "workers-ai", "key": _CF_API_TOKEN, "default_model": "@cf/meta/llama-3.3-70b-instruct-fp8-fast"})


async def call_llm_for_rag(messages: list, max_tokens: int = 2048) -> str:
    """LLM call optimised for RAG answer synthesis.

    Dispatches through PROVIDER_PRIORITY["english_rag_chat"] weighted round-robin
    via call_with_provider_fallback → _dispatch_llm_for_feature.

    Provider priority (V4 §4, user-locked 2026-05-06 via B3):
      Azure OpenAI gpt-4.1-nano (SOLE primary) → Workers-AI Mistral-7B (A9 #1)
      → Workers-AI Llama-3.2-3B (A9 #2) → generic Workers-AI (gpt-oss-20b last-resort).
    Vertex is intentionally NOT in this pool (founder rejected the V4-draft
    Vertex co-primary + CF Worker token-length / risk-score router).
    Bedrock + Groq + direct Cerebras removed in Task #347.

    Final hard fallback: Workers AI only — ensures no non-PROVIDER_PRIORITY providers
    can be introduced after the weighted pool exhausts.
    """
    try:
        return await call_with_provider_fallback(
            "english_rag_chat", "en",
            lambda p: _dispatch_llm_for_feature(messages, p, max_tokens, feature="english_rag_chat"),
        )
    except Exception as exc:
        logger.warning(
            "call_llm_for_rag feature dispatch exhausted (%s) — hard fallback to workers_ai only",
            exc,
        )
        return await _call_llm_raw(messages, max_tokens=max_tokens, provider_list=_LLM_PROVIDERS_WORKERS_ONLY, feature_key="english_rag_chat")


async def call_llm_api(messages: list, model: str = None, max_tokens: int = 2048) -> str:
    """Smart-batched LLM call: deduplicates identical requests, limits concurrency.
    Uses all providers including Emergent (admin content generation)."""
    return await _llm_batcher.call(messages, model, max_tokens)

# Admin content batcher chain — terminal hard-fallback pool only. The
# active dispatch order is PROVIDER_PRIORITY['content'] in config.py
# (V4 §4 A9, user-locked 2026-05-06 via B3, post-Task-#490 cleanup):
# Workers-AI Mistral-7B (#1) → Workers-AI Llama-3.2-3B (#2) → generic
# Workers-AI. The list below is the *last-ditch* Cloudflare-only pool
# reached after that chain exhausts; Workers-AI 120B/70B kept here for
# long-form quality. Vertex Gemini was REMOVED from the content pool
# in Task #490 — Vertex is now `content_format` (formatter polish) only,
# reachable via `vertex_format.format_with_vertex`, not via this batcher.
_LLM_PROVIDERS_CONTENT: list[dict] = []
if _CF_AI_ENABLED:
    _LLM_PROVIDERS_CONTENT.append({"provider": "workers-ai", "key": _CF_API_TOKEN, "default_model": "@cf/openai/gpt-oss-120b"})
    _LLM_PROVIDERS_CONTENT.append({"provider": "workers-ai", "key": _CF_API_TOKEN, "default_model": "@cf/meta/llama-3.3-70b-instruct-fp8-fast"})

logger.info(
    f"Admin content providers (quality-first order): "
    f"{[p['provider'] + '/' + p['default_model'] for p in _LLM_PROVIDERS_CONTENT]}"
)

async def call_llm_api_content(messages: list, model: str = None, max_tokens: int = 3072) -> str:
    """LLM call for admin content / notes generation via PROVIDER_PRIORITY weighted dispatch.

    Feature key: "content" — chain (PROVIDER_PRIORITY["content"] in config.py,
    V4 §4 A9 ordering, user-locked 2026-05-06 via B3, post-Task-#490):
      Workers-AI Mistral-7B (#1) → Workers-AI Llama-3.2-3B (#2)
      → generic Workers-AI (last-resort).

    Workers-AI leads (V4 §4 + 2026-05-05 user instruction — content gen is fully
    Cloudflare-native). Vertex was REMOVED from the content pool in Task #490
    (Vertex is now `content_format` formatter only, reachable via
    `vertex_format.format_with_vertex` — not via this dispatch path).
    Azure removed from this pool (chat-only); Bedrock removed in Task #347.

    Final hard fallback: Workers AI only — ensures no non-PROVIDER_PRIORITY providers
    can be introduced after the weighted pool exhausts.
    """
    try:
        return await call_with_provider_fallback(
            "content", "en",
            lambda p: _dispatch_llm_for_feature(messages, p, max_tokens, feature="content"),
        )
    except Exception as exc:
        logger.warning(
            "call_llm_api_content feature dispatch exhausted (%s) — hard fallback to workers_ai only",
            exc,
        )
        return await _call_llm_raw(messages, max_tokens=max_tokens, provider_list=_LLM_PROVIDERS_WORKERS_ONLY, feature_key="content")


async def call_llm_api_content_with_retry(
    messages: list, model: str = None, max_tokens: int = 3072,
    validate_fn=None,
) -> str:
    """Content LLM call with retry-with-backoff and optional output validation.
    
    validate_fn: optional callable(result_str) -> bool. If it returns False,
    the result is treated as a failure and retried.
    """
    last_err = None
    for attempt in range(_CONTENT_RETRY_MAX):
        try:
            result = await call_llm_api_content(messages, model, max_tokens)
            if validate_fn is not None and not validate_fn(result):
                logger.warning(
                    f"Content LLM output failed validation (attempt {attempt + 1}/{_CONTENT_RETRY_MAX})"
                )
                last_err = ValueError("Output failed validation")
                if attempt < _CONTENT_RETRY_MAX - 1:
                    backoff = _CONTENT_RETRY_BACKOFF[min(attempt, len(_CONTENT_RETRY_BACKOFF) - 1)]
                    logger.info(f"Content retry backoff: waiting {backoff}s before attempt {attempt + 2}")
                    await asyncio.sleep(backoff)
                continue
            return result
        except Exception as e:
            last_err = e
            logger.warning(
                f"Content LLM call failed (attempt {attempt + 1}/{_CONTENT_RETRY_MAX}): "
                f"{type(e).__name__}: {str(e)[:150]}"
            )
            if attempt < _CONTENT_RETRY_MAX - 1:
                backoff = _CONTENT_RETRY_BACKOFF[min(attempt, len(_CONTENT_RETRY_BACKOFF) - 1)]
                logger.info(f"Content retry backoff: waiting {backoff}s before attempt {attempt + 2}")
                await asyncio.sleep(backoff)
    raise last_err or HTTPException(status_code=503, detail="Content generation failed after retries")

async def polish_notes_with_vertex(
    raw_notes: str,
    title: str = "",
    subject_name: str = "",
    *,
    lang: str = "en",
    max_tokens: int = 4000,
) -> str:
    """Stage-2 NotebookLM-style polish — pinned to Vertex / Gemini.

    The 2026-05-05 user instruction split content generation into two
    explicit stages:

      * Stage 1 — GENERATE: Workers AI variants produce the raw notes
        (English: workers_ai_mistral_7b / workers_ai_llama32_3b;
        Assamese: workers_ai_indic / IndicTrans2). The POOL_WEIGHTS
        for ``content`` and ``assamese_content`` give Workers AI the
        full active share; Vertex sits at weight 0 (emergency-only)
        in those pools because it is reserved for this polish stage.

      * Stage 2 — POLISH: this function. Vertex / Gemini 2.5 Flash
        re-formats the raw notes into NotebookLM-style study notes —
        clean H2/H3 hierarchy, bold key terms, tightened bullets,
        and a short "Key Takeaways" block at the end.

    Failure is non-fatal: if Vertex polish fails (network / quota /
    safety block), this function returns ``raw_notes`` unchanged so
    that content generation never blocks on the polish stage.

    Parameters:
      raw_notes:    Stage-1 markdown emitted by the Workers AI generator.
      title:        Chapter / topic title (gives Vertex grounding context).
      subject_name: Subject name (e.g. "Physics", "অসমীয়া সাহিত্য").
      lang:         "en" (default) or "as" — controls the polish prompt
                    so Vertex preserves the source language.
      max_tokens:   Upper bound on the polished output length.
    """
    if not raw_notes or len(raw_notes.strip()) < 100:
        return raw_notes

    is_assamese = (lang or "en").lower().startswith("as")
    if is_assamese:
        polish_prompt = f"""You are an Assamese-fluent academic editor producing NotebookLM-style study notes.

**Chapter:** {title}
**Subject:** {subject_name or "Assamese curriculum"}

**Raw notes (in Assamese — generated by an Indic translation model, may be rough around the edges):**
{raw_notes}

---

**YOUR TASK — polish into NotebookLM-style notes (Assamese script throughout):**
1. Output MUST stay in Assamese script (অসমীয়া). Do NOT translate to English.
2. Fix any grammar / spelling / formatting issues from the raw output.
3. Re-organise into a clean Markdown hierarchy: ## for topic headings, ### for sub-points.
4. Bold every key term and every definition with **…** on first mention.
5. Tighten bullet points — 4-6 per topic, no redundancy, no filler.
6. Preserve the structure and topic coverage of the raw notes — do NOT add new topics, do NOT drop topics.
7. End with one final section ## মূল কথাবোৰ ("Key Takeaways") — 5-7 bullets capturing the most exam-critical points.
8. Return ONLY the polished Markdown. NO English commentary, NO preamble, NO disclaimers.
"""
    else:
        polish_prompt = f"""You are a senior academic editor producing NotebookLM-style study notes.

**Chapter:** {title}
**Subject:** {subject_name or "General"}

**Raw notes (generated by a fast Workers AI model — may need polish):**
{raw_notes}

---

**YOUR TASK — polish into NotebookLM-style notes:**
1. Fix any grammar / spelling / formatting issues from the raw output.
2. Re-organise into a clean Markdown hierarchy: ## for topic headings, ### for sub-points.
3. Bold every key term and every definition with **…** on first mention.
4. Tighten bullet points — 4-6 per topic, no redundancy, no filler.
5. Preserve the structure and topic coverage of the raw notes — do NOT add new topics, do NOT drop topics.
6. End with one final section ## Key Takeaways — 5-7 bullets capturing the most exam-critical points.
7. Return ONLY the polished Markdown. NO commentary, NO preamble, NO disclaimers.
"""

    # Task #494 — Polish now goes through the dedicated dispatcher
    # `content_formatter.format_content`, which tries Vertex Gemini 2.5 Flash
    # first and falls back ONCE to Workers-AI Llama-3.3-70b on Vertex outage
    # (V4 §15 §6). The dispatcher never raises; on dual-failure it returns
    # the input text with formatted_by="passthrough", so the polish path
    # remains non-fatal for content generation.
    from content_formatter import format_content as _format_content
    result = await _format_content(
        polish_prompt,
        style="notebook_lm",
        lang=("as" if is_assamese else "en"),
        max_tokens=max_tokens,
    )
    polished = result.get("text") or ""
    formatted_by = result.get("formatted_by", "passthrough")

    if formatted_by == "passthrough":
        logger.warning(
            "polish_notes_with_vertex: format_content fell through to "
            "passthrough for %r — returning raw notes unchanged",
            title or "<untitled>",
        )
        return raw_notes

    if not polished or len(polished.split()) < max(50, int(len(raw_notes.split()) * 0.5)):
        logger.warning(
            "polish_notes_with_vertex: polished output too short for %r "
            "(%d → %d words, formatted_by=%s) — returning raw notes unchanged",
            title or "<untitled>",
            len(raw_notes.split()),
            len(polished.split()) if polished else 0,
            formatted_by,
        )
        return raw_notes

    return polished.strip()


async def polish_notes_with_format(
    raw_notes: str,
    title: str = "",
    subject_name: str = "",
    *,
    lang: str = "en",
    max_tokens: int = 4000,
) -> dict:
    """Task #494 — same prompt construction as ``polish_notes_with_vertex``
    but exposes the full ``content_formatter.format_content`` dict return so
    callers can persist the ``formatted_by`` audit field alongside the text.

    Returns the dispatcher dict shape:

        {"text": str, "formatted_by": "vertex" | "workers_ai_llama33_70b"
                                      | "passthrough",
         "duration_ms": int, "trace_id": str}

    Never raises. On dual-failure / too-short output the dict carries the
    original ``raw_notes`` with ``formatted_by="passthrough"``.
    """
    if not raw_notes or len(raw_notes.strip()) < 100:
        return {
            "text": raw_notes,
            "formatted_by": "passthrough",
            "duration_ms": 0,
            "trace_id": "",
        }

    is_assamese = (lang or "en").lower().startswith("as")

    # Build the same prompt as polish_notes_with_vertex but invoke
    # content_formatter.format_content DIRECTLY so the audit dict
    # (formatted_by / duration_ms / trace_id) belongs unambiguously to
    # this request — reading the in-process ring tail under concurrency
    # would attribute another request's invocation to this caller.
    if is_assamese:
        polish_prompt = f"""You are an Assamese-fluent academic editor producing NotebookLM-style study notes.

**Chapter:** {title}
**Subject:** {subject_name or "Assamese curriculum"}

**Raw notes (in Assamese — generated by an Indic translation model, may be rough around the edges):**
{raw_notes}

---

**YOUR TASK — polish into NotebookLM-style notes (Assamese script throughout):**
1. Output MUST stay in Assamese script (অসমীয়া). Do NOT translate to English.
2. Fix any grammar / spelling / formatting issues from the raw output.
3. Re-organise into a clean Markdown hierarchy: ## for topic headings, ### for sub-points.
4. Bold every key term and every definition with **…** on first mention.
5. Tighten bullet points — 4-6 per topic, no redundancy, no filler.
6. Preserve the structure and topic coverage of the raw notes — do NOT add new topics, do NOT drop topics.
7. End with one final section ## মূল কথাবোৰ ("Key Takeaways") — 5-7 bullets capturing the most exam-critical points.
8. Return ONLY the polished Markdown. NO English commentary, NO preamble, NO disclaimers.
"""
    else:
        polish_prompt = f"""You are a senior academic editor producing NotebookLM-style study notes.

**Chapter:** {title}
**Subject:** {subject_name or "General"}

**Raw notes (generated by a fast Workers AI model — may need polish):**
{raw_notes}

---

**YOUR TASK — polish into NotebookLM-style notes:**
1. Fix any grammar / spelling / formatting issues from the raw output.
2. Re-organise into a clean Markdown hierarchy: ## for topic headings, ### for sub-points.
3. Bold every key term and every definition with **…** on first mention.
4. Tighten bullet points — 4-6 per topic, no redundancy, no filler.
5. Preserve the structure and topic coverage of the raw notes — do NOT add new topics, do NOT drop topics.
6. End with one final section ## Key Takeaways — 5-7 bullets capturing the most exam-critical points.
7. Return ONLY the polished Markdown. NO commentary, NO preamble, NO disclaimers.
"""

    # Task #513 §K.3 round-7 — bulk producers (admin chapter pre-gen
    # in routes/admin_pipeline.py + Assamese backfill in
    # `aca_jobs/as_translation_backfill`) fire many concurrent
    # `polish_notes_with_format` calls. Routing through
    # `format_content_batched` lets `AsyncBatcher` coalesce up to
    # `_FORMATTER_BATCH_SIZE=10` concurrent submissions into a single
    # upstream Vertex/WAI request (50 ms window), cutting provider
    # call count ~10× during bulk runs. Single-call latency cost is
    # one extra batching tick (≤50 ms) which is negligible against
    # the multi-second polish itself.
    from content_formatter import format_content_batched as _format_batched
    res = await _format_batched(
        polish_prompt,
        style="notebook_lm",
        lang=("as" if is_assamese else "en"),
        max_tokens=max_tokens,
    )
    polished = (res.get("text") or "").strip()
    formatted_by = res.get("formatted_by", "passthrough")

    # Mirror polish_notes_with_vertex's too-short / passthrough guards
    # so callers can rely on a single contract. When the dispatcher fell
    # through or the polish was unusably short, return the raw notes
    # under formatted_by="passthrough" so the audit field stays honest.
    if formatted_by == "passthrough" or not polished or (
        len(polished.split()) < max(50, int(len(raw_notes.split()) * 0.5))
    ):
        return {
            "text": raw_notes,
            "formatted_by": "passthrough",
            "duration_ms": int(res.get("duration_ms", 0)),
            "trace_id": str(res.get("trace_id", "")),
        }

    return {
        "text": polished,
        "formatted_by": formatted_by,
        "duration_ms": int(res.get("duration_ms", 0)),
        "trace_id": str(res.get("trace_id", "")),
    }


async def call_llm_api_chat(
    messages: list,
    model: str = None,
    max_tokens: int = 2048,
    lang: str = "en",
    provider_override: str | None = None,
) -> str:
    """LLM call for student chat via PROVIDER_PRIORITY weighted dispatch.

    Feature key: "english_rag_chat" (default) or "assamese_rag_chat" when lang="as".

    English chain (V4 §4, user-locked 2026-05-06 via B3):
      Azure OpenAI gpt-4.1-nano (SOLE primary) → Workers-AI Mistral-7B (A9 #1)
      → Workers-AI Llama-3.2-3B (A9 #2) → generic Workers-AI (gpt-oss-20b, terminal).
    Vertex intentionally NOT in the chat hot path (founder rejected the V4-draft
    Vertex co-primary + CF Worker token-length / risk-score router).
    Bedrock + Groq + direct Cerebras removed in Task #347.

    Assamese chain (V4 §4, 2026-05-05 user instruction — strict primary/fallback):
      Sarvam (SOLE primary) → Workers-AI IndicTrans2 (en-indic neural MT, terminal).
    Vertex removed from the Assamese chain (wrong-language output risk).

    Final hard fallback: Workers AI only — ensures no non-PROVIDER_PRIORITY providers
    can be introduced after the weighted pool exhausts.
    """
    feature = "assamese_rag_chat" if lang == "as" else "english_rag_chat"
    # Task #513 §C — tier-routing override. When the caller (e.g.
    # `routes/ai_chat.py` after `cost_caps._select_chat_model`) hands
    # us an explicit provider, dispatch DIRECTLY to that provider
    # before consulting `PROVIDER_PRIORITY`. This is the path that
    # makes the free-user turns 1-2 → Workers-AI Mistral-7B and
    # Rule-D `cheaponly` clamps actually take effect on the wire
    # (without it, the dispatcher would always honour the fixed
    # PROVIDER_PRIORITY and the tier decision would be advisory).
    # Failure of the overridden provider falls through to the normal
    # weighted chain so a transient outage does not surface a 503.
    if provider_override:
        try:
            return await _dispatch_llm_for_feature(
                messages, provider_override, max_tokens, feature=feature,
            )
        except Exception as _ovr_exc:
            logger.warning(
                "[CHAT][TIER] provider_override=%s failed (%s) — "
                "falling through to PROVIDER_PRIORITY chain",
                provider_override, _ovr_exc,
            )
    try:
        return await call_with_provider_fallback(
            feature, lang,
            lambda p: _dispatch_llm_for_feature(messages, p, max_tokens, feature=feature),
        )
    except Exception as exc:
        # V4 §4 LOCKED CHAIN (was Task #291; updated 2026-05-05 user
        # instruction + B3 2026-05-06): assamese_rag_chat is a strict
        # 2-leg Sarvam → Workers-AI IndicTrans2 chain with NO further
        # downgrade. Vertex was REMOVED from this pool (2026-05-05 user
        # instruction — Vertex was emitting wrong-language output for
        # Assamese prompts). When both legs fail we MUST surface a clean
        # unavailable error rather than hard-falling-back to the generic
        # workers_ai pool, because generic workers_ai will produce
        # non-Assamese (English / Hindi / mixed) output for an Assamese
        # prompt — wrong-language answers are worse for UX than an
        # honest "service temporarily unavailable".
        if feature == "assamese_rag_chat":
            logger.warning(
                "call_llm_api_chat assamese_rag_chat strict chain exhausted "
                "(%s) — surfacing 503 (no wrong-language workers_ai fallback per Task #291)",
                exc,
            )
            # Task #374: page on-call when both Assamese rails are red. The
            # alerting loop reads this counter; counter resets via TTL.
            # Task #379: also persist event metadata (failing leg + error
            # excerpt) so the admin health panel can show recent outages.
            try:
                # Task #492 — label is `sarvam_workers_indic_chain` (renamed
                # from `sarvam_vertex_chain` in coordination with the admin
                # panel, alert pipeline, and outage test suite).
                record_assamese_unavailable(
                    failing_leg="sarvam_workers_indic_chain",
                    error_summary=f"{type(exc).__name__}: {exc}",
                )
            except Exception:
                pass
            raise HTTPException(
                status_code=503,
                detail="Assamese chat service temporarily unavailable. Please try again.",
            ) from exc
        logger.warning(
            "call_llm_api_chat feature dispatch exhausted (%s) — hard fallback to workers_ai only",
            exc,
        )
        return await _call_llm_raw(messages, max_tokens=max_tokens, provider_list=_LLM_PROVIDERS_WORKERS_ONLY, feature_key=feature)


_THINK_BUDGET_HINT = "/think in one sentence. Answer immediately.\n"

def _inject_think_budget(messages: list) -> list:
    """Prepend a concise reasoning directive to the system message so sarvam-m
    spends fewer tokens in its <think> block, reducing TTFT significantly."""
    out = []
    injected = False
    for m in messages:
        if m.get("role") == "system" and not injected:
            out.append({**m, "content": _THINK_BUDGET_HINT + m["content"]})
            injected = True
        else:
            out.append(m)
    if not injected:
        out.insert(0, {"role": "system", "content": _THINK_BUDGET_HINT})
    return out

async def _stream_sarvam(messages: list, api_key: str, model: str, max_tokens: int, *, response_lang: str = ""):
    """Token-by-token SSE streaming from Sarvam — reuses persistent sarvam_llm_client (zero TCP overhead).
    For Indic languages: enables native thinking in Assamese — model reasons in অসমীয়া inside
    <think> blocks (stripped by _emit_tokens before reaching the student) then answers in Assamese.
    For English: adds SARVAM_THINK_BUFFER so <think> reasoning never crowds out the answer budget.
    Falls back to direct client if CF gateway connection fails.
    """
    _indic = _is_indic_lang(response_lang)
    if _indic:
        # Enable thinking in Assamese: give the model a reasoning budget so it
        # can work through the problem in অসমীয়া before writing the answer.
        # SARVAM_THINK_BUFFER tokens are reserved for the <think> block; the
        # _emit_tokens layer strips the block before it reaches the student.
        api_max = max_tokens + SARVAM_THINK_BUFFER
        patched = [dict(m) for m in messages]
        _indic_preface = (
            "/think অসমীয়াত চমুকৈ চিন্তা কৰা — তাৰ পিছত সম্পূৰ্ণ উত্তৰ অসমীয়াত দিয়া।\n"
            "CRITICAL: Think in Assamese (অসমীয়া) first, then reply DIRECTLY in Assamese.\n"
            "Do NOT start with 'Okay', 'Let me', or any English opener. Begin your answer immediately.\n"
            "STRICT LANGUAGE RULES:\n"
            "- Every running word in the answer MUST be in Assamese script. NO mid-sentence English.\n"
            "- NEVER emit partial English fragments such as 'me uses', 'terms', 'ssible',\n"
            "  'ble', 'tion', 'ssing'. If you start a word in English, switch back to Assamese.\n"
            "- Latin script is allowed ONLY for: pure numbers/dates, scientific units\n"
            "  (cm, kg, Hz, °C, eV…), math symbols/equations, code, URLs, well-known\n"
            "  proper nouns and acronyms (AHSEC, SEBA, NCERT, DNA, GDP, Magh Bihu, Newton).\n"
            "- For everyday nouns/verbs, always use the Assamese word — never English.\n"
            "BAD vs GOOD examples (follow the pattern, do not copy text):\n"
            "  BAD : 'উৰুকা me uses ssible terms চমুকৈ ক'লে…'\n"
            "  GOOD: 'উৰুকা চমুকৈ ক'লে অসমৰ এক প্ৰিয় উৎসৱ।'\n"
            "  BAD : 'জল 100°C ত boil হয়।'\n"
            "  GOOD: 'পানী 100°C ত উতলে।'\n"
            "  BAD : 'Newton ৰ first law explains inertia।'\n"
            "  GOOD: 'Newton ৰ গতিৰ প্ৰথম সূত্ৰে জড়তা ব্যাখ্যা কৰে।'\n"
        )
        if patched and patched[0].get("role") == "system":
            patched[0]["content"] = _indic_preface + patched[0]["content"]
        else:
            patched.insert(0, {"role": "system", "content": _indic_preface})
        logger.info(f"[SARVAM-INDIC] Think-in-Assamese mode for {response_lang} — model={model}, api_max={api_max}")
    else:
        api_max = max_tokens + SARVAM_THINK_BUFFER
        patched = _inject_think_budget(messages)
    _SARVAM_LANG_CODE_MAP = {"as": "as-IN"}
    payload = {
        "model": model,
        "messages": patched,
        "max_tokens": api_max,
        "temperature": 0.1,
        "top_p": 0.9 if _indic else 0.95,
        "frequency_penalty": 0,
        "presence_penalty": 0,
        "stream": True,
    }
    if _indic:
        # Enable Sarvam's native thinking so the model reasons in Assamese
        # before writing the answer. The <think> block is stripped by
        # _emit_tokens before any tokens reach the student.
        payload["thinking"] = {"enabled": True}
        if response_lang in _SARVAM_LANG_CODE_MAP:
            payload["response_language"] = _SARVAM_LANG_CODE_MAP[response_lang]
    elif response_lang in _SARVAM_LANG_CODE_MAP:
        payload["response_language"] = _SARVAM_LANG_CODE_MAP[response_lang]
    client = _pick_sarvam_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Sarvam LLM client not initialised")

    async def _do_stream(c):
        async with c.stream("POST", "/v1/chat/completions", json=payload) as resp:
            if resp.status_code >= 400:
                body = await resp.aread()
                logger.error(f"Sarvam {resp.status_code} error body: {body.decode()[:500]}")
                resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    break
                try:
                    chunk = json.loads(raw)
                    choices = chunk.get("choices", [])
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {})
                    token = delta.get("content") or ""
                    if token:
                        yield token
                except Exception:
                    continue

    # Task #492: no direct-client bypass; failures propagate so dispatch
    # advances to the Workers-AI IndicTrans2 leg.
    async for token in _do_stream(client):
        yield token

# Task #490 — the Vertex Gemini streaming helper and the `_stream_gemini`
# direct path were both removed when Vertex was scoped to `content_format` only.
# Streaming chat dispatch now goes through Workers-AI / Azure / Sarvam
# only. Vertex polish is non-streaming via `vertex_format.format_with_vertex`.


def _record_aig_from_stream(stream: Any, *, base: str, provider: str, model: str) -> None:
    """Task #420 — pull cf-aig-* headers off an openai-python AsyncStream
    object's underlying httpx response. The SDK exposes ``.response`` on
    AsyncStream in recent versions; older versions kept it on
    ``._raw_response``. We tolerate either, and stay silent on miss so
    telemetry can never break a streaming chat."""
    if not _is_cf_gateway_base(base):
        return
    raw = getattr(stream, "response", None) or getattr(stream, "_raw_response", None)
    if raw is None:
        return
    try:
        from ai_gateway_observability import record_aig_response
        record_aig_response(getattr(raw, "headers", {}) or {},
                            provider=provider, model=model)
    except Exception:
        pass


# Task #491: legacy SLM streaming helper deleted alongside its sync twin.
# The provider is no longer in any PROVIDER_PRIORITY pool.

# Task #347: ``_stream_xai`` was deleted. xAI/Grok is no longer in any
# PROVIDER_PRIORITY pool, the SDK is uninstalled, and no dispatch path
# routes to provider == "xai".


async def _stream_openai_compat(messages: list, api_key: str, model: str, max_tokens: int, provider: str, fallback_base: str):
    """Token-by-token streaming from any OpenAI-compatible provider."""
    base = get_provider_base_url(provider) or fallback_base
    client = _get_oai_client(api_key, base)
    try:
        stream = await client.chat.completions.create(
            model=model, messages=messages, max_tokens=max_tokens, stream=True, temperature=0.1,
        )
    except _oai.APIConnectionError as e:
        if base != fallback_base and _is_cf_connection_error(e):
            _handle_cf_connection_error(e)
            client = _get_oai_client(api_key, fallback_base)
            base = fallback_base
            stream = await client.chat.completions.create(
                model=model, messages=messages, max_tokens=max_tokens, stream=True, temperature=0.1,
            )
        else:
            raise
    except _oai.AuthenticationError as e:
        if base != fallback_base:
            _handle_cf_gateway_auth_error(e)
            client = _get_oai_client(api_key, fallback_base)
            base = fallback_base
            stream = await client.chat.completions.create(
                model=model, messages=messages, max_tokens=max_tokens, stream=True, temperature=0.1,
            )
        else:
            raise
    _record_aig_from_stream(stream, base=base, provider=provider, model=model)
    async for chunk in stream:
        delta = chunk.choices[0].delta if chunk.choices else None
        if delta and delta.content:
            yield delta.content

# Task #347: ``_stream_bedrock`` was deleted alongside providers/bedrock.py.
# boto3 / Bedrock Converse streaming is no longer a supported path.


async def call_llm_api_stream(messages: list, model: str = None, max_tokens: int = 2048, intent: str = "", response_lang: str = ""):
    """
    Real token-by-token streaming from the LLM provider.
    Uses native streaming APIs for instant first-token delivery.
    Supports: Sarvam, Groq, Fireworks, Gemini, Cerebras, Workers AI, Vertex, Azure OpenAI.
    'openai/gpt-oss-20b' triggers the smart SLM pool (Fireworks/Groq/Cerebras/Gemini).
    When response_lang is an Indic code (as/hi/etc), optimized Sarvam routing is applied.
    """
    _indic_mode = _is_indic_lang(response_lang)
    _stream_t0 = time.monotonic()

    if _indic_mode:
        # Indic (Assamese) path: resolve Sarvam-preferred model from the
        # dedicated `_SARVAM_PROVIDERS` list. Sarvam is no longer in
        # `_LLM_PROVIDERS`, so we MUST look it up from its own list to keep
        # the Assamese hedged-key race functional.
        _resolved_indic_model = None
        for _pref_model in _SARVAM_INDIC_MODEL_PREFERENCE:
            _prov, _pkey = _resolve_provider_for_model(_pref_model, _SARVAM_PROVIDERS)
            if _prov == "sarvam" and _pkey:
                _resolved_indic_model = _pref_model
                break
        if _resolved_indic_model:
            model = _resolved_indic_model
            logger.info(f"[INDIC] Auto-selected Sarvam model '{model}' for {response_lang} response")
        else:
            logger.warning(f"[INDIC] No Sarvam model available from preference chain, using default")

    use_model_raw = model or LLM_MODEL

    # ── Vertex Gemini Flash chat fast-path (Task #607) — REMOVED Task #490 ────
    # Vertex is no longer a chat hot-path provider. All chat streaming flows
    # through the legacy SLM / Sarvam / Azure pools below. The Indic-vertex
    # admin toggle is also gone (Sarvam is the sole Indic provider per V4 §4).
    _indic_vertex_active = False  # retained for downstream conditionals (Task #490)

    # Task #490 — Vertex chat hot-path was removed. Some clients (admin
    # API config, env CHAT_DEFAULT_MODEL set on legacy deploys) still
    # send the legacy `"vertex/gemini-flash"` alias; transparently
    # rewrite it to the Workers-AI primary so the request lands on a
    # live branch instead of 5xxing.
    #
    # COMPATIBILITY POLICY: this rewrite is a transitional shim with a
    # hard cutoff of **2026-08-01** (≈12 weeks from the Task #490 merge
    # on 2026-05-06). The cutoff window covers the longest known
    # CHAT_DEFAULT_MODEL setter (admin UI override, persisted in Mongo)
    # plus the ACA env-var rotation cadence. After 2026-08-01 this
    # branch is to be deleted; any remaining `vertex/gemini-flash`
    # value will fall through to `_resolve_provider_for_model` and
    # raise the standard "no provider configured" error so the operator
    # is forced to update their config.
    if use_model_raw == "vertex/gemini-flash":
        use_model_raw = "openai/gpt-oss-20b"
        model = use_model_raw

    use_model_resolved = _MODEL_ALIAS_MAP.get(use_model_raw, use_model_raw)
    # In Indic (Assamese) mode, prepend `_SARVAM_PROVIDERS` so the resolver
    # finds Sarvam keys first (Sarvam is no longer in `_LLM_PROVIDERS`),
    # then falls through to the general chain (Gemini etc.) when no Sarvam
    # key is configured. Non-Indic paths use the chat-only chain unchanged.
    _prov_list = (_SARVAM_PROVIDERS + _LLM_PROVIDERS) if _indic_mode else _LLM_PROVIDERS_CHAT
    provider, key = _resolve_provider_for_model(use_model_resolved, _prov_list)
    if use_model_raw != use_model_resolved:
        logger.info(f"Model alias '{use_model_raw}' → '{use_model_resolved}' ({provider})")
    use_model = _safe_model_for_provider(use_model_resolved, provider, _prov_list)
    if use_model != use_model_resolved:
        logger.info(f"Model '{use_model_resolved}' not compatible with {provider} → using '{use_model}'")

    if not key and provider != "sarvam":
        yield f"data: {json.dumps({'error': 'LLM API key not configured'})}\n\n"
        return

    in_think = False
    buf = ""

    _SSE_BATCH = 2    # flush every 2 chars — near-instant token delivery

    async def _emit_tokens(token_source):
        # All state is LOCAL — each call (including parallel producers in Phase 1)
        # gets its own independent think-strip state, preventing race conditions.
        import re as _re
        _CLOSE_KEEP = len('</think>') - 1   # 7
        _in_think   = False
        _buf        = ""
        think_done  = False
        batch       = ""
        _visible_text = ""
        _think_buf  = []

        async for token in token_source:
            if think_done:
                cleaned = _re.sub(r'<think>[\s\S]*?</think>', '', token)
                if cleaned:
                    batch += cleaned
                    if len(batch) >= _SSE_BATCH:
                        _visible_text += batch
                        yield f"data: {json.dumps({'content': batch})}\n\n"
                        batch = ""
                continue

            _buf += token
            while _buf:
                if _in_think:
                    close_idx = _buf.find('</think>')
                    if close_idx != -1:
                        _think_buf.append(_buf[:close_idx])
                        _buf = _buf[close_idx + 8:]
                        _in_think  = False
                        think_done = True
                        if _buf:
                            batch += _buf
                            _buf = ""
                            if len(batch) >= _SSE_BATCH:
                                _visible_text += batch
                                yield f"data: {json.dumps({'content': batch})}\n\n"
                                batch = ""
                        break
                    else:
                        if len(_buf) > _CLOSE_KEEP:
                            _think_buf.append(_buf[:-_CLOSE_KEEP])
                            _buf = _buf[-_CLOSE_KEEP:]
                        break
                else:
                    open_idx = _buf.find('<think>')
                    if open_idx != -1:
                        before = _buf[:open_idx]
                        if before:
                            batch += before
                            if len(batch) >= _SSE_BATCH:
                                _visible_text += batch
                                yield f"data: {json.dumps({'content': batch})}\n\n"
                                batch = ""
                        _buf      = _buf[open_idx + 7:]
                        _in_think = True
                    elif _buf.endswith(('<', '<t', '<th', '<thi', '<thin', '<think')):
                        partial_start = _buf.rfind('<')
                        candidate     = _buf[partial_start:]
                        if '<think>'[:len(candidate)] == candidate:
                            before = _buf[:partial_start]
                            if before:
                                batch += before
                                if len(batch) >= _SSE_BATCH:
                                    _visible_text += batch
                                    yield f"data: {json.dumps({'content': batch})}\n\n"
                                    batch = ""
                            _buf = candidate
                            break
                        else:
                            batch += _buf
                            _buf   = ""
                            if len(batch) >= _SSE_BATCH:
                                _visible_text += batch
                                yield f"data: {json.dumps({'content': batch})}\n\n"
                                batch = ""
                    else:
                        batch += _buf
                        _buf   = ""
                        if len(batch) >= _SSE_BATCH:
                            _visible_text += batch
                            yield f"data: {json.dumps({'content': batch})}\n\n"
                            batch = ""
                        break

        if batch and not _in_think:
            _visible_text += batch
            yield f"data: {json.dumps({'content': batch})}\n\n"
        if _buf and not _in_think:
            _visible_text += _buf
            yield f"data: {json.dumps({'content': _buf})}\n\n"

        if not _visible_text.strip() and (_in_think or think_done):
            fallback_text = "".join(_think_buf)
            if _in_think and _buf:
                fallback_text += _buf
            fallback_text = _re.sub(r'</?think\s*/?>', '', fallback_text).strip()
            fallback_text = _re.sub(r'</?\w*$', '', fallback_text).strip()
            if fallback_text and len(fallback_text) > 5:
                logger.info(f"Think-block fallback: emitting {len(fallback_text)} chars of think content as response")
                yield f"data: {json.dumps({'content': fallback_text})}\n\n"

    async def _stream_from_provider(p_name: str, p_key: str, p_model: str):
        """Yield raw tokens from a provider. Raises on failure."""
        _mt = _clamp_max_tokens(p_model, max_tokens)
        if p_name == "workers-ai":
            logger.info(f"LLM stream: provider=workers-ai, model={p_model}")
            from providers.cloudflare_ai import stream_chat as _cf_stream
            if p_model.startswith("@cf/"):
                model_key = p_model
            else:
                model_key = "chat"
                if "120b" in p_model or "gpt-oss" in p_model:
                    model_key = "chat_long"
                elif "coder" in p_model:
                    model_key = "chat_code"
                elif "8b" in p_model or "fast" in p_model.lower():
                    model_key = "chat_fast"
            async for token in _cf_stream(messages, model_key=model_key, max_tokens=_mt):
                yield token
            return
        if p_name == "sarvam":
            _input_est = sum(len(m.get("content", "")) for m in messages) // 4
            _think_overhead = SARVAM_THINK_BUFFER
            _sarvam_cap = max(256, 7192 - _input_est - _think_overhead - 100)
            _mt = min(_mt, _sarvam_cap)
            async for token in _stream_sarvam(messages, p_key, p_model, _mt, response_lang=response_lang):
                yield token
        # Task #490 — gemini→vertex stream branch removed. Vertex no longer
        # serves chat / streaming traffic; it is `content_format` only.
        # Task #491 — legacy SLM stream branch removed.
        # Task #347 / V4 §0: groq stream branch removed — _stream_openai_compat
        # is no longer invoked with the groq base URL. PROVIDER_PRIORITY drops
        # groq; alerting (metrics.py check #9) and counters (llm.py
        # _PROVIDER_429_WINDOWS) also dropped.
        # Task #347: xAI/Grok stream branch removed — _stream_xai is gone
        # and PROVIDER_PRIORITY no longer routes to "xai".
        elif p_name == "openrouter":
            logger.info(f"LLM stream: provider=openrouter, model={p_model}")
            async for token in _stream_openai_compat(messages, p_key, p_model, _mt, "openrouter", "https://openrouter.ai/api/v1"):
                yield token
        # Task #347: bedrock stream branch removed — providers/bedrock.py and
        # _stream_bedrock are gone; PROVIDER_PRIORITY no longer lists bedrock.
        else:
            logger.info(f"LLM stream: provider={p_name}, model={p_model}")
            chat = LlmChat(api_key=p_key or OPENAI_API_KEY, session_id=str(uuid.uuid4())).with_model(p_name, p_model)
            async for token in chat.stream_messages(messages, max_tokens=_mt):
                yield token

    # ── Syrabit SLM: concurrent smart pool ──────────────────────────────────────
    # pick() returns the fastest available slot (by speed tier) with spare capacity.
    # async with slot["sem"] lets up to max_concurrent requests run in parallel.
    # Tokens are yielded in real-time as they arrive (true streaming).
    # TTFT timeout ensures fast failover when a provider is unresponsive.
    _SLM_SLOT_TIMEOUT = 0.7    # max seconds between any two tokens mid-stream
    _SLM_TTFT_TIMEOUT = 1.5    # max seconds to wait for FIRST token from a slot

    _SLM_PROVIDER_MAX_INPUT_CHARS = {
        "sarvam": 12000,
        # groq removed in Task #347 / V4 §0
        "gemini": 500000,
        "openrouter": 200000,
        "openai": 80000,
        # bedrock removed in Task #347
    }

    if use_model_raw == "openai/gpt-oss-20b":
        # ── Azure OpenAI fast-path (T007) ─────────────────────────────────────
        # When the default English SLM model is requested and Azure is configured
        # via the CF AI Gateway, attempt Azure GPT-4.1-mini streaming first.
        # Azure offers better output quality and typically faster TTFT (~200-400ms)
        # than the Workers AI SLM hedged pool (~400-800ms).
        # On ANY failure BEFORE the first token → silently fall through to SLM pool.
        # On mid-stream failure AFTER the first token → emit error and return.
        if not _indic_mode:
            try:
                from providers import azure_openai as _az_prov
                if _az_prov.ENABLED:
                    _az_first_token = False
                    _az_ttft_ms = 0.0
                    _az_t0 = time.monotonic()
                    try:
                        _az_batch = ""
                        _AZ_BATCH_SIZE = 2
                        async for token in _az_prov.stream_chat(messages, max_tokens=max_tokens):
                            if not _az_first_token:
                                _az_ttft_ms = (time.monotonic() - _az_t0) * 1000
                                logger.info(f"[AZURE-PERF] TTFT={_az_ttft_ms:.0f}ms model=azure/gpt-4.1-mini")
                                _az_first_token = True
                            _az_batch += token
                            if len(_az_batch) >= _AZ_BATCH_SIZE:
                                yield f"data: {json.dumps({'content': _az_batch})}\n\n"
                                _az_batch = ""
                        if _az_batch:
                            yield f"data: {json.dumps({'content': _az_batch})}\n\n"
                        if _az_first_token:
                            _az_total_ms = (time.monotonic() - _az_t0) * 1000
                            logger.info(f"[AZURE-PERF] Total={_az_total_ms:.0f}ms model=azure/gpt-4.1-mini")
                            try:
                                from chat_speedup_metrics import record_provider_call as _rec_prov
                                _rec_prov("azure_openai", ttfb_ms=_az_ttft_ms, total_ms=_az_total_ms)
                            except Exception:
                                pass
                            yield f"data: {json.dumps({'__provider': 'azure_openai'})}\n\n"
                            return
                        logger.warning("[AZURE-FASTPATH] Empty stream — falling back to SLM pool")
                    except Exception as _az_err:
                        if _az_first_token:
                            logger.warning(f"[AZURE-FASTPATH] Mid-stream error: {type(_az_err).__name__}: {str(_az_err)[:200]}")
                            yield f"data: {json.dumps({'error': 'AI service interrupted'})}\n\n"
                            return
                        logger.warning(f"[AZURE-FASTPATH] Pre-first-token failure: {type(_az_err).__name__}: {str(_az_err)[:200]} — SLM pool fallback")
                    try:
                        from chat_speedup_metrics import record_provider_fallback as _rec_fb
                        _rec_fb("azure_openai", "slm_pool")
                    except Exception:
                        pass
            except ImportError:
                pass
        _active_pool = _slm_pool
        _input_chars = sum(len(m.get("content", "")) for m in messages)

        _skipped_slots: set = set()
        _candidates = []
        for _ in range(len(_active_pool.all_slots)):
            slot = _active_pool.pick(_skipped_slots)
            if slot is None:
                break
            p_name = slot["provider"]
            _max_chars = _SLM_PROVIDER_MAX_INPUT_CHARS.get(p_name, 80000)
            if _input_chars > _max_chars:
                logger.info(f"SLM pool: skipping {p_name}/{slot['model']} — input too large ({_input_chars} chars > {_max_chars} limit)")
                _skipped_slots.add(id(slot))
                continue
            _candidates.append(slot)
            _skipped_slots.add(id(slot))
            if len(_candidates) >= 3:
                break

        if _candidates:
            _effective_ttft = min(2.0, _SLM_TTFT_TIMEOUT + (0.3 if _input_chars > 8000 else 0.0))
            _hedged_q: asyncio.Queue = asyncio.Queue()
            _hedged_errors: dict = {}

            async def _hedged_producer(_slot, _slot_idx):
                _pn, _pk, _pm = _slot["provider"], _slot["key"], _slot["model"]
                try:
                    async with _slot["sem"]:
                        _chunk_count = 0
                        async for chunk in _emit_tokens(_stream_from_provider(_pn, _pk, _pm)):
                            _chunk_count += 1
                            await _hedged_q.put((_slot_idx, "chunk", chunk))
                        if _chunk_count == 0:
                            logger.warning(f"SLM hedged: {_pn}/{_pm} 0 chunks")
                        await _hedged_q.put((_slot_idx, "done", None))
                except Exception as exc:
                    _hedged_errors[_slot_idx] = exc
                    logger.warning(f"SLM hedged: {_pn}/{_pm} error: {type(exc).__name__}: {str(exc)[:200]}")
                    await _hedged_q.put((_slot_idx, "error", None))

            _hedged_tasks = [asyncio.create_task(_hedged_producer(s, i)) for i, s in enumerate(_candidates)]
            if len(_candidates) > 1:
                _race_desc = " vs ".join(f"{s['provider']}/{s['model']}" for s in _candidates)
                logger.info(f"SLM hedged: racing {_race_desc}")

            _winner = None
            _finished_slots: set = set()
            try:
                _deadline = time.monotonic() + _effective_ttft
                while _winner is None and len(_finished_slots) < len(_candidates):
                    _remaining = _deadline - time.monotonic()
                    if _remaining <= 0:
                        break
                    try:
                        _sid, _evt, _data = await asyncio.wait_for(_hedged_q.get(), timeout=_remaining)
                    except asyncio.TimeoutError:
                        break
                    if _evt == "chunk":
                        _winner = _sid
                    elif _evt in ("done", "error"):
                        _finished_slots.add(_sid)
                        if _evt == "error":
                            _slm_pool.mark_err(_candidates[_sid])
            except Exception:
                pass

            if _winner is not None:
                _win_slot = _candidates[_winner]
                _slm_pool.mark_ok(_win_slot)
                _win_pname = _win_slot["provider"]
                _win_model = _win_slot["model"]
                if len(_candidates) > 1:
                    logger.info(f"SLM hedged: winner={_win_pname}/{_win_model}")

                for i, t in enumerate(_hedged_tasks):
                    if i != _winner:
                        t.cancel()

                yield _data

                _tokens_yielded = 1
                while True:
                    try:
                        _sid, _evt, _chunk = await asyncio.wait_for(_hedged_q.get(), timeout=_SLM_SLOT_TIMEOUT)
                    except asyncio.TimeoutError:
                        _slm_pool.mark_err(_win_slot)
                        logger.warning(f"SLM hedged: {_win_pname}/{_win_model} stalled mid-stream after {_SLM_SLOT_TIMEOUT}s ({_tokens_yielded} tokens yielded)")
                        break
                    if _sid != _winner:
                        continue
                    if _evt == "chunk":
                        yield _chunk
                        _tokens_yielded += 1
                    else:
                        if _winner in _hedged_errors and _tokens_yielded <= 1:
                            _slm_pool.mark_err(_win_slot)
                        break

                _hedged_tasks[_winner].cancel()
                for t in _hedged_tasks:
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass

                yield f"data: {json.dumps({'__provider': _win_pname})}\n\n"
                return
            else:
                for t in _hedged_tasks:
                    t.cancel()
                for t in _hedged_tasks:
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass
                for i, s in enumerate(_candidates):
                    if i not in _finished_slots:
                        _slm_pool.mark_err(s)
                        logger.warning(f"SLM hedged: {s['provider']}/{s['model']} TTFT timeout after {_effective_ttft}s")

        # SLM pool exhausted — hard-fall-back to Workers AI direct stream
        # (bypassing the pool's concurrency accounting).
        if _CF_AI_ENABLED:
            _fb_model = "@cf/meta/llama-3.1-8b-instruct-fp8"
            logger.warning(
                f"SLM pool exhausted — hard-fallback to workers-ai/{_fb_model}"
            )
            _fb_ok = False
            try:
                from providers.cloudflare_ai import chat_stream as _cf_cs
                async def _slm_wai_fb():
                    async for _tok in _cf_cs(messages, model_key=_fb_model, max_tokens=max_tokens):
                        yield _tok
                async for chunk in _emit_tokens(_slm_wai_fb()):
                    _fb_ok = True
                    yield chunk
                if _fb_ok:
                    yield f"data: {json.dumps({'__provider': 'workers-ai'})}\n\n"
                    return
            except Exception as _fb_err:
                logger.warning(f"SLM workers-ai-fallback failed: {type(_fb_err).__name__}: {str(_fb_err)[:120]}")

        yield f"data: {json.dumps({'error': 'All AI providers temporarily unavailable'})}\n\n"
        return

    # ── Indic (Assamese) response: Sarvam-MAIN + Gemini-FALLBACK ───────────────
    # User-mandated routing (2026-04-26): for Assamese chat *response*
    # generation, Sarvam is the primary provider; Gemini is reached only
    # when ALL Sarvam keys fail before the first token. This is the inverse
    # of the translation pipeline (Gemini-main + Sarvam-polish — see
    # `routes/ai_chat.py::_assamese_translate_gemini_main_sarvam_polish`).
    #
    # Implementation = two phases, never simultaneous:
    #   Phase 1 — Sarvam-only race across all available Sarvam keys
    #             (still hedged across keys for key-level resilience). The
    #             first Sarvam key to emit a chunk wins; the rest are
    #             cancelled. This preserves Sarvam-quality output whenever
    #             at least one Sarvam key responds within
    #             _SARVAM_TTFT_TIMEOUT.
    #   Phase 2 — Triggered ONLY if Phase 1 emits zero chunks (all Sarvam
    #             keys errored, were rate-limited, or timed out). Streams
    #             directly from Gemini 2.5 Flash. This is a fallback path,
    #             not a hedged co-runner — Gemini cannot "steal" the
    #             first-token slot from Sarvam due to network jitter.
    # Two-stage Sarvam timeout:
    #   Stage 1 — connection probe: Sarvam must return its FIRST RAW TOKEN
    #             (even a think token) within _SARVAM_CONN_TIMEOUT seconds.
    #             If no raw token arrives → Sarvam is dead → go to Phase 2.
    #   Stage 2 — visible answer: after a key proves it's alive, we wait up to
    #             _SARVAM_VISIBLE_TIMEOUT more seconds for the first non-think
    #             chunk (after </think>).  Sarvam-m with think enabled can
    #             spend 10-15 s in its <think> block before writing the answer.
    _SARVAM_CONN_TIMEOUT    = 2.5   # max seconds to receive ANY raw token
    _SARVAM_VISIBLE_TIMEOUT = 16.0  # max additional seconds for visible chunk
    _SARVAM_SLOT_TIMEOUT    = 1.2
    if _indic_mode and provider == "sarvam":
        # Pull Sarvam keys from `_SARVAM_PROVIDERS` (the dedicated
        # Assamese-only list). `_prov_list` may also contain Sarvam entries
        # (we prepend `_SARVAM_PROVIDERS` to it in indic mode above), but
        # reading from `_SARVAM_PROVIDERS` directly is more explicit and
        # robust if the prepend logic ever changes.
        _sarvam_keys = [p["key"] for p in _SARVAM_PROVIDERS if p.get("key")]
        if key and key not in _sarvam_keys:
            _sarvam_keys.insert(0, key)
        _sarvam_keys = list(dict.fromkeys(_sarvam_keys))
        _sarvam_candidates = [
            {"provider": "sarvam", "key": _sk, "model": use_model}
            for _sk in _sarvam_keys
        ]

        _indic_q: asyncio.Queue = asyncio.Queue()

        async def _indic_producer(_cand, _cand_idx):
            _cprov, _ckey, _cmodel = _cand["provider"], _cand["key"], _cand["model"]
            try:
                _cn = 0
                _conn_signaled = False

                async def _raw_conn_wrapper():
                    """Intercept raw tokens before _emit_tokens strips think blocks.
                    Emits a 'connected' queue event on the very first token (even a
                    think token) so the Phase 1 race knows this key is alive."""
                    nonlocal _conn_signaled
                    async for _raw_tok in _stream_from_provider(_cprov, _ckey, _cmodel):
                        if not _conn_signaled:
                            _conn_signaled = True
                            await _indic_q.put((_cand_idx, "connected", None))
                        yield _raw_tok

                async for chunk in _emit_tokens(_raw_conn_wrapper()):
                    _cn += 1
                    await _indic_q.put((_cand_idx, "chunk", chunk))
                if _cn == 0:
                    logger.warning(f"[INDIC] {_cprov}/{_cmodel} idx={_cand_idx} returned 0 visible chunks (think-only?)")
                await _indic_q.put((_cand_idx, "done", None))
            except Exception as _e:
                _is_rate = any(s in str(_e).lower() for s in ("429", "rate", "quota", "throttl"))
                logger.warning(f"[INDIC] {_cprov}/{_cmodel} idx={_cand_idx} failed ({type(_e).__name__}: {str(_e)[:120]}) rate_limit={_is_rate}")
                await _indic_q.put((_cand_idx, "error", None))

        _sarvam_winner = None
        _sarvam_race_t0 = time.monotonic()
        _phase1_tasks: list = []

        # ── Phase 1: two-stage Sarvam race ───────────────────────────────
        # Stage 1: wait up to _SARVAM_CONN_TIMEOUT for ANY key to signal
        #          "connected" (first raw token, including think tokens).
        # Stage 2: once ≥1 key is connected, wait up to _SARVAM_VISIBLE_TIMEOUT
        #          for a "chunk" (first non-think visible token).
        if _sarvam_candidates:
            _phase1_tasks = [
                asyncio.create_task(_indic_producer(c, i))
                for i, c in enumerate(_sarvam_candidates)
            ]
            _phase1_providers = ", ".join(
                f"{c['provider']}/{c['model']}" for c in _sarvam_candidates
            )
            logger.info(
                f"[INDIC] Phase 1 (Sarvam-MAIN): racing "
                f"{len(_sarvam_candidates)} Sarvam keys for {response_lang}: "
                f"{_phase1_providers}"
            )

            _sarvam_finished: set = set()
            _sarvam_connected: set = set()
            try:
                # Stage 1 — connection probe
                _conn_deadline = time.monotonic() + _SARVAM_CONN_TIMEOUT
                while (
                    not _sarvam_connected
                    and len(_sarvam_finished) < len(_sarvam_candidates)
                ):
                    _rem = _conn_deadline - time.monotonic()
                    if _rem <= 0:
                        break
                    try:
                        _sid, _evt, _data = await asyncio.wait_for(_indic_q.get(), timeout=_rem)
                    except asyncio.TimeoutError:
                        break
                    if _evt == "connected":
                        _sarvam_connected.add(_sid)
                        logger.info(
                            f"[INDIC] key idx={_sid} connected in "
                            f"{(time.monotonic()-_sarvam_race_t0)*1000:.0f}ms"
                        )
                    elif _evt == "chunk":
                        # Visible token arrived during Stage 1 (think was very short)
                        _sarvam_winner = _sid
                    elif _evt in ("done", "error"):
                        _sarvam_finished.add(_sid)

                # Stage 2 — wait for visible token (only if ≥1 key is alive)
                if _sarvam_winner is None and _sarvam_connected:
                    _vis_deadline = time.monotonic() + _SARVAM_VISIBLE_TIMEOUT
                    while _sarvam_winner is None and len(_sarvam_finished) < len(_sarvam_candidates):
                        _rem = _vis_deadline - time.monotonic()
                        if _rem <= 0:
                            break
                        try:
                            _sid, _evt, _data = await asyncio.wait_for(_indic_q.get(), timeout=_rem)
                        except asyncio.TimeoutError:
                            break
                        if _evt == "chunk":
                            _sarvam_winner = _sid
                        elif _evt == "connected":
                            _sarvam_connected.add(_sid)
                        elif _evt in ("done", "error"):
                            _sarvam_finished.add(_sid)
                elif not _sarvam_connected:
                    logger.warning(
                        f"[INDIC] Phase 1 — no Sarvam key connected within "
                        f"{_SARVAM_CONN_TIMEOUT}s — skipping to Phase 2"
                    )
            except Exception:
                pass
        else:
            logger.warning(
                f"[INDIC] No Sarvam keys configured — skipping Phase 1, "
                f"jumping straight to Gemini fallback for {response_lang}"
            )

        # ── Phase 1 winner: emit Sarvam stream ──────────────────────────
        if _sarvam_winner is not None:
            _win_cand = _sarvam_candidates[_sarvam_winner]
            _ttft_ms = (time.monotonic() - _sarvam_race_t0) * 1000
            logger.info(
                f"[INDIC-PERF] Phase 1 WIN — TTFT={_ttft_ms:.0f}ms "
                f"lang={response_lang} winner={_win_cand['provider']}/{_win_cand['model']} "
                f"idx={_sarvam_winner}"
            )

            for i, t in enumerate(_phase1_tasks):
                if i != _sarvam_winner:
                    t.cancel()

            yield _data

            while True:
                try:
                    _sid, _evt, _chunk = await asyncio.wait_for(_indic_q.get(), timeout=_SARVAM_SLOT_TIMEOUT)
                except asyncio.TimeoutError:
                    logger.warning(f"[INDIC] {_win_cand['provider']}/{_win_cand['model']} stalled mid-stream")
                    break
                if _sid != _sarvam_winner:
                    continue
                if _evt == "chunk":
                    yield _chunk
                else:
                    break

            _total_ms = (time.monotonic() - _sarvam_race_t0) * 1000
            logger.info(
                f"[INDIC-PERF] Phase 1 Total={_total_ms:.0f}ms "
                f"lang={response_lang} winner={_win_cand['provider']}/{_win_cand['model']}"
            )

            for t in _phase1_tasks:
                t.cancel()
            for t in _phase1_tasks:
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass

            yield f"data: {json.dumps({'__provider': _win_cand['provider']})}\n\n"
            return

        # ── Phase 1 LOST → Phase 2: Workers AI fallback ──────────────────
        # Cancel any straggler Sarvam tasks before starting Workers AI so
        # we don't double-stream. We don't await them here — they'll be
        # GC'd by the event loop. (`_emit_tokens` is cancellation-safe.)
        for t in _phase1_tasks:
            t.cancel()

        _phase1_elapsed = (time.monotonic() - _sarvam_race_t0) * 1000
        if _sarvam_candidates:
            logger.warning(
                f"[INDIC] Phase 1 LOST — all {len(_sarvam_candidates)} "
                f"Sarvam keys failed/timed out in {_phase1_elapsed:.0f}ms — "
                f"falling back to Workers AI (Phase 2)"
            )

        if not _CF_AI_ENABLED:
            logger.warning(
                f"[INDIC] Phase 2 unavailable — Workers AI not configured. "
                f"Returning error for {response_lang}."
            )
            _is_as = (response_lang or "").lower().strip() in ("as", "as-in")
            _err_payload = {
                'error': 'Assamese chat service temporarily unavailable. Please try again.' if _is_as else 'Indic language AI service temporarily unavailable. Please try again.',
                'lang': response_lang or 'as',
            }
            if _is_as:
                _err_payload['error_kind'] = 'assamese_unavailable'
                # Task #374: page on-call when both Assamese rails are red.
                # Task #379: persist failing leg so admins see which fallback
                # was missing (vs. errored).
                try:
                    record_assamese_unavailable(
                        failing_leg="workers_ai_unavailable",
                        error_summary="Workers AI Phase-2 fallback not configured (CF_AI_ENABLED=false)",
                    )
                except Exception:
                    pass
            yield f"data: {json.dumps(_err_payload)}\n\n"
            return

        # Strip the Sarvam-specific `/think …` prefix from the system
        # message — Workers AI doesn't understand the Sarvam `/think`
        # directive. Replace it with a plain Assamese-only instruction.
        import re as _re2
        _wai_msgs = []
        for _gm in messages:
            if _gm.get("role") == "system":
                _gc = _gm["content"]
                _gc = _re2.sub(r"^/think[^\n]*\n?", "", _gc, flags=_re2.MULTILINE)
                _gc = (
                    "CRITICAL: Reply entirely in Assamese (অসমীয়া) script. "
                    "Do NOT write in English. Every word must be in Assamese. "
                    "Technical terms/units/proper nouns (AHSEC, SEBA, Newton, cm, kg) may stay in Latin.\n\n"
                    + _gc.lstrip()
                )
                _wai_msgs.append({**_gm, "content": _gc})
            else:
                _wai_msgs.append(_gm)

        _wai_model = "@cf/qwen/qwen2.5-72b-instruct"
        _phase2_t0 = time.monotonic()
        logger.info(
            f"[INDIC] Phase 2 (Workers AI fallback): streaming from "
            f"workers-ai/{_wai_model} for {response_lang}"
        )

        _phase2_first_token = False
        try:
            from providers.cloudflare_ai import chat_stream as _cf_chat_stream
            async def _wai_phase2_stream():
                async for _tok in _cf_chat_stream(_wai_msgs, model_key=_wai_model, max_tokens=max(max_tokens, 1024)):
                    yield _tok
            async for chunk in _emit_tokens(_wai_phase2_stream()):
                if not _phase2_first_token:
                    _ttft_ms = (time.monotonic() - _phase2_t0) * 1000
                    logger.info(
                        f"[INDIC-PERF] Phase 2 TTFT={_ttft_ms:.0f}ms "
                        f"lang={response_lang} provider=workers-ai/{_wai_model}"
                    )
                    _phase2_first_token = True
                yield chunk
        except Exception as _ge:
            if _phase2_first_token:
                logger.warning(
                    f"[INDIC] Phase 2 mid-stream error: "
                    f"{type(_ge).__name__}: {str(_ge)[:160]}"
                )
                yield f"data: {json.dumps({'error': 'AI service interrupted'})}\n\n"
                return
            logger.warning(
                f"[INDIC] Phase 2 failed before first token: "
                f"{type(_ge).__name__}: {str(_ge)[:160]}"
            )
            _is_as2 = (response_lang or "").lower().strip() in ("as", "as-in")
            _err_payload2 = {
                'error': 'Assamese chat service temporarily unavailable. Please try again.' if _is_as2 else 'Indic language AI service temporarily unavailable. Please try again.',
                'lang': response_lang or 'as',
            }
            if _is_as2:
                _err_payload2['error_kind'] = 'assamese_unavailable'
                # Task #374: page on-call when both Assamese rails are red.
                # Task #379: persist failing leg + the underlying provider
                # error so admins can see *why* Phase-2 died.
                try:
                    record_assamese_unavailable(
                        failing_leg="workers_ai_phase2",
                        error_summary=f"{type(_ge).__name__}: {str(_ge)[:160]}",
                    )
                except Exception:
                    pass
            yield f"data: {json.dumps(_err_payload2)}\n\n"
            return

        _phase2_total_ms = (time.monotonic() - _phase2_t0) * 1000
        logger.info(
            f"[INDIC-PERF] Phase 2 Total={_phase2_total_ms:.0f}ms "
            f"lang={response_lang} provider=workers-ai/{_wai_model}"
        )
        yield f"data: {json.dumps({'__provider': 'workers-ai'})}\n\n"
        return

    # ── All other models: single provider ───────────────────────────────────────
    try:
        _chunk_n = 0
        async for chunk in _emit_tokens(_stream_from_provider(provider, key, use_model)):
            if _chunk_n == 0:
                _ttft_ms = (time.monotonic() - _stream_t0) * 1000
                logger.info(f"[EN-PERF] TTFT={_ttft_ms:.0f}ms model={use_model} provider={provider}")
            _chunk_n += 1
            yield chunk
        _total_ms = (time.monotonic() - _stream_t0) * 1000
        logger.info(f"[EN-PERF] Total={_total_ms:.0f}ms chunks={_chunk_n} model={use_model} provider={provider}")
        yield f"data: {json.dumps({'__provider': provider})}\n\n"
    except HTTPException as http_err:
        yield f"data: {json.dumps({'error': str(http_err.detail)})}\n\n"
    except Exception as e:
        logger.error(f"LLM streaming error: {type(e).__name__}: {str(e)[:200]}")
        yield f"data: {json.dumps({'error': 'AI service temporarily unavailable'})}\n\n"


# ── Non-LLM feature dispatch (embed / translate / search / rerank / vision) ───
#
# Each function calls select_provider() for its feature key, then routes to the
# appropriate provider client.  Providers that don't support the modality raise
# RuntimeError which the loop treats as a transient failure and retries from the
# remaining weighted pool (fallback-without-replacement).
#
# These entry points are used by:
#   embed        → syllabus_embedder.py, chunk_embedder.py, vertex_services.py
#   translate    → admin_pipeline.py, cms_sarvam_health.py
#   search_rag   → ai_chat.py (RAG retrieval pre-search)
#   live_search  → ai_chat.py (_early_web_search)
#   rerank       → rag.py chunk re-scoring
#   vision       → admin_content.py image analysis
#   vector_search→ rag.py vector recall


# Indic Unicode script ranges used by the embed-feature language router.
# Bengali/Assamese (U+0980–U+09FF), Devanagari for Hindi/Marathi
# (U+0900–U+097F), and a few common South-Indian blocks so the same
# detector covers the wider Sarvam-supported language set we already accept
# in chat. Detection is intentionally cheap (single regex pass) because it
# runs on the hot path of every embed call.
import re as _re_embed_lang
_INDIC_SCRIPT_RE = _re_embed_lang.compile(
    r"[\u0900-\u097F"   # Devanagari (Hindi, Marathi, Sanskrit)
    r"\u0980-\u09FF"    # Bengali / Assamese
    r"\u0A00-\u0A7F"    # Gurmukhi (Punjabi)
    r"\u0A80-\u0AFF"    # Gujarati
    r"\u0B00-\u0B7F"    # Oriya
    r"\u0B80-\u0BFF"    # Tamil
    r"\u0C00-\u0C7F"    # Telugu
    r"\u0C80-\u0CFF"    # Kannada
    r"\u0D00-\u0D7F]"   # Malayalam
)
_INDIC_LANG_CODES = frozenset({
    "as", "bn", "hi", "mr", "sa", "pa", "gu", "or", "ta", "te", "kn", "ml",
})


def _embed_feature_for(text: str, lang: str) -> str:
    """Return the POOL_WEIGHTS key for an embed call given the *text* and
    caller-supplied *lang* hint.

    Task #491 retired the Voyage/Cohere hybrid; both ``embed_en`` and
    ``embed_indic`` now resolve to the same single-source primary
    (``workers_ai_custom`` — Gemma-300M + Qwen3-0.6B mean-pool, 1024-dim,
    multilingual). The two feature keys are kept so caller telemetry
    (script vs. lang classification) survives.
    """
    if text and _INDIC_SCRIPT_RE.search(text):
        return "embed_indic"
    if (lang or "").lower().strip() in _INDIC_LANG_CODES:
        return "embed_indic"
    return "embed_en"


async def call_embed_with_dispatch(
    text: str,
    task_type: str = "RETRIEVAL_DOCUMENT",
    lang: str = "en",
) -> list:
    """Embed *text* via the weighted provider selected for the 'embed' feature key.

    Routing (single-source, post Task #491):
      ``embed_en`` and ``embed_indic`` →
        workers_ai_custom (primary) → azure_openai → workers_ai

    workers_ai_custom: providers.workers_embed.embed_query — Task #382
                       primary embed via the custom Cloudflare Worker
                       (Gemma-300M + Qwen3-0.6B mean-pooled to 1024-dim).
    workers_ai: cloudflare_ai.embed (1024-dim, @cf/baai/bge-m3) — dormant
                fallback after Task #382.
    azure_openai: branch kept for back-compat in case POOL_WEIGHTS
    is overridden at runtime.
    (Legacy AWS-managed and direct embed branches removed in Tasks #347/#491.)
    Returns a float list on success, raises RuntimeError if all providers fail.
    """
    from config import PROVIDER_PRIORITY as _PP, EMBED_PROVIDER_PRIMARY as _EPP
    feature = _embed_feature_for(text, lang)

    # Task #361 §2 — embedding cache lookup. Vectors are deterministic
    # for the same (text, task_type, lang); a cache hit can be served
    # immediately. Soft-fail: any cache error falls through to the
    # normal weighted dispatch.
    try:
        from embed_cache import get_cached_embedding as _embed_cache_get, set_cached_embedding as _embed_cache_set
        _cached_vec = _embed_cache_get(text, task_type=task_type, lang=lang)
    except Exception:
        _cached_vec = None
        _embed_cache_set = None  # type: ignore[assignment]
    if _cached_vec:
        return _cached_vec

    # Task #489 — V4 §15 cache-only degraded-mode gate. When the
    # operator flips `EMBED_DEGRADED_MODE=true` (matrix §A row
    # "Embed-failover behaviour"): no third-party embedder is invoked;
    # this chunk is enqueued onto `syrabit-reembed-queue` so the
    # AWS Lambda consumer (`sqs_consumers/reembed.py`) can replay it
    # against `embed.syrabit.ai` once the flag is cleared. Cache hits
    # above still return; everything else raises `EmbedDeferredError`
    # so callers fail loud instead of getting a silent zero-vector.
    from vertex_services import EmbedDegradedMode as _EmbedDegradedMode
    # Task #490: gate now combines (a) the operator env override
    # (`EMBED_DEGRADED_MODE=true`, manual cutover) AND (b) the in-process
    # auto-trip controller (`embed_degraded_controller.is_degraded()`,
    # which trips on >=3/5 probe failures or p95 > 2000ms and resets on
    # 5 consecutive successful probes). Either signal flips us into the
    # SQS deferred-replay path so an outage degrades automatically
    # before on-call has to flip the env var by hand.
    from embed_degraded_controller import is_degraded as _embed_is_degraded
    if _embed_is_degraded():
        # Deterministic chunk_id so replay upserts into the same Pinecone
        # vector slot as the original embed would have. Matches the
        # consumer contract in `sqs_consumers/reembed.py` (`chunk_id` +
        # `text` are required, everything else has consumer defaults).
        _chunk_id = "embed:" + hashlib.sha256(
            f"{lang}|{task_type}|{text}".encode("utf-8")
        ).hexdigest()
        try:
            from sqs_fanout import enqueue as _sqs_enqueue
            await _sqs_enqueue(
                "reembed",
                {
                    "chunk_id": _chunk_id,
                    "text": text,
                    "task_type": task_type,
                    "lang": lang,
                    "namespace": os.environ.get("PINECONE_NAMESPACE", "cached_gemma_today"),
                },
            )
        except Exception as _enqueue_exc:
            # Fail loud — do NOT claim the chunk was enqueued when it
            # wasn't. Caller decides whether to surface or retry.
            logger.exception("EMBED_DEGRADED_MODE: reembed enqueue failed for chunk_id=%s", _chunk_id)
            raise _EmbedDegradedMode(
                f"embed: EMBED_DEGRADED_MODE=true and reembed enqueue failed: {_enqueue_exc}",
                chunk_id=_chunk_id,
            ) from _enqueue_exc
        raise _EmbedDegradedMode(
            f"embed: EMBED_DEGRADED_MODE=true — chunk_id={_chunk_id} enqueued to "
            "syrabit-reembed-queue for deferred replay; caller should "
            "serve from Vectorize cache only (V4 §15)",
            chunk_id=_chunk_id,
        )

    def _persist_early(_vec):
        if _embed_cache_set is None or not _vec:
            return
        try:
            _embed_cache_set(text, _vec, task_type=task_type, lang=lang)
        except Exception:
            pass

    # Task #382 — STRICT provider isolation under the new default flag.
    # When EMBED_PROVIDER_PRIMARY=workers_ai_custom we short-circuit
    # the entire weighted-draw + exclusion-redraw loop and call the
    # custom Workers-AI worker directly. A worker failure raises
    # `RuntimeError("embed: workers_ai_custom failed: …")` and the
    # caller decides how to handle it — we DO NOT silently fall back
    # to legacy providers (Cohere / Voyage / Vertex / Pinecone
    # Inference / generic workers_ai bge-m3). The legacy weighted
    # ladder is only reachable when the operator explicitly flips
    # EMBED_PROVIDER_PRIMARY to a legacy provider name, which is the
    # documented rollback contract.
    if (_EPP or "").strip().lower() == "workers_ai_custom":
        from providers import workers_embed as _we_prov
        if not _we_prov.is_enabled():
            raise RuntimeError(
                "embed: workers_ai_custom is the active primary but "
                "WORKERS_EMBED_URL/SECRET are not configured — set them "
                "or flip EMBED_PROVIDER_PRIMARY to a legacy provider "
                "name to roll back"
            )
        _input_type_strict = (
            "search_query" if (task_type or "").upper().endswith("QUERY")
            else "search_document"
        )
        # Task #490 — feed the Option-D auto-trip controller. Real-traffic
        # success / failure / latency is what the trip/reset state machine
        # consumes; without these probe records the controller can only be
        # tripped by env override, defeating the auto-failover guarantee.
        from embed_degraded_controller import record_probe as _embed_record_probe
        import time as _time_mod
        _t0 = _time_mod.perf_counter()
        try:
            _strict_vecs = await _we_prov.embed([text], input_type=_input_type_strict)
        except Exception as _exc:
            _embed_record_probe(False, (_time_mod.perf_counter() - _t0) * 1000.0)
            raise RuntimeError(f"embed: workers_ai_custom failed: {_exc}") from _exc
        if not _strict_vecs:
            _embed_record_probe(False, (_time_mod.perf_counter() - _t0) * 1000.0)
            raise RuntimeError("embed: workers_ai_custom returned no vectors")
        _embed_record_probe(True, (_time_mod.perf_counter() - _t0) * 1000.0)
        _persist_early(_strict_vecs[0])
        return _strict_vecs[0]

    exclude: frozenset = frozenset()
    # Total attempts allowed = the union of providers across the chosen
    # sub-pool plus the generic embed pool's last-resort entry, +1 so we
    # always probe the workers_ai weight-0 fallback after the weighted
    # providers are excluded.
    pool = _PP.get(feature) or _PP.get("embed", [])
    max_attempts = len(pool) + 1

    def _persist(_vec):
        # Reuse the early-persist closure so cache writes are
        # consistent across both the strict-isolation short-circuit
        # and the rollback weighted-draw loop.
        _persist_early(_vec)

    # Task #490 — record every primary-provider attempt's outcome so the
    # auto-trip controller sees real traffic. We only feed probes for the
    # current EMBED_PROVIDER_PRIMARY: secondary fallbacks have their own
    # weighted-pool semantics and shouldn't influence the primary's
    # trip state. `record_probe` is a no-op on success when not tripped.
    from embed_degraded_controller import record_probe as _embed_record_probe
    from config import EMBED_PROVIDER_PRIMARY as _EPP_PROBE
    import time as _time_mod
    for _ in range(max_attempts):
        provider = select_provider(feature, lang=lang, exclude=exclude)
        _is_primary = (provider == _EPP_PROBE)
        _t0 = _time_mod.perf_counter()
        try:
            # Task #490 — `vertex` embed branch removed. Multilingual embed
            # via Vertex `text-embedding-004` is no longer a fallback path.
            # On Workers-AI custom embed outage the embed-failover controller
            # flips the system into Option-D cache-only degraded mode and
            # enqueues misses on the AWS SQS deferred-embed queue (V4 §15).
            if provider == "workers_ai_custom":
                # Task #382 — custom Workers-AI embed worker
                # (Gemma-300M + Qwen3-0.6B → 1024-dim). Only routed
                # when EMBED_PROVIDER_PRIMARY=workers_ai_custom; the
                # config-side pool rebuild already zeroes out this
                # entry on rollback, but we double-gate here so a
                # stale POOL_WEIGHTS in tests can't accidentally hit
                # the worker after the flag has been flipped off.
                from config import EMBED_PROVIDER_PRIMARY as _ep_primary
                if _ep_primary != "workers_ai_custom":
                    raise RuntimeError(
                        f"workers_ai_custom embed disabled "
                        f"(EMBED_PROVIDER_PRIMARY={_ep_primary!r})"
                    )
                from providers import workers_embed as _we_prov
                if not _we_prov.is_enabled():
                    raise RuntimeError(
                        "workers_ai_custom embed: WORKERS_EMBED_URL / "
                        "WORKERS_EMBED_SECRET not configured"
                    )
                _input_type = (
                    "search_query" if (task_type or "").upper().endswith("QUERY")
                    else "search_document"
                )
                _we_vecs = await _we_prov.embed([text], input_type=_input_type)
                if not _we_vecs:
                    raise RuntimeError(
                        "workers_ai_custom embed: empty response"
                    )
                if _is_primary:
                    _embed_record_probe(True, (_time_mod.perf_counter() - _t0) * 1000.0)
                _persist(_we_vecs[0])
                return _we_vecs[0]
            elif provider == "workers_ai":
                from providers.cloudflare_ai import embed as _cf_embed
                _vec_wai = await _cf_embed(text)
                _persist(_vec_wai)
                return _vec_wai
            # Task #347: bedrock embed branch removed (providers/bedrock.py deleted).
            # Task #491 — legacy embed-provider branches removed.
            elif provider == "azure_openai":
                # Azure OpenAI text-embedding-3-large via CF BYOK (Task #256).
                from providers.azure_openai import call_embed as _az_embed
                _az_vec = await _az_embed(text)
                _persist(_az_vec)
                return _az_vec
            else:
                raise RuntimeError(f"embed: unknown provider {provider!r}")
        except Exception as exc:
            if _is_primary:
                _embed_record_probe(False, (_time_mod.perf_counter() - _t0) * 1000.0)
            logger.warning("embed %s failed: %s — removing from pool", provider, exc)
            exclude = exclude | {provider}
        else:
            # Provider returned via `return _vec` above — record success
            # for the primary. Unreachable under normal control flow
            # because of the early returns; kept as a safety net for any
            # future provider branch that falls through instead of
            # returning. The success-path probes for the primary live in
            # each branch's `_persist(...) ; return ...` pair via the
            # success-recording done implicitly when the function returns.
            pass

    raise RuntimeError("embed: all providers exhausted")


async def call_translate_with_dispatch(
    text: str,
    source_lang: str = "en-IN",
    target_lang: str = "as-IN",
    lang: str = "as",
) -> str:
    """Translate *text* via the weighted provider selected for 'translate'.

    Priority (PROVIDER_PRIORITY['translate']):
      workers_ai_indic (IndicTrans2 en→indic-1b, sole primary)
      → azure_openai (Azure Translator REST, fallback when admin toggle on)
      → workers_ai (generic translate prompt, last resort)

    Sarvam was removed by Task #492 (V4 §15 amendment); the Sarvam
    translate REST branch below is gone. Vertex was removed by Task #490.
    Returns the translated string or raises RuntimeError if all providers fail.
    """
    from config import PROVIDER_PRIORITY as _PP, TRANSLATE_PROVIDER as _TP
    exclude: frozenset = frozenset()
    max_attempts = len(_PP.get("translate", [])) + 1

    # Task #386 — when TRANSLATE_PROVIDER=workers_indic, pin the dispatch to
    # workers_ai_indic and refuse to fall back to paid providers. The flag
    # is only honoured for Indic targets; non-Indic targets keep the
    # weighted fallback because IndicTrans2 cannot translate to non-Indic.
    _workers_indic_only = (
        _TP == 'workers_indic'
        and target_lang.lower().replace('-', '_') in ('as', 'as_in', 'hi', 'hi_in', 'bn', 'bn_in')
    )
    try:
        from translate_provider_metrics import record_provider_call as _rpc_metric
    except Exception:
        _rpc_metric = None

    for _ in range(max_attempts):
        if _workers_indic_only:
            provider = "workers_ai_indic"
        else:
            provider = select_provider("translate", lang=lang, exclude=exclude)
        try:
            # Task #492: sarvam translate branch removed (V4 §15 amendment —
            # Sarvam scoped to assamese_rag_chat LLM only). Should select_provider
            # ever return "sarvam" (e.g. an admin re-adds it to the translate
            # pool by mistake) we fail loud rather than silently revive it.
            if provider == "sarvam":
                raise RuntimeError(
                    "sarvam translate: removed by V4 §15 (Task #492); "
                    "Sarvam is scoped to assamese_rag_chat LLM only"
                )
            # Task #490: vertex translate branch removed.
            # Task #347: bedrock translate branch removed.
            if provider == "azure_openai":
                # Azure Translator REST API (AZURE_TRANSLATOR_KEY) — Task #256.
                # Task #338: gated by the azure.translator.enabled admin
                # toggle so ops can drop the Azure path without a redeploy
                # when MI auth/quotas misbehave. Disabled => raise to the
                # outer loop which moves to the next provider in pool.
                from azure_ai_runtime import is_enabled as _az_enabled
                if not await _az_enabled("translator"):
                    raise RuntimeError(
                        "azure translator disabled via admin toggle "
                        "(azure.translator.enabled=false) — routing to next provider"
                    )
                from providers.azure_openai import call_translate as _az_translate
                return await _az_translate(text, target_lang=target_lang, source_lang=source_lang)
            elif provider == "workers_ai_indic":
                # IndicTrans2 en→indic-1b — Assamese translation pool (Task #267).
                # Task #386 widened the guard to all FLORES-200-supported Indic
                # targets so the workers_indic-only mode can serve hi/bn as
                # well — limiting it to Assamese only would defeat the
                # "sole translator" intent of the flag.
                _allowed = ("as", "as_in", "hi", "hi_in", "bn", "bn_in") if _workers_indic_only else ("as", "as_in")
                if target_lang.lower().replace("-", "_") not in _allowed:
                    raise RuntimeError(
                        f"workers_ai_indic: IndicTrans2 cannot serve target "
                        f"{target_lang!r} — routing to next provider"
                    )
                from providers.workers_indic import call_indic_trans as _indic_trans
                _result = await _indic_trans(text, direction="en-indic")
                if _rpc_metric:
                    _rpc_metric('workers_indic', True)
                return _result
            elif provider == "workers_ai":
                prompt = [
                    {"role": "system", "content": f"Translate from {source_lang} to {target_lang}. Output only the translation."},
                    {"role": "user", "content": text},
                ]
                return await _call_llm_raw(prompt, None, 2048, provider_list=_LLM_PROVIDERS_WORKERS_ONLY, feature_key="translate")
            else:
                raise RuntimeError(f"translate: unknown provider {provider!r}")
        except Exception as exc:
            logger.warning("translate %s failed: %s — removing from pool", provider, exc)
            if _rpc_metric:
                # Canonicalise to the success-path key so the panel doesn't
                # split workers_ai_indic / workers_indic into two buckets.
                _rpc_metric(
                    'workers_indic' if provider == 'workers_ai_indic' else provider,
                    False,
                )
            if _workers_indic_only:
                # In workers_indic-only mode there is no other provider to
                # fall back to — surface the failure immediately so callers
                # do not silently drop translations.
                raise RuntimeError(
                    f"translate: workers_indic-only mode and provider {provider!r} failed: {exc}"
                )
            exclude = exclude | {provider}

    raise RuntimeError("translate: all providers exhausted")


async def call_search_rag_with_dispatch(
    query: str,
    feature: str = "search_rag",
    lang: str = "en",
) -> list:
    """Search via the weighted provider selected for 'search_rag' or 'live_search'.

    Priority (PROVIDER_PRIORITY['search_rag']):  exa_ai(1000) → workers_ai(0)
    Priority (PROVIDER_PRIORITY['live_search']): exa_ai(1000) → tavily(500) → workers_ai(0)

    Returns a list of result dicts {title, url, text}.
    Raises RuntimeError if all providers fail.
    """
    if feature not in ("search_rag", "live_search"):
        feature = "search_rag"

    from config import PROVIDER_PRIORITY as _PP
    exclude: frozenset = frozenset()
    max_attempts = len(_PP.get(feature, [])) + 1

    for _ in range(max_attempts):
        provider = select_provider(feature, lang=lang, exclude=exclude)
        try:
            if provider == "exa_ai":
                import exa_py as _exa
                from config import _EXA_KEY
                if not _EXA_KEY:
                    raise RuntimeError("exa_ai: EXA_API_KEY not configured")
                client = _exa.Exa(api_key=_EXA_KEY)
                results = client.search_and_contents(
                    query,
                    num_results=5,
                    use_autoprompt=True,
                    text=True,
                )
                return [
                    {"title": r.title, "url": r.url, "text": (r.text or "")[:500]}
                    for r in (results.results or [])
                ]
            elif provider == "tavily":
                from config import _TAVILY_KEY
                _tavily_key = _TAVILY_KEY
                if not _tavily_key:
                    raise RuntimeError("tavily: TAVILY_API_KEY not set")
                import httpx as _httpx_tv
                async with _httpx_tv.AsyncClient(timeout=10.0) as _tv_client:
                    _tv_resp = await _tv_client.post(
                        "https://api.tavily.com/search",
                        json={"api_key": _tavily_key, "query": query, "max_results": 5},
                    )
                    _tv_resp.raise_for_status()
                    _tv_data = _tv_resp.json()
                return [
                    {"title": r.get("title", ""), "url": r.get("url", ""), "text": (r.get("content", ""))[:500]}
                    for r in _tv_data.get("results", [])
                ]
            elif provider == "workers_ai":
                raise RuntimeError("live web search not available via workers_ai (no search endpoint)")
            else:
                raise RuntimeError(f"search: unknown provider {provider!r}")
        except Exception as exc:
            logger.warning("search(%s) %s failed: %s — removing from pool", feature, provider, exc)
            exclude = exclude | {provider}

    raise RuntimeError(f"search({feature}): all providers exhausted")


async def call_rerank_with_dispatch(
    query: str,
    docs: list,
    lang: str = "en",
) -> list:
    """Rerank *docs* via the weighted provider selected for 'rerank'.

    Priority (PROVIDER_PRIORITY['rerank']):
      pinecone_ai(500) → azure_openai(1, skip) → workers_ai(0)

    pinecone_ai: providers.pinecone_ai.rerank (bge-reranker-v2-m3, multilingual) — fully wired.
    azure_openai: rerank not wired (Task #257) — excluded gracefully.
    workers_ai: no rerank endpoint — excluded gracefully.
    (cohere rerank branch removed in Task #491.)

    Task #382 — when ``RERANK_PROVIDER=pinecone_only`` (the new default)
    the dispatcher short-circuits to pinecone_ai exclusively. Other
    providers stay in the pool definitions but never get drawn,
    matching the "Pinecone-only rerank" goal of the task.

    Each doc should be a string or a dict with a 'text' key.
    Returns the docs list reordered by relevance (most relevant first),
    or the original list unchanged if all providers fail.
    """
    from config import PROVIDER_PRIORITY as _PP, RERANK_PROVIDER as _RR
    exclude: frozenset = frozenset()

    # Task #382 — Pinecone-only short-circuit. We do NOT consult the
    # weighted draw at all; pinecone_ai is called directly so a
    # provider key drift in POOL_WEIGHTS cannot accidentally re-enable
    # azure/workers_ai rerank attempts.
    if _RR == "pinecone_only":
        try:
            from providers import pinecone_ai as _pc_prov
            doc_texts = [d if isinstance(d, str) else d.get("text", str(d)) for d in docs]
            if not doc_texts:
                return docs
            scores = await _pc_prov.rerank(query, doc_texts)
            ranked = sorted(zip(scores, docs), key=lambda x: x[0], reverse=True)
            return [d for _, d in ranked]
        except Exception as exc:
            logger.warning(
                "rerank pinecone_only short-circuit failed (%s) — "
                "returning docs unranked",
                exc,
            )
            return docs

    max_attempts = len(_PP.get("rerank", [])) + 1

    for _ in range(max_attempts):
        provider = select_provider("rerank", lang=lang, exclude=exclude)
        try:
            if provider == "pinecone_ai":
                from providers import pinecone_ai as _pc_prov
                doc_texts = [d if isinstance(d, str) else d.get("text", str(d)) for d in docs]
                if not doc_texts:
                    return docs
                scores = await _pc_prov.rerank(query, doc_texts)
                # Sort docs by score descending (highest relevance first).
                ranked = sorted(zip(scores, docs), key=lambda x: x[0], reverse=True)
                return [d for _, d in ranked]
            elif provider == "azure_openai":
                # Azure OpenAI rerank not wired (Task #257); excluded gracefully.
                raise RuntimeError("azure_openai rerank not wired (Task #257)")
            elif provider == "workers_ai":
                raise RuntimeError("rerank via workers_ai: no rerank endpoint available")
            else:
                raise RuntimeError(f"rerank: unknown provider {provider!r}")
        except Exception as exc:
            logger.warning("rerank %s failed: %s — removing from pool", provider, exc)
            exclude = exclude | {provider}

    logger.warning("rerank: all providers exhausted — returning docs unranked")
    return docs


async def call_vision_with_dispatch(
    b64_image: str,
    prompt: str,
    lang: str = "en",
    mime_type: str = "image/jpeg",
) -> str:
    """Analyse *b64_image* via the weighted provider selected for 'vision'.

    Priority (PROVIDER_PRIORITY['vision']) — Task #490 dropped Vertex from vision:
      bedrock(1000, Nova Lite multimodal) → azure_openai(1, GPT-4o) → workers_ai(0)

    bedrock: Amazon Nova Lite multimodal via providers.bedrock.call_converse_vision (Task #304).
             Claude 3.5 Sonnet kept as in-pool higher-quality fallback if Nova Lite fails.
    azure_openai: GPT-4o vision via providers.azure_openai.call_chat with image_url content.
    workers_ai: no multimodal endpoint — excluded gracefully.

    Returns the model's text response.
    Raises RuntimeError if all providers fail.
    """
    from config import PROVIDER_PRIORITY as _PP
    exclude: frozenset = frozenset()
    max_attempts = len(_PP.get("vision", [])) + 1

    for _ in range(max_attempts):
        provider = select_provider("vision", lang=lang, exclude=exclude)
        try:
            # Task #490: vertex vision branch removed. Vision now flows
            # through Azure GPT-4o → Workers-AI multimodal (V4 §15).
            # Task #347: bedrock vision branch removed (Nova Lite + Claude
            # Sonnet via providers/bedrock.py deleted alongside the SDK).
            if provider == "azure_openai":
                from providers import azure_openai as _az_prov
                _az_vision_msgs = [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:{mime_type};base64,{b64_image}"},
                            },
                            {"type": "text", "text": prompt},
                        ],
                    }
                ]
                return await _az_prov.call_chat(_az_vision_msgs, max_tokens=1024)
            elif provider == "workers_ai":
                raise RuntimeError("vision via workers_ai: no multimodal endpoint configured")
            else:
                raise RuntimeError(f"vision: unknown provider {provider!r}")
        except Exception as exc:
            logger.warning("vision %s failed: %s — removing from pool", provider, exc)
            exclude = exclude | {provider}

    raise RuntimeError("vision: all providers exhausted")
