"""Syrabit.ai — LLM infrastructure: batching, smart key pool, streaming."""
import os, re, json, asyncio, uuid, time, logging, httpx, hashlib
import openai as _oai

_INDIC_LANG_CODES = frozenset({"as", "hi", "bn", "hi-in", "bn-in", "as-in"})

def _is_indic_lang(lang: str | None) -> bool:
    return bool(lang and lang.lower().strip() in _INDIC_LANG_CODES)

_SARVAM_INDIC_MODEL_PREFERENCE = ["sarvam-m", "sarvam-105b"]


class LlmResult(str):
    """String subclass that carries the provider that produced the result.

    `provider` is the canonical name (e.g. "gemini", "cerebras", "workers-ai").
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
    "gemini-2.0-flash": 65536,  # alias → gemini-2.5-flash at call time
}

# Deprecated / renamed models — resolved before the provider call so we
# never send a stale model name to the upstream API.
_MODEL_ALIASES: dict[str, str] = {
    # Task #247: gemini-2.0-flash is kept as a valid model ID in the SLM pool
    # (position-2 fallback), so we do NOT alias it to gemini-2.5-flash here.
    # The existing slot entry uses "gemini-2.0-flash" directly with the Gemini
    # provider which resolves it through the _stream_gemini path.
}

def _clamp_max_tokens(model: str, max_tokens: int) -> int:
    cap = _MODEL_MAX_OUTPUT_TOKENS.get(model)
    return min(max_tokens, cap) if cap else max_tokens
from typing import Dict, Optional
from fastapi import HTTPException
from emergentintegrations.llm.chat import LlmChat, UserMessage
from config import (
    LLM_PROVIDER, LLM_MODEL, OPENAI_API_KEY, SARVAM_THINK_BUFFER,
    _GROQ_KEY, _GEMINI_KEY, _GEMINI_KEY_2, _OPENAI_KEY,
    _SARVAM_LLM_KEY, _SARVAM_LLM_KEY_2, _SARVAM_LLM_KEY_3, _CEREBRAS_KEY, _OPENROUTER_KEY, _AWS_ACCESS_KEY, _AWS_SECRET_KEY, _AWS_REGION,
    is_cf_gateway_up, mark_cf_gateway_down, get_provider_base_url,
    byok_headers, BYOK_PLACEHOLDER,
    VERTEX_GEMINI_MODEL,
    ENABLE_PARALLEL_LLM_RACE, PARALLEL_RACE_TIMEOUT, MIN_PROVIDERS_TO_RACE, MAX_CONCURRENT_RACE_PROVIDERS,
)
import vertex_chat as _vertex_chat
from deps import sarvam_llm_client, sarvam_llm_client_direct
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
# Providers included: workers-ai, groq, gemini.  Others silently no-op.
_PROVIDER_429_BURST_WINDOW_S = 180   # shared lookback / Redis TTL for all providers
_PROVIDER_429_WINDOWS: dict = {       # provider → list[float epoch timestamps]
    "workers-ai":   [],
    "groq":         [],
    "gemini":       [],
    "azure_openai": [],   # Task #267: azure is now primary for english_rag_chat + content
    "bedrock":      [],   # Task #267: bedrock is now second-tier for english pools
}
_PROVIDER_429_REDIS_KEYS: dict = {
    "workers-ai":   "wai_429_burst",    # keeps existing Redis key for backwards compat
    "groq":         "groq_429_burst",
    "gemini":       "gemini_429_burst",
    "azure_openai": "azure_429_burst",  # Task #267: burst tracking for Azure primary
    "bedrock":      "bedrock_429_burst",# Task #267: burst tracking for Bedrock second-tier
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


def _record_llm_call(provider: str, model: str, duration_ms: float, success: bool, tokens_approx: int = 0, fallback: bool = False, error_type: str = ""):
    _LLM_PROVIDER_METRICS.append({
        "ts": time.time(),
        "provider": provider,
        "model": model,
        "duration_ms": round(duration_ms, 1),
        "success": success,
        "tokens_approx": tokens_approx,
        "fallback": fallback,
        "error_type": error_type,
    })
    if len(_LLM_PROVIDER_METRICS) > _LLM_PROVIDER_METRICS_MAX:
        del _LLM_PROVIDER_METRICS[:1000]

def get_llm_provider_stats(window_seconds: int = 3600) -> dict:
    cutoff = time.time() - window_seconds
    recent = [m for m in _LLM_PROVIDER_METRICS if m["ts"] >= cutoff]
    by_provider: dict = {}
    for m in recent:
        p = m["provider"]
        if p not in by_provider:
            by_provider[p] = {"calls": 0, "successes": 0, "failures": 0, "total_ms": 0, "tokens": 0, "models": set()}
        by_provider[p]["calls"] += 1
        by_provider[p]["tokens"] += m["tokens_approx"]
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
    fallback_calls = sum(1 for m in recent if m["fallback"])
    return {
        "providers": result,
        "total_calls": total_calls,
        "overall_success_rate": round(total_success / max(total_calls, 1) * 100, 1),
        "fallback_rate": round(fallback_calls / max(total_calls, 1) * 100, 1),
        "window_seconds": window_seconds,
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
if _GROQ_KEY:
    _LLM_PROVIDERS.append({"provider": "groq", "key": _GROQ_KEY, "default_model": "meta-llama/llama-4-scout-17b-16e-instruct"})
if _GEMINI_KEY:
    _LLM_PROVIDERS.append({"provider": "gemini", "key": _GEMINI_KEY, "default_model": "gemini-2.5-flash"})
if _CEREBRAS_KEY:
    _LLM_PROVIDERS.append({"provider": "cerebras", "key": _CEREBRAS_KEY, "default_model": "llama3.1-8b"})

_LLM_PROVIDERS_CHAT: list[dict] = []
if _CF_AI_ENABLED:
    _LLM_PROVIDERS_CHAT.append({"provider": "workers-ai", "key": _CF_API_TOKEN, "default_model": "@cf/meta/llama-3.3-70b-instruct-fp8-fast"})
if _GROQ_KEY:
    _LLM_PROVIDERS_CHAT.append({"provider": "groq", "key": _GROQ_KEY, "default_model": "meta-llama/llama-4-scout-17b-16e-instruct"})
if _GEMINI_KEY:
    _LLM_PROVIDERS_CHAT.append({"provider": "gemini", "key": _GEMINI_KEY, "default_model": "gemini-2.5-flash"})
if _CEREBRAS_KEY:
    _LLM_PROVIDERS_CHAT.append({"provider": "cerebras", "key": _CEREBRAS_KEY, "default_model": "llama3.1-8b"})

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
    "llama3.1-8b": "cerebras",
    "qwen-3-235b-a22b-instruct-2507": "cerebras",
    "gemini-2.5-flash": "gemini",
    "gemini-2.0-flash": "gemini",
    "deepseek/deepseek-chat-v3-0324": "openrouter",
    "deepseek/deepseek-r1": "openrouter",
    "qwen/qwen3-235b-a22b": "openrouter",
    "google/gemini-2.5-flash-preview": "openrouter",
    "google/gemini-2.0-flash-lite-001": "openrouter",
    "google/gemma-3-27b-it": "openrouter",
    "meta-llama/llama-4-maverick": "openrouter",
    "meta-llama/llama-4-scout": "openrouter",
    "meta-llama/llama-4-scout-17b-16e-instruct": "groq",
    # Legacy entries — kept for cost-lookup back-compat on historical
    # records, but no live provider call site references these any
    # more (Cerebras dropped llama-3.3-70b from our account; the SLM
    # tier-0 slot is now llama3.1-8b which is mapped above).
    "llama-3.3-70b-versatile": "cerebras",
    "llama-3.3-70b": "cerebras",
}

_MODEL_ALIAS_MAP = {
    "openai/gpt-oss-20b": "deepseek/deepseek-chat-v3-0324",
    "openai/gpt-oss-120b": "qwen-3-235b-a22b-instruct-2507",
    # Cerebras dropped llama-3.3-70b — redirect to Workers AI FP8 equivalent.
    # Handles both the Groq-style "-versatile" suffix and the bare form so
    # any caller or env var using these names automatically gets the correct
    # Workers AI model without a 404 round-trip.
    "llama-3.3-70b-versatile": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
    "llama-3.3-70b": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
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
    # Tier 5: Gemini 2.0 Flash — position-2 GCP fallback (Task #247).
    # Activates only when Workers AI load > 0.80 (effective_priority boost).
    # Consumes GCP credits; intentionally NOT a primary slot.
    # RPM cap: 600 (AI Studio Tier 1) → shared with _GEMINI_KEY.
    ("gemini",      "gemini-2.0-flash",                                  4, 5),
    # Tier 6: Groq llama-4-scout — external fallback when Workers AI is saturated.
    ("groq",        "meta-llama/llama-4-scout-17b-16e-instruct",         4, 6),
    # Tier 7: Cerebras llama3.1-8b — secondary external fallback.
    ("cerebras",    "llama3.1-8b",                                        4, 7),
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
_POOL_RPM_LIMITS = {
    "workers-ai": _parse_rpm_limit("WORKERS_AI_RPM_LIMIT", 10000),
    "groq":        _parse_rpm_limit("GROQ_RPM_LIMIT",          30),
    "cerebras":    _parse_rpm_limit("CEREBRAS_RPM_LIMIT",      30),
    "sarvam":      _parse_rpm_limit("SARVAM_RPM_LIMIT",        30),
    "gemini":      _parse_rpm_limit("GEMINI_RPM_LIMIT",       600),
    "openrouter":  60,
    "openai":      60,
    "bedrock":     30,
}
logger.info("SLM RPM limits (overridable via env): %s", _POOL_RPM_LIMITS)


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
    # as primary until 85% (8 500 RPM) before soft-shifting to Groq/Cerebras,
    # and hard-deprioritize only at 95% (9 500 RPM).  Per-model quota is
    # independent so a single model saturating does not affect others.
    _RPM_SOFT_THRESHOLD = 0.85
    _RPM_HARD_THRESHOLD = 0.95

    # RPM limits per provider — see _parse_rpm_limit() for the env-var safe parser.
    # Workers AI: CF Standard plan with unified billing — 3 000 RPM per model.
    # Override with WORKERS_AI_RPM_LIMIT env var if the account tier differs.
    # Groq / Cerebras: 30 RPM on the free / preview tier; set GROQ_RPM_LIMIT
    # or CEREBRAS_RPM_LIMIT to a higher value on a paid plan.
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
            if key or real_provider in ("sarvam", "bedrock"):
                if real_provider == "bedrock" and not (_AWS_ACCESS_KEY and _AWS_SECRET_KEY):
                    logger.info("SLM pool: skipping bedrock slot (AWS credentials not set)")
                    continue
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
        # Task #247: Gemini 2.0 Flash is position-2 external fallback.
        # Penalize it until Workers AI aggregate load exceeds 80%, ensuring it
        # is not used before we actually need the GCP credit budget.
        if slot["provider"] == "gemini":
            wai_load = self._workers_ai_aggregate_load()
            if wai_load < self._GEMINI_WAI_LOAD_THRESHOLD:
                return base + self._GEMINI_WAI_PENALTY
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
    if provider == "groq" and not model.startswith(("llama-", "meta-llama/")):
        return "meta-llama/llama-4-scout-17b-16e-instruct"
    mapped_provider = _MODEL_PROVIDER_MAP.get(model)
    if mapped_provider == provider:
        return model
    plist = _LLM_PROVIDERS if provider_list is None else provider_list
    matched = next((p for p in plist if p["provider"] == provider), None)
    if matched:
        return matched["default_model"]
    return model

def _pick_sarvam_client():
    if sarvam_llm_client_direct is not None and not is_cf_gateway_up():
        return sarvam_llm_client_direct
    return sarvam_llm_client

async def _call_sarvam_llm(messages: list, api_key: str, model: str, max_tokens: int) -> str:
    """Non-streaming call to Sarvam LLM — reuses persistent sarvam_llm_client (zero TCP overhead).
    Adds SARVAM_THINK_BUFFER so the <think> block never consumes the user's answer budget.
    Falls back to direct client if CF gateway returns connection error or 401."""
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
    try:
        resp = await client.post("/v1/chat/completions", json=payload)
        resp.raise_for_status()
    except (httpx.ConnectError, httpx.ConnectTimeout) as e:
        if sarvam_llm_client_direct is not None and client is not sarvam_llm_client_direct:
            _handle_cf_connection_error(e)
            resp = await sarvam_llm_client_direct.post("/v1/chat/completions", json=payload)
            resp.raise_for_status()
        else:
            raise
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401 and sarvam_llm_client_direct is not None and client is not sarvam_llm_client_direct:
            _handle_cf_gateway_auth_error(e)
            resp = await sarvam_llm_client_direct.post("/v1/chat/completions", json=payload)
            resp.raise_for_status()
        else:
            raise
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

async def _call_gemini(messages: list, api_key: str, model: str, max_tokens: int) -> str:
    """Non-streaming call to Google Gemini via its OpenAI-compatible endpoint."""
    direct_base = "https://generativelanguage.googleapis.com/v1beta/openai/"
    base = get_provider_base_url("gemini") or direct_base
    client = _get_oai_client(api_key, base)
    try:
        resp = await client.chat.completions.create(
            model=model, messages=messages, max_tokens=max_tokens, temperature=0.1,
            # Pass api_key so the BYOK-aware helper can decide whether to
            # clear the upstream Authorization (placeholder → clear so CF
            # substitutes; real key → keep so SDK bearer reaches upstream).
            extra_headers=_cf_cache_headers(api_key=api_key) or None,
        )
    except _oai.APIConnectionError as e:
        if base != direct_base and _is_cf_connection_error(e):
            _handle_cf_connection_error(e)
            client = _get_oai_client(api_key, direct_base)
            resp = await client.chat.completions.create(
                model=model, messages=messages, max_tokens=max_tokens, temperature=0.1,
            )
        else:
            raise
    except _oai.AuthenticationError as e:
        if base != direct_base:
            _handle_cf_gateway_auth_error(e)
            client = _get_oai_client(api_key, direct_base)
            resp = await client.chat.completions.create(
                model=model, messages=messages, max_tokens=max_tokens, temperature=0.1,
            )
        else:
            raise
    content = resp.choices[0].message.content or ""
    return re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()

async def _call_openai_compat(messages: list, api_key: str, model: str, max_tokens: int, provider: str, fallback_base: str) -> str:
    """Non-streaming call via an OpenAI-compatible provider (OpenAI, xAI, Fireworks)."""
    base = get_provider_base_url(provider) or fallback_base
    client = _get_oai_client(api_key, base)
    try:
        resp = await client.chat.completions.create(
            model=model, messages=messages, max_tokens=max_tokens, temperature=0.1,
            # See _call_gemini for the rationale — pass api_key so BYOK
            # placeholders correctly trigger the clear-Authorization branch.
            extra_headers=_cf_cache_headers(api_key=api_key) or None,
        )
    except _oai.APIConnectionError as e:
        if base != fallback_base and _is_cf_connection_error(e):
            _handle_cf_connection_error(e)
            client = _get_oai_client(api_key, fallback_base)
            resp = await client.chat.completions.create(
                model=model, messages=messages, max_tokens=max_tokens, temperature=0.1,
            )
        else:
            raise
    except _oai.AuthenticationError as e:
        if base != fallback_base:
            _handle_cf_gateway_auth_error(e)
            client = _get_oai_client(api_key, fallback_base)
            resp = await client.chat.completions.create(
                model=model, messages=messages, max_tokens=max_tokens, temperature=0.1,
            )
        else:
            raise
    content = resp.choices[0].message.content or ""
    return re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()

async def _call_cerebras(messages: list, api_key: str, model: str, max_tokens: int) -> str:
    direct_base = "https://api.cerebras.ai/v1"
    base = get_provider_base_url("cerebras") or direct_base
    client = _get_oai_client(api_key, base)
    try:
        resp = await client.chat.completions.create(
            model=model, messages=messages, max_tokens=max_tokens, temperature=0.1,
            extra_headers=_cf_cache_headers(api_key=api_key) or None,
        )
    except _oai.APIConnectionError as e:
        if base != direct_base and _is_cf_connection_error(e):
            _handle_cf_connection_error(e)
            client = _get_oai_client(api_key, direct_base)
            resp = await client.chat.completions.create(
                model=model, messages=messages, max_tokens=max_tokens, temperature=0.1,
            )
        else:
            raise
    except _oai.AuthenticationError as e:
        if base != direct_base:
            _handle_cf_gateway_auth_error(e)
            client = _get_oai_client(api_key, direct_base)
            resp = await client.chat.completions.create(
                model=model, messages=messages, max_tokens=max_tokens, temperature=0.1,
            )
        else:
            raise
    content = resp.choices[0].message.content or ""
    return re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()

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
    if provider == "gemini":
        return await _call_gemini(messages, api_key, model, max_tokens)
    if provider == "cerebras":
        return await _call_cerebras(messages, api_key, model, max_tokens)
    if provider == "groq":
        return await _call_openai_compat(messages, api_key, model, max_tokens, "groq", "https://api.groq.com/openai/v1")
    if provider == "xai":
        return await _call_openai_compat(messages, api_key, model, max_tokens, "xai", "https://api.x.ai/v1")
    if provider == "openrouter":
        return await _call_openai_compat(messages, api_key, model, max_tokens, "openrouter", "https://openrouter.ai/api/v1")

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

async def _call_llm_raw(messages: list, model: str = None, max_tokens: int = 1024, provider_list=None) -> str:
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
            _record_llm_call(provider_cfg["provider"], try_model, _dur, True, tok, is_fallback)
            log_prefix = "llm_call provider=" + provider_cfg["provider"] + f" model={try_model} duration_ms={_dur} tokens_approx={tok}"
            if is_fallback:
                logger.info(log_prefix + " fallback=true")
            else:
                logger.info(log_prefix)
            return (True, LlmResult(result, provider=provider_cfg["provider"]), None)
        except asyncio.TimeoutError:
            _dur = int((_t.perf_counter() - _t0) * 1000)
            _record_llm_call(provider_cfg["provider"], try_model, _dur, False, 0, is_fallback, "Timeout")
            err = TimeoutError(f"{provider_cfg['provider']}/{try_model} timed out after {_PROVIDER_TIMEOUT}s")
            logger.warning(f"LLM {'fallback' if is_fallback else 'primary'} TIMEOUT ({provider_cfg['provider']}/{try_model}): {_dur}ms > {_PROVIDER_TIMEOUT}s limit")
            return (False, None, err)
        except Exception as e:
            _dur = int((_t.perf_counter() - _t0) * 1000)
            _record_llm_call(provider_cfg["provider"], try_model, _dur, False, 0, is_fallback, type(e).__name__)
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
                                 len(value.split()), True)
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
    "reasoning":     ("cerebras",   "qwen-3-235b-a22b-instruct-2507"),
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
# model identifiers.  For non-LLM providers (cartesia, assemblyai, cohere,
# pinecone_ai, exa_ai, tavily) the model string is a descriptive tag only —
# the actual API call goes through the provider's own client module.
_PROVIDER_DEFAULT_MODELS: dict[str, str] = {
    "vertex":           "gemini-2.5-flash",                          # Vertex AI Gemini 2.5 Flash — highest TPS
    "bedrock":          "amazon.nova-micro-v1:0",                    # AWS Bedrock Nova Micro — fastest/cheapest Nova (Task #267)
    "azure_openai":     "gpt-4.1-mini",                              # Azure OpenAI GPT-4.1-mini — highest TPS on Azure (Task #267)
    "sarvam":           "sarvam-m",                                  # Sarvam LLM (Indic)
    "cartesia":         "sonic-2",                                   # Cartesia TTS
    "elevenlabs":       "eleven_multilingual_v2",                    # ElevenLabs TTS
    "assemblyai":       "best",                                      # AssemblyAI STT
    "cohere":           "embed-multilingual-v3.0",                   # Cohere Embed
    "pinecone_ai":      "llama-text-embed-v2",                       # Pinecone embed/rerank
    "exa_ai":           "exa",                                       # Exa neural search
    "tavily":           "tavily-search",                             # Tavily search
    "mongodb_atlas":    "vector-search",                             # Atlas $vectorSearch (fallback)
    "workers_ai":       "@cf/meta/llama-3.3-70b-instruct-fp8-fast",  # CF Workers AI general LLM (last resort)
    "workers_ai_indic": "@cf/ai4bharat/indictrans2-en-indic-1b",     # CF Workers AI IndicTrans2 English→Assamese (Task #267)
}

# Maps provider names to the canonical provider string used by _call_single_provider.
# This bridges Task #250's semantic provider names to llm.py's internal strings.
_PROVIDER_CANONICAL: dict[str, str] = {
    "vertex":           "gemini",           # Vertex Gemini = gemini provider
    "bedrock":          "bedrock",
    "azure_openai":     "openai",           # Azure OpenAI is OpenAI-compatible
    "sarvam":           "sarvam",
    "cartesia":         "cartesia",
    "elevenlabs":       "elevenlabs",
    "assemblyai":       "assemblyai",
    "cohere":           "cohere",
    "pinecone_ai":      "pinecone_ai",
    "exa_ai":           "exa_ai",
    "tavily":           "tavily",
    "mongodb_atlas":    "mongodb_atlas",
    "workers_ai":       "workers-ai",
    "workers_ai_indic": "workers-ai-indic",  # CF IndicTrans2 — Assamese last resort (Task #267)
}

# CF AI Gateway saturation threshold — providers above this RPM ratio are
# temporarily deprioritized in the weighted draw (same threshold as SLM pool).
_SELECT_SATURATION_THRESHOLD = 0.80


def _get_provider_saturation(provider_name: str) -> float:
    """Return current RPM saturation ratio (0.0–1.0) for a provider.

    Reads from the SmartKeyPool's rpm_window for workers-ai, and from
    the in-process 429 burst counter for other providers (gemini, groq).
    Returns 0.0 (not saturated) for providers not tracked by the pool.
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
    """Weighted round-robin provider selection for *feature*.

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
    Returns ``"workers_ai"`` when all else fails.
    """
    import random as _random
    from config import PROVIDER_PRIORITY, PROVIDER_CREDITS

    candidates = PROVIDER_PRIORITY.get(feature, ["workers_ai"])
    _is_assamese_feature = feature in ("assamese_rag_chat", "assamese_content")

    pool: list[str] = []
    weights: list[int] = []

    for p in candidates:
        credit = PROVIDER_CREDITS.get(p, 0)
        if credit == 0:
            continue                                   # weight-0 → fallback only
        if p in exclude:
            continue                                   # already failed
        if _is_assamese_feature and p == "sarvam" and lang.lower().strip() != "as":
            continue                                   # Sarvam reserved for Assamese only
        saturation = _get_provider_saturation(p)
        if saturation >= _SELECT_SATURATION_THRESHOLD:
            logger.info(
                "select_provider: skipping %s for feature=%s — saturated (%.0f%%)",
                p, feature, saturation * 100,
            )
            continue
        pool.append(p)
        weights.append(credit)

    if pool:
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

    logger.warning("select_provider: feature=%s — no provider available, defaulting to workers_ai", feature)
    return "workers_ai"


