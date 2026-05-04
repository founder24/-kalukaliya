"""Tests for SEO Phase C — Google Indexing API client + sitemap-ping wiring.

Covers the pure-helper behavior of `google_indexing_client` (quota cap,
stats counters, service-account loading, disabled/missing-secret graceful
paths), the `_ping_google_sitemap` wrapper in routes.bot_discovery, and
the end-to-end hook that `diff_sitemap_against_submitted` fires a sitemap
ping whenever there is at least one queued URL.

All outbound HTTP is mocked via httpx.AsyncClient monkeypatches — no real
network traffic.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from tests._deps_stub import install_deps_stub  # noqa: E402

install_deps_stub()


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Minimal fake service-account JSON (valid structure, irrelevant key).
# We monkeypatch _mint_access_token so the fake private_key is never parsed.
# ---------------------------------------------------------------------------

_FAKE_SA = {
    "type": "service_account",
    "client_email": "syrabit-indexing@example.iam.gserviceaccount.com",
    "private_key": "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n",
    "token_uri": "https://oauth2.googleapis.com/token",
}


def _fresh_client(monkeypatch, *, enabled=True, limit=None, sa_json=None,
                  token="fake-token"):
    import google_indexing_client as gic

    # Reset all module-level state so tests don't leak into each other.
    gic._reset_state_for_tests()

    if enabled:
        monkeypatch.setenv("GOOGLE_INDEXING_ENABLED", "true")
    else:
        monkeypatch.setenv("GOOGLE_INDEXING_ENABLED", "false")

    if limit is not None:
        monkeypatch.setenv("GOOGLE_INDEXING_DAILY_LIMIT", str(limit))
    else:
        monkeypatch.delenv("GOOGLE_INDEXING_DAILY_LIMIT", raising=False)

    if sa_json is None:
        monkeypatch.setenv("GOOGLE_INDEXING_SERVICE_ACCOUNT", json.dumps(_FAKE_SA))
    elif sa_json == "__missing__":
        monkeypatch.delenv("GOOGLE_INDEXING_SERVICE_ACCOUNT", raising=False)
    else:
        monkeypatch.setenv("GOOGLE_INDEXING_SERVICE_ACCOUNT", sa_json)

    # Short-circuit the real JWT+OAuth exchange so we never touch
    # google-auth's crypto stack in tests.
    async def _fake_mint():
        return token

    monkeypatch.setattr(gic, "_mint_access_token", _fake_mint)
    return gic


class _FakeResponse:
    def __init__(self, status_code=200, text="", json_body=None):
        self.status_code = status_code
        self.text = text
        self._json = json_body

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


class _FakeAsyncClient:
    """Drop-in for httpx.AsyncClient used as `async with` context manager."""

    def __init__(self, *, post_response=None, get_response=None,
                 post_side_effect=None, get_side_effect=None):
        self._post_response = post_response
        self._get_response = get_response
        self._post_side_effect = post_side_effect
        self._get_side_effect = get_side_effect
        self.post_calls = []
        self.get_calls = []

    def __call__(self, *a, **kw):  # support both patterns
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        if self._post_side_effect is not None:
            raise self._post_side_effect
        return self._post_response or _FakeResponse(200, "{}")

    async def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        if self._get_side_effect is not None:
            raise self._get_side_effect
        return self._get_response or _FakeResponse(200, "OK")


def _install_httpx(monkeypatch, gic, fake_client: _FakeAsyncClient):
    """Patch httpx.AsyncClient inside the client module to return fake_client."""
    class _Factory:
        def __init__(self, *a, **kw):
            pass
        async def __aenter__(self_inner):
            return fake_client
        async def __aexit__(self_inner, *a):
            return False

    monkeypatch.setattr(gic.httpx, "AsyncClient", _Factory)


# ---------------------------------------------------------------------------
# Service-account loading
# ---------------------------------------------------------------------------

def test_missing_secret_disables_indexing_but_not_ping(monkeypatch):
    gic = _fresh_client(monkeypatch, sa_json="__missing__")
    res = _run(gic.notify_url_updated("https://syrabit.ai/foo"))
    assert res["status"] == "skipped"
    assert res["reason"] == "no_service_account"
    stats = gic.get_stats()
    assert stats["service_account_loaded"] is False
    assert stats["service_account_error"] == "missing_secret"
    assert stats["sent"] == 0


def test_base64_encoded_secret_is_decoded(monkeypatch):
    b64 = base64.b64encode(json.dumps(_FAKE_SA).encode()).decode()
    gic = _fresh_client(monkeypatch, sa_json=b64)
    info = gic._load_service_account()
    assert info is not None
    assert info["client_email"] == _FAKE_SA["client_email"]


def test_malformed_secret_logged_and_skipped(monkeypatch):
    gic = _fresh_client(monkeypatch, sa_json="not-json-and-not-b64!!!")
    info = gic._load_service_account()
    assert info is None
    stats = gic.get_stats()
    assert stats["service_account_loaded"] is False


def test_service_account_missing_required_field(monkeypatch):
    bad = dict(_FAKE_SA)
    del bad["client_email"]
    gic = _fresh_client(monkeypatch, sa_json=json.dumps(bad))
    info = gic._load_service_account()
    assert info is None
    stats = gic.get_stats()
    assert "missing_fields" in (stats["service_account_error"] or "")


# ---------------------------------------------------------------------------
# notify_url_updated happy path + quota + errors
# ---------------------------------------------------------------------------

def test_notify_url_updated_happy_path(monkeypatch):
    gic = _fresh_client(monkeypatch)
    fake = _FakeAsyncClient(post_response=_FakeResponse(200, "{}"))
    _install_httpx(monkeypatch, gic, fake)

    res = _run(gic.notify_url_updated("https://syrabit.ai/ahsec/class-12/physics"))
    assert res["status"] == "ok"
    assert res["http_status"] == 200
    # Verified the right endpoint + payload shape.
    assert len(fake.post_calls) == 1
    url, kwargs = fake.post_calls[0]
    assert url == gic.INDEXING_API_URL
    assert kwargs["json"]["type"] == "URL_UPDATED"
    assert kwargs["json"]["url"].endswith("/physics")
    assert kwargs["headers"]["Authorization"].startswith("Bearer ")

    stats = gic.get_stats()
    assert stats["sent"] == 1
    assert stats["status_2xx"] == 1


def test_daily_quota_stops_further_sends(monkeypatch):
    gic = _fresh_client(monkeypatch, limit=2)
    fake = _FakeAsyncClient(post_response=_FakeResponse(200, "{}"))
    _install_httpx(monkeypatch, gic, fake)

    # Pass tier=1 explicitly so the per-tier sub-cap (Task #246) does not
    # gate these calls before the global daily limit is reached. The bare
    # 1-segment URLs below would otherwise classify as Tier-3, which is
    # capped at 25% of the daily limit (= 1 with limit=2).
    r1 = _run(gic.notify_url_updated("https://syrabit.ai/a", tier=1))
    r2 = _run(gic.notify_url_updated("https://syrabit.ai/b", tier=1))
    r3 = _run(gic.notify_url_updated("https://syrabit.ai/c", tier=1))

    assert r1["status"] == "ok"
    assert r2["status"] == "ok"
    assert r3["status"] == "quota_blocked"
    assert len(fake.post_calls) == 2  # third never hit the network
    stats = gic.get_stats()
    assert stats["sent"] == 2
    assert stats["quota_blocks"] == 1
    assert stats["quota_remaining"] == 0


def test_notify_url_updated_429_records_quota_error(monkeypatch):
    gic = _fresh_client(monkeypatch)
    fake = _FakeAsyncClient(post_response=_FakeResponse(429, "Too Many"))
    _install_httpx(monkeypatch, gic, fake)
    res = _run(gic.notify_url_updated("https://syrabit.ai/x"))
    assert res["status"] == "quota_error"
    stats = gic.get_stats()
    assert stats["status_4xx"] == 1


def test_notify_url_updated_auth_error_clears_token_cache(monkeypatch):
    gic = _fresh_client(monkeypatch)
    fake = _FakeAsyncClient(post_response=_FakeResponse(401, "invalid token"))
    _install_httpx(monkeypatch, gic, fake)
    # Pre-seed the token cache so the 401 handler actually clears something.
    _run(gic._get_cached_token())
    assert gic._cached_token is not None

    res = _run(gic.notify_url_updated("https://syrabit.ai/y"))
    assert res["status"] == "auth_error"
    assert gic._cached_token_expires_at == 0.0
    stats = gic.get_stats()
    assert stats["status_4xx"] == 1


def test_notify_url_updated_network_error_never_raises(monkeypatch):
    gic = _fresh_client(monkeypatch)
    fake = _FakeAsyncClient(post_side_effect=RuntimeError("network down"))
    _install_httpx(monkeypatch, gic, fake)
    res = _run(gic.notify_url_updated("https://syrabit.ai/z"))
    assert res["status"] == "error"
    assert res["reason"] == "RuntimeError"
    stats = gic.get_stats()
    assert stats["errors"] == 1


def test_notify_url_updated_disabled_killswitch(monkeypatch):
    gic = _fresh_client(monkeypatch, enabled=False)
    res = _run(gic.notify_url_updated("https://syrabit.ai/a"))
    assert res["status"] == "skipped"
    assert res["reason"] == "disabled"


def test_notify_url_updated_empty_url(monkeypatch):
    gic = _fresh_client(monkeypatch)
    res = _run(gic.notify_url_updated(""))
    assert res["status"] == "skipped"
    assert res["reason"] == "empty_url"


# ---------------------------------------------------------------------------
# ping_sitemap
# ---------------------------------------------------------------------------

def test_ping_sitemap_happy_path(monkeypatch):
    gic = _fresh_client(monkeypatch)
    fake = _FakeAsyncClient(get_response=_FakeResponse(200, "OK"))
    _install_httpx(monkeypatch, gic, fake)
    res = _run(gic.ping_sitemap("https://syrabit.ai/sitemap-index.xml"))
    assert res["status"] == "ok"
    assert len(fake.get_calls) == 1
    ping_url, _ = fake.get_calls[0]
    # URL-encoded sitemap param.
    assert ping_url.startswith("https://www.google.com/ping?sitemap=")
    assert "sitemap-index.xml" in ping_url
    stats = gic.get_stats()
    assert stats["sitemap_ping_sent"] == 1
    assert stats["sitemap_ping_2xx"] == 1


def test_ping_sitemap_network_error(monkeypatch):
    gic = _fresh_client(monkeypatch)
    fake = _FakeAsyncClient(get_side_effect=RuntimeError("boom"))
    _install_httpx(monkeypatch, gic, fake)
    res = _run(gic.ping_sitemap())
    assert res["status"] == "error"
    stats = gic.get_stats()
    assert stats["sitemap_ping_errors"] == 1


def test_ping_sitemap_disabled_skips_without_calling(monkeypatch):
    gic = _fresh_client(monkeypatch, enabled=False)
    fake = _FakeAsyncClient(get_response=_FakeResponse(200, "OK"))
    _install_httpx(monkeypatch, gic, fake)
    res = _run(gic.ping_sitemap())
    assert res["status"] == "skipped"
    assert res["reason"] == "disabled"
    assert len(fake.get_calls) == 0


# ---------------------------------------------------------------------------
# token caching
# ---------------------------------------------------------------------------

def test_token_is_cached_across_calls(monkeypatch):
    gic = _fresh_client(monkeypatch)
    fake = _FakeAsyncClient(post_response=_FakeResponse(200, "{}"))
    _install_httpx(monkeypatch, gic, fake)

    call_count = {"n": 0}

    async def _fake_mint_counting():
        call_count["n"] += 1
        return "t"

    monkeypatch.setattr(gic, "_mint_access_token", _fake_mint_counting)
    _run(gic.notify_url_updated("https://syrabit.ai/a"))
    _run(gic.notify_url_updated("https://syrabit.ai/b"))
    assert call_count["n"] == 1  # minted once, reused for 2nd POST


def test_token_refresh_is_single_flight(monkeypatch):
    """Five coroutines race to get a token from an empty cache. The
    single-flight lock must ensure only one mint call actually happens;
    the other four should wait and reuse the freshly-cached token."""
    gic = _fresh_client(monkeypatch)

    call_count = {"n": 0}
    started = asyncio.Event()

    async def _slow_mint():
        call_count["n"] += 1
        started.set()
        # Simulate a slow token-endpoint round-trip; other coroutines will
        # pile up on the refresh lock during this sleep.
        await asyncio.sleep(0.05)
        return "single-flight-token"

    monkeypatch.setattr(gic, "_mint_access_token", _slow_mint)

    async def _drive():
        # Reset the refresh lock so it binds to THIS event loop.
        gic._token_refresh_lock = None
        tokens = await asyncio.gather(*[gic._get_cached_token() for _ in range(5)])
        return tokens

    tokens = _run(_drive())
    assert all(t == "single-flight-token" for t in tokens)
    assert call_count["n"] == 1


# ---------------------------------------------------------------------------
# _ping_google_sitemap wrapper in routes.bot_discovery
# ---------------------------------------------------------------------------

def test_bot_discovery_ping_helper_delegates(monkeypatch):
    gic = _fresh_client(monkeypatch)
    fake = _FakeAsyncClient(get_response=_FakeResponse(200, "OK"))
    _install_httpx(monkeypatch, gic, fake)
    import routes.bot_discovery as bd
    res = _run(bd._ping_google_sitemap())
    assert res["status"] == "ok"
    assert len(fake.get_calls) == 1


# ---------------------------------------------------------------------------
# diff_sitemap_against_submitted now also fires the Google sitemap ping
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def __aiter__(self):
        async def _gen():
            for d in self._docs:
                yield d
        return _gen()

    async def to_list(self, length=None):
        return list(self._docs)[:length] if length else list(self._docs)


class _FakeCollection:
    def __init__(self, docs):
        self._docs = list(docs)

    def find(self, *_a, **_kw):
        return _FakeCursor(self._docs)


def test_diff_fires_google_sitemap_ping_when_changes_detected(monkeypatch):
    """End-to-end: a new URL in the sitemap causes the diff loop to call
    `_ping_google_sitemap` exactly once, and the summary captures the
    ping status."""
    import routes.bot_discovery as bd
    import deps as deps_mod

    fake_db = MagicMock()
    fake_db.subjects = _FakeCollection([])
    fake_db.chapters = _FakeCollection([])
    fake_db.streams = _FakeCollection([])
    fake_db.classes = _FakeCollection([])
    fake_db.boards = _FakeCollection([])
    fake_db.seo_pages = _FakeCollection([])
    fake_db.cms_documents = _FakeCollection([])
    fake_db.indexnow_submitted_urls = _FakeCollection([])
    fake_db.indexnow_sitemap_diff_log = MagicMock()
    fake_db.indexnow_sitemap_diff_log.insert_one = AsyncMock()

    async def _avail():
        return True

    monkeypatch.setattr(deps_mod, "db", fake_db, raising=False)
    monkeypatch.setattr(deps_mod, "is_mongo_available", _avail, raising=False)

    fresh_url = f"{bd.BASE_URL}/ahsec/class-12/physics/new-chapter"

    async def _fake_sitemap():
        return [fresh_url]

    monkeypatch.setattr(bd, "_collect_current_sitemap_urls", _fake_sitemap)

    bd.indexnow_batcher = MagicMock()
    bd.indexnow_batcher.queue = AsyncMock()
    bd.indexnow_batcher.flush_force = AsyncMock()

    pings: list = []

    async def _fake_ping(*a, **kw):
        pings.append((a, kw))
        return {"status": "ok", "http_status": 200}

    monkeypatch.setattr(bd, "_ping_google_sitemap", _fake_ping)

    summary = _run(bd.diff_sitemap_against_submitted(source="test"))

    assert summary["new_queued"] == 1
    assert summary["google_sitemap_ping"] == "ok"
    assert len(pings) == 1


def test_diff_skips_google_sitemap_ping_when_no_changes(monkeypatch):
    """If nothing is queued (every URL already submitted AND unedited),
    skip the ping — no point nudging Google to re-fetch an unchanged
    sitemap."""
    import routes.bot_discovery as bd
    import deps as deps_mod

    fake_db = MagicMock()
    for name in ("subjects", "chapters", "streams", "classes", "boards",
                 "seo_pages", "cms_documents"):
        setattr(fake_db, name, _FakeCollection([]))
    fake_db.indexnow_submitted_urls = _FakeCollection([])
    fake_db.indexnow_sitemap_diff_log = MagicMock()
    fake_db.indexnow_sitemap_diff_log.insert_one = AsyncMock()

    async def _avail():
        return True

    monkeypatch.setattr(deps_mod, "db", fake_db, raising=False)
    monkeypatch.setattr(deps_mod, "is_mongo_available", _avail, raising=False)

    # Empty sitemap → no candidates → early-return before queue, and the
    # ping branch is only hit after `to_queue` is populated.
    async def _empty_sitemap():
        return []

    monkeypatch.setattr(bd, "_collect_current_sitemap_urls", _empty_sitemap)

    pings: list = []

    async def _fake_ping(*a, **kw):
        pings.append((a, kw))
        return {"status": "ok"}

    monkeypatch.setattr(bd, "_ping_google_sitemap", _fake_ping)

    summary = _run(bd.diff_sitemap_against_submitted(source="test"))
    assert summary["new_queued"] == 0
    # Empty sitemap returns early — ping never attempted.
    assert len(pings) == 0


# ---------------------------------------------------------------------------
# seo_fanout now runs the Google Indexing hook as a 4th gathered signal
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Persistence — Task #327: counters survive a restart
# ---------------------------------------------------------------------------

class _FakeMongoCollection:
    """Minimal stand-in for a motor collection exposing `update_one` and
    `find_one`. Supports $inc (primary write path), $set, $setOnInsert,
    plus $max (kept only so older helper tests keep working). Production
    code only issues $inc deltas — see `_flush_to_store`."""

    def __init__(self):
        self.docs: dict = {}  # day -> doc
        self.update_calls: list = []

    async def update_one(self, filt, update, upsert=False):
        self.update_calls.append((filt, update, upsert))
        day = filt["day"]
        new_doc = day not in self.docs
        current = self.docs.get(day, {})
        if "$inc" in update:
            for k, v in update["$inc"].items():
                current[k] = current.get(k, 0) + v
        if "$max" in update:
            for k, v in update["$max"].items():
                current[k] = max(current.get(k, 0), v)
        if "$set" in update:
            current.update(update["$set"])
        if "$setOnInsert" in update and new_doc:
            current.update(update["$setOnInsert"])
        self.docs[day] = current
        return MagicMock()

    async def find_one(self, filt, *_a, **_kw):
        day = filt["day"]
        doc = self.docs.get(day)
        if doc is None:
            return None
        return dict(doc)


def _install_fake_store(monkeypatch, gic, store_docs=None):
    """Wire a fake `deps.db.google_indexing_daily` that tests can observe.
    Also flip the persist-in-tests switch on so _schedule_flush fires."""
    import deps as deps_mod

    fake_coll = _FakeMongoCollection()
    if store_docs:
        fake_coll.docs.update(store_docs)

    fake_db = MagicMock()

    def _getitem(_self, name):
        if name == gic._STORE_COLLECTION:
            return fake_coll
        return MagicMock()

    class _FakeDb:
        google_indexing_daily = fake_coll

        def __getitem__(self, name):
            if name == gic._STORE_COLLECTION:
                return fake_coll
            return MagicMock()

    async def _avail():
        return True

    monkeypatch.setattr(deps_mod, "db", _FakeDb(), raising=False)
    monkeypatch.setattr(deps_mod, "is_mongo_available", _avail, raising=False)
    monkeypatch.setenv("GOOGLE_INDEXING_PERSIST_IN_TESTS", "1")
    return fake_coll


def test_counters_flush_to_mongo_on_bump(monkeypatch):
    gic = _fresh_client(monkeypatch)
    store = _install_fake_store(monkeypatch, gic)
    fake = _FakeAsyncClient(post_response=_FakeResponse(200, "{}"))
    _install_httpx(monkeypatch, gic, fake)

    async def _drive():
        await gic.notify_url_updated("https://syrabit.ai/a")
        # Allow _schedule_flush's create_task to actually run.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    _run(_drive())

    today = gic._today_key()
    assert today in store.docs
    doc = store.docs[today]
    assert doc["sent"] >= 1
    assert doc["status_2xx"] >= 1
    assert doc["day"] == today


def test_counters_hydrate_from_mongo_on_first_call(monkeypatch):
    """Simulate a restart: a previous process left counters in Mongo. When
    the first notify_url_updated runs on the new process it must hydrate
    from the store so the 200/day cap isn't reset to zero."""
    gic = _fresh_client(monkeypatch, limit=5)
    today = gic._today_key()
    # Previous process already sent 4 submissions today.
    store = _install_fake_store(monkeypatch, gic, store_docs={
        today: {
            "day": today, "sent": 4, "status_2xx": 4,
            "status_4xx": 0, "status_5xx": 0, "errors": 0,
            "quota_blocks": 0, "skipped_disabled": 0,
            "sitemap_ping_sent": 0, "sitemap_ping_2xx": 0,
            "sitemap_ping_errors": 0,
        },
    })
    fake = _FakeAsyncClient(post_response=_FakeResponse(200, "{}"))
    _install_httpx(monkeypatch, gic, fake)

    async def _drive():
        # First call — hydrates, then sends (bumping sent 4→5).
        # tier=1 bypasses the per-tier sub-cap (Task #246) so the assertion
        # exercises the global daily-limit cap rather than tier-3 throttling.
        r1 = await gic.notify_url_updated("https://syrabit.ai/a", tier=1)
        # Second call — cap of 5 reached after first send, must block.
        r2 = await gic.notify_url_updated("https://syrabit.ai/b", tier=1)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return r1, r2

    r1, r2 = _run(_drive())
    assert r1["status"] == "ok"
    assert r2["status"] == "quota_blocked", (
        "restart must not reset the counter to zero"
    )
    stats = gic.get_stats()
    assert stats["sent"] == 5
    assert stats["quota_blocks"] == 1


