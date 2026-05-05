"""Task #798 — `chat.anon_quota_exhausted` metric coverage.

The per-device 30/day cap (Task #793 / #797) is the visible "wall" that
anonymous students hit before being asked to sign up. Without a metric
on how often the wall fires we can't tell whether 30 is the right cap;
without a join against subsequent sign-ups we can't tell whether the
wall is doing its job or just bouncing students off the site.

This file pins the wiring end-to-end:

* the metric fires from `auth_deps.rate_limit_chat_optional` exactly
  once per (device, day) — so a hammering script that retries a
  hundred times after the 429 doesn't inflate the unique-devices
  count we're going to base capacity decisions on;
* a brand-new account created within ~48h of the wall firing is
  attributed to "exhausted -> sign-up" via the device-cookie join;
* the admin chart endpoint surfaces the daily counts + conversion %;
* the one-shot Redis backfill replays today's already-at-cap counters
  so the chart isn't empty on first load.
"""
import asyncio
import importlib

import pytest
from fastapi import HTTPException
from starlette.responses import Response

from tests._deps_stub import install_deps_stub

install_deps_stub(force=True)

import deps  # noqa: E402
import db_ops  # noqa: E402
import auth_deps  # noqa: E402
import metrics  # noqa: E402
from device_token import mint_device_token, device_token_id  # noqa: E402

fakeredis = pytest.importorskip("fakeredis")


class _FakeReq:
    """Same minimal Request double the existing rate-limit tests use."""

    def __init__(self, ip: str = "", headers: dict | None = None):
        self.client = type("c", (), {"host": ip})()
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}


def _install_fake_redis(monkeypatch):
    """Wire fakeredis into deps + dependents and reset the per-test
    state on the metrics module so a previous test can't leak counts.

    The metric maintains in-memory window/dedupe/counter state at
    module scope (so workers don't lose data between requests). Each
    test gets a clean slate by zeroing them under the module's own
    lock.
    """
    fake = fakeredis.FakeStrictRedis(decode_responses=True)
    monkeypatch.setattr(auth_deps, "redis_client", fake, raising=False)
    monkeypatch.setattr(db_ops, "redis_client", fake, raising=False)
    monkeypatch.setattr(deps, "redis_client", fake, raising=False)
    # Disable the per-minute throttle so we exercise only the daily
    # quota path (the per-minute logic has its own test surface).
    monkeypatch.setattr(auth_deps, "check_rate_limit", lambda *a, **kw: True)
    # Task #407 — the per-minute throttle now flows through
    # ``do_chat.rate_check``; stub it so daily-quota tests can fire
    # the dependency many times in a tight loop without tripping the
    # in-process token bucket.
    import do_chat as _do_chat_mod
    async def _allow_all(*_a, **_kw):
        return True, 999
    monkeypatch.setattr(_do_chat_mod, "rate_check", _allow_all)

    # Reset metric state. Hold the lock so we don't race a snapshot
    # collector that might be live in the test interpreter.
    with metrics._anon_exhaust_lock:
        metrics._anon_exhaust_window.clear()
        metrics._anon_exhaust_seen.clear()
        metrics._anon_exhausted_devices.clear()
        metrics._anon_signup_after_exhaust_devices.clear()
        # Task #808 — clear the per-event ring buffer too so tests
        # don't see stale events from a previous test's recordings.
        metrics._anon_exhaust_recent.clear()
    # Task #809 — fakeredis instance is fresh per call, so the
    # per-day aggregate hashes are already empty. We don't need to
    # explicitly purge them; this comment exists so the next person
    # adding state doesn't wonder why there's no `agg_hash.clear()`
    # here.
    return fake


async def _call(req: _FakeReq, cookie: str | None = None) -> Response:
    resp = Response()
    await auth_deps.rate_limit_chat_optional(
        req, resp, user=None, syrabit_device=cookie,
    )
    return resp


# ─────────────────────────────────────────────────────────────────────
# Wall-firing metric
# ─────────────────────────────────────────────────────────────────────


def test_metric_fires_once_when_device_cap_is_hit(monkeypatch):
    """Drain a single device cookie's 30/day budget end-to-end and
    assert that the 31st request both 429s AND records exactly one
    `chat.anon_quota_exhausted` event for that device."""
    _install_fake_redis(monkeypatch)
    cookie = mint_device_token()
    token_id = device_token_id(cookie)
    req = _FakeReq(headers={"cf-connecting-ip": "192.0.2.50"})

    for _ in range(30):
        asyncio.run(_call(req, cookie=cookie))

    # 31st must 429 and record the metric.
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(_call(req, cookie=cookie))
    assert excinfo.value.status_code == 429

    stats = metrics.get_anon_quota_exhausted_stats(days=1)
    assert stats["total_exhausted"] == 1, stats
    assert stats["unique_devices_exhausted"] == 1, stats
    assert stats["has_data"] is True

    # Cross-worker tracking: the device hash should be in today's
    # Redis sorted set (TTL 48h) so a sign-up join from another
    # gunicorn worker can find it.
    fake = deps.redis_client
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert fake.zscore(f"chat:anon_exhausted_devices:{today}", token_id) is not None