def route_for_feature(feature: str, lang: str = "") -> tuple[str, str]:
    """Return (canonical_provider, model) for a Task #250 feature key via select_provider.

    Bridges the semantic feature-key system into the existing (provider, model)
    tuple used by _call_single_provider and route_for_task callers.

    Example::

        provider, model = route_for_feature("english_rag_chat")
        # → ("gemini", "gemini-2.5-flash") when vertex is selected, or
        #   ("workers-ai", "@cf/meta/llama-3.3-70b-instruct-fp8-fast") as fallback.
    """
    provider_name = select_provider(feature, lang=lang)
    canonical = _PROVIDER_CANONICAL.get(provider_name, provider_name)
    model = _PROVIDER_DEFAULT_MODELS.get(provider_name, "@cf/meta/llama-3.3-70b-instruct-fp8-fast")
    return (canonical, model)


async def _dispatch_llm_for_feature(messages: list, provider: str, max_tokens: int) -> str:
    """Dispatch a chat LLM call to the named Task #250 provider.

    Used as the ``attempt_fn`` argument to ``call_with_provider_fallback`` so
    that chat/content/RAG entrypoints route through the weighted pool.

    Raises ``RuntimeError`` for Phase-2 providers (bedrock, azure_openai) that
    are not yet wired as full client modules (see Task #256).

    Falls back to the Workers AI SmartKeyPool batcher for ``workers_ai`` and
    any unrecognised provider name.
    """
    if provider == "vertex":
        if not _GEMINI_KEY:
            raise RuntimeError("vertex: GEMINI_API_KEY not available")
        return await _call_gemini(messages, _GEMINI_KEY, "gemini-2.5-flash", max_tokens)

    if provider == "sarvam":
        sarvam_slot = _SARVAM_PROVIDERS[0] if _SARVAM_PROVIDERS else None
        if not sarvam_slot:
            raise RuntimeError("sarvam: no Sarvam LLM key available")
        return await _call_sarvam_llm(messages, sarvam_slot["key"], "sarvam-m", max_tokens)

    if provider == "bedrock":
        # AWS Bedrock Converse API via CF AI Gateway BYOK (aws-bedrock slug).
        # CF handles SigV4 signing — no AWS SDK required in the backend.
        from providers.bedrock import call_converse as _bedrock_converse
        return await _bedrock_converse(messages, max_tokens=max_tokens)

    if provider == "azure_openai":
        # Azure OpenAI chat/completions via CF AI Gateway BYOK (azure-openai slug).
        # CF injects the Azure API key stored in the dashboard.
        # Model: gpt-4.1-mini — highest TPS on Azure as of 2025 (Task #267).
        from providers.azure_openai import call_chat as _az_chat
        return await _az_chat(messages, model="gpt-4.1-mini", max_tokens=max_tokens)

    if provider == "workers_ai_indic":
        # CF Workers AI IndicTrans2 — Assamese last resort (Task #267).
        # Extracts the last user message text and translates to Assamese.
        # Direction: English → Assamese (en-indic-1b).  For chat responses the
        # caller has already produced English output; IndicTrans2 translates it.
        from providers.workers_indic import call_indic_trans as _indic_trans
        # Extract the last user message to pass as source text.
        src_text = ""
        for m in reversed(messages):
            if m.get("role") in ("user", "assistant") and m.get("content"):
                src_text = str(m["content"])
                break
        if not src_text:
            raise RuntimeError("workers_ai_indic: no source text in messages")
        return await _indic_trans(src_text, direction="en-indic")

    # workers_ai or any unknown provider → Workers-AI-only dispatch.
    # Use _LLM_PROVIDERS_WORKERS_ONLY so deprecated providers (Groq, Cerebras,
    # Gemini) cannot re-enter routing via this fallback path — they are absent
    # from PROVIDER_PRIORITY and must stay out of the weighted dispatch chain.
    if not _LLM_PROVIDERS_WORKERS_ONLY:
        raise RuntimeError("workers_ai: Cloudflare AI (CF_AI_ENABLED) is not configured")
    return await _call_llm_raw(messages, max_tokens=max_tokens, provider_list=_LLM_PROVIDERS_WORKERS_ONLY)


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

    for attempt in range(max_attempts):
        provider = select_provider(feature, lang=lang, exclude=exclude)
        try:
            return await attempt_fn(provider)
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
# Gemini 2.5 Flash is the best available model for RAG synthesis:
#   • native long-context window (1M tokens)
#   • strong factual grounding across retrieved chunks
#   • multilingual (handles Assamese syllabus text natively)
#
# Falls back to Workers AI 70B if Gemini is unavailable or hits quota.
_RAG_PROVIDERS: list[dict] = []
if _CF_AI_ENABLED:
    _RAG_PROVIDERS.append({"provider": "workers-ai", "key": _CF_API_TOKEN, "default_model": "@cf/openai/gpt-oss-120b"})
    _RAG_PROVIDERS.append({"provider": "workers-ai", "key": _CF_API_TOKEN, "default_model": "@cf/meta/llama-3.3-70b-instruct-fp8-fast"})