def test_multi_worker_flushes_aggregate_via_inc(monkeypatch):
    """Two workers each send their own deltas; the stored total must
    equal the SUM of both workers' work, not the max of either. This is
    the whole reason we use $inc-on-delta instead of $max."""
    gic = _fresh_client(monkeypatch)
    store = _install_fake_store(monkeypatch, gic)

    async def _drive():
        # Worker A: simulate 10 locally-bumped sends.
        with gic._stats_lock:
            gic._stats["sent"] = 10
        await gic._flush_to_store()
        # Clear worker-A's baseline and switch to worker-B simulation by
        # resetting in-memory counters (emulates a fresh process).
        gic._reset_state_for_tests()
        monkeypatch.setenv("GOOGLE_INDEXING_PERSIST_IN_TESTS", "1")
        # Worker B needs to know the shared baseline is already 10, then
        # add its own 3 sends on top.
        await gic._ensure_loaded()
        with gic._stats_lock:
            gic._stats["sent"] = gic._stats.get("sent", 0) + 3
        await gic._flush_to_store()

    _run(_drive())
    today = gic._today_key()
    assert store.docs[today]["sent"] == 13, (
        "aggregate must be 10 (worker A) + 3 (worker B) = 13"
    )


def test_hydrate_after_restart_does_not_double_count(monkeypatch):
    """A worker that hydrates `sent=5` and then immediately flushes must
    NOT re-send those 5 as a fresh $inc. The baseline must be seeded on
    hydrate so the first post-hydrate flush sends a zero delta."""
    gic = _fresh_client(monkeypatch)
    today = gic._today_key()
    store = _install_fake_store(monkeypatch, gic, store_docs={
        today: {"day": today, "sent": 5, "status_2xx": 5,
                "status_4xx": 0, "status_5xx": 0, "errors": 0,
                "quota_blocks": 0, "skipped_disabled": 0,
                "sitemap_ping_sent": 0, "sitemap_ping_2xx": 0,
                "sitemap_ping_errors": 0},
    })

    async def _drive():
        await gic._ensure_loaded()
        await gic._flush_to_store()  # should be a no-op (zero delta)

    _run(_drive())
    # Still just 5 — hydrate+flush cycle must not inflate the number.
    assert store.docs[today]["sent"] == 5