def test_metric_dedupes_repeated_429s_from_same_device(monkeypatch):
    """The cap fires on every retry from an already-exhausted device.
    The metric must collapse those into a single event per device per
    day — otherwise a 100-rps panic-retry loop would dwarf the real
    "students who hit the wall" signal we want to chart.
    """
    _install_fake_redis(monkeypatch)
    cookie = mint_device_token()
    req = _FakeReq(headers={"cf-connecting-ip": "192.0.2.51"})

    for _ in range(30):
        asyncio.run(_call(req, cookie=cookie))

    # 10 hammered retries after the cap. All must 429; the metric
    # must record exactly one event total.
    for _ in range(10):
        with pytest.raises(HTTPException):
            asyncio.run(_call(req, cookie=cookie))

    stats = metrics.get_anon_quota_exhausted_stats(days=1)
    assert stats["total_exhausted"] == 1, stats
    assert stats["unique_devices_exhausted"] == 1, stats


def test_metric_counts_distinct_devices_separately(monkeypatch):
    """Two devices sharing a NAT each consume their own 30/day quota
    (Task #793). Each one hitting the wall must register as its own
    event in the metric — that's the per-device unique we want to
    track for capacity tuning."""
    _install_fake_redis(monkeypatch)
    cookie_a = mint_device_token()
    cookie_b = mint_device_token()
    req = _FakeReq(headers={"cf-connecting-ip": "192.0.2.52"})

    for cookie in (cookie_a, cookie_b):
        for _ in range(30):
            asyncio.run(_call(req, cookie=cookie))
        with pytest.raises(HTTPException):
            asyncio.run(_call(req, cookie=cookie))

    stats = metrics.get_anon_quota_exhausted_stats(days=1)
    assert stats["unique_devices_exhausted"] == 2, stats
    assert stats["total_exhausted"] == 2, stats


def test_metric_emits_label_breakdowns(monkeypatch):
    """The chart needs at least one populated by-hour and by-day-of-week
    bucket. The exact day/hour depends on the test wall-clock, but the
    sum across all buckets must equal `total_exhausted` so the chart
    components don't render empty bars."""
    _install_fake_redis(monkeypatch)
    cookie = mint_device_token()
    req = _FakeReq(headers={"cf-connecting-ip": "192.0.2.53"})

    for _ in range(30):
        asyncio.run(_call(req, cookie=cookie))
    with pytest.raises(HTTPException):
        asyncio.run(_call(req, cookie=cookie))

    stats = metrics.get_anon_quota_exhausted_stats(days=1)
    assert sum(stats["by_hour"].values()) == stats["total_exhausted"]
    assert sum(stats["by_day_of_week"].values()) == stats["total_exhausted"]
    assert any(v > 0 for v in stats["by_hour"].values())


# ─────────────────────────────────────────────────────────────────────
# Sign-up conversion join
# ─────────────────────────────────────────────────────────────────────


def test_signup_with_recent_exhausted_device_counts_as_conversion(monkeypatch):
    """The whole point of the metric: a brand-new account created on
    a device that hit the wall ~hours ago is the funnel we want to
    measure. `record_signup_with_device` must return True and bump
    the cumulative conversion counter."""
    _install_fake_redis(monkeypatch)
    token_id = "f" * 32
    metrics.record_anon_quota_exhausted(token_id, ip="203.0.113.10", plan_target="free")

    matched = metrics.record_signup_with_device(token_id)
    assert matched is True

    stats = metrics.get_anon_quota_exhausted_stats(days=1)
    assert stats["signup_after_exhaust"] >= 1
    assert stats["unique_devices_exhausted"] == 1
    # 1 signup / 1 unique exhausted device = 100% in the toy test.
    assert stats["conversion_pct"] == 100.0


def test_signup_with_unrelated_device_does_not_count(monkeypatch):
    """An organic sign-up from a device that never hit the wall must
    NOT inflate the conversion ratio — otherwise the metric would
    just track total sign-ups and tell us nothing about the cap."""
    _install_fake_redis(monkeypatch)
    metrics.record_anon_quota_exhausted("a" * 32, ip="203.0.113.11", plan_target="free")
    matched = metrics.record_signup_with_device("b" * 32)  # different device
    assert matched is False
    stats = metrics.get_anon_quota_exhausted_stats(days=1)
    assert stats["signup_after_exhaust"] == 0
    assert stats["conversion_pct"] == 0.0


