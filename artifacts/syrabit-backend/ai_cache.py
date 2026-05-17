"""Syrabit.ai — Managed AI response cache.

Provides a process-local L1 in-memory cache plus an optional managed Redis L2.

Architecture (post-2026-04 Cloudflare-only refactor):

  * **L1 (always on)**: per-worker `cachetools.TTLCache` keyed by namespace+key.
    Survives the lifetime of a single gunicorn worker. Bounded by entry count
    AND each value's byte size (oversize payloads are dropped, not truncated,
    so we never serve a half-cached answer).
  * **L2 (optional)**: env-driven `MEMORYSTORE_REDIS_URL` (any Redis-compatible
    endpoint — `rediss://` for TLS, `redis://` otherwise). Currently unset —
    Cloudflare AI Gateway handles upstream LLM caching with its own 3600s TTL,
    so an L2 here is redundant for the LLM use case. Re-enable for cross-worker
    dedupe (e.g. on Cloud Run with Memorystore) by setting the env var.
  * Deterministic, namespaced keys scoped to model + normalized prompt +
    retrieval fingerprint + language + scope.
  * Built-in metrics: hits, misses, errors, bytes_stored, hit_rate,
    saved-latency (avg + total estimated).
  * Lightweight circuit breaker so a flapping L2 cannot stall the chat
    request path; all aget/aset calls are timeout-bounded.
  * Admin purge of the full AI namespace + stats snapshot for observability.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from typing import Any, Dict, Optional

from cachetools import TTLCache

logger = logging.getLogger(__name__)

try:
    import redis.asyncio as aioredis
except ImportError:
    aioredis = None

from config import (
    MEMORYSTORE_REDIS_URL,
    REDIS_AI_CACHE_NAMESPACE,
    REDIS_AI_CACHE_MAX_ENTRY_BYTES,
    REDIS_AI_CACHE_CONNECT_TIMEOUT_MS,
    REDIS_AI_CACHE_OP_TIMEOUT_MS,
    REDIS_AI_CACHE_TTL,
    REDIS_URL as _UPSTASH_URL,
    REDIS_TOKEN as _UPSTASH_TOKEN,
)

__all__ = [
    "build_ai_cache_key",
    "aget",
    "aset",
    "record_hit_saved_latency",
    "purge_all",
    "stats",
    "init_async_client",
    "close_async_client",
    "active_backend",
]


# ── Stats ───────────────────────────────────────────────────────────────────
class _AICacheStats:
    __slots__ = (
        "hits", "misses", "errors", "bytes_stored", "entries_stored",
        "entries_skipped_oversize", "saved_latency_ms_total", "saved_latency_samples",
        "observed_miss_latency_ms_total", "observed_miss_latency_samples",
        "last_error", "last_error_ts", "last_purge_ts", "purge_count",
    )

    def __init__(self) -> None:
        self.hits = 0
        self.misses = 0
        self.errors = 0
        self.bytes_stored = 0
        self.entries_stored = 0
        self.entries_skipped_oversize = 0
        self.saved_latency_ms_total = 0.0
        self.saved_latency_samples = 0
        self.observed_miss_latency_ms_total = 0.0
        self.observed_miss_latency_samples = 0
        self.last_error = ""
        self.last_error_ts = 0.0
        self.last_purge_ts = 0.0
        self.purge_count = 0


_stats = _AICacheStats()


# ── Circuit breaker ─────────────────────────────────────────────────────────
_BREAKER_THRESHOLD = 5
_BREAKER_OPEN_SECS = 30.0
_breaker_failures = 0
_breaker_opened_at = 0.0


def _breaker_open() -> bool:
    if _breaker_opened_at == 0.0:
        return False
    return (time.time() - _breaker_opened_at) < _BREAKER_OPEN_SECS


def _record_failure(err: str) -> None:
    global _breaker_failures, _breaker_opened_at
    _breaker_failures += 1
    _stats.errors += 1
    _stats.last_error = err[:200]
    _stats.last_error_ts = time.time()
    if _breaker_failures >= _BREAKER_THRESHOLD and _breaker_opened_at == 0.0:
        _breaker_opened_at = time.time()
        logger.warning(
            "ai_cache: circuit breaker OPEN after %d consecutive failures (last: %s)",
            _breaker_failures, _stats.last_error,
        )


def _record_success() -> None:
    global _breaker_failures, _breaker_opened_at
    if _breaker_failures or _breaker_opened_at:
        if _breaker_opened_at:
            logger.info("ai_cache: circuit breaker CLOSED — Redis healthy again")
        _breaker_failures = 0
        _breaker_opened_at = 0.0


# ── Backend init ────────────────────────────────────────────────────────────
_async_pool: Optional["aioredis.Redis"] = None
_backend = "uninitialized"

# ── L1 in-memory cache (always on; bounded LRU+TTL) ────────────────────────
# 2048 entries × 64KB max = ~128MB worst case per worker. Real usage is far
# smaller because most cached values are short LLM responses (~1-4KB each).
_L1_MAX_ENTRIES = 2048
_l1: TTLCache = TTLCache(maxsize=_L1_MAX_ENTRIES, ttl=REDIS_AI_CACHE_TTL)


def active_backend() -> str:
    return _backend


def _detect_initial_backend() -> str:
    if _UPSTASH_URL and _UPSTASH_TOKEN:
        return "upstash"
    if aioredis and MEMORYSTORE_REDIS_URL:
        return "memorystore"
    return "memory_only"


def _detect_fallback_backend() -> str:
    """Last-resort backend when a configured Redis endpoint is unreachable at
    startup. LLM upstream caching lives at Cloudflare AI Gateway; this
    fallback only affects the per-worker L1 cache."""
    return "memory_only"


async def init_async_client() -> str:
    """Initialise the async Redis pool. Safe to call multiple times."""
    global _async_pool, _backend
    chosen = _detect_initial_backend()

    # ── Upstash (REST-API, serverless, no TCP) ────────────────────────────────
    if chosen == "upstash":
        try:
            from upstash_redis.asyncio import Redis as _UpstashAsyncRedis
            pool = _UpstashAsyncRedis(url=_UPSTASH_URL, token=_UPSTASH_TOKEN)
            await asyncio.wait_for(
                pool.ping(),
                timeout=(REDIS_AI_CACHE_CONNECT_TIMEOUT_MS + REDIS_AI_CACHE_OP_TIMEOUT_MS) / 1000.0,
            )
            _async_pool = pool
            _backend = "upstash"
            _safe_url = _UPSTASH_URL.split("//", 1)[-1][:40]
            logger.info("ai_cache: Upstash Redis L2 ready (%s…, ns=%s, ttl=%ds)",
                        _safe_url, REDIS_AI_CACHE_NAMESPACE, REDIS_AI_CACHE_TTL)
            return _backend
        except ImportError:
            logger.warning("ai_cache: upstash-redis not installed, falling back to memory_only")
        except Exception as e:
            logger.warning("ai_cache: Upstash unreachable, falling back to memory_only: %s", e)
        _async_pool = None
        chosen = _detect_fallback_backend()

    # ── Legacy Memorystore / any redis:// endpoint ────────────────────────────
    elif chosen == "memorystore":
        try:
            pool = aioredis.from_url(
                MEMORYSTORE_REDIS_URL,
                socket_timeout=REDIS_AI_CACHE_OP_TIMEOUT_MS / 1000.0,
                socket_connect_timeout=REDIS_AI_CACHE_CONNECT_TIMEOUT_MS / 1000.0,
                decode_responses=True,
                health_check_interval=30,
                max_connections=64,
                retry_on_timeout=False,
            )
            await asyncio.wait_for(
                pool.ping(),
                timeout=(REDIS_AI_CACHE_CONNECT_TIMEOUT_MS + REDIS_AI_CACHE_OP_TIMEOUT_MS) / 1000.0,
            )
            _async_pool = pool
            _backend = "memorystore"
            _safe_url = MEMORYSTORE_REDIS_URL
            if "@" in _safe_url:
                _safe_url = _safe_url.split("@", 1)[-1]
            logger.info("ai_cache: Memorystore Redis ready (%s, ns=%s)", _safe_url, REDIS_AI_CACHE_NAMESPACE)
            return _backend
        except Exception as e:
            logger.warning("ai_cache: Memorystore unreachable, falling back to memory_only: %s", e)
            _async_pool = None
            chosen = _detect_fallback_backend()

    _backend = chosen
    if _backend == "memory_only":
        logger.info(
            "ai_cache: backend=L1_only (per-worker TTLCache, maxsize=%d, ttl=%ds; "
            "Cloudflare AI Gateway handles upstream LLM cache)",
            _L1_MAX_ENTRIES, REDIS_AI_CACHE_TTL,
        )
    else:
        logger.info("ai_cache: backend=%s (ttl=%ss, max_entry=%dB)",
                    _backend, REDIS_AI_CACHE_TTL, REDIS_AI_CACHE_MAX_ENTRY_BYTES)
    return _backend


async def close_async_client() -> None:
    global _async_pool
    if _async_pool is not None:
        try:
            await _async_pool.close()
        except Exception:
            pass
        _async_pool = None


# ── Key builder ─────────────────────────────────────────────────────────────
_WS_RE = re.compile(r"\s+")


def _normalize_prompt(prompt: str) -> str:
    s = (prompt or "").strip().lower()
    s = _WS_RE.sub(" ", s)
    # Cap to prevent pathological keys; collisions absorbed by the hash.
    return s[:8000]


def _retrieval_fingerprint(retrieval: Any) -> str:
    """Build a stable fingerprint from a RAG context dict, list, or string.

    Includes the retrieval source plus deterministic ids of chunks / chapters
    / subjects so different retrieval results for the same prompt produce
    different cache entries (avoids serving stale answers when the underlying
    corpus changes)."""
    if not retrieval:
        return ""
    if isinstance(retrieval, str):
        raw = retrieval
    elif isinstance(retrieval, dict):
        chunks = []
        for c in (retrieval.get("chunks") or [])[:32]:
            cid = c.get("id") or c.get("_id") or c.get("chunk_id") or c.get("title") or ""
            chunks.append(str(cid))
        chaps = [str(c.get("id") or c.get("_id") or c.get("slug") or "")
                 for c in (retrieval.get("chapters") or [])[:16]]
        subs = [str(s.get("id") or "") for s in (retrieval.get("subjects") or [])[:8]]
        raw = json.dumps(
            {"src": retrieval.get("source") or "",
             "q":   retrieval.get("quality") or "",
             "chunks": chunks, "chaps": chaps, "subs": subs},
            sort_keys=True,
        )
    elif isinstance(retrieval, (list, tuple)):
        raw = json.dumps([str(x) for x in retrieval][:64], sort_keys=True)
    else:
        raw = str(retrieval)
def _cache_key_md5(raw: str) -> str:
    """Generate cache key using MD5 (legacy, kept for backward compatibility)."""
    return hashlib.md5(raw.encode("utf-8", "ignore"), usedforsecurity=False).hexdigest()[:16]


def _cache_key_sha256(raw: str) -> str:
    """Generate secure cache key using SHA-256."""
    return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()[:32]


def build_ai_cache_key(
    *,
    model: str,
    prompt: str,
    retrieval: Any = None,
    language: str = "",
    scope: str = "",
) -> str:
    """Deterministic AI response cache key.

    Scoped by (model, normalized prompt, retrieval fingerprint, language, scope).
    Returns the bare key — `_full_key()` adds the namespace prefix internally.
    """
    raw = "|".join([
        (model or "").strip(),
        _normalize_prompt(prompt),
        _retrieval_fingerprint(retrieval),
        (language or "").strip().lower(),
        (scope or "").strip(),
    ])
    return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()[:40]


def _full_key(key: str) -> str:
    return f"{REDIS_AI_CACHE_NAMESPACE}:{key}"


# ── Async cache ops ────────────────────────────────────────────────────────
_OP_TIMEOUT_S = REDIS_AI_CACHE_OP_TIMEOUT_MS / 1000.0


async def aget(key: str) -> Optional[str]:
    """Fetch a cached AI response. Returns None on miss / breaker / error."""
    if not key:
        return None
    if _breaker_open():
        return None
    if _async_pool is not None:
        try:
            val = await asyncio.wait_for(_async_pool.get(_full_key(key)), timeout=_OP_TIMEOUT_S)
            _record_success()
            if val is None:
                _stats.misses += 1
                return None
            _stats.hits += 1
            return val if isinstance(val, str) else val.decode("utf-8", "ignore")
        except asyncio.TimeoutError:
            _record_failure("get_timeout")
            return None
        except Exception as e:
            _record_failure(f"get:{e}")
            return None
    # L1-only path (no L2 configured). Process-local TTL cache.
    val = _l1.get(_full_key(key))
    if val is None:
        _stats.misses += 1
        return None
    _stats.hits += 1
    return val


async def aset(
    key: str,
    value: str,
    ttl: int = REDIS_AI_CACHE_TTL,
    *,
    saved_ms: float = 0.0,
) -> bool:
    """Store a cached AI response. Returns True on success.

    `saved_ms` should be the original LLM-call latency the cache will save on
    the next hit; it feeds the saved-latency metric.
    """
    if not key or value is None:
        return False
    payload = value if isinstance(value, str) else str(value)
    sz = len(payload.encode("utf-8", "ignore"))
    if sz > REDIS_AI_CACHE_MAX_ENTRY_BYTES:
        _stats.entries_skipped_oversize += 1
        return False
    if _breaker_open():
        return False
    if _async_pool is not None:
        try:
            await asyncio.wait_for(
                _async_pool.set(_full_key(key), payload, ex=int(ttl)),
                timeout=_OP_TIMEOUT_S,
            )
            _stats.entries_stored += 1
            _stats.bytes_stored += sz
            if saved_ms > 0:
                _stats.observed_miss_latency_ms_total += saved_ms
                _stats.observed_miss_latency_samples += 1
            _record_success()
            return True
        except asyncio.TimeoutError:
            _record_failure("set_timeout")
            return False
        except Exception as e:
            _record_failure(f"set:{e}")
            return False
    # L1-only path (no L2 configured). Process-local TTL cache.
    try:
        _l1[_full_key(key)] = payload
        _stats.entries_stored += 1
        _stats.bytes_stored += sz
        if saved_ms > 0:
            _stats.observed_miss_latency_ms_total += saved_ms
            _stats.observed_miss_latency_samples += 1
        return True
    except Exception as e:
        _record_failure(f"legacy_set:{e}")
        return False


def record_hit_saved_latency(ms: float) -> None:
    """Caller records the latency a cache hit saved (vs running the LLM)."""
    if ms <= 0:
        return
    _stats.saved_latency_ms_total += ms
    _stats.saved_latency_samples += 1


def expected_saved_ms() -> float:
    """Best-effort estimate of the LLM latency a hit just avoided.

    Uses the rolling average of observed cache-miss LLM latencies recorded by
    `aset(..., saved_ms=...)`. Returns 0.0 if we have no observations yet."""
    n = _stats.observed_miss_latency_samples
    if n <= 0:
        return 0.0
    return _stats.observed_miss_latency_ms_total / n


# ── Admin: purge + stats ───────────────────────────────────────────────────
async def purge_all(pattern: str = "*") -> Dict[str, Any]:
    """Purge all AI cache keys under the configured namespace.

    `pattern` is appended after `<namespace>:` (default `*` = everything).
    Also clears the in-memory L1 cache so a stale entry can't re-leak."""
    deleted = 0
    full_pattern = f"{REDIS_AI_CACHE_NAMESPACE}:{pattern}"
    # Always clear the L1 cache first so a stale entry can't re-leak.
    l1_cleared = len(_l1)
    _l1.clear()
    deleted += l1_cleared
    if _async_pool is not None:
        try:
            cursor = 0
            while True:
                cursor, batch = await _async_pool.scan(cursor=cursor, match=full_pattern, count=500)
                if batch:
                    await _async_pool.delete(*batch)
                    deleted += len(batch)
                if cursor == 0:
                    break
        except Exception as e:
            return {"ok": False, "error": str(e)[:200], "deleted": deleted, "backend": _backend}
    else:
        try:
            from deps import redis_client as _rc
            if _rc is not None:
                def _scan_del() -> int:
                    n = 0
                    for k in _rc.scan_iter(full_pattern):
                        try:
                            _rc.delete(k)
                            n += 1
                        except Exception:
                            pass
                    return n
                deleted = await asyncio.get_event_loop().run_in_executor(None, _scan_del)
        except Exception as e:
            return {"ok": False, "error": str(e)[:200], "deleted": deleted, "backend": _backend}
    try:
        from cache import _ai_response_cache
        l1_size = len(_ai_response_cache)
        _ai_response_cache.clear()
    except Exception:
        l1_size = 0
    _stats.last_purge_ts = time.time()
    _stats.purge_count += 1
    return {
        "ok": True,
        "deleted": deleted,
        "l1_cleared": l1_size,
        "backend": _backend,
        "pattern": full_pattern,
    }