async def call_llm_for_rag(messages: list, max_tokens: int = 2048) -> str:
    """LLM call optimised for RAG answer synthesis.

    Dispatches through PROVIDER_PRIORITY["english_rag_chat"] weighted round-robin
    via call_with_provider_fallback → _dispatch_llm_for_feature.

    Weighted provider priority (Task #267):
      Azure OpenAI (gpt-4.1-mini, weight 2500) → Bedrock (nova-micro, weight 1000)
      → Workers AI (llama-3.3-70b, weight 0, last-resort).

    Final hard fallback: Workers AI only — ensures no non-PROVIDER_PRIORITY providers
    (Groq, Cerebras, Gemini direct) can be introduced after the weighted pool exhausts.
    """
    try:
        return await call_with_provider_fallback(
            "english_rag_chat", "en",
            lambda p: _dispatch_llm_for_feature(messages, p, max_tokens),
        )
    except Exception as exc:
        logger.warning(
            "call_llm_for_rag feature dispatch exhausted (%s) — hard fallback to workers_ai only",
            exc,
        )
        return await _call_llm_raw(messages, max_tokens=max_tokens, provider_list=_LLM_PROVIDERS_WORKERS_ONLY)


async def call_llm_api(messages: list, model: str = None, max_tokens: int = 2048) -> str:
    """Smart-batched LLM call: deduplicates identical requests, limits concurrency.
    Uses all providers including Emergent (admin content generation)."""
    return await _llm_batcher.call(messages, model, max_tokens)