def test_flush_rolls_back_baseline_on_mongo_error(monkeypatch):
    """If the Mongo write raises, the delta must stay un-committed so the
    next flush retries it."""
    gic = _fresh_client(monkeypatch)
    store = _install_fake_store(monkeypatch, gic)

    async def _failing_update(*a, **kw):
        raise RuntimeError("mongo is sulking")

    async def _drive():
        with gic._stats_lock:
            gic._stats["sent"] = 7
        # Swap in a failing update_one just for this flush.
        good = store.update_one
        store.update_one = _failing_update
        await gic._flush_to_store()
        store.update_one = good
        # Retry — baseline should have been rolled back, so the delta is
        # still 7 and the store gets the full 7.
        await gic._flush_to_store()

    _run(_drive())
    today = gic._today_key()
    assert store.docs[today]["sent"] == 7


def test_concurrent_first_use_single_flights_hydrate(monkeypatch):
    """Two coroutines calling notify_url_updated concurrently on a fresh
    process must both wait for the same hydrate, not race past it using
    zeroed counters. We prove this by seeding the store with sent=199
    (cap=200) and asserting only ONE of two concurrent submissions
    succeeds — if the race existed, both would see sent=0 and both would
    succeed."""
    gic = _fresh_client(monkeypatch, limit=200)
    today = gic._today_key()
    _install_fake_store(monkeypatch, gic, store_docs={
        today: {"day": today, "sent": 199, "status_2xx": 199,
                "status_4xx": 0, "status_5xx": 0, "errors": 0,
                "quota_blocks": 0, "skipped_disabled": 0,
                "sitemap_ping_sent": 0, "sitemap_ping_2xx": 0,
                "sitemap_ping_errors": 0},
    })
    fake = _FakeAsyncClient(post_response=_FakeResponse(200, "{}"))
    _install_httpx(monkeypatch, gic, fake)

    async def _drive():
        # Issue two concurrent requests BEFORE any hydrate has happened.
        r1, r2 = await asyncio.gather(
            gic.notify_url_updated("https://syrabit.ai/a"),
            gic.notify_url_updated("https://syrabit.ai/b"),
        )
        return r1, r2

    r1, r2 = _run(_drive())
    statuses = sorted([r1["status"], r2["status"]])
    assert statuses == ["ok", "quota_blocked"], (
        f"exactly one must succeed (cap=200, stored=199), got {statuses}"
    )


