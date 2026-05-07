"""ai_input_cache — Task #513 §K.2 deterministic-input AI response cache.

A small cache that short-circuits the LLM dispatch for *deterministic*
inputs — i.e. the input is fully captured by a stable hash of the
(model, messages, max_tokens, temperature=0) tuple. Streaming responses,
random-seeded responses, RAG contexts that include a wall-clock
timestamp, and anything that varies per-user are NEVER cached.

Two storage tiers:

  1. In-process LRU (`functools.lru_cache`-style) — sub-millisecond hit;
     gone on pod restart. Used for hot duplicates within a single
     request burst (admin batch jobs frequently re-emit the same prompt
     dozens of times in <1 s).
  2. Redis (`deps.redis_client`) — TTL-bound (default 24 h) shared
     cache across pods. Round-trip is a single GETEX; on miss the
     dispatcher proceeds normally and writes the result back via
     `set_response`.

The cache key is `aic:v1:<model>:<sha256(canonical_json(messages))>`.
Canonical JSON sorts keys + uses `(",", ":")` separators so the same
logical message produces the same key regardless of dict construction
order.

Public API:
    get_response(messages, model, *, max_tokens=None) -> str | None
    set_response(messages, model, text, *, max_tokens=None, ttl=86400) -> None
    is_deterministic(messages, model, *, temperature=None, stream=False) -> bool

Importantly: this module is OPT-IN. The dispatcher must explicitly
guard the call site with `is_deterministic(...)`; otherwise we risk
serving a cached response across users. Sites where opt-in is safe
include the admin chapter pre-gen pipeline and the
content_formatter polish path (both are pure functions of the input
text + style + lang).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from collections import OrderedDict
from typing import Any, Iterable, Mapping, Optional

logger = logging.getLogger(__name__)

_REDIS_KEY_PREFIX = "ai_response_cache:v1"
_DEFAULT_TTL_SEC = 30 * 24 * 60 * 60
_INPROC_MAX = 2_048
_INPROC_LOCK = threading.Lock()
_INPROC: "OrderedDict[str, str]" = OrderedDict()


def _canonical_messages(messages: Iterable[Mapping]) -> str:
    payload = []
    for m in messages:
        role = str(m.get("role", "user"))
        content = m.get("content", "")
        if isinstance(content, list):
            content = json.dumps(content, sort_keys=True, separators=(",", ":"))
        payload.append({"role": role, "content": str(content)})
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _key(messages: Iterable[Mapping], model: str, *, max_tokens: Optional[int]) -> str:
    canon = _canonical_messages(messages)
    digest = hashlib.sha256(
        f"{model}|{max_tokens or ''}|{canon}".encode("utf-8")
    ).hexdigest()
    return f"{_REDIS_KEY_PREFIX}:{model}:{digest}"


def is_deterministic(
    messages: Iterable[Mapping],
    model: str,
    *,
    temperature: Optional[float] = None,
    stream: bool = False,
) -> bool:
    """Return True iff this call is safe to cache.

    Conservative — bails out on streaming, on any non-zero temperature,
    on missing model id, and on empty message lists (which usually
    indicate the caller is still constructing the request).
    """
    if not model or not messages:
        return False
    if stream:
        return False
    if temperature is not None and float(temperature) > 0.0:
        return False
    return True


def _inproc_get(key: str) -> Optional[str]:
    with _INPROC_LOCK:
        if key in _INPROC:
            _INPROC.move_to_end(key)
            return _INPROC[key]
    return None


def _inproc_set(key: str, value: str) -> None:
    with _INPROC_LOCK:
        _INPROC[key] = value
        _INPROC.move_to_end(key)
        while len(_INPROC) > _INPROC_MAX:
            _INPROC.popitem(last=False)


def _redis_client():  # pragma: no cover — wires to deps at call time
    try:
        import deps
        return getattr(deps, "redis_client", None)
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────
# §K.2 Cloudflare KV tier — `ai_response_cache` namespace, 30-day TTL.
#
# This is the canonical storage tier per V4 §K.2: the SAME KV namespace
# the edge worker binds as `AI_RESPONSE_CACHE` so cache-hits served from
# the worker (e.g. /api/edu/study/explain repeats from a logged-out
# crawler) and cache-hits served from the backend share a single key
# space. Backend writes go through the Cloudflare KV REST API (no SDK
# dependency) — the worker reads/writes directly via its KV binding.
#
# Required env: CF_ACCOUNT_ID, CLOUDFLARE_API_TOKEN (or CF_API_TOKEN),
# AI_RESPONSE_CACHE_KV_ID (the namespace id from wrangler.toml). Absent
# any of these, the KV tier is silently disabled and we degrade to
# Redis-only — never raise from the cache hot path.
# ─────────────────────────────────────────────────────────────────────────
_CF_ACCOUNT_ID    = os.environ.get("CF_ACCOUNT_ID") or os.environ.get("CLOUDFLARE_ACCOUNT_ID")
_CF_API_TOKEN     = os.environ.get("CLOUDFLARE_API_TOKEN") or os.environ.get("CF_API_TOKEN")
_CF_KV_NAMESPACE  = os.environ.get("AI_RESPONSE_CACHE_KV_ID")
_CF_KV_ENABLED    = bool(_CF_ACCOUNT_ID and _CF_API_TOKEN and _CF_KV_NAMESPACE)


def _cf_kv_url(key: str) -> str:
    return (
        f"https://api.cloudflare.com/client/v4/accounts/{_CF_ACCOUNT_ID}"
        f"/storage/kv/namespaces/{_CF_KV_NAMESPACE}/values/{key}"
    )


def _cf_kv_get(key: str) -> Optional[str]:
    if not _CF_KV_ENABLED:
        return None
    try:
        import urllib.request as _ur
        req = _ur.Request(
            _cf_kv_url(key),
            method="GET",
            headers={"Authorization": f"Bearer {_CF_API_TOKEN}"},
        )
        with _ur.urlopen(req, timeout=2.0) as resp:
            if resp.status != 200:
                return None
            body = resp.read()
            return body.decode("utf-8") if body else None
    except Exception as e:
        logger.debug("[ai_input_cache] cf-kv get miss/err: %s", e)
        return None


def _cf_kv_set(key: str, value: str, ttl: int) -> None:
    if not _CF_KV_ENABLED or not value:
        return
    try:
        import urllib.request as _ur
        # CF KV PUT honours `expiration_ttl` as a query-string param.
        url = f"{_cf_kv_url(key)}?expiration_ttl={int(ttl)}"
        req = _ur.Request(
            url, method="PUT", data=value.encode("utf-8"),
            headers={
                "Authorization": f"Bearer {_CF_API_TOKEN}",
                "Content-Type":  "text/plain; charset=utf-8",
            },
        )
        with _ur.urlopen(req, timeout=2.0) as resp:
            _ = resp.read()
    except Exception as e:
        logger.debug("[ai_input_cache] cf-kv put err: %s", e)


def get_response(
    messages: Iterable[Mapping],
    model: str,
    *,
    max_tokens: Optional[int] = None,
) -> Optional[str]:
    """Return the cached completion for this (model, messages,
    max_tokens) tuple, or None on miss. Never raises."""
    msgs = list(messages)
    key = _key(msgs, model, max_tokens=max_tokens)
    val = _inproc_get(key)
    if val is not None:
        return val
    # Tier 2: Cloudflare KV (canonical per V4 §K.2 — same namespace
    # the edge worker reads from). Tried before Redis so a worker-
    # written entry is honoured even if the backend Redis is cold.
    cf_val = _cf_kv_get(key)
    if cf_val:
        _inproc_set(key, cf_val)
        return cf_val
    rc = _redis_client()
    if rc is None:
        return None
    try:
        raw = rc.get(key)
        if not raw:
            return None
        text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
        _inproc_set(key, text)
        return text
    except Exception as e:
        logger.debug("[ai_input_cache] redis get failed: %s", e)
        return None


def set_response(
    messages: Iterable[Mapping],
    model: str,
    text: str,
    *,
    max_tokens: Optional[int] = None,
    ttl: int = _DEFAULT_TTL_SEC,
) -> None:
    """Store the completion for this (model, messages) tuple."""
    if not text:
        return
    msgs = list(messages)
    key = _key(msgs, model, max_tokens=max_tokens)
    _inproc_set(key, text)
    # §K.2 fan-out write: CF KV (canonical) + Redis (fast pod-local).
    # Both are best-effort and never raise from the cache hot path.
    _cf_kv_set(key, text, int(ttl))
    rc = _redis_client()
    if rc is None:
        return
    try:
        rc.set(key, text, ex=int(ttl))
    except Exception as e:
        logger.debug("[ai_input_cache] redis set failed: %s", e)


__all__ = ["get_response", "set_response", "is_deterministic"]