# Admin content batcher chain — Cerebras qwen-235B (high-quality, fast)
# preferred, Gemini 2.5 Flash as fallback. Sarvam was previously inserted
# between them but has been removed: this batcher serves admin notes,
# important_questions and PYQ for ALL languages, and Sarvam quota is now
# reserved for Assamese-only paths (see `_SARVAM_PROVIDERS` rationale at
# top of this module).
_LLM_PROVIDERS_CONTENT: list[dict] = []
if _CF_AI_ENABLED:
    _LLM_PROVIDERS_CONTENT.append({"provider": "workers-ai", "key": _CF_API_TOKEN, "default_model": "@cf/openai/gpt-oss-120b"})
    _LLM_PROVIDERS_CONTENT.append({"provider": "workers-ai", "key": _CF_API_TOKEN, "default_model": "@cf/qwen/qwen2.5-72b-instruct"})
    _LLM_PROVIDERS_CONTENT.append({"provider": "workers-ai", "key": _CF_API_TOKEN, "default_model": "@cf/meta/llama-3.3-70b-instruct-fp8-fast"})
if _GEMINI_KEY:
    _LLM_PROVIDERS_CONTENT.append({"provider": "gemini", "key": _GEMINI_KEY, "default_model": "gemini-2.5-flash"})

