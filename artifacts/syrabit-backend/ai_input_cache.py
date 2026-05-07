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


def _canonical_messages(messages: Iterable[Mapping], *, normalize_text: bool = False) -> str:
    """Canonicalize the messages list to a stable JSON string.

    When `normalize_text=True` (Task #571), every textual content payload
    is run through `prompt_normalizer.normalize` before serialization so
    cosmetically different prompts (case, punctuation, "what is X" vs
    "define X") collapse to a single cache key.
    """
    payload = []
    norm_fn = None
    if normalize_text:
        try:
            from prompt_normalizer import normalize as _norm
            norm_fn = _norm
        except Exception:
            norm_fn = None
    for m in messages:
        role = str(m.get("role", "user"))
        content = m.get("content", "")
        if isinstance(content, list):
            content = json.dumps(content, sort_keys=True, separators=(",", ":"))
        s = str(content)
        if norm_fn is not None and role in ("user", "system"):
            s = norm_fn(s)
        payload.append({"role": role, "content": s})
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _key(
    messages: Iterable[Mapping],
    model: str,
    *,
    max_tokens: Optional[int],
    normalize_text: bool = False,
    template_version: str = "",
) -> str:
    canon = _canonical_messages(messages, normalize_text=normalize_text)
    # `template_version` is folded into the key so a prompt-template bump
    # invalidates the cache without touching the messages payload — the
    # `template_version_bump` miss-reason below recognises this case.
    digest = hashlib.sha256(
        f"{model}|{template_version}|{max_tokens or ''}|{canon}".encode("utf-8")
    ).hexdigest()
    return f"{_REDIS_KEY_PREFIX}:{model}:{digest}"


# ── Task #571 — per-content-type counters + miss-reason tagging ───────
# Tracked entirely in-process. The nightly Lambda
# `lambda_batch.cache_effectiveness` scrapes `/api/health/cache` (which
# calls `snapshot()` below) and emits the rolled-up numbers to the
# `Syrabit/Cache` CloudWatch namespace where the hit-ratio + cardinality
# alarms live.
_KNOWN_CONTENT_TYPES = (
    "mcq", "flashcard", "definition", "formatter",
    "translate", "ocr", "stage3_polish", "unknown",
)
_MISS_REASONS = (
    "normalization_mismatch",
    "template_version_bump",
    "ttl_expiry",
    "uncached_content_type",
    "cold",
)


def _empty_ct_counters() -> dict:
    return {
        "hits": 0, "misses": 0, "sets": 0,
        "miss_reasons": {r: 0 for r in _MISS_REASONS},
        "unique_keys_24h": [],  # ring of (epoch_seconds, key) for cardinality
    }


_COUNTERS_LOCK = threading.Lock()
_COUNTERS: dict[str, dict] = {ct: _empty_ct_counters() for ct in _KNOWN_CONTENT_TYPES}
# Recently-set keys (key -> epoch_seconds set) used to attribute
# `ttl_expiry` vs `cold`. Bounded ring; older entries fall out.
_RECENT_SETS: "OrderedDict[str, float]" = OrderedDict()
_RECENT_SETS_MAX = 8_192
# Last-seen template version per content_type. A miss whose
# template_version differs from the last set's is `template_version_bump`.
_LAST_TEMPLATE_VERSION: dict[str, str] = {}


def _bump_unique_key(ct: str, key: str) -> None:
    now = time.time()
    cutoff = now - 86_400.0
    bucket = _COUNTERS[ct]["unique_keys_24h"]
    bucket.append((now, key))
    # Drop entries older than 24 h. Cheap because the list is append-only
    # and the prefix is monotonically aged.
    while bucket and bucket[0][0] < cutoff:
        bucket.pop(0)


def _classify_miss(
    *,
    ct: str,
    key: str,
    normalized_key: str,
    template_version: str,
) -> str:
    if ct == "unknown":
        return "uncached_content_type"
    if key in _RECENT_SETS:
        # We wrote this key recently and Redis returned None → TTL expired.
        return "ttl_expiry"
    last_tv = _LAST_TEMPLATE_VERSION.get(ct, "")
    if template_version and last_tv and template_version != last_tv:
        return "template_version_bump"
    if normalized_key and normalized_key != key and normalized_key in _RECENT_SETS:
        return "normalization_mismatch"
    return "cold"


def snapshot() -> dict:
    """Return a JSON-safe snapshot of the per-content-type counters.

    Consumed by `/api/health/cache` (admin-only) and by the nightly
    `lambda_batch.cache_effectiveness` shipper.
    """
    with _COUNTERS_LOCK:
        out: dict = {"content_types": {}, "totals": {"hits": 0, "misses": 0, "sets": 0}}
        for ct, c in _COUNTERS.items():
            total = c["hits"] + c["misses"]
            hr = round(c["hits"] / total, 4) if total else 0.0
            out["content_types"][ct] = {
                "hits": c["hits"],
                "misses": c["misses"],
                "sets": c["sets"],
                "hit_ratio": hr,
                "unique_keys_24h": len({k for _, k in c["unique_keys_24h"]}),
                "miss_reasons": dict(c["miss_reasons"]),
            }
            out["totals"]["hits"] += c["hits"]
            out["totals"]["misses"] += c["misses"]
            out["totals"]["sets"] += c["sets"]
        gt = out["totals"]["hits"] + out["totals"]["misses"]
        out["totals"]["hit_ratio"] = round(out["totals"]["hits"] / gt, 4) if gt else 0.0
        out["totals"]["unique_keys_24h"] = sum(
            entry["unique_keys_24h"] for entry in out["content_types"].values()
        )
    return out


