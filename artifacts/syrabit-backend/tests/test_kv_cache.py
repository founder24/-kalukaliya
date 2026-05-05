"""Task #383 — KV write-through cache tests."""
from __future__ import annotations

import time

import pytest


@pytest.fixture
def cache():
    from kv_cache import KvCache
    # Force the edge layer off by returning empty url/secret so all tests
    # run against the LRU only unless they explicitly configure it.
    return KvCache(
        max_entries=4,
        default_ttl_s=60,
        edge_url_getter=lambda: "",
        edge_secret_getter=lambda: "",
    )


@pytest.mark.asyncio
async def test_set_then_get_local_hit(cache):
    await cache.set("chapters/index", {"a": 1})
    assert cache.get_local("chapters/index") == {"a": 1}
    snap = cache.snapshot()
    assert snap["hits"] == 1
    assert snap["misses"] == 0


@pytest.mark.asyncio
async def test_get_local_miss_returns_none(cache):
    assert cache.get_local("missing") is None
    assert cache.snapshot()["misses"] == 1


@pytest.mark.asyncio
async def test_invalidate_drops_entry(cache):
    await cache.set("k", "v")
    await cache.invalidate("k")
    assert cache.get_local("k") is None


@pytest.mark.asyncio
async def test_lru_evicts_oldest_when_full(cache):
    for i in range(5):
        await cache.set(f"k{i}", i)
    snap = cache.snapshot()
    assert snap["entries"] == 4
    # k0 should have been evicted (oldest).
    assert cache.get_local("k0") is None
    assert cache.get_local("k4") == 4


@pytest.mark.asyncio
async def test_ttl_expiry(cache):
    await cache.set("ttl-key", "v", ttl_s=1)
    # Bypass time.sleep for speed — manipulate the entry directly via
    # the LRU internal store. This keeps the test sub-millisecond while
    # still proving expiry triggers a miss.
    key = "ttl-key"
    expires_at, value = cache._lru._data[key]  # noqa: SLF001
    cache._lru._data[key] = (time.time() - 1, value)  # noqa: SLF001
    assert cache.get_local(key) is None


@pytest.mark.asyncio
async def test_hit_ratio_in_snapshot(cache):
    await cache.set("a", 1)
    await cache.set("b", 2)
    cache.get_local("a")
    cache.get_local("a")
    cache.get_local("missing")
    snap = cache.snapshot()
    assert snap["hits"] == 2
    assert snap["misses"] == 1
    assert snap["hit_ratio"] == pytest.approx(2 / 3, rel=1e-3)


@pytest.mark.asyncio
async def test_default_cache_singleton():
    from kv_cache import default_cache, reset_default_for_tests
    reset_default_for_tests()
    a = default_cache()
    b = default_cache()
    assert a is b
    reset_default_for_tests()
    c = default_cache()
    assert c is not a