logger.info(
    f"Admin content providers (quality-first order): "
    f"{[p['provider'] + '/' + p['default_model'] for p in _LLM_PROVIDERS_CONTENT]}"
)

async def call_llm_api_content(messages: list, model: str = None, max_tokens: int = 3072) -> str:
    """LLM call for admin content generation via PROVIDER_PRIORITY weighted dispatch.

    Feature key: "content" — Task #267 chain:
      Azure OpenAI (gpt-4.1-mini, weight 2500) → Bedrock (nova-micro, weight 1000)
      → Workers AI (llama-3.3-70b, weight 0, last-resort).

    Final hard fallback: Workers AI only — ensures no non-PROVIDER_PRIORITY providers
    (Gemini direct, Cerebras) can be introduced after the weighted pool exhausts.
    """
    try:
        return await call_with_provider_fallback(
            "content", "en",
            lambda p: _dispatch_llm_for_feature(messages, p, max_tokens),
        )
    except Exception as exc:
        logger.warning(
            "call_llm_api_content feature dispatch exhausted (%s) — hard fallback to workers_ai only",
            exc,
        )
        return await _call_llm_raw(messages, max_tokens=max_tokens, provider_list=_LLM_PROVIDERS_WORKERS_ONLY)


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

async def call_llm_api_chat(
    messages: list,
    model: str = None,
    max_tokens: int = 2048,
    lang: str = "en",
) -> str:
    """LLM call for student chat via PROVIDER_PRIORITY weighted dispatch.

    Feature key: "english_rag_chat" (default) or "assamese_rag_chat" when lang="as".

    English chain (Task #267):
      Azure OpenAI (gpt-4.1-mini, weight 2500) → Bedrock (nova-micro, weight 1000)
      → Workers AI (llama-3.3-70b, weight 0, last-resort).

    Assamese chain (Task #267):
      Sarvam (sarvam-m, weight 500) → Vertex/Gemini (gemini-2.5-flash, weight 2000)
      → Workers AI IndicTrans2 (weight 0, last-resort).

    Final hard fallback: Workers AI only — ensures no non-PROVIDER_PRIORITY providers
    (Groq, Cerebras) can be introduced after the weighted pool exhausts.
    """
    feature = "assamese_rag_chat" if lang == "as" else "english_rag_chat"
    try:
        return await call_with_provider_fallback(
            feature, lang,
            lambda p: _dispatch_llm_for_feature(messages, p, max_tokens),
        )
    except Exception as exc:
        logger.warning(
            "call_llm_api_chat feature dispatch exhausted (%s) — hard fallback to workers_ai only",
            exc,
        )
        return await _call_llm_raw(messages, max_tokens=max_tokens, provider_list=_LLM_PROVIDERS_WORKERS_ONLY)


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

    try:
        async for token in _do_stream(client):
            yield token
    except (httpx.ConnectError, httpx.ConnectTimeout) as e:
        if sarvam_llm_client_direct is not None and client is not sarvam_llm_client_direct:
            _handle_cf_connection_error(e)
            async for token in _do_stream(sarvam_llm_client_direct):
                yield token
        else:
            raise
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401 and sarvam_llm_client_direct is not None and client is not sarvam_llm_client_direct:
            _handle_cf_gateway_auth_error(e)
            async for token in _do_stream(sarvam_llm_client_direct):
                yield token
        else:
            raise

async def _stream_gemini(messages: list, api_key: str, model: str, max_tokens: int):
    """Token-by-token streaming from Google Gemini via its OpenAI-compatible endpoint."""
    direct_base = "https://generativelanguage.googleapis.com/v1beta/openai/"
    base = get_provider_base_url("gemini") or direct_base
    client = _get_oai_client(api_key, base)
    try:
        stream = await client.chat.completions.create(
            model=model, messages=messages, max_tokens=max_tokens, stream=True, temperature=0.1,
        )
    except _oai.APIConnectionError as e:
        if base != direct_base and _is_cf_connection_error(e):
            _handle_cf_connection_error(e)
            client = _get_oai_client(api_key, direct_base)
            stream = await client.chat.completions.create(
                model=model, messages=messages, max_tokens=max_tokens, stream=True, temperature=0.1,
            )
        else:
            raise
    except _oai.AuthenticationError as e:
        if base != direct_base:
            _handle_cf_gateway_auth_error(e)
            client = _get_oai_client(api_key, direct_base)
            stream = await client.chat.completions.create(
                model=model, messages=messages, max_tokens=max_tokens, stream=True, temperature=0.1,
            )
        else:
            raise
    async for chunk in stream:
        delta = chunk.choices[0].delta if chunk.choices else None
        if delta and delta.content:
            yield delta.content

