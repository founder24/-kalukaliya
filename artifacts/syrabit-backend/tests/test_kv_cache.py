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


# ─────────────── Edge worker contract (Task #405) ───────────────
#
# These tests pin the HTTP shape `kv_cache.KvCache` uses against the
# new `/api/edge/kv-cache/<key>` worker routes so a contract drift on
# either side surfaces immediately. The worker side lives in
# `artifacts/syrabit/workers/edge-proxy/src/index.ts::dispatchKvCache`.

class _CapturingHttpx:
    """Fake httpx.AsyncClient that records every call and returns
    whatever the test sets on ``self.responses`` for the matching method.
    """

    def __init__(self, *args, **kwargs):
        # Per-instance state but tests inspect the class-level
        # ``calls`` list so we don't need to thread the instance through
        # the production code path.
        pass

    calls: list[dict] = []
    responses: dict[str, "object"] = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def get(self, url, headers=None):
        self.calls.append({"method": "GET", "url": url, "headers": headers or {}})
        return self.responses.get("GET")

    async def put(self, url, json=None, headers=None):
        self.calls.append(
            {"method": "PUT", "url": url, "json": json, "headers": headers or {}}
        )
        return self.responses.get("PUT")

    async def delete(self, url, headers=None):
        self.calls.append({"method": "DELETE", "url": url, "headers": headers or {}})
        return self.responses.get("DELETE")


class _FakeResp:
    def __init__(self, status_code: int, body: dict | None = None) -> None:
        self.status_code = status_code
        self._body = body or {}

    def json(self):
        return self._body


@pytest.fixture
def edge_cache(monkeypatch):
    from kv_cache import KvCache
    import kv_cache as kv_cache_mod

    # Force the edge layer ON regardless of the process-wide flag — the
    # test cares about the wire contract, not the runtime gate.
    monkeypatch.setattr(kv_cache_mod, "CF_EDGE_CACHE_ON", True)
    _CapturingHttpx.calls = []
    _CapturingHttpx.responses = {}
    return KvCache(
        max_entries=4,
        default_ttl_s=60,
        edge_url_getter=lambda: "https://edge.example.com",
        edge_secret_getter=lambda: "shared-secret",
    )


@pytest.mark.asyncio
async def test_edge_set_calls_worker_with_value_ttl_and_secret(edge_cache, monkeypatch):
    """Write-through ``set`` must PUT ``{value, ttl_s}`` to the
    worker's ``/api/edge/kv-cache/<key>`` and carry the shared secret
    via ``X-Edge-Admin-Secret``."""
    import httpx
    _CapturingHttpx.responses["PUT"] = _FakeResp(200, {"ok": True, "ttl_s": 120})
    monkeypatch.setattr(httpx, "AsyncClient", _CapturingHttpx)

    await edge_cache.set("chapters/index", {"v": 1}, ttl_s=120)

    assert len(_CapturingHttpx.calls) == 1
    call = _CapturingHttpx.calls[0]
    assert call["method"] == "PUT"
    assert call["url"] == "https://edge.example.com/api/edge/kv-cache/chapters/index"
    assert call["json"] == {"value": {"v": 1}, "ttl_s": 120}
    assert call["headers"].get("X-Edge-Admin-Secret") == "shared-secret"
    assert edge_cache.kv_writes == 1
    assert edge_cache.kv_failures == 0


@pytest.mark.asyncio
async def test_edge_get_warms_local_lru_from_worker_payload(edge_cache, monkeypatch):
    """A miss in the local LRU should fall back to GET on the worker
    and warm the LRU with the returned ``value`` + ``ttl_s`` so a
    sibling pod doesn't have to round-trip again."""
    import httpx
    _CapturingHttpx.responses["GET"] = _FakeResp(
        200, {"value": {"warm": True}, "ttl_s": 90}
    )
    monkeypatch.setattr(httpx, "AsyncClient", _CapturingHttpx)

    got = await edge_cache.get("flags/runtime")
    assert got == {"warm": True}
    # LRU was warmed — a follow-up sync read returns it without a
    # second HTTP call.
    assert edge_cache.get_local("flags/runtime") == {"warm": True}
    assert len(_CapturingHttpx.calls) == 1
    assert _CapturingHttpx.calls[0]["method"] == "GET"
    assert edge_cache.kv_reads == 1


@pytest.mark.asyncio
async def test_edge_invalidate_calls_worker_delete(edge_cache, monkeypatch):
    """``invalidate`` must drop both the LRU entry AND tell the worker
    to delete its KV mirror so admin writes propagate immediately."""
    import httpx
    _CapturingHttpx.responses["DELETE"] = _FakeResp(200, {"ok": True})
    monkeypatch.setattr(httpx, "AsyncClient", _CapturingHttpx)

    await edge_cache.set("edu_allowlist", ["a", "b"], ttl_s=300)
    _CapturingHttpx.calls.clear()  # ignore the PUT from the set above

    await edge_cache.invalidate("edu_allowlist")
    assert edge_cache.get_local("edu_allowlist") is None
    assert len(_CapturingHttpx.calls) == 1
    call = _CapturingHttpx.calls[0]
    assert call["method"] == "DELETE"
    assert call["url"] == "https://edge.example.com/api/edge/kv-cache/edu_allowlist"
    assert call["headers"].get("X-Edge-Admin-Secret") == "shared-secret"


@pytest.mark.asyncio
async def test_edge_get_swallows_404_and_records_no_failure(edge_cache, monkeypatch):
    """A 404 from the worker (cold KV namespace) is a normal miss —
    the cache must return ``None`` without bumping ``kv_failures``."""
    import httpx
    _CapturingHttpx.responses["GET"] = _FakeResp(404, {})
    monkeypatch.setattr(httpx, "AsyncClient", _CapturingHttpx)

    assert await edge_cache.get("missing") is None
    assert edge_cache.kv_failures == 0
    assert edge_cache.kv_reads == 1