def test_load_failure_does_not_mark_day_loaded(monkeypatch):
    """A transient Mongo error on first hydrate must NOT permanently
    strand the worker — the next call must retry."""
    import deps as deps_mod
    gic = _fresh_client(monkeypatch)

    fails = {"n": 0}

    async def _avail_fail_then_ok():
        fails["n"] += 1
        return fails["n"] > 1  # first call fails, second succeeds

    class _FakeDb:
        def __getitem__(self, name):
            coll = _FakeMongoCollection()
            coll.docs[gic._today_key()] = {
                "day": gic._today_key(), "sent": 42, "status_2xx": 42,
                "status_4xx": 0, "status_5xx": 0, "errors": 0,
                "quota_blocks": 0, "skipped_disabled": 0,
                "sitemap_ping_sent": 0, "sitemap_ping_2xx": 0,
                "sitemap_ping_errors": 0,
            }
            return coll

    monkeypatch.setattr(deps_mod, "db", _FakeDb(), raising=False)
    monkeypatch.setattr(deps_mod, "is_mongo_available",
                        _avail_fail_then_ok, raising=False)
    monkeypatch.setenv("GOOGLE_INDEXING_PERSIST_IN_TESTS", "1")

    async def _drive():
        await gic._ensure_loaded()  # fails — day not marked
        assert gic._today_key() not in gic._loaded_days
        await gic._ensure_loaded()  # retries — succeeds
        assert gic._today_key() in gic._loaded_days

    _run(_drive())