async def _stream_vertex_gemini(messages: list, model: str, max_tokens: int):
    """Token-by-token streaming from Vertex AI Gemini Flash (Task #607).

    Uses google-auth + Vertex `streamGenerateContent` REST endpoint.
    Raises on misconfiguration / network errors so the caller can fall
    back to the legacy hedged SLM pool.
    """
    async for token in _vertex_chat.stream_chat(
        messages, model=model, max_tokens=max_tokens, temperature=0.1,
    ):
        yield token


async def _stream_cerebras(messages: list, api_key: str, model: str, max_tokens: int):
    base = get_provider_base_url("cerebras") or "https://api.cerebras.ai/v1"
    client = _get_oai_client(api_key, base)
    stream = await client.chat.completions.create(
        model=model, messages=messages, max_tokens=max_tokens, stream=True, temperature=0.1,
    )
    async for chunk in stream:
        delta = chunk.choices[0].delta if chunk.choices else None
        if delta and delta.content:
            yield delta.content

async def _stream_xai(messages: list, api_key: str, model: str, max_tokens: int):
    """Token-by-token streaming from xAI Grok via its OpenAI-compatible endpoint."""
    direct_base = "https://api.x.ai/v1"
    base = get_provider_base_url("xai") or direct_base
    client = _get_oai_client(api_key, base)
    try:
        stream = await client.chat.completions.create(
            model=model, messages=messages, max_tokens=max_tokens, stream=True, temperature=0.1,
        )
    except _oai.APIConnectionError as e:
        if base != direct_base and _is_cf_connection_error(e):
            _handle_cf_connection_error(e)
            client = _get_oai_client(api_key, direct_base)
            stream = await client.chat.completions.create(
                model=model, messages=messages, max_tokens=max_tokens, stream=True, temperature=0.1,
            )
        else:
            raise
    except _oai.AuthenticationError as e:
        if base != direct_base:
            _handle_cf_gateway_auth_error(e)
            client = _get_oai_client(api_key, direct_base)
            stream = await client.chat.completions.create(
                model=model, messages=messages, max_tokens=max_tokens, stream=True, temperature=0.1,
            )
        else:
            raise
    async for chunk in stream:
        delta = chunk.choices[0].delta if chunk.choices else None
        if delta and delta.content:
            yield delta.content

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
            stream = await client.chat.completions.create(
                model=model, messages=messages, max_tokens=max_tokens, stream=True, temperature=0.1,
            )
        else:
            raise
    except _oai.AuthenticationError as e:
        if base != fallback_base:
            _handle_cf_gateway_auth_error(e)
            client = _get_oai_client(api_key, fallback_base)
            stream = await client.chat.completions.create(
                model=model, messages=messages, max_tokens=max_tokens, stream=True, temperature=0.1,
            )
        else:
            raise
    async for chunk in stream:
        delta = chunk.choices[0].delta if chunk.choices else None
        if delta and delta.content:
            yield delta.content

async def _stream_bedrock(messages: list, model: str, max_tokens: int):
    """Token-by-token streaming from Amazon Bedrock via Converse streaming API.
    boto3 is synchronous — runs in a thread pool; tokens passed back via asyncio.Queue.
    Supports Amazon Nova family (nova-micro, nova-lite, nova-pro) and any Converse-compatible model.
    """
    if not _AWS_ACCESS_KEY or not _AWS_SECRET_KEY:
        raise ValueError("AWS credentials not configured (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY)")

    # Convert OpenAI-format messages to Bedrock Converse format
    system_parts = []
    converse_messages = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "system":
            system_parts.append({"text": content})
        else:
            converse_messages.append({"role": role, "content": [{"text": content}]})

    loop = asyncio.get_event_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def _sync_stream():
        try:
            import boto3 as _boto3
            client = _boto3.client(
                "bedrock-runtime",
                region_name=_AWS_REGION,
                aws_access_key_id=_AWS_ACCESS_KEY,
                aws_secret_access_key=_AWS_SECRET_KEY,
            )
            kwargs = dict(
                modelId=model,
                messages=converse_messages,
                inferenceConfig={"maxTokens": max_tokens, "temperature": 0.1},
            )
            if system_parts:
                kwargs["system"] = system_parts
            resp = client.converse_stream(**kwargs)
            for event in resp["stream"]:
                if "contentBlockDelta" in event:
                    text = event["contentBlockDelta"].get("delta", {}).get("text", "")
                    if text:
                        loop.call_soon_threadsafe(queue.put_nowait, text)
            loop.call_soon_threadsafe(queue.put_nowait, None)   # sentinel → done
        except Exception as exc:
            loop.call_soon_threadsafe(queue.put_nowait, exc)

    loop.run_in_executor(None, _sync_stream)

    while True:
        item = await queue.get()
        if item is None:
            break
        if isinstance(item, Exception):
            raise item
        yield item


