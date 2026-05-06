"""Task #383 — KV + Cache Reserve write-through helper for hot reads.

The site has four datasets that are read on essentially every page
request and changed by an admin a few times a week:

  * Chapter index             (``chapters/index``)
  * Syllabus document         (``syllabus/<class>/<subject>``)
  * Feature flags             (``flags/runtime``)
  * Edu domain allowlist      (``edu_allowlist``)

Cloudflare Cache Reserve handles asset traffic, but those datasets
are dynamic JSON served from the origin and would normally hit Mongo
on every request. This module gives us a single in-process write-
through cache for them, with optional mirroring into a Cloudflare KV
namespace so a second pod can serve a freshly-warmed value via the
edge worker.

Two layers, both safe to disable:

  Layer 1 — in-process LRU
    Always on. Bounded size (default 512 keys). TTL-based expiry per
    entry. Hit ratio is exposed for the ``/admin/cf-health`` route.

  Layer 2 — CF KV mirror (best-effort)
    Active when ``CF_EDGE_CACHE_ON`` is true AND a CF edge proxy URL
    + shared secret are configured. Get/set/invalidate fan out to the
    worker via the same ``D1_SYNC_SECRET`` handshake the existing
    ``/admin/kv-health`` route already uses, so no new credential
    needs provisioning. Failures are logged + counted but never
    raised — the in-process layer keeps serving traffic.

The cache is intentionally NOT a generic Redis substitute. Use
``deps.redis_client`` for cross-pod coordination, rate limits, locks.
This wrapper is for the cold-and-flat read-only data above.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections import OrderedDict
from typing import Any, Optional

from config import CF_EDGE_CACHE_ON

logger = logging.getLogger(__name__)

_DEFAULT_MAX_ENTRIES = 512
_DEFAULT_TTL_S = 5 * 60  # 5 minutes


class _LRU:
    """Threadsafe O(1) LRU with TTL per entry."""

    def __init__(self, max_entries: int = _DEFAULT_MAX_ENTRIES) -> None:
        self._max = max(1, int(max_entries))
        self._data: "OrderedDict[str, tuple[float, Any]]" = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    def get(self, key: str) -> Optional[Any]:
        now = time.time()
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                self.misses += 1
                return None
            expires_at, value = entry
            if expires_at <= now:
                self._data.pop(key, None)
                self.misses += 1
                return None
            self._data.move_to_end(key)
            self.hits += 1
            return value

    def set(self, key: str, value: Any, ttl_s: int) -> None:
        expires_at = time.time() + max(1, int(ttl_s))
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
            self._data[key] = (expires_at, value)
            while len(self._data) > self._max:
                self._data.popitem(last=False)
                self.evictions += 1

    def invalidate(self, key: str) -> bool:
        with self._lock:
            return self._data.pop(key, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


class KvCache:
    """Two-layer cache: in-process LRU + best-effort CF KV mirror.

    Construction is dependency-injected for testability — tests pass an
    ``http_get`` / ``http_put`` / ``http_delete`` triplet and assert
    the calls without spinning up an httpx mock.
    """

    def __init__(
        self,
        *,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
        default_ttl_s: int = _DEFAULT_TTL_S,
        edge_url_getter=lambda: (os.environ.get("CF_EDGE_PROXY_URL") or "").strip().rstrip("/"),
        edge_secret_getter=lambda: (os.environ.get("D1_SYNC_SECRET") or "").strip(),
    ) -> None:
        self._lru = _LRU(max_entries=max_entries)
        self._default_ttl = default_ttl_s
        self._edge_url = edge_url_getter
        self._edge_secret = edge_secret_getter
        self.kv_writes = 0
        self.kv_reads = 0
        self.kv_failures = 0

    # ── Public API ───────────────────────────────────────────────────────
    def get_local(self, key: str) -> Optional[Any]:
        """Synchronous LRU lookup. Use for the hot path where you want
        to avoid even an event-loop tick."""
        return self._lru.get(key)

    async def get(self, key: str) -> Optional[Any]:
        """Get with KV fallback. Local LRU first; on miss, when the KV
        mirror is active, try the worker. Successful KV reads warm the
        local LRU."""
        v = self._lru.get(key)
        if v is not None:
            return v
        if not self._edge_active():
            return None
        try:
            self.kv_reads += 1
            url = f"{self._edge_url()}/api/edge/kv-cache/{key}"
            import httpx  # local import — keep startup time low
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(url, headers={
                    "X-Edge-Admin-Secret": self._edge_secret(),
                })
            if resp.status_code != 200:
                return None
            payload = resp.json()
            value = payload.get("value")
            ttl = int(payload.get("ttl_s") or self._default_ttl)
            self._lru.set(key, value, ttl)
            return value
        except Exception as exc:
            self.kv_failures += 1
            logger.warning("[kv-cache] edge get failed for %r: %s", key, exc)
            return None

    async def set(self, key: str, value: Any,
                  ttl_s: Optional[int] = None) -> None:
        """Write-through: LRU first, then mirror to KV when active."""
        ttl = int(ttl_s if ttl_s is not None else self._default_ttl)
        self._lru.set(key, value, ttl)
        if not self._edge_active():
            return
        try:
            self.kv_writes += 1
            url = f"{self._edge_url()}/api/edge/kv-cache/{key}"
            import httpx
            async with httpx.AsyncClient(timeout=2.0) as client:
                await client.put(url, json={"value": value, "ttl_s": ttl},
                                 headers={"X-Edge-Admin-Secret": self._edge_secret()})
        except Exception as exc:
            self.kv_failures += 1
            logger.warning("[kv-cache] edge set failed for %r: %s", key, exc)

    def clear_local(self, key: str) -> bool:
        """Drop the in-process LRU entry WITHOUT touching the worker
        mirror. Used by the Task #425 smoke endpoint to force the next
        ``get`` to round-trip through the edge worker (and therefore
        bump ``kv_reads``); a normal ``invalidate`` would also delete
        the KV side and the follow-up GET would just see a 404 miss."""
        return self._lru.invalidate(key)

    async def invalidate(self, key: str) -> None:
        """Remove from local + KV mirror (best-effort)."""
        self._lru.invalidate(key)
        if not self._edge_active():
            return
        try:
            url = f"{self._edge_url()}/api/edge/kv-cache/{key}"
            import httpx
            async with httpx.AsyncClient(timeout=2.0) as client:
                await client.delete(url, headers={
                    "X-Edge-Admin-Secret": self._edge_secret(),
                })
        except Exception as exc:
            self.kv_failures += 1
            logger.warning("[kv-cache] edge invalidate failed for %r: %s",
                           key, exc)

    # ── Introspection ────────────────────────────────────────────────────
    def snapshot(self) -> dict[str, Any]:
        total = self._lru.hits + self._lru.misses
        ratio = (self._lru.hits / total) if total else 0.0
        return {
            "enabled": bool(CF_EDGE_CACHE_ON),
            "edge_active": self._edge_active(),
            "entries": len(self._lru._data),
            "max_entries": self._lru._max,
            "hits": self._lru.hits,
            "misses": self._lru.misses,
            "hit_ratio": round(ratio, 4),
            "evictions": self._lru.evictions,
            "kv_reads": self.kv_reads,
            "kv_writes": self.kv_writes,
            "kv_failures": self.kv_failures,
        }

    def reset(self) -> None:
        self._lru.clear()
        self._lru.hits = 0
        self._lru.misses = 0
        self._lru.evictions = 0
        self.kv_writes = self.kv_reads = self.kv_failures = 0

    def _edge_active(self) -> bool:
        return bool(CF_EDGE_CACHE_ON and self._edge_url() and self._edge_secret())


# Process-wide singleton — most callers should reuse this rather than
# constructing their own (so the hit-ratio numbers are aggregate).
_default_cache: Optional[KvCache] = None
_default_lock = threading.Lock()


def default_cache() -> KvCache:
    global _default_cache
    if _default_cache is None:
        with _default_lock:
            if _default_cache is None:
                _default_cache = KvCache()
    return _default_cache


def reset_default_for_tests() -> None:
    global _default_cache
    with _default_lock:
        _default_cache = None


__all__ = ["KvCache", "default_cache", "reset_default_for_tests"]