def test_signup_join_uses_redis_so_it_works_across_workers(monkeypatch):
    """The exhaustion event happened on worker A; the sign-up arrives
    on worker B with no in-memory state. The Redis sorted set is what
    bridges them. We simulate that by clearing the in-process mirror
    AFTER the exhaustion is recorded but BEFORE the sign-up call —
    the join must still find the device via Redis.
    """
    _install_fake_redis(monkeypatch)
    token_id = "c" * 32
    metrics.record_anon_quota_exhausted(token_id, ip="203.0.113.12", plan_target="free")

    # Wipe the in-process mirror to simulate a different gunicorn
    # worker that never saw the original 429.
    with metrics._anon_exhaust_lock:
        metrics._anon_exhausted_devices.clear()

    matched = metrics.record_signup_with_device(token_id)
    assert matched is True


def test_conversion_ratio_is_cross_worker_consistent(monkeypatch):
    """Multi-worker correctness pin. Without source-aligned KPIs, a
    deployment with N workers could compute the conversion ratio with
    a per-worker denominator (smaller) and a cross-worker numerator
    (larger), producing >100% rates. We simulate the same race here:
    record exhaustion+signup, then wipe the entire in-process
    rolling window (as if the chart endpoint is now serving from a
    third worker that never saw either event). The headline KPIs
    must still come out consistent and within [0, 100]%.
    """
    _install_fake_redis(monkeypatch)
    for i in range(5):
        tok = f"crossworker-{i:02d}".ljust(32, "x")
        metrics.record_anon_quota_exhausted(tok, ip=f"203.0.113.{i}", plan_target="free")
        if i < 3:   # 3 of 5 sign up
            metrics.record_signup_with_device(tok)

    # Now wipe ALL local mirrors — simulates a fresh worker handling
    # the admin chart request.
    with metrics._anon_exhaust_lock:
        metrics._anon_exhaust_window.clear()
        metrics._anon_exhaust_seen.clear()
        metrics._anon_exhausted_devices.clear()
        metrics._anon_signup_after_exhaust_devices.clear()

    stats = metrics.get_anon_quota_exhausted_stats(days=1)
    assert stats["data_source"] == "redis", stats
    assert stats["unique_devices_exhausted"] == 5, stats
    assert stats["signup_after_exhaust"] == 3, stats
    assert 0.0 <= stats["conversion_pct"] <= 100.0, stats
    assert stats["conversion_pct"] == 60.0, stats   # 3/5


# ─────────────────────────────────────────────────────────────────────
# Redis-down misclassification guard
# ─────────────────────────────────────────────────────────────────────


def test_metric_not_emitted_when_redis_is_down(monkeypatch):
    """`atomic_deduct_device_credit` returns False in two very different
    states: (a) the device's daily counter actually reached the cap, and
    (b) Redis itself is unreachable (fail-closed). Without the
    `peek_device_credit_used` guard added in Task #798, every request
    during a Redis outage would show up in the chart as a "wall-hit"
    and we'd misread the outage as a sudden capacity-tuning crisis.
    This test pins the guard: with Redis offline, no metric is recorded.
    """
    _install_fake_redis(monkeypatch)
    # Now drop Redis on the floor — same shape as the fail-closed path
    # `atomic_deduct_device_credit` exits through when redis_client is
    # None.
    monkeypatch.setattr(db_ops, "redis_client", None, raising=False)
    monkeypatch.setattr(deps, "redis_client", None, raising=False)

    cookie = mint_device_token()
    req = _FakeReq(headers={"cf-connecting-ip": "192.0.2.99"})

    # The very first request will get a 429 (atomic_deduct returns
    # False because Redis is down), but the metric must NOT fire —
    # the peek guard sees 0 < 30 and short-circuits.
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(_call(req, cookie=cookie))
    assert excinfo.value.status_code == 429

    stats = metrics.get_anon_quota_exhausted_stats(days=1)
    assert stats["total_exhausted"] == 0, stats
    assert stats["unique_devices_exhausted"] == 0, stats
    assert stats["has_data"] is False


# ─────────────────────────────────────────────────────────────────────
# 24h conversion-window boundary
# ─────────────────────────────────────────────────────────────────────


def test_signup_outside_24h_window_does_not_count_as_conversion(monkeypatch):
    """The product question is `next-24h sign-up conversion among
    exhausted devices`. The Redis sorted set's TTL is sized at 48h to
    catch late signups arriving on a different worker, but the actual
    conversion gate is 24h. A signup arriving at T+25h must NOT be
    counted, otherwise we'd be measuring "next-48h conversion" and
    overstating the funnel by ~30-40%.
    """
    fake = _install_fake_redis(monkeypatch)
    token_id = "d" * 32
    metrics.record_anon_quota_exhausted(token_id, ip="203.0.113.20", plan_target="free")

    # Fast-forward both the in-memory mirror and the Redis zscore back
    # to T-25h so the signup arrives outside the 24h window.
    import time as _t
    twenty_five_h_ago = _t.time() - (25 * 3600)
    with metrics._anon_exhaust_lock:
        metrics._anon_exhausted_devices[token_id] = twenty_five_h_ago
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fake.zadd(f"chat:anon_exhausted_devices:{today}", {token_id: twenty_five_h_ago})

    matched = metrics.record_signup_with_device(token_id)
    assert matched is False, "25h-old exhaustion event must not count"

    stats = metrics.get_anon_quota_exhausted_stats(days=1)
    assert stats["signup_after_exhaust"] == 0