async def call_llm_api_stream(messages: list, model: str = None, max_tokens: int = 2048, intent: str = "", response_lang: str = ""):
    """
    Real token-by-token streaming from the LLM provider.
    Uses native streaming APIs for instant first-token delivery.
    Supports: Sarvam, Groq, Fireworks, Gemini, Cerebras, xAI, Bedrock.
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

    # ── Vertex AI Gemini Flash fast-path (Task #607) ──────────────────────────
    # When the requested model resolves to Vertex Gemini Flash, stream through
    # Vertex AI's REST endpoint directly. On any error before the first token
    # is delivered, automatically fall back to the legacy SLM hedged pool by
    # re-routing through the standard resolution path below.
    # Task #628 — Indic (Assamese) admin toggle: when the admin has
    # flipped the Indic provider to "vertex" AND Vertex is configured,
    # route Assamese chat through the same Vertex fast-path used for
    # English below. The leak sanitiser downstream still runs on the
    # emitted content, so stray English words are cleaned regardless
    # of provider. On pre-first-token Vertex failure we fall through
    # to the legacy Sarvam hedged pool below (NOT the English SLM
    # pool) to preserve Assamese quality.
    _indic_vertex_active = False
    if _indic_mode:
        try:
            from lang_sanitizer import get_indic_provider as _get_indic_provider
            _indic_provider_pref = _get_indic_provider()
        except Exception:
            _indic_provider_pref = "sarvam"
        if _indic_provider_pref == "vertex" and _vertex_chat.is_configured():
            _indic_vertex_active = True

    _use_vertex_fastpath = (
        (use_model_raw == "vertex/gemini-flash" and not _indic_mode)
        or _indic_vertex_active
    )
    _vertex_fallback_target = (
        # Indic fallback uses the Sarvam hedged pool (preserves Assamese
        # quality); English fallback uses the SLM pool.
        "sarvam-m" if _indic_vertex_active else "openai/gpt-oss-20b"
    )
    _vertex_metric_bucket = "vertex_gemini_indic" if _indic_vertex_active else "vertex_gemini"

    if _use_vertex_fastpath:
        if not _vertex_chat.is_configured():
            logger.warning("gemini-flash requested but neither GEMINI_API_KEY nor VERTEX_PROJECT_ID is set — falling back to legacy SLM pool")
            use_model_raw = _vertex_fallback_target
        elif not _vertex_chat.is_available():
            # Circuit breaker is open — Vertex is known-broken right now.
            # Skip it entirely so we don't pay the connect timeout
            # (~10s) per request. The breaker will auto-attempt
            # recovery after its cooldown.
            logger.info(
                "vertex/gemini-flash skipped — circuit breaker is open; "
                f"routing to {_vertex_fallback_target}"
            )
            use_model_raw = _vertex_fallback_target
        else:
            _vertex_first_token = False
            _vertex_t0 = time.monotonic()
            try:
                _mt_vx = _clamp_max_tokens(VERTEX_GEMINI_MODEL, max_tokens)
                _vx_batch = ""
                _VX_BATCH_SIZE = 2
                # For Indic mode, prepend the same strict-Assamese system
                # preface used by `_stream_sarvam` so Gemini commits to
                # Assamese script from the first token and we don't rely
                # on the sanitizer to clean up provider-level leakage.
                _vx_messages = messages
                if _indic_vertex_active:
                    _vx_messages = [dict(m) for m in messages]
                    _asm_preface = (
                        "/think অসমীয়াত চমুকৈ চিন্তা কৰা — তাৰ পিছত সম্পূৰ্ণ উত্তৰ অসমীয়াত দিয়া।\n"
                        "CRITICAL: Think in Assamese (অসমীয়া) first, then reply DIRECTLY in Assamese.\n"
                        "Do NOT start with 'Okay' or 'Let me'. Begin your answer immediately.\n"
                        "STRICT LANGUAGE RULES:\n"
                        "- Every running word MUST be in Assamese script. "
                        "NO mid-sentence English words.\n"
                        "- Latin script is allowed ONLY for: pure numbers/dates, "
                        "scientific units (cm, kg, Hz, °C, eV…), math symbols/equations, "
                        "code, URLs, well-known proper nouns and acronyms "
                        "(AHSEC, SEBA, NCERT, DNA, GDP, Magh Bihu, Newton).\n"
                        "- For everyday nouns/verbs, use the Assamese word — "
                        "do NOT fall back to English.\n\n"
                    )
                    if _vx_messages and _vx_messages[0].get("role") == "system":
                        _vx_messages[0]["content"] = _asm_preface + _vx_messages[0]["content"]
                    else:
                        _vx_messages.insert(0, {"role": "system", "content": _asm_preface})
                async for token in _stream_vertex_gemini(_vx_messages, VERTEX_GEMINI_MODEL, _mt_vx):
                    if not _vertex_first_token:
                        _ttft_ms = (time.monotonic() - _vertex_t0) * 1000
                        logger.info(f"[VERTEX-PERF] TTFT={_ttft_ms:.0f}ms model={VERTEX_GEMINI_MODEL} indic={_indic_vertex_active}")
                        _vertex_first_token = True
                    _vx_batch += token
                    if len(_vx_batch) >= _VX_BATCH_SIZE:
                        yield f"data: {json.dumps({'content': _vx_batch})}\n\n"
                        _vx_batch = ""
                if _vx_batch:
                    yield f"data: {json.dumps({'content': _vx_batch})}\n\n"
                if _vertex_first_token:
                    _total_ms = (time.monotonic() - _vertex_t0) * 1000
                    logger.info(f"[VERTEX-PERF] Total={_total_ms:.0f}ms model={VERTEX_GEMINI_MODEL} indic={_indic_vertex_active}")
                    try:
                        from chat_speedup_metrics import record_provider_call as _rec_prov
                        _rec_prov(_vertex_metric_bucket, ttfb_ms=_ttft_ms, total_ms=_total_ms)
                    except Exception:
                        pass
                    yield f"data: {json.dumps({'__provider': _vertex_metric_bucket})}\n\n"
                    return
                # Stream completed without ever yielding a token — treat as
                # failure and fall through to legacy.
                logger.warning("Vertex Gemini Flash returned empty stream — falling back to legacy pool")
            except Exception as _vx_err:
                if _vertex_first_token:
                    # We already started streaming to the client; we can't
                    # silently restart. Emit error and stop.
                    logger.warning(f"Vertex Gemini Flash mid-stream error: {type(_vx_err).__name__}: {str(_vx_err)[:200]}")
                    yield f"data: {json.dumps({'error': 'AI service interrupted'})}\n\n"
                    return
                logger.warning(f"Vertex Gemini Flash failed before first token: {type(_vx_err).__name__}: {str(_vx_err)[:200]} — falling back to legacy pool")
            # Fall back: rewrite the requested model to the legacy default
            # and continue with the standard resolution path below.
            try:
                from chat_speedup_metrics import record_provider_fallback as _rec_fb
                _rec_fb(_vertex_metric_bucket, _vertex_fallback_target)
            except Exception:
                pass
            use_model_raw = _vertex_fallback_target
            model = use_model_raw
            _indic_vertex_active = False  # Fallback → resolve normally

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
        elif p_name == "gemini":
            logger.info(f"LLM stream: provider=gemini, model={p_model}")
            async for token in _stream_gemini(messages, p_key, p_model, _mt):
                yield token
        elif p_name == "cerebras":
            logger.info(f"LLM stream: provider=cerebras, model={p_model}")
            async for token in _stream_cerebras(messages, p_key, p_model, _mt):
                yield token
        elif p_name == "groq":
            logger.info(f"LLM stream: provider=groq, model={p_model}")
            async for token in _stream_openai_compat(messages, p_key, p_model, _mt, "groq", "https://api.groq.com/openai/v1"):
                yield token
        elif p_name == "xai":
            logger.info(f"LLM stream: provider=xai, model={p_model}")
            async for token in _stream_xai(messages, p_key, p_model, _mt):
                yield token
        elif p_name == "openrouter":
            logger.info(f"LLM stream: provider=openrouter, model={p_model}")
            async for token in _stream_openai_compat(messages, p_key, p_model, _mt, "openrouter", "https://openrouter.ai/api/v1"):
                yield token
        elif p_name == "bedrock":
            logger.info(f"LLM stream: provider=bedrock, model={p_model}")
            async for token in _stream_bedrock(messages, p_model, _mt):
                yield token
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
        "cerebras": 24000,
        "sarvam": 12000,
        "groq": 100000,
        "gemini": 500000,
        "openrouter": 200000,
        "openai": 80000,
        "bedrock": 40000,
    }

    if use_model_raw == "openai/gpt-oss-20b":
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
            yield f"data: {json.dumps({'error': 'Indic language AI service temporarily unavailable'})}\n\n"
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
            f"[INDIC] Phase 2 (Qwen FALLBACK): streaming from "
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
            yield f"data: {json.dumps({'error': 'Indic language AI service temporarily unavailable'})}\n\n"
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


async def call_embed_with_dispatch(
    text: str,
    task_type: str = "RETRIEVAL_DOCUMENT",
    lang: str = "en",
) -> list:
    """Embed *text* via the weighted provider selected for the 'embed' feature key.

    Priority (PROVIDER_PRIORITY['embed']):
      vertex(2000) → bedrock(1000, Titan embed) → cohere(1000) → azure_openai(1, skip) → workers_ai(0)

    bedrock: Amazon Titan Text Embeddings v1 via CF gateway BYOK (/model/.../invoke).
    cohere: providers.cohere.embed_query (1024-dim, embed-multilingual-v3.0).
    azure_openai: embeddings endpoint not wired (Task #257) — excluded gracefully.
    Returns a float list on success, raises RuntimeError if all providers fail.
    """
    from config import PROVIDER_PRIORITY as _PP
    exclude: frozenset = frozenset()
    max_attempts = len(_PP.get("embed", [])) + 1

    for _ in range(max_attempts):
        provider = select_provider("embed", lang=lang, exclude=exclude)
        try:
            if provider == "vertex":
                import vertex_services
                result = await vertex_services.embed_text(text, task_type=task_type)
                if result is None:
                    raise RuntimeError("vertex embed_text returned None")
                return result
            elif provider == "workers_ai":
                from providers.cloudflare_ai import embed as _cf_embed
                return await _cf_embed(text)
            elif provider == "bedrock":
                # Amazon Titan Embeddings v2 via CF AI Gateway BYOK (Task #256).
                from providers.bedrock import call_embed as _bk_embed
                return await _bk_embed(text, task_type=task_type)
            elif provider == "cohere":
                from providers.cohere import embed_query as _cohere_embed_q, ENABLED as _cohere_enabled
                if not _cohere_enabled:
                    raise RuntimeError("cohere embed: COHERE_API_KEY not configured")
                _cohere_vec = await _cohere_embed_q(text)
                if not _cohere_vec:
                    raise RuntimeError("cohere embed: embed_query returned empty vector")
                return _cohere_vec
            elif provider == "azure_openai":
                # Azure OpenAI text-embedding-3-large via CF BYOK (Task #256).
                from providers.azure_openai import call_embed as _az_embed
                return await _az_embed(text)
            else:
                raise RuntimeError(f"embed: unknown provider {provider!r}")
        except Exception as exc:
            logger.warning("embed %s failed: %s — removing from pool", provider, exc)
            exclude = exclude | {provider}

    raise RuntimeError("embed: all providers exhausted")


async def call_translate_with_dispatch(
    text: str,
    source_lang: str = "en-IN",
    target_lang: str = "as-IN",
    lang: str = "as",
) -> str:
    """Translate *text* via the weighted provider selected for 'translate'.

    Priority (PROVIDER_PRIORITY['translate']):
      sarvam(500) → vertex(2000) → bedrock(1000, call_converse) → azure_openai(1, call_chat) → workers_ai(0)

    bedrock: routes via providers.bedrock.call_converse with translation system prompt.
    azure_openai: routes via providers.azure_openai.call_chat with translation system prompt.
    Returns the translated string or raises RuntimeError if all providers fail.
    """
    from config import PROVIDER_PRIORITY as _PP
    exclude: frozenset = frozenset()
    max_attempts = len(_PP.get("translate", [])) + 1

    for _ in range(max_attempts):
        provider = select_provider("translate", lang=lang, exclude=exclude)
        try:
            if provider == "sarvam":
                sarvam_slot = _SARVAM_PROVIDERS[0] if _SARVAM_PROVIDERS else None
                if not sarvam_slot:
                    raise RuntimeError("sarvam translate: no API key configured")
                import httpx as _hx
                resp = await _hx.AsyncClient(timeout=20).post(
                    "https://api.sarvam.ai/translate",
                    headers={"API-Subscription-Key": sarvam_slot["key"]},
                    json={
                        "input": text,
                        "source_language_code": source_lang,
                        "target_language_code": target_lang,
                        "speaker_gender": "Female",
                        "mode": "formal",
                        "enable_preprocessing": True,
                    },
                )
                resp.raise_for_status()
                return resp.json().get("translated_text") or ""
            elif provider == "vertex":
                prompt = [
                    {"role": "system", "content": f"Translate the following text from {source_lang} to {target_lang}. Output only the translation, no commentary."},
                    {"role": "user", "content": text},
                ]
                return await _call_gemini(prompt, _GEMINI_KEY, "gemini-2.5-flash", 2048)
            elif provider == "bedrock":
                # Amazon Translate via bedrock-proxy Worker (SigV4) — Task #256.
                # Falls back to RuntimeError if BEDROCK_PROXY_URL not configured.
                from providers.bedrock import call_translate as _bk_translate
                return await _bk_translate(text, target_lang=target_lang, source_lang=source_lang)
            elif provider == "azure_openai":
                # Azure Translator REST API (AZURE_TRANSLATOR_KEY) — Task #256.
                # Falls back to RuntimeError if AZURE_TRANSLATOR_KEY not configured.
                from providers.azure_openai import call_translate as _az_translate
                return await _az_translate(text, target_lang=target_lang, source_lang=source_lang)
            elif provider == "workers_ai":
                prompt = [
                    {"role": "system", "content": f"Translate from {source_lang} to {target_lang}. Output only the translation."},
                    {"role": "user", "content": text},
                ]
                return await _call_llm_raw(prompt, None, 2048, provider_list=_LLM_PROVIDERS_WORKERS_ONLY)
            else:
                raise RuntimeError(f"translate: unknown provider {provider!r}")
        except Exception as exc:
            logger.warning("translate %s failed: %s — removing from pool", provider, exc)
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
                import os as _os_tv
                _tavily_key = _os_tv.environ.get("TAVILY_API_KEY") or _os_tv.environ.get("TAVILY_KEY")
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
      pinecone_ai(500) → cohere(1000, skip) → azure_openai(1, skip) → workers_ai(0)

    pinecone_ai: providers.pinecone_ai.rerank (bge-reranker-v2-m3, multilingual) — fully wired.
    cohere: /rerank endpoint not accessible through CF gateway slug (Task #257) — excluded gracefully.
    azure_openai: rerank not wired (Task #257) — excluded gracefully.
    workers_ai: no rerank endpoint — excluded gracefully.

    Each doc should be a string or a dict with a 'text' key.
    Returns the docs list reordered by relevance (most relevant first),
    or the original list unchanged if all providers fail.
    """
    from config import PROVIDER_PRIORITY as _PP
    exclude: frozenset = frozenset()
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
            elif provider == "cohere":
                # Cohere /rerank endpoint not accessible through the current CF gateway
                # slug — wiring deferred to Task #257. Excluded gracefully.
                raise RuntimeError("cohere rerank: endpoint not wired via CF gateway (Task #257)")
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

    Priority (PROVIDER_PRIORITY['vision']):
      vertex(2000, Gemini) → bedrock(1000, Claude multimodal) → azure_openai(1, GPT-4o) → workers_ai(0)

    vertex: Gemini 2.5 Flash vision via _call_gemini with image_url content.
    bedrock: Claude 3.5 Sonnet v2 multimodal via providers.bedrock.call_converse_vision.
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
            if provider == "vertex":
                if not _GEMINI_KEY:
                    raise RuntimeError("vertex vision: GEMINI_API_KEY not set")
                vision_messages = [
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
                return await _call_gemini(vision_messages, _GEMINI_KEY, "gemini-2.5-flash", 1024)
            elif provider == "bedrock":
                from providers.bedrock import call_converse_vision as _bk_vision
                return await _bk_vision(b64_image, mime_type, prompt, max_tokens=1024)
            elif provider == "azure_openai":
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