def test_get_stats_with_history_returns_yesterday(monkeypatch):
    from datetime import timedelta
    gic = _fresh_client(monkeypatch)
    today = gic._today_key()
    yesterday = (
        datetime.now(timezone.utc) - timedelta(days=1)
    ).strftime("%Y-%m-%d")
    _install_fake_store(monkeypatch, gic, store_docs={
        yesterday: {
            "day": yesterday, "sent": 42, "status_2xx": 40,
            "status_4xx": 2, "status_5xx": 0, "errors": 0,
            "quota_blocks": 0, "skipped_disabled": 0,
            "sitemap_ping_sent": 1, "sitemap_ping_2xx": 1,
            "sitemap_ping_errors": 0,
        },
    })
    snapshot = _run(gic.get_stats_with_history())
    assert snapshot["day"] == today
    assert snapshot["yesterday"] is not None
    assert snapshot["yesterday"]["day"] == yesterday
    assert snapshot["yesterday"]["sent"] == 42
    assert snapshot["yesterday"]["status_2xx"] == 40


def test_get_stats_with_history_returns_none_when_no_prior_day(monkeypatch):
    gic = _fresh_client(monkeypatch)
    _install_fake_store(monkeypatch, gic)  # empty store
    snapshot = _run(gic.get_stats_with_history())
    assert snapshot["yesterday"] is None


def test_hydrate_only_happens_once_per_day(monkeypatch):
    """Repeated calls within the same day must not re-hit Mongo."""
    gic = _fresh_client(monkeypatch)
    today = gic._today_key()
    store = _install_fake_store(monkeypatch, gic, store_docs={
        today: {"day": today, "sent": 2, "status_2xx": 2,
                "status_4xx": 0, "status_5xx": 0, "errors": 0,
                "quota_blocks": 0, "skipped_disabled": 0,
                "sitemap_ping_sent": 0, "sitemap_ping_2xx": 0,
                "sitemap_ping_errors": 0},
    })

    find_calls = {"n": 0}
    original_find_one = store.find_one

    async def _counting_find_one(*a, **kw):
        find_calls["n"] += 1
        return await original_find_one(*a, **kw)

    store.find_one = _counting_find_one

    async def _drive():
        await gic._ensure_loaded()
        await gic._ensure_loaded()
        await gic._ensure_loaded()

    _run(_drive())
    assert find_calls["n"] == 1
    assert gic._stats["sent"] == 2


def test_hydrate_takes_max_of_local_and_stored(monkeypatch):
    """If a worker has already bumped locally before hydrating (rare —
    only possible if load is deferred), the hydrate must not clobber the
    higher in-memory value."""
    gic = _fresh_client(monkeypatch)
    today = gic._today_key()
    _install_fake_store(monkeypatch, gic, store_docs={
        today: {"day": today, "sent": 3, "status_2xx": 3,
                "status_4xx": 0, "status_5xx": 0, "errors": 0,
                "quota_blocks": 0, "skipped_disabled": 0,
                "sitemap_ping_sent": 0, "sitemap_ping_2xx": 0,
                "sitemap_ping_errors": 0},
    })
    # Pre-bump in-memory so the local value is higher than Mongo.
    with gic._stats_lock:
        gic._stats["sent"] = 8

    _run(gic._ensure_loaded())
    assert gic._stats["sent"] == 8  # local max preserved


def test_flush_is_skipped_when_mongo_unavailable(monkeypatch):
    """Never raises back to content generator when Mongo is down."""
    import deps as deps_mod
    gic = _fresh_client(monkeypatch)
    monkeypatch.setenv("GOOGLE_INDEXING_PERSIST_IN_TESTS", "1")

    async def _unavail():
        return False

    monkeypatch.setattr(deps_mod, "is_mongo_available", _unavail, raising=False)
    # Should not raise.
    _run(gic._flush_to_store())


def test_day_rollover_clears_loaded_days(monkeypatch):
    """After UTC rollover, the first read must re-hydrate with the new
    day's stored counters (not the previous day's)."""
    gic = _fresh_client(monkeypatch)

    from datetime import timedelta

    # Pretend "today" is day-1.
    fixed_today = "2026-04-16"
    fixed_tomorrow = "2026-04-17"

    def _today_fixed():
        return _today_fixed.val

    _today_fixed.val = fixed_today
    monkeypatch.setattr(gic, "_today_key", _today_fixed)

    store = _install_fake_store(monkeypatch, gic, store_docs={
        fixed_today: {"day": fixed_today, "sent": 10, "status_2xx": 10,
                      "status_4xx": 0, "status_5xx": 0, "errors": 0,
                      "quota_blocks": 0, "skipped_disabled": 0,
                      "sitemap_ping_sent": 0, "sitemap_ping_2xx": 0,
                      "sitemap_ping_errors": 0},
        fixed_tomorrow: {"day": fixed_tomorrow, "sent": 1, "status_2xx": 1,
                         "status_4xx": 0, "status_5xx": 0, "errors": 0,
                         "quota_blocks": 0, "skipped_disabled": 0,
                         "sitemap_ping_sent": 0, "sitemap_ping_2xx": 0,
                         "sitemap_ping_errors": 0},
    })

    # Reset _stats to match fixed_today.
    with gic._stats_lock:
        gic._stats.clear()
        gic._stats.update(gic._fresh_stats())

    _run(gic._ensure_loaded())
    assert gic._stats["sent"] == 10

    # Simulate UTC rollover.
    _today_fixed.val = fixed_tomorrow
    _run(gic._ensure_loaded())
    # After rollover, the new day's counters are hydrated, not the old.
    assert gic._stats["day"] == fixed_tomorrow
    assert gic._stats["sent"] == 1