def test_signup_just_inside_24h_window_does_count(monkeypatch):
    """The strict-boundary partner of the previous test. A signup at
    T+23h59m is still inside the 24h window and must register, so we
    don't accidentally exclude legitimate next-day-morning signups by
    being one second too aggressive.
    """
    fake = _install_fake_redis(monkeypatch)
    token_id = "e" * 32
    metrics.record_anon_quota_exhausted(token_id, ip="203.0.113.21", plan_target="free")

    import time as _t
    just_under_24h_ago = _t.time() - (23 * 3600 + 59 * 60)
    with metrics._anon_exhaust_lock:
        metrics._anon_exhausted_devices[token_id] = just_under_24h_ago
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fake.zadd(f"chat:anon_exhausted_devices:{today}", {token_id: just_under_24h_ago})

    matched = metrics.record_signup_with_device(token_id)
    assert matched is True


def test_signup_conversion_is_unique_per_device(monkeypatch):
    """If a device retries signup (e.g. duplicate-email error then
    successful retry under a new email), `record_signup_with_device`
    will be called twice for the same device cookie. The conversion
    metric must count the device once, not twice — otherwise we'd
    inflate the numerator and could even compute a >100% conversion
    rate, which is nonsensical for a funnel ratio.
    """
    _install_fake_redis(monkeypatch)
    token_id = "1" * 32
    metrics.record_anon_quota_exhausted(token_id, ip="203.0.113.30", plan_target="free")

    assert metrics.record_signup_with_device(token_id) is True
    assert metrics.record_signup_with_device(token_id) is True   # retry path

    stats = metrics.get_anon_quota_exhausted_stats(days=1)
    assert stats["signup_after_exhaust"] == 1, stats
    assert stats["conversion_pct"] == 100.0


# ─────────────────────────────────────────────────────────────────────
# Backfill
# ─────────────────────────────────────────────────────────────────────


def test_backfill_replays_already_exhausted_devices_today(monkeypatch):
    """Today's chart would otherwise be empty until the first new
    exhaustion event under the deployed metric. The backfill scans
    the existing `device_daily_credits:*:<today>` keys and replays
    the metric for any counter already at the cap."""
    fake = _install_fake_redis(monkeypatch)
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Three devices already at cap (30/30) and one well below (5/30).
    fake.set(f"device_daily_credits:dev_a:{today}", "30")
    fake.set(f"device_daily_credits:dev_b:{today}", "30")
    fake.set(f"device_daily_credits:dev_c:{today}", "30")
    fake.set(f"device_daily_credits:dev_d:{today}", "5")

    backfilled = metrics.backfill_anon_quota_exhausted_today()
    assert backfilled == 3, backfilled

    stats = metrics.get_anon_quota_exhausted_stats(days=1)
    assert stats["unique_devices_exhausted"] == 3
    assert stats["total_exhausted"] == 3


def test_backfill_is_idempotent(monkeypatch):
    """A clumsy admin clicking "Backfill" twice in a row must not
    double-count. Dedupe inside `record_anon_quota_exhausted` is what
    protects us — this test pins that contract from the backfill
    entry point."""
    fake = _install_fake_redis(monkeypatch)
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fake.set(f"device_daily_credits:dev_x:{today}", "30")
    fake.set(f"device_daily_credits:dev_y:{today}", "30")

    first = metrics.backfill_anon_quota_exhausted_today()
    second = metrics.backfill_anon_quota_exhausted_today()
    assert first == 2
    assert second == 0   # already counted; no new events
    stats = metrics.get_anon_quota_exhausted_stats(days=1)
    assert stats["unique_devices_exhausted"] == 2


# ─────────────────────────────────────────────────────────────────────
# Edge cases
# ─────────────────────────────────────────────────────────────────────


def test_metric_no_op_without_token(monkeypatch):
    """`record_anon_quota_exhausted("")` must not crash and must not
    inflate the cross-worker chart. The function returns True and
    appends to the local rolling window (used for hour/dow histograms)
    but cannot push to the Redis zset without a token id, so it stays
    invisible to the cross-worker headline KPIs that the admin chart
    uses. Belt-and-braces against a future caller that forgets to pass
    the token id — the production callers in `auth_deps.py` always do.
    """
    _install_fake_redis(monkeypatch)
    assert metrics.record_anon_quota_exhausted("", ip="") is True
    stats = metrics.get_anon_quota_exhausted_stats(days=1)
    # Headline KPIs come from Redis, where no-token events don't land.
    assert stats["unique_devices_exhausted"] == 0
    assert stats["total_exhausted"] == 0
    assert stats["data_source"] == "redis"