def stats() -> Dict[str, Any]:
    total = _stats.hits + _stats.misses
    hit_rate = (_stats.hits / total) if total else 0.0
    avg_saved = (_stats.saved_latency_ms_total / _stats.saved_latency_samples
                 if _stats.saved_latency_samples else 0.0)
    avg_obs_miss = (_stats.observed_miss_latency_ms_total / _stats.observed_miss_latency_samples
                    if _stats.observed_miss_latency_samples else 0.0)
    # If hits never recorded saved_ms explicitly, estimate from observed-miss latency.
    estimated_total_saved = (
        _stats.saved_latency_ms_total
        + max(0, _stats.hits - _stats.saved_latency_samples) * avg_obs_miss
    )
    return {
        "backend": _backend,
        "pool_ready": _async_pool is not None,
        "breaker_open": _breaker_open(),
        "namespace": REDIS_AI_CACHE_NAMESPACE,
        "ttl_seconds": REDIS_AI_CACHE_TTL,
        "max_entry_bytes": REDIS_AI_CACHE_MAX_ENTRY_BYTES,
        "hits": _stats.hits,
        "misses": _stats.misses,
        "hit_rate": round(hit_rate, 4),
        "errors": _stats.errors,
        "last_error": _stats.last_error,
        "last_error_ts": _stats.last_error_ts,
        "entries_stored": _stats.entries_stored,
        "entries_skipped_oversize": _stats.entries_skipped_oversize,
        "bytes_stored": _stats.bytes_stored,
        "avg_saved_latency_ms": round(avg_saved, 1),
        "avg_observed_miss_latency_ms": round(avg_obs_miss, 1),
        "estimated_total_saved_ms": round(estimated_total_saved, 1),
        "purge_count": _stats.purge_count,
        "last_purge_ts": _stats.last_purge_ts,
    }