# ---------------------------------------------------------------------------
# Task #331 — Mongo-authoritative quota cap across workers
# ---------------------------------------------------------------------------

class _AtomicQuotaCollection:
    """Fake motor collection that honours `{sent: {$lt: limit}}` and
    `$setOnInsert` upsert semantics. Models real Mongo's per-document
    atomicity by serialising each await call through an asyncio lock,
    which is enough to prove the cap holds under concurrent workers."""

    def __init__(self):
        self.docs: dict = {}
        self._lock = asyncio.Lock()

    async def update_one(self, filt, update, upsert=False):
        async with self._lock:
            day = filt["day"]
            doc = self.docs.get(day)
            cond = filt.get("sent")
            # Evaluate the conditional `sent: {$lt: <limit>}` predicate.
            if isinstance(cond, dict) and "$lt" in cond:
                limit = cond["$lt"]
                if doc is None or doc.get("sent", 0) >= limit:
                    matched = 0
                else:
                    matched = 1
            else:
                matched = 1 if doc is not None else 0
            if matched == 1:
                if "$inc" in update:
                    for k, v in update["$inc"].items():
                        doc[k] = doc.get(k, 0) + v
                if "$set" in update:
                    doc.update(update["$set"])
                self.docs[day] = doc
            elif upsert and doc is None:
                new_doc = {"day": day}
                if "$setOnInsert" in update:
                    new_doc.update(update["$setOnInsert"])
                if "$set" in update:
                    new_doc.update(update["$set"])
                if "$inc" in update:
                    for k, v in update["$inc"].items():
                        new_doc[k] = new_doc.get(k, 0) + v
                self.docs[day] = new_doc
                matched = 1
            result = MagicMock()
            result.matched_count = matched
            result.modified_count = matched
            return result

    async def find_one(self, filt, *_a, **_kw):
        async with self._lock:
            day = filt["day"]
            doc = self.docs.get(day)
            return dict(doc) if doc else None


def _install_atomic_store(monkeypatch, gic):
    import deps as deps_mod
    coll = _AtomicQuotaCollection()

    class _FakeDb:
        def __getitem__(self, name):
            if name == gic._STORE_COLLECTION:
                return coll
            return MagicMock()

    async def _avail():
        return True

    monkeypatch.setattr(deps_mod, "db", _FakeDb(), raising=False)
    monkeypatch.setattr(deps_mod, "is_mongo_available", _avail, raising=False)
    monkeypatch.setenv("GOOGLE_INDEXING_PERSIST_IN_TESTS", "1")
    return coll


def test_db_authoritative_cap_holds_under_concurrent_load(monkeypatch):
    """Prove the Mongo-side cap holds when many concurrent reservations
    race against a shared store. With limit=200 and 250 concurrent
    `_reserve_quota_in_store` calls, exactly 200 must succeed and the
    stored `sent` must equal the cap — this is the cross-worker behavior
    Task #331 enforces."""
    gic = _fresh_client(monkeypatch, limit=200)
    store = _install_atomic_store(monkeypatch, gic)

    async def _drive():
        results = await asyncio.gather(*[
            gic._reserve_quota_in_store(200) for _ in range(250)
        ])
        return results

    results = _run(_drive())
    successes = sum(1 for r in results if r is True)
    failures = sum(1 for r in results if r is False)
    assert successes == 200, f"expected 200 reservations, got {successes}"
    assert failures == 50, f"expected 50 cap-blocked, got {failures}"
    today = gic._today_key()
    assert store.docs[today]["sent"] == 200, (
        "stored counter must be pinned at the cap, never above"
    )


def test_db_cap_blocks_even_when_local_mirror_is_stale(monkeypatch):
    """Simulate a 'second worker' scenario: local in-memory `_stats`
    shows sent=0 (fresh process), but the shared Mongo doc already has
    sent=200 because peer workers exhausted the day's budget. The next
    submission attempt MUST be quota-blocked even though the local
    fast-path would otherwise let it through."""
    gic = _fresh_client(monkeypatch, limit=200)
    today = gic._today_key()
    store = _install_atomic_store(monkeypatch, gic)
    store.docs[today] = {"day": today, "sent": 200, "status_2xx": 200}
    fake = _FakeAsyncClient(post_response=_FakeResponse(200, "{}"))
    _install_httpx(monkeypatch, gic, fake)

    # Skip hydrate so the local mirror stays at zero — this is the worst
    # case where DB-authoritative gating is the only thing protecting the
    # cap.
    gic._loaded_days.add(today)

    async def _drive():
        return await gic.notify_url_updated("https://syrabit.ai/peer-blocked")

    res = _run(_drive())
    assert res["status"] == "quota_blocked", (
        f"DB cap must override stale local mirror, got {res}"
    )
    # No outbound POST should have happened.
    assert len(fake.post_calls) == 0
    # Local mirror should be synced up to the cap so subsequent calls
    # short-circuit on the fast path.
    assert gic._stats["sent"] >= 200
    # Stored counter must NOT have been incremented past the cap.
    assert store.docs[today]["sent"] == 200


def test_successful_reserve_does_not_double_count_on_flush(monkeypatch):
    """A successful DB-side reservation already $inc'd `sent` directly.
    The next `_flush_to_store` MUST send a zero delta for `sent`,
    otherwise the same submission would be counted twice in the stored
    aggregate."""
    gic = _fresh_client(monkeypatch, limit=200)
    store = _install_atomic_store(monkeypatch, gic)
    fake = _FakeAsyncClient(post_response=_FakeResponse(200, "{}"))
    _install_httpx(monkeypatch, gic, fake)

    async def _drive():
        await gic.notify_url_updated("https://syrabit.ai/once")
        # Force flush to drain any pending counters.
        await gic._flush_to_store()
        await asyncio.sleep(0)
        await gic._flush_to_store()

    _run(_drive())
    today = gic._today_key()
    assert store.docs[today]["sent"] == 1, (
        "stored sent must equal exactly one — reserve already incremented it"
    )


def test_seo_fanout_includes_google_indexing_step(monkeypatch):
    import seo_fanout
    import google_indexing_client as gic
    gic._reset_state_for_tests()

    monkeypatch.setenv("SEO_FANOUT_ENABLED", "true")

    calls = {"indexing": 0, "indexnow": 0, "purge": 0, "prewarm": 0}

    async def _fake_indexnow(url, source):
        calls["indexnow"] += 1
        return True

    async def _fake_purge(url, parent, page_type):
        calls["purge"] += 1
        return True

    async def _fake_prewarm(urls):
        calls["prewarm"] += 1
        return True

    async def _fake_notify(url, source="x"):
        calls["indexing"] += 1
        return {"status": "ok"}

    monkeypatch.setattr(seo_fanout, "_do_indexnow", _fake_indexnow)
    monkeypatch.setattr(seo_fanout, "_do_cache_purge", _fake_purge)
    monkeypatch.setattr(seo_fanout, "_do_prewarm", _fake_prewarm)
    monkeypatch.setattr(gic, "notify_url_updated", _fake_notify)

    page = {
        "board_slug": "ahsec", "class_slug": "class-12",
        "subject_slug": "physics", "topic_slug": "kinematics",
        "page_type": "notes",
    }

    async def _drive():
        task = seo_fanout.fanout_for_page(page, source="unit-test")
        assert task is not None
        await task

    _run(_drive())

    assert calls["indexing"] == 1
    assert calls["indexnow"] == 1
    assert calls["purge"] == 1
    assert calls["prewarm"] == 1

    events = seo_fanout.recent_fanout_events(limit=1)
    assert events
    assert events[-1]["google_indexing"] == "ok"


