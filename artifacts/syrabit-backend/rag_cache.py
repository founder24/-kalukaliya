"""Task #361 §1 — RAG result cache (shadow mode).

A Redis-backed cache for RAG search results keyed
``rag:answer:<retriever>:<top_k>:<lang>:<curriculum_version>:<hash>``.

**Shadow mode (default for the first 7 days post-rollout):**

* ``record_rag_result(...)`` *writes* to the cache on every miss.
* ``get_cached_rag_result(...)`` returns ``None`` unless the serve flag
  is explicitly enabled (``cache:rag_serve_enabled = "1"``).

This lets us measure hit rates and inspect cached values for
correctness before flipping a switch to begin serving from cache. To
graduate from shadow to live mode, the operator sets
``cache:rag_serve_enabled = "1"`` in Redis (no deploy needed).

Cache invalidation:

* TTL: ``RAG_CACHE_TTL_S`` (default 6 h).
* Manual: include the syllabus / curriculum version in the key so a
  curriculum bump silently invalidates everything.
* Per-chapter purge helper :func:`invalidate_chapter` deletes all
  entries that reference *chapter_id* (best-effort SCAN; bounded).

Soft-fail throughout — Redis or serialization errors never block a RAG
call.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────
RAG_CACHE_TTL_S = int(os.environ.get("RAG_CACHE_TTL_S", str(6 * 3600)))
RAG_CACHE_ENABLED_DEFAULT = os.environ.get("RAG_CACHE_ENABLED", "1").strip() == "1"
# Hard cap on serialized payload size (256 KB).
_MAX_ENTRY_BYTES = 256 * 1024

_KEY_TMPL = "rag:answer:{retr}:{k}:{lang}:{ver}:{h}"
_HIT_COUNTER = "rag:cache:hits"
_MISS_COUNTER = "rag:cache:misses"
_SHADOW_WRITE_COUNTER = "rag:cache:shadow_writes"
_KILL_FLAG = "cache:rag_enabled"  # disables both writes and serves
_SERVE_FLAG = "cache:rag_serve_enabled"  # default off → shadow mode

_WHITESPACE_RE = re.compile(r"\s+")


def _r():
    try:
        from deps import redis_client
        return redis_client
    except Exception:
        return None


def _writes_enabled() -> bool:
    if not RAG_CACHE_ENABLED_DEFAULT:
        return False
    r = _r()
    if not r:
        return False
    try:
        flag = r.get(_KILL_FLAG)
        if flag is None:
            return True
        return str(flag).strip() != "0"
    except Exception:
        return True


def _serves_enabled() -> bool:
    """Return True only when shadow mode has been *explicitly* graduated."""
    if not _writes_enabled():
        return False
    r = _r()
    if not r:
        return False
    try:
        flag = r.get(_SERVE_FLAG)
        return str(flag or "").strip() == "1"
    except Exception:
        return False


def _normalize_query(q: str) -> str:
    return _WHITESPACE_RE.sub(" ", (q or "")).strip().lower()


def _key(
    query: str,
    *,
    retriever: str,
    top_k: int,
    lang: str,
    curriculum_version: str,
) -> str:
    h = hashlib.sha256(_normalize_query(query).encode("utf-8")).hexdigest()[:32]
    return _KEY_TMPL.format(
        retr=(retriever or "default")[:24],
        k=int(top_k or 0),
        lang=(lang or "en")[:8],
        ver=(curriculum_version or "v0")[:24],
        h=h,
    )


def _bump(counter: str) -> None:
    r = _r()
    if not r:
        return
    try:
        r.incr(counter)
    except Exception:  # pragma: no cover
        pass


def get_cached_rag_result(
    query: str,
    *,
    retriever: str = "default",
    top_k: int = 8,
    lang: str = "en",
    curriculum_version: str = "v0",
) -> Optional[Any]:
    """Return the cached RAG result *only* when serve mode is graduated.

    During shadow mode this always returns ``None`` even on a stored hit
    so the live RAG call still happens. The hit/miss counters are still
    incremented so the operator can sample expected hit-rate before
    flipping the serve flag.
    """
    if not query or not query.strip():
        return None
    r = _r()
    if not r:
        return None
    key = _key(query, retriever=retriever, top_k=top_k, lang=lang,
               curriculum_version=curriculum_version)
    try:
        raw = r.get(key)
    except Exception as exc:
        logger.debug("rag_cache: get failed key=%s: %s", key, exc)
        return None
    if not raw:
        _bump(_MISS_COUNTER)
        return None
    _bump(_HIT_COUNTER)
    if not _serves_enabled():
        # Shadow mode: count the hit, don't serve it.
        return None
    try:
        return json.loads(raw)
    except Exception as exc:
        logger.debug("rag_cache: decode failed key=%s: %s", key, exc)
        return None


def record_rag_result(
    query: str,
    result: Any,
    *,
    retriever: str = "default",
    top_k: int = 8,
    lang: str = "en",
    curriculum_version: str = "v0",
) -> bool:
    """Persist a RAG result. Soft-fail. Increments shadow-write counter."""
    if not query or not query.strip() or result is None:
        return False
    if not _writes_enabled():
        return False
    r = _r()
    if not r:
        return False
    try:
        payload = json.dumps(result, default=str)
    except Exception as exc:
        logger.debug("rag_cache: encode failed: %s", exc)
        return False
    if len(payload) > _MAX_ENTRY_BYTES:
        logger.debug("rag_cache: entry too large (%d B) — skip", len(payload))
        return False
    key = _key(query, retriever=retriever, top_k=top_k, lang=lang,
               curriculum_version=curriculum_version)
    try:
        r.set(key, payload, ex=RAG_CACHE_TTL_S)
        _bump(_SHADOW_WRITE_COUNTER)
        return True
    except Exception as exc:
        logger.debug("rag_cache: set failed key=%s: %s", key, exc)
        return False


def invalidate_curriculum_version(version: str) -> int:
    """Best-effort purge of all RAG cache entries for *version*. Bounded scan.

    Returns the count of keys deleted; 0 on failure.
    """
    r = _r()
    if not r or not version:
        return 0
    deleted = 0
    cursor: Any = 0
    try:
        for _ in range(50):  # bounded — at most 50 SCAN pages.
            res = r.scan(cursor, match=f"rag:answer:*:{version}:*", count=200)
            if not isinstance(res, (list, tuple)) or len(res) < 2:
                break
            cursor, keys = res[0], res[1] or []
            for k in keys:
                try:
                    if hasattr(r, "delete"):
                        r.delete(k)
                    elif hasattr(r, "kv"):  # test fake
                        r.kv.pop(k, None)
                    deleted += 1
                except Exception:
                    pass
            if str(cursor) in ("0", "b'0'"):
                break
    except Exception as exc:
        logger.debug("rag_cache: invalidate scan failed: %s", exc)
    return deleted


__all__ = [
    "RAG_CACHE_TTL_S",
    "get_cached_rag_result",
    "invalidate_curriculum_version",
    "record_rag_result",
]
