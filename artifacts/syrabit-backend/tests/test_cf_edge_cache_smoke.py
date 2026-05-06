"""Task #425 — tests for the CF_EDGE_CACHE write-through smoke endpoint.

Two layers:

1. ``POST /admin/cf-health/kv-smoke`` — must trigger a real round trip
   through the deployed edge worker (modelled here by a fake httpx that
   emulates ``dispatchKvCache``) and the resulting ``kv_writes`` /
   ``kv_reads`` deltas must be visible in a follow-up ``GET
   /admin/cf-health`` call. This is the contract the staging CI smoke
   in ``scripts/cf_edge_cache_smoke.py`` relies on.

2. ``POST /admin/cf-health/kv-smoke`` — when the edge mirror is not
   active (``CF_EDGE_CACHE_ON=0`` or no URL/secret) the endpoint must
   return 503 so the smoke fails loud rather than silently pretending
   the flip worked.
"""
from __future__ import annotations

import pytest


# ─────────────── Fake httpx that emulates the edge worker ───────────────
class _FakeKvWorker:
    """In-memory stand-in for ``dispatchKvCache`` in
    ``artifacts/syrabit/workers/edge-proxy/src/index.ts``.

    Every PUT stores the payload, every GET returns it (or 404 for a
    cold key), every DELETE drops it. Mirrors the request/response
    shape the Python ``KvCache`` client actually speaks so a divergence
    in the wire contract surfaces here.
    """

    store: dict[str, dict] = {}
    calls: list[dict] = []

    @classmethod
    def reset(cls) -> None:
        cls.store = {}
        cls.calls = []

    def __init__(self, *_, **__):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def put(self, url, json=None, headers=None):
        self.calls.append({"method": "PUT", "url": url, "json": json,
                           "headers": dict(headers or {})})
        # Worker requires the shared secret header.
        if (headers or {}).get("X-Edge-Admin-Secret") != "shared-secret":
            return _FakeResp(401)
        key = url.rsplit("/api/edge/kv-cache/", 1)[1]
        self.store[key] = json
        return _FakeResp(200, {"ok": True, "ttl_s": (json or {}).get("ttl_s")})

    async def get(self, url, headers=None):
        self.calls.append({"method": "GET", "url": url,
                           "headers": dict(headers or {})})
        if (headers or {}).get("X-Edge-Admin-Secret") != "shared-secret":
            return _FakeResp(401)
        key = url.rsplit("/api/edge/kv-cache/", 1)[1]
        entry = self.store.get(key)
        if entry is None:
            return _FakeResp(404, {})
        return _FakeResp(200, {"value": entry["value"],
                               "ttl_s": entry.get("ttl_s") or 60})

    async def delete(self, url, headers=None):
        self.calls.append({"method": "DELETE", "url": url,
                           "headers": dict(headers or {})})
        key = url.rsplit("/api/edge/kv-cache/", 1)[1]
        self.store.pop(key, None)
        return _FakeResp(200, {"ok": True})


class _FakeResp:
    def __init__(self, status_code: int, body: dict | None = None) -> None:
        self.status_code = status_code
        self._body = body or {}

    def json(self):
        return self._body


# ─────────────── Test client + edge-active fixture ───────────────
@pytest.fixture
def admin_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.admin_cf_health import router
    from auth_deps import get_admin_user

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_admin_user] = lambda: {
        "id": "admin-1", "email": "ops@syrabit.ai", "is_admin": True,
        "sub": "admin-1",
    }
    return TestClient(app)


@pytest.fixture
def edge_active_cache(monkeypatch):
    """Wire the process-wide ``KvCache`` singleton to a fake worker so
    the smoke endpoint exercises a deterministic in-memory edge."""
    import httpx
    import kv_cache as kv_cache_mod

    monkeypatch.setattr(kv_cache_mod, "CF_EDGE_CACHE_ON", True)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeKvWorker)

    kv_cache_mod.reset_default_for_tests()
    cache = kv_cache_mod.KvCache(
        max_entries=16,
        default_ttl_s=60,
        edge_url_getter=lambda: "https://edge.example.com",
        edge_secret_getter=lambda: "shared-secret",
    )
    monkeypatch.setattr(kv_cache_mod, "_default_cache", cache)
    _FakeKvWorker.reset()
    yield cache
    kv_cache_mod.reset_default_for_tests()