def reset_for_tests() -> None:
    """Test-only — wipe in-process counters and recent-sets ring."""
    with _COUNTERS_LOCK:
        for ct in _KNOWN_CONTENT_TYPES:
            _COUNTERS[ct] = _empty_ct_counters()
        _RECENT_SETS.clear()
        _LAST_TEMPLATE_VERSION.clear()
        _INPROC.clear()


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
    content_type: Optional[str] = None,
    template_version: str = "",
    normalize_text: bool = False,
) -> Optional[str]:
    """Return the cached completion for this (model, messages,
    max_tokens) tuple, or None on miss. Never raises.

    Task #571 additions: callers may pass `content_type`
    (`mcq` / `flashcard` / `definition` / `formatter` / ...),
    `template_version` (folded into the key for safe rotation), and
    `normalize_text=True` to apply prompt-normalization. The miss path
    bumps a per-content-type miss-reason counter exposed via
    `snapshot()` and surfaced through `/api/health/cache`.
    """
    msgs = list(messages)
    ct = content_type if content_type in _KNOWN_CONTENT_TYPES else "unknown"
    key = _key(msgs, model, max_tokens=max_tokens,
               normalize_text=normalize_text, template_version=template_version)
    with _COUNTERS_LOCK:
        _bump_unique_key(ct, key)
    val = _inproc_get(key)
    if val is not None:
        with _COUNTERS_LOCK:
            _COUNTERS[ct]["hits"] += 1
        return val
    # Tier 2: Cloudflare KV (canonical per V4 §K.2 — same namespace
    # the edge worker reads from). Tried before Redis so a worker-
    # written entry is honoured even if the backend Redis is cold.
    cf_val = _cf_kv_get(key)
    if cf_val:
        _inproc_set(key, cf_val)
        with _COUNTERS_LOCK:
            _COUNTERS[ct]["hits"] += 1
        return cf_val
    rc = _redis_client()
    if rc is not None:
        try:
            raw = rc.get(key)
            if raw:
                text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
                _inproc_set(key, text)
                with _COUNTERS_LOCK:
                    _COUNTERS[ct]["hits"] += 1
                return text
        except Exception as e:
            logger.debug("[ai_input_cache] redis get failed: %s", e)
    # Genuine miss — attribute it to a reason and bump the counter.
    # For normalization-mismatch detection we also compute what the
    # key WOULD have been with normalize_text flipped, so an unnormalized
    # caller can be told a normalized peer is already cached.
    normalized_key = ""
    try:
        if not normalize_text:
            normalized_key = _key(msgs, model, max_tokens=max_tokens,
                                  normalize_text=True,
                                  template_version=template_version)
    except Exception:
        pass
    reason = _classify_miss(
        ct=ct, key=key, normalized_key=normalized_key,
        template_version=template_version,
    )
    with _COUNTERS_LOCK:
        _COUNTERS[ct]["misses"] += 1
        _COUNTERS[ct]["miss_reasons"][reason] += 1
    return None


def set_response(
    messages: Iterable[Mapping],
    model: str,
    text: str,
    *,
    max_tokens: Optional[int] = None,
    ttl: int = _DEFAULT_TTL_SEC,
    content_type: Optional[str] = None,
    template_version: str = "",
    normalize_text: bool = False,
) -> None:
    """Store the completion for this (model, messages) tuple.

    Task #571 additions: `content_type`, `template_version`, and
    `normalize_text` mirror `get_response` so writes land on the same
    canonical key the next read will compute. The recently-set ring is
    bumped so a subsequent miss for the same key gets attributed to
    `ttl_expiry` rather than `cold`.
    """
    if not text:
        return
    msgs = list(messages)
    ct = content_type if content_type in _KNOWN_CONTENT_TYPES else "unknown"
    key = _key(msgs, model, max_tokens=max_tokens,
               normalize_text=normalize_text, template_version=template_version)
    _inproc_set(key, text)
    with _COUNTERS_LOCK:
        _COUNTERS[ct]["sets"] += 1
        _RECENT_SETS[key] = time.time()
        _RECENT_SETS.move_to_end(key)
        while len(_RECENT_SETS) > _RECENT_SETS_MAX:
            _RECENT_SETS.popitem(last=False)
        if template_version:
            _LAST_TEMPLATE_VERSION[ct] = template_version
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


__all__ = [
    "get_response", "set_response", "is_deterministic",
    "snapshot", "reset_for_tests",
]