# ---------------------------------------------------------------------------
# Quota-exhausted ops alert
# ---------------------------------------------------------------------------

class _FakeClaimsCollection:
    """In-memory stand-in for the cross-worker alert claims collection.
    Mirrors the real Mongo behavior: `insert_one` raises DuplicateKeyError
    when an `_id` is already present. Shared across workers in a test by
    passing the SAME instance into both `_install_alert_db` calls."""

    def __init__(self):
        self.docs: dict = {}

    async def insert_one(self, doc):
        from pymongo.errors import DuplicateKeyError
        _id = doc["_id"]
        if _id in self.docs:
            raise DuplicateKeyError(f"E11000 duplicate key: {_id}")
        self.docs[_id] = dict(doc)
        return MagicMock()


def _install_alert_db(monkeypatch, gic, claims_coll=None):
    """Wire a fake `deps.db` exposing the alert claims collection so the
    cross-worker dedupe path can run end-to-end. Pass an existing
    `claims_coll` to share the claim-set between simulated workers."""
    import deps as deps_mod
    if claims_coll is None:
        claims_coll = _FakeClaimsCollection()

    class _FakeDb:
        def __getitem__(self, name):
            if name == gic._QUOTA_ALERT_CLAIM_COLLECTION:
                return claims_coll
            return MagicMock()

    async def _avail():
        return True

    monkeypatch.setattr(deps_mod, "db", _FakeDb(), raising=False)
    monkeypatch.setattr(deps_mod, "is_mongo_available", _avail, raising=False)
    return claims_coll


def test_quota_exhausted_fires_ops_alert_once_per_day(monkeypatch):
    """Hitting the daily cap dispatches a single ops alert via the shared
    `metrics._dispatch_alert` pipeline. Subsequent quota_blocks on the
    same UTC day must NOT re-dispatch (alert is de-duped per day per
    process)."""
    gic = _fresh_client(monkeypatch, limit=1)
    fake = _FakeAsyncClient(post_response=_FakeResponse(200, "{}"))
    _install_httpx(monkeypatch, gic, fake)
    _install_alert_db(monkeypatch, gic)

    # Opt the alert path INTO this test (it's gated off under pytest).
    monkeypatch.setenv("GOOGLE_INDEXING_QUOTA_ALERT_IN_TESTS", "1")

    dispatched: list = []

    async def _fake_dispatch(alert_type, title, body, threshold_snapshot=None):
        dispatched.append({
            "alert_type": alert_type,
            "title": title,
            "body": body,
            "threshold_snapshot": threshold_snapshot or {},
        })

    import sys, types
    fake_metrics = types.ModuleType("metrics")
    fake_metrics._dispatch_alert = _fake_dispatch
    monkeypatch.setitem(sys.modules, "metrics", fake_metrics)

    async def _drive():
        # First call uses the only slot; second + third both quota_block.
        # tier=1 bypasses the per-tier sub-cap so the test exercises the
        # *global* limit-exhaustion path the alert pipeline cares about.
        await gic.notify_url_updated("https://syrabit.ai/a", tier=1)
        await gic.notify_url_updated("https://syrabit.ai/b", tier=1)
        await gic.notify_url_updated("https://syrabit.ai/c", tier=1)
        # Let the create_task'd alert dispatcher actually run.
        for _ in range(4):
            await asyncio.sleep(0)

    _run(_drive())

    assert len(dispatched) == 1, (
        "exactly one alert per UTC day even with multiple quota_blocks"
    )
    alert = dispatched[0]
    assert alert["alert_type"] == "google_indexing_quota_exhausted"
    snap = alert["threshold_snapshot"]
    assert snap["metric"] == "google_indexing_daily_limit"
    assert snap["value"] == 1           # daily_limit
    assert snap["actual"] == 1          # sent
    assert snap["day"] == gic._today_key()
    assert "/admin/seo/google-indexing-stats" in snap["dashboard_url"]
    # The body summarises sent/cap and links to the admin page.
    assert "1/1" in alert["body"]
    assert "/admin/seo/google-indexing-stats" in alert["body"]


def test_quota_alert_resets_in_process_dedupe_on_day_rollover(monkeypatch):
    """When the UTC day rolls over, the in-process dedupe sentinel
    `_quota_alert_fired_day` must be cleared so a fresh alert can be
    scheduled the next time quota is exhausted (cross-worker Mongo dedupe
    is keyed by date, so a new date is naturally a new claim slot)."""
    import google_indexing_client as gic
    gic._reset_state_for_tests()
    # Pretend yesterday's run already fired an alert.
    gic._quota_alert_fired_day = "1999-01-01"
    # Stats still reflect yesterday; calling _roll_day_if_needed must
    # detect the date change and clear the sentinel.
    with gic._stats_lock:
        gic._stats["day"] = "1999-01-01"
        gic._roll_day_if_needed()
    assert gic._quota_alert_fired_day == "", (
        "day rollover must clear the in-process quota-alert dedupe so the "
        "next exhaustion event can fire today's alert"
    )


def test_quota_alert_skipped_when_disabled_killswitch(monkeypatch):
    """The ops alert respects an explicit env killswitch so operators can
    silence the channel without disabling the underlying quota cap."""
    gic = _fresh_client(monkeypatch, limit=1)
    fake = _FakeAsyncClient(post_response=_FakeResponse(200, "{}"))
    _install_httpx(monkeypatch, gic, fake)
    _install_alert_db(monkeypatch, gic)
    monkeypatch.setenv("GOOGLE_INDEXING_QUOTA_ALERT_IN_TESTS", "1")
    monkeypatch.setenv("GOOGLE_INDEXING_QUOTA_ALERT_DISABLED", "true")

    dispatched: list = []

    async def _fake_dispatch(*a, **kw):
        dispatched.append(a)

    import sys, types
    fake_metrics = types.ModuleType("metrics")
    fake_metrics._dispatch_alert = _fake_dispatch
    monkeypatch.setitem(sys.modules, "metrics", fake_metrics)

    async def _drive():
        await gic.notify_url_updated("https://syrabit.ai/a")
        await gic.notify_url_updated("https://syrabit.ai/b")
        for _ in range(4):
            await asyncio.sleep(0)

    _run(_drive())
    assert dispatched == []


