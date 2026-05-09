"""Task #361 §2 — Embedding cache for repeated questions.

A thin Redis-backed cache keyed ``embed:question:<task_type>:<lang>:<hash>``
storing the bge-m3 (or compatible 1024-dim) vector. The hash is content-
normalized (lowercased, whitespace-collapsed) so same-chapter / same-
topic repeats hit. TTL aligned with the syllabus version so a curriculum
bump silently invalidates stale entries.

Vectors are deterministic for the same model + input, so no shadow-mode
gate is needed — cache hits are safe to serve immediately.

The module is **soft-fail**: any Redis or serialization error logs a
warning and the caller continues with a normal embed call. We never
block an embed because the cache failed.

Hit-rate is tracked with two counters that the operator can sample:

* ``embed:cache:hits``    incremented on every cache hit
* ``embed:cache:misses``  incremented on every cache miss

These are best-effort counters, not strict accounting; loss of a counter
increment never blocks the embed.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from typing import Optional

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────
EMBED_CACHE_TTL_S = int(os.environ.get("EMBED_CACHE_TTL_S", str(24 * 3600)))
EMBED_CACHE_ENABLED_DEFAULT = os.environ.get("EMBED_CACHE_ENABLED", "1").strip() == "1"
# Hard upper bound on per-entry size (bytes) to avoid OOM on a malformed
# vector. 1024-dim float32 ≈ 8 KB JSON; cap at 64 KB for safety.
_MAX_ENTRY_BYTES = 64 * 1024

_KEY_TMPL = "embed:question:{task}:{lang}:{provider}:{h}"
_HIT_COUNTER = "embed:cache:hits"
_MISS_COUNTER = "embed:cache:misses"
# Task #27 — per-provider counters so the admin cache panel can show
# Workers-AI vs. Cohere-via-Bedrock effectiveness independently.
_HIT_COUNTER_PROVIDER = "embed:cache:{provider}:hits"
_MISS_COUNTER_PROVIDER = "embed:cache:{provider}:misses"
# Default provider tag — pre-Task-#27 callers (and tests) that don't
# pass `embed_provider` get the historical default. Live dispatch
# always passes the explicit provider name.
_DEFAULT_PROVIDER = "workers_ai_custom"
_KILL_FLAG = "cache:embed_enabled"

_WHITESPACE_RE = re.compile(r"\s+")


def _r():
    """Return the shared Upstash Redis client, or None when unavailable."""
    try:
        from deps import redis_client
        return redis_client
    except Exception:
        return None


def _enabled() -> bool:
    """Cache active when env default is on AND the kill-switch isn't '0'.

    The Redis kill-switch ``cache:embed_enabled`` lets on-call disable the
    cache in <1 s without a deploy. Defaults to enabled.
    """
    if not EMBED_CACHE_ENABLED_DEFAULT:
        return False
    r = _r()
    if not r:
        # Without Redis we can't read or write — degrade silently.
        return False
    try:
        flag = r.get(_KILL_FLAG)
        # Treat unset as enabled (opt-out, not opt-in).
        if flag is None:
            return True
        return str(flag).strip() != "0"
    except Exception:
        return True


def _normalize(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", (text or "")).strip().lower()


def _key(text: str, task_type: str, lang: str, provider: str) -> str:
    h = hashlib.sha256(_normalize(text).encode("utf-8")).hexdigest()[:32]
    t = (task_type or "default").strip().lower()[:24]
    l = (lang or "en").strip().lower()[:8]
    p = (provider or _DEFAULT_PROVIDER).strip().lower()[:48]
    return _KEY_TMPL.format(task=t, lang=l, provider=p, h=h)


def _bump(counter: str) -> None:
    r = _r()
    if not r:
        return
    try:
        r.incr(counter)
    except Exception:  # pragma: no cover — best-effort counters
        pass


def get_cached_embedding(
    text: str,
    task_type: str = "RETRIEVAL_DOCUMENT",
    lang: str = "en",
    embed_provider: str = _DEFAULT_PROVIDER,
) -> Optional[list]:
    """Return the cached vector for *text* if present, else None.

    Soft-fail: returns None on any Redis / decode error.

    Task #27 — `embed_provider` is folded into the key so a
    Workers-AI vector and a Bedrock-Cohere vector for the same text
    are stored under distinct slots and never cross-pollinate.
    """
    if not text or not text.strip():
        return None
    if not _enabled():
        return None
    r = _r()
    if not r:
        return None
    key = _key(text, task_type, lang, embed_provider)
    _provider_tag = (embed_provider or _DEFAULT_PROVIDER).strip().lower()[:48]
    try:
        raw = r.get(key)
    except Exception as exc:
        logger.debug("embed_cache: get failed key=%s: %s", key, exc)
        return None
    if not raw:
        _bump(_MISS_COUNTER)
        _bump(_MISS_COUNTER_PROVIDER.format(provider=_provider_tag))
        return None
    try:
        vec = json.loads(raw)
        if not isinstance(vec, list) or not vec:
            return None
        _bump(_HIT_COUNTER)
        _bump(_HIT_COUNTER_PROVIDER.format(provider=_provider_tag))
        return vec
    except Exception as exc:
        logger.debug("embed_cache: decode failed key=%s: %s", key, exc)
        return None


def set_cached_embedding(
    text: str,
    vector: list,
    task_type: str = "RETRIEVAL_DOCUMENT",
    lang: str = "en",
    embed_provider: str = _DEFAULT_PROVIDER,
) -> bool:
    """Persist *vector* against (text, task_type, lang, provider). Soft-fail on errors."""
    if not text or not text.strip():
        return False
    if not isinstance(vector, list) or not vector:
        return False
    if not _enabled():
        return False
    r = _r()
    if not r:
        return False
    try:
        payload = json.dumps(vector)
    except Exception as exc:
        logger.debug("embed_cache: encode failed: %s", exc)
        return False
    if len(payload) > _MAX_ENTRY_BYTES:
        logger.debug("embed_cache: entry too large (%d B) — skip cache", len(payload))
        return False
    key = _key(text, task_type, lang, embed_provider)
    try:
        r.set(key, payload, ex=EMBED_CACHE_TTL_S)
        return True
    except Exception as exc:
        logger.debug("embed_cache: set failed key=%s: %s", key, exc)
        return False


__all__ = [
    "EMBED_CACHE_TTL_S",
    "get_cached_embedding",
    "set_cached_embedding",
]