def test_signup_no_op_without_token(monkeypatch):
    """`record_signup_with_device("")` must short-circuit to False so
    we don't accidentally tag the entire anonymous sign-up funnel as
    "converted" the first time the metric ships."""
    _install_fake_redis(monkeypatch)
    metrics.record_anon_quota_exhausted("a" * 32, ip="", plan_target="free")
    assert metrics.record_signup_with_device("") is False
    stats = metrics.get_anon_quota_exhausted_stats(days=1)
    assert stats["signup_after_exhaust"] == 0


# ─────────────────────────────────────────────────────────────────────
# Admin endpoint contract
# ─────────────────────────────────────────────────────────────────────


def test_admin_endpoint_returns_payload_and_honours_backfill(monkeypatch):
    """`GET /admin/chat/anon-quota-exhausted` is the only consumer of
    the metric today. Pin the contract:
      * gated on `get_admin_user` (we override it to a fake admin
        rather than actually authing — the production gating is
        covered by `auth_deps.get_admin_user`'s own tests);
      * empty state returns a clean `has_data: false` envelope, not
        a 500;
      * `?backfill=1` triggers the Redis scan and returns the count
        in `backfilled_today` so the admin UI can show "+N events
        seeded" feedback;
      * the response shape includes `daily`, `by_hour`,
        `by_day_of_week`, `signup_after_exhaust`, `conversion_pct`,
        `period_days`, and `alert` so the dashboard component
        doesn't have to defensively guard against missing keys.
    """
    fake = _install_fake_redis(monkeypatch)
    fastapi = pytest.importorskip("fastapi")
    from fastapi import FastAPI  # noqa: E402
    from fastapi.testclient import TestClient  # noqa: E402
    from routes.admin_advanced import router as admin_router  # noqa: E402
    from auth_deps import get_admin_user  # noqa: E402

    app = FastAPI()
    app.include_router(admin_router)
    app.dependency_overrides[get_admin_user] = lambda: {"email": "admin@example.com"}

    client = TestClient(app)

    # 1. Empty-state happy path.
    r = client.get("/admin/chat/anon-quota-exhausted?days=7")
    assert r.status_code == 200, r.text
    body = r.json()
    for k in (
        "period_days", "total_exhausted", "unique_devices_exhausted",
        "signup_after_exhaust", "conversion_pct", "daily",
        "by_hour", "by_day_of_week", "has_data", "backfilled_today",
        "alert", "data_source",
    ):
        assert k in body, f"missing key {k} in response: {body}"
    assert body["has_data"] is False
    assert body["backfilled_today"] == 0
    assert body["alert"] == "green"
    assert body["data_source"] == "redis"

    # 2. Seed Redis with two at-cap devices and call with backfill=1.
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fake.set(f"device_daily_credits:dev_admin_a:{today}", "30")
    fake.set(f"device_daily_credits:dev_admin_b:{today}", "30")

    r = client.get("/admin/chat/anon-quota-exhausted?days=7&backfill=1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["backfilled_today"] == 2
    assert body["unique_devices_exhausted"] == 2
    assert body["has_data"] is True

    app.dependency_overrides = {}


# ─────────────────────────────────────────────────────────────────────
# Task #808 — per-event detail (Recent feed + top-devices leaderboard)
# ─────────────────────────────────────────────────────────────────────


def test_recent_feed_captures_country_and_asn_from_cf_headers(monkeypatch):
    """Drain a device's quota with cf-ipcountry / cf-ipasn headers
    set; the resulting event must surface in the Recent feed with
    those tags (Task #808's "who hit the wall" detail). Without this,
    support has to guess from a count whether an angry ticket maps
    to the actual cap firing.
    """
    _install_fake_redis(monkeypatch)
    cookie = mint_device_token()
    token_id = device_token_id(cookie)
    req = _FakeReq(headers={
        "cf-connecting-ip": "203.0.113.40",
        "cf-ipcountry": "IN",
        "cf-ipasn": "AS24560",
    })

    for _ in range(30):
        asyncio.run(_call(req, cookie=cookie))
    with pytest.raises(HTTPException):
        asyncio.run(_call(req, cookie=cookie))

    recent = metrics.get_anon_quota_exhausted_recent(limit=10)
    assert len(recent) == 1, recent
    ev = recent[0]
    # The hashed device id must be present (never the raw cookie).
    assert ev["token_hash"] and len(ev["token_hash"]) == 12
    assert ev["token_hash"] != token_id
    assert ev["country"] == "IN"
    assert ev["asn"] == "AS24560"
    # Belt-and-braces: we must never echo the raw IP back into the
    # admin payload — only the country/ASN tags are safe.
    assert "ip" not in ev


def test_recent_feed_is_newest_first_and_bounded(monkeypatch):
    """The Recent feed must order newest-first (so support sees the
    angry-ticket device at the top) and stay within the ring
    buffer's configured ceiling regardless of caller-supplied
    ``limit``."""
    _install_fake_redis(monkeypatch)
    # Record three exhaustion events with monotonically increasing
    # timestamps (different token ids so dedupe doesn't collapse
    # them into one).
    for i, country in enumerate(("IN", "US", "BD")):
        metrics.record_anon_quota_exhausted(
            f"recent-{i:02d}".ljust(32, "x"),
            ip=f"203.0.113.{50 + i}",
            plan_target="free",
            country=country,
            asn=f"AS{1000 + i}",
        )

    feed = metrics.get_anon_quota_exhausted_recent(limit=10)
    assert [e["country"] for e in feed] == ["BD", "US", "IN"]

    # An over-sized request is clamped at the ring buffer's max so
    # a careless caller can't pull more than we keep.
    over = metrics.get_anon_quota_exhausted_recent(limit=10_000)
    assert len(over) == 3
    # And limit=0 returns an empty feed (admin opt-out).
    assert metrics.get_anon_quota_exhausted_recent(limit=0) == []


def test_top_devices_leaderboard_ranks_by_hit_count(monkeypatch):
    """The leaderboard answers "who is the chronic offender?"
    Per-device dedupe means an entry of N hits = N distinct days
    that this device hit the cap. Pinned: ranking is hits desc
    with most-recent-first as a stable tiebreaker.
    """
    _install_fake_redis(monkeypatch)
    import time as _t
    now = _t.time()

    # Helper: directly seed the rolling window with backdated events
    # since `record_anon_quota_exhausted` dedupes per (device, day).
    def _seed(token_hash: str, days_ago: int, country: str = "IN"):
        ts = now - days_ago * 86400
        with metrics._anon_exhaust_lock:
            metrics._anon_exhaust_window.append({
                "ts": ts,
                "plan_target": "free",
                "dow": "Mon",
                "hour": 12,
                "token_hash": token_hash,
                "country": country,
                "asn": "AS24560",
            })

    # Device A: 4 daily wall-hits over the last 7 days.
    for d in range(4):
        _seed("aaaaaaaaaaaa", days_ago=d)
    # Device B: 2 wall-hits.
    for d in range(2):
        _seed("bbbbbbbbbbbb", days_ago=d, country="US")
    # Device C: 1 wall-hit.
    _seed("cccccccccccc", days_ago=0, country="BD")

    top = metrics.get_anon_quota_exhausted_top_devices(days=7, top_n=10)
    assert [r["token_hash"] for r in top] == [
        "aaaaaaaaaaaa", "bbbbbbbbbbbb", "cccccccccccc",
    ]
    assert [r["hits"] for r in top] == [4, 2, 1]
    # The most-recent country tag is what we surface (devices roam).
    assert top[0]["country"] == "IN"
    assert top[1]["country"] == "US"

    # ``top_n`` truncates without re-ordering.
    top2 = metrics.get_anon_quota_exhausted_top_devices(days=7, top_n=2)
    assert [r["token_hash"] for r in top2] == ["aaaaaaaaaaaa", "bbbbbbbbbbbb"]


def test_admin_endpoint_exposes_recent_and_top_devices_flag(monkeypatch):
    """Pin the admin contract for Task #808: the response always
    includes a ``recent`` list (possibly empty), and the
    ``top_devices`` leaderboard appears only behind the explicit
    flag so the default page load is cheap.
    """
    _install_fake_redis(monkeypatch)
    pytest.importorskip("fastapi")
    from fastapi import FastAPI  # noqa: E402
    from fastapi.testclient import TestClient  # noqa: E402
    from routes.admin_advanced import router as admin_router  # noqa: E402
    from auth_deps import get_admin_user  # noqa: E402

    app = FastAPI()
    app.include_router(admin_router)
    app.dependency_overrides[get_admin_user] = lambda: {"email": "admin@example.com"}
    client = TestClient(app)

    # Seed two real exhaustion events so the recent feed has rows.
    metrics.record_anon_quota_exhausted(
        "z" * 32, ip="203.0.113.60", plan_target="free",
        country="IN", asn="AS24560",
    )
    metrics.record_anon_quota_exhausted(
        "y" * 32, ip="203.0.113.61", plan_target="free",
        country="US", asn="AS7018",
    )

    # 1. Default load: recent present, top_devices absent.
    r = client.get("/admin/chat/anon-quota-exhausted?days=7")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "recent" in body
    assert isinstance(body["recent"], list)
    assert len(body["recent"]) == 2
    assert {e["country"] for e in body["recent"]} == {"IN", "US"}
    assert "top_devices" not in body

    # 2. With top_devices=1 the leaderboard shows up.
    r = client.get(
        "/admin/chat/anon-quota-exhausted?days=7&top_devices=1&top_devices_n=5"
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "top_devices" in body
    assert isinstance(body["top_devices"], list)
    assert len(body["top_devices"]) == 2
    assert all(row["hits"] == 1 for row in body["top_devices"])

    # 3. recent_limit=0 opts out of the feed entirely (still keyed,
    # just empty) — useful for callers that only want aggregates.
    r = client.get("/admin/chat/anon-quota-exhausted?days=7&recent_limit=0")
    assert r.status_code == 200, r.text
    assert r.json()["recent"] == []

    app.dependency_overrides = {}


# ─────────────────────────────────────────────────────────────────────
# Task #809 — durable per-day aggregate + weekly trend
# ─────────────────────────────────────────────────────────────────────


def test_record_persists_durable_daily_aggregate(monkeypatch):
    """A successful `record_anon_quota_exhausted` must write through
    to a per-day Redis HASH that survives gunicorn restarts. Without
    this, the daily sparkline truncates after every deploy because
    the in-memory rolling window is process-local. Pin both fields
    (`events`, `unique`) and the TTL so the schema is intentional.
    """
    from datetime import datetime, timezone
    fake = _install_fake_redis(monkeypatch)
    metrics.record_anon_quota_exhausted(
        "durable-1".ljust(32, "x"), ip="203.0.113.10",
        plan_target="free", country="IN", asn="AS24560",
    )
    metrics.record_anon_quota_exhausted(
        "durable-2".ljust(32, "x"), ip="203.0.113.11",
        plan_target="free", country="IN", asn="AS24560",
    )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"chat:anon_exhausted_daily_agg:{today}"
    raw = fake.hgetall(key)
    # `decode_responses=True` so str-keys; both fields are present
    # and `events` matches the count of distinct devices today.
    assert int(raw.get("events") or 0) == 2, raw
    assert int(raw.get("unique") or 0) == 2, raw
    # TTL is set to ~13 months so quarterly capacity reviews work.
    ttl = fake.ttl(key)
    assert ttl > 350 * 86400, ttl
    assert ttl <= 400 * 86400, ttl


def test_record_dedupe_does_not_double_count_aggregate(monkeypatch):
    """Repeated calls for the same (device, day) must not inflate the
    durable aggregate — otherwise a hammering script that retries 100
    times after the 429 would distort the cap-tuning numbers we just
    spent a task ensuring are honest.
    """
    from datetime import datetime, timezone
    fake = _install_fake_redis(monkeypatch)
    token = "dedupe-1".ljust(32, "x")
    for _ in range(5):
        metrics.record_anon_quota_exhausted(
            token, ip="203.0.113.10", plan_target="free",
        )
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    raw = fake.hgetall(f"chat:anon_exhausted_daily_agg:{today}")
    assert int(raw.get("events") or 0) == 1, raw
    assert int(raw.get("unique") or 0) == 1, raw


def test_stats_reads_persisted_aggregate_after_zset_expiry(monkeypatch):
    """Simulate a gunicorn restart by clearing the in-memory window
    AND deleting the 14-day zset, leaving only the durable per-day
    aggregate. `get_anon_quota_exhausted_stats` must still surface
    yesterday's numbers — that's the whole point of Task #809.
    """
    from datetime import datetime, timezone, timedelta
    fake = _install_fake_redis(monkeypatch)
    # Pre-seed the durable aggregate for yesterday and the day before
    # (simulates a fleet that's been emitting the metric for a while
    # before the current process started).
    for n, count in [(1, 7), (2, 12)]:
        d = (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d")
        key = f"chat:anon_exhausted_daily_agg:{d}"
        fake.hset(key, mapping={"events": count, "unique": count})

    # Wipe all in-memory state to simulate a fresh worker boot.
    with metrics._anon_exhaust_lock:
        metrics._anon_exhaust_window.clear()
        metrics._anon_exhaust_recent.clear()
        metrics._anon_exhaust_seen.clear()

    stats = metrics.get_anon_quota_exhausted_stats(days=7)
    by_date = {row["date"]: row["exhausted"] for row in stats["daily"]}
    yday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    dby = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d")
    assert by_date.get(yday) == 7, stats
    assert by_date.get(dby) == 12, stats
    # Cross-check: the headline KPI sums across days.
    assert stats["unique_devices_exhausted"] == 19, stats
    # And the chart is correctly labelled as redis-backed (not the
    # degraded memory_fallback that would render the warning banner).
    assert stats["data_source"] == "redis"


def test_weekly_trend_groups_days_by_iso_monday(monkeypatch):
    """The weekly trend must bucket by ISO week (Monday-anchored UTC)
    so `week_start` lines up with how the rest of the company already
    talks about weeks. Empty weeks must come back as zero buckets so
    the dashboard's x-axis stays evenly spaced.
    """
    from datetime import datetime, timezone, timedelta
    fake = _install_fake_redis(monkeypatch)

    # Seed daily aggregates: today + 8 days ago so we land in two
    # different ISO weeks regardless of which weekday the test runs.
    today = datetime.now(timezone.utc).date()
    eight_days_ago = today - timedelta(days=8)
    for d, n in ((today, 5), (eight_days_ago, 3)):
        fake.hset(
            f"chat:anon_exhausted_daily_agg:{d.strftime('%Y-%m-%d')}",
            mapping={"events": n, "unique": n},
        )

    trend = metrics.get_anon_quota_exhausted_weekly_trend(weeks=4)
    # 4 weekly buckets, regardless of how many had data.
    assert len(trend) == 4, trend
    # Sorted oldest-first.
    assert trend == sorted(trend, key=lambda r: r["week_start"])
    # Each row has the documented schema.
    for row in trend:
        assert set(row.keys()) >= {"week_start", "exhausted", "days_with_data"}
    # The two seeded days land in (likely-different) weeks; their
    # combined exhausted total across the trend must equal what we
    # wrote — proving no double-counting and no silent dropping.
    assert sum(r["exhausted"] for r in trend) == 8, trend


def test_aggregate_dedupes_across_simulated_workers(monkeypatch):
    """Multi-worker safety net: each gunicorn worker has its own
    `_anon_exhaust_seen` set, so the in-memory dedupe doesn't
    protect us across processes. We rely on Redis ZADD NX in
    `record_anon_quota_exhausted` to keep the durable `events`
    field from drifting upward when worker A and worker B both
    record the same (token, day). Simulate this by clearing the
    per-process seen-set between two record() calls — the wire
    behaviour is the same as two workers each receiving the
    request without prior knowledge of the device.
    """
    from datetime import datetime, timezone
    fake = _install_fake_redis(monkeypatch)
    token = "x" * 32

    # Worker A processes the device's first wall-hit of the day.
    metrics.record_anon_quota_exhausted(token, ip="203.0.113.30", plan_target="free")
    # Simulate a different worker (no shared in-memory dedupe state)
    # picking up the next request from the same device the same day.
    with metrics._anon_exhaust_lock:
        metrics._anon_exhaust_seen.clear()
    metrics.record_anon_quota_exhausted(token, ip="203.0.113.30", plan_target="free")
    # And once more for good measure (third "worker").
    with metrics._anon_exhaust_lock:
        metrics._anon_exhaust_seen.clear()
    metrics.record_anon_quota_exhausted(token, ip="203.0.113.30", plan_target="free")

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    raw = fake.hgetall(f"chat:anon_exhausted_daily_agg:{today}")
    # `events` MUST stay at 1 because the ZADD NX gate detected that
    # the second and third calls did not introduce a new device.
    # Without the NX gate this would be 3, which would inflate both
    # the daily count and the 12-week trend in production.
    assert int(raw.get("events") or 0) == 1, raw
    assert int(raw.get("unique") or 0) == 1, raw

    # And the headline KPI must agree (single device, regardless of
    # how many workers saw it).
    stats = metrics.get_anon_quota_exhausted_stats(days=1)
    assert stats["unique_devices_exhausted"] == 1, stats


def test_admin_endpoint_includes_weekly_trend(monkeypatch):
    """Pin the wire contract: ``weekly_trend`` is always present (so
    the dashboard never has to feature-flag the second sparkline) and
    its ``weeks`` query param is honoured (clamped server-side).
    """
    from datetime import datetime, timezone
    _install_fake_redis(monkeypatch)
    pytest.importorskip("fastapi")
    from fastapi import FastAPI  # noqa: E402
    from fastapi.testclient import TestClient  # noqa: E402
    from routes.admin_advanced import router as admin_router  # noqa: E402
    from auth_deps import get_admin_user  # noqa: E402

    app = FastAPI()
    app.include_router(admin_router)
    app.dependency_overrides[get_admin_user] = lambda: {"email": "admin@example.com"}
    client = TestClient(app)

    metrics.record_anon_quota_exhausted(
        "weekly-1".ljust(32, "x"), ip="203.0.113.20", plan_target="free",
    )

    r = client.get("/admin/chat/anon-quota-exhausted?days=7&weeks=4")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "weekly_trend" in body
    assert isinstance(body["weekly_trend"], list)
    assert len(body["weekly_trend"]) == 4
    for row in body["weekly_trend"]:
        assert "week_start" in row
        assert "exhausted" in row
    # Today's recording should show up in the most-recent bucket.
    assert body["weekly_trend"][-1]["exhausted"] >= 1

    # Server-side clamp: ?weeks=999 must be capped at 52 (one year).
    r = client.get("/admin/chat/anon-quota-exhausted?days=7&weeks=999")
    assert r.status_code == 200, r.text
    assert len(r.json()["weekly_trend"]) == 52

    # Lower bound: weeks=0 must coerce up to 1 (never zero buckets).
    r = client.get("/admin/chat/anon-quota-exhausted?days=7&weeks=0")
    assert r.status_code == 200, r.text
    assert len(r.json()["weekly_trend"]) == 1

    app.dependency_overrides = {}