def test_quota_alert_dedupes_across_workers_via_mongo_claim(monkeypatch):
    """Two simulated worker processes both hit quota_blocks on the same
    UTC day. The Mongo-backed claim collection (atomic insert with
    duplicate-key contention) must guarantee only ONE worker's alert is
    actually dispatched, not one-per-worker."""
    import google_indexing_client as gic

    # Single shared claims collection mirroring a real Mongo instance.
    shared_claims = _FakeClaimsCollection()

    monkeypatch.setenv("GOOGLE_INDEXING_QUOTA_ALERT_IN_TESTS", "1")

    dispatched: list = []

    async def _fake_dispatch(alert_type, title, body, threshold_snapshot=None):
        dispatched.append(alert_type)

    import sys, types
    fake_metrics = types.ModuleType("metrics")
    fake_metrics._dispatch_alert = _fake_dispatch
    monkeypatch.setitem(sys.modules, "metrics", fake_metrics)

    async def _simulate_worker(label):
        # Each worker resets the in-process state to mimic a separate
        # Python process on a fresh import (so the in-process dedupe is
        # NOT what's protecting us — the Mongo claim is).
        gic._reset_state_for_tests()
        monkeypatch.setenv("GOOGLE_INDEXING_ENABLED", "true")
        monkeypatch.setenv("GOOGLE_INDEXING_DAILY_LIMIT", "1")
        monkeypatch.setenv("GOOGLE_INDEXING_SERVICE_ACCOUNT", json.dumps(_FAKE_SA))

        async def _fake_mint():
            return f"tok-{label}"
        monkeypatch.setattr(gic, "_mint_access_token", _fake_mint)
        fake_http = _FakeAsyncClient(post_response=_FakeResponse(200, "{}"))
        _install_httpx(monkeypatch, gic, fake_http)
        _install_alert_db(monkeypatch, gic, claims_coll=shared_claims)

        # tier=1 bypasses the per-tier sub-cap (Task #246) so the test
        # exercises the cross-worker dedupe path on a global-limit hit.
        await gic.notify_url_updated(f"https://syrabit.ai/{label}-a", tier=1)
        await gic.notify_url_updated(f"https://syrabit.ai/{label}-b", tier=1)  # blocks
        for _ in range(4):
            await asyncio.sleep(0)

    async def _drive():
        await _simulate_worker("w1")
        await _simulate_worker("w2")

    _run(_drive())

    assert len(dispatched) == 1, (
        f"cross-worker dedupe must yield exactly one dispatch, "
        f"got {len(dispatched)}"
    )
    today = gic._today_key()
    expected_id = f"{gic._QUOTA_ALERT_TYPE}:{today}"
    assert expected_id in shared_claims.docs


def test_quota_alert_bypasses_metrics_cooldown_at_day_boundary(monkeypatch):
    """Regression for the cooldown-at-midnight failure mode: if day N's
    quota alert fired moments before UTC midnight, day N+1's alert must
    still get through even though `metrics._dispatch_alert`'s 30-minute
    per-alert-type cooldown would normally suppress it. Our dispatcher
    pops the cooldown entry before sending, so a real "new day" event is
    never silently swallowed."""
    import google_indexing_client as gic
    gic._reset_state_for_tests()

    monkeypatch.setenv("GOOGLE_INDEXING_QUOTA_ALERT_IN_TESTS", "1")

    # Simulated metrics module that mimics the real cooldown gate using
    # `_alert_last_fired`. _dispatch_alert returns silently if the entry
    # is fresh — exactly the suppression failure mode the reviewer flagged.
    import sys, types, time as _time
    fake_metrics = types.ModuleType("metrics")
    fake_metrics._alert_last_fired = {}
    fake_metrics._ALERT_COOLDOWN_S = 1800  # 30 min, matches prod
    fake_metrics.dispatched: list = []  # type: ignore[attr-defined]

    async def _fake_dispatch(alert_type, title, body, threshold_snapshot=None):
        now = _time.time()
        if now - fake_metrics._alert_last_fired.get(alert_type, 0) < fake_metrics._ALERT_COOLDOWN_S:
            return
        fake_metrics._alert_last_fired[alert_type] = now
        fake_metrics.dispatched.append({
            "alert_type": alert_type,
            "day": (threshold_snapshot or {}).get("day"),
        })

    fake_metrics._dispatch_alert = _fake_dispatch
    monkeypatch.setitem(sys.modules, "metrics", fake_metrics)

    # Two distinct days, single shared claims collection.
    shared_claims = _FakeClaimsCollection()
    _install_alert_db(monkeypatch, gic, claims_coll=shared_claims)

    async def _drive():
        await gic._dispatch_quota_alert("2026-04-17", sent=200, limit=200)
        # Same wall-clock time (well within the metrics cooldown) but a
        # NEW UTC day → must still dispatch.
        await gic._dispatch_quota_alert("2026-04-18", sent=200, limit=200)

    _run(_drive())

    days_alerted = [d["day"] for d in fake_metrics.dispatched]
    assert days_alerted == ["2026-04-17", "2026-04-18"], (
        f"day-boundary alert must bypass the metrics cooldown; got {days_alerted}"
    )


def test_quota_alert_falls_back_when_mongo_unavailable(monkeypatch):
    """When the cross-worker claim collection is unreachable, prefer a
    duplicate alert over silently swallowing a real quota event."""
    gic = _fresh_client(monkeypatch, limit=1)
    fake = _FakeAsyncClient(post_response=_FakeResponse(200, "{}"))
    _install_httpx(monkeypatch, gic, fake)
    monkeypatch.setenv("GOOGLE_INDEXING_QUOTA_ALERT_IN_TESTS", "1")

    # Wire `is_mongo_available` to return False.
    import deps as deps_mod

    async def _avail_false():
        return False

    monkeypatch.setattr(deps_mod, "db", MagicMock(), raising=False)
    monkeypatch.setattr(deps_mod, "is_mongo_available", _avail_false, raising=False)

    dispatched: list = []

    async def _fake_dispatch(alert_type, title, body, threshold_snapshot=None):
        dispatched.append(alert_type)

    import sys, types
    fake_metrics = types.ModuleType("metrics")
    fake_metrics._dispatch_alert = _fake_dispatch
    monkeypatch.setitem(sys.modules, "metrics", fake_metrics)

    async def _drive():
        # tier=1 bypasses the per-tier sub-cap so the test exercises the
        # mongo-fallback path on a global-limit hit.
        await gic.notify_url_updated("https://syrabit.ai/a", tier=1)
        await gic.notify_url_updated("https://syrabit.ai/b", tier=1)
        for _ in range(4):
            await asyncio.sleep(0)

    _run(_drive())
    assert dispatched == ["google_indexing_quota_exhausted"]