# ─────────────── /admin/cf-health/kv-smoke ───────────────
def test_kv_smoke_round_trip_advances_counters(admin_client, edge_active_cache):
    """End-to-end contract: a single POST must PUT + GET + DELETE
    against the worker AND the resulting kv_writes/kv_reads deltas must
    be visible to a subsequent GET /admin/cf-health."""
    pre = admin_client.get("/admin/cf-health").json()["kv_cache"]
    assert pre["kv_writes"] == 0
    assert pre["kv_reads"] == 0

    res = admin_client.post("/admin/cf-health/kv-smoke")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["round_trip_ok"] is True
    assert body["deltas"]["kv_writes"] >= 1
    assert body["deltas"]["kv_reads"] >= 1
    assert body["deltas"]["kv_failures"] == 0

    # The wire shape the staging script relies on must surface in
    # /admin/cf-health → kv_cache too.
    after = admin_client.get("/admin/cf-health").json()["kv_cache"]
    assert after["kv_writes"] - pre["kv_writes"] >= 1
    assert after["kv_reads"] - pre["kv_reads"] >= 1
    assert after["kv_failures"] == 0

    methods = [c["method"] for c in _FakeKvWorker.calls]
    assert "PUT" in methods
    assert "GET" in methods


def test_kv_smoke_uses_fresh_key_each_call(admin_client, edge_active_cache):
    """Two consecutive smokes must not collide on the same key — the
    follow-up GET in the second call would otherwise either short-
    circuit on the previous run's value or 404 from a stale delete."""
    a = admin_client.post("/admin/cf-health/kv-smoke").json()
    b = admin_client.post("/admin/cf-health/kv-smoke").json()
    assert a["key"] != b["key"]
    assert a["ok"] and b["ok"]


def test_kv_smoke_returns_503_when_edge_inactive(admin_client, monkeypatch):
    """When CF_EDGE_CACHE_ON is off (or URL/secret missing) the smoke
    must fail loud with 503 — silently returning 200/zero counters
    would mask the very regression the smoke exists to catch."""
    import kv_cache as kv_cache_mod

    monkeypatch.setattr(kv_cache_mod, "CF_EDGE_CACHE_ON", False)
    kv_cache_mod.reset_default_for_tests()
    cache = kv_cache_mod.KvCache(
        edge_url_getter=lambda: "",
        edge_secret_getter=lambda: "",
    )
    monkeypatch.setattr(kv_cache_mod, "_default_cache", cache)
    try:
        res = admin_client.post("/admin/cf-health/kv-smoke")
        assert res.status_code == 503
        assert "CF_EDGE_CACHE" in res.json()["detail"]
    finally:
        kv_cache_mod.reset_default_for_tests()


def test_kv_smoke_requires_admin():
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from routes.admin_cf_health import router
    from auth_deps import get_admin_user

    app = FastAPI()
    app.include_router(router)

    def _deny():
        raise HTTPException(status_code=401, detail="Not authenticated")

    app.dependency_overrides[get_admin_user] = _deny
    client = TestClient(app)
    res = client.post("/admin/cf-health/kv-smoke")
    assert res.status_code in (401, 403)


# ─────────────── KvCache.clear_local helper ───────────────
def test_clear_local_drops_lru_without_touching_worker(monkeypatch):
    """``clear_local`` is what makes the smoke's follow-up ``get``
    actually round-trip: a normal ``invalidate`` would also delete the
    KV side and the GET would just see a 404."""
    import httpx
    import kv_cache as kv_cache_mod

    monkeypatch.setattr(kv_cache_mod, "CF_EDGE_CACHE_ON", True)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeKvWorker)
    _FakeKvWorker.reset()

    cache = kv_cache_mod.KvCache(
        edge_url_getter=lambda: "https://edge.example.com",
        edge_secret_getter=lambda: "shared-secret",
    )

    import asyncio
    asyncio.get_event_loop().run_until_complete(
        cache.set("k", {"v": 1}, ttl_s=60))
    assert cache.get_local("k") == {"v": 1}
    assert cache.clear_local("k") is True
    # LRU dropped — but the worker's KV mirror still holds the value.
    assert cache.get_local("k") is None
    assert "k" in _FakeKvWorker.store
    # Second clear is a no-op (already gone).
    assert cache.clear_local("k") is False
