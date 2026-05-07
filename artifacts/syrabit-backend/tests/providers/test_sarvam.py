"""tests.providers.test_sarvam — Task #553.

Hermetic unit tests for ``providers.sarvam.chat`` covering the four
contract cases the task lists as "VCR fixtures":

  1. Successful Assamese reply           → ``ChatResponse`` with text
  2. Upstream 429                        → ``SarvamRateLimited("upstream_429")``
  3. Upstream 500                        → ``SarvamUnavailable``
  4. Network timeout                     → ``SarvamUnavailable``

Plus a fifth case for the per-user cap (``SarvamRateLimited
("per_user_monthly_cap")``) and a sixth for the success-rate
snapshot powering the admin tile + Sentry alert.

We don't actually shell out to ``vcrpy`` because the live dispatcher
already has chain-shape coverage in ``test_assamese_routing_chain_e2e.py``;
these tests pin the **facade contract** (typed exceptions, dataclass
shape, cap enforcement, success-rate counters) and use a dummy async
client to keep them hermetic in CI.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from providers import sarvam as sarvam_mod
from providers.sarvam import (
    ChatResponse,
    SarvamRateLimited,
    SarvamUnavailable,
    chat,
    success_rate_snapshot,
)


class _FakeResp:
    def __init__(self, status_code: int, body=None, headers=None) -> None:
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.headers = headers or {}
        self.text = str(body)[:200] if body is not None else ""

    def json(self) -> dict:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class _FakeClient:
    def __init__(self, behavior) -> None:
        # `behavior` is either a _FakeResp, an Exception to raise, or
        # a callable returning either of those.
        self.behavior = behavior
        self.calls: list[dict] = []

    async def post(self, path, json=None):  # noqa: A002 - shadowing stdlib `json` in signature
        self.calls.append({"path": path, "json": json})
        b = self.behavior() if callable(self.behavior) else self.behavior
        if isinstance(b, Exception):
            raise b
        return b


@pytest.fixture(autouse=True)
def _reset_recent_calls():
    sarvam_mod._RECENT_CALLS.clear()
    yield
    sarvam_mod._RECENT_CALLS.clear()


def _install_client(monkeypatch, fake):
    import deps

    monkeypatch.setattr(deps, "sarvam_llm_client", fake, raising=False)


def _ok_body(text: str = "নমস্কাৰ") -> dict:
    return {
        "model": "sarvam-m",
        "choices": [{"message": {"content": text}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
    }


# ── 1. Successful Assamese reply ──────────────────────────────────────────
def test_chat_success_returns_chat_response(monkeypatch):
    fake = _FakeClient(_FakeResp(200, _ok_body("নমস্কাৰ, মই ভালে আছোঁ।")))
    _install_client(monkeypatch, fake)

    out = asyncio.run(chat([{"role": "user", "content": "Hi"}], user_id=None))

    assert isinstance(out, ChatResponse)
    assert out.provider == "sarvam"
    assert out.model == "sarvam-m"
    assert "নমস্কাৰ" in out.text
    assert out.usage["total_tokens"] == 16
    # Snapshot reflects the success
    snap = success_rate_snapshot()
    assert snap["ok"] == 1 and snap["err"] == 0
    assert snap["success_rate"] == 1.0


def test_chat_strips_think_block(monkeypatch):
    fake = _FakeClient(_FakeResp(200, _ok_body("<think>reasoning</think>উত্তৰ")))
    _install_client(monkeypatch, fake)
    out = asyncio.run(chat([{"role": "user", "content": "q"}], user_id=None))
    assert out.text == "উত্তৰ"


# ── 2. Upstream 429 → SarvamRateLimited("upstream_429") ───────────────────
def test_chat_upstream_429_raises_rate_limited(monkeypatch):
    fake = _FakeClient(_FakeResp(429, {"error": "rate_limit"}, headers={"retry-after": "12"}))
    _install_client(monkeypatch, fake)

    with pytest.raises(SarvamRateLimited) as exc_info:
        asyncio.run(chat([{"role": "user", "content": "q"}], user_id=None))

    assert exc_info.value.reason == "upstream_429"
    assert exc_info.value.retry_after == 12
    snap = success_rate_snapshot()
    assert snap["err"] == 1


# ── 3. Upstream 500 → SarvamUnavailable ───────────────────────────────────
def test_chat_upstream_500_raises_unavailable(monkeypatch):
    fake = _FakeClient(_FakeResp(500, "boom"))
    _install_client(monkeypatch, fake)
    with pytest.raises(SarvamUnavailable):
        asyncio.run(chat([{"role": "user", "content": "q"}], user_id=None))


# ── 4. Transport / timeout → SarvamUnavailable ────────────────────────────
def test_chat_timeout_raises_unavailable(monkeypatch):
    fake = _FakeClient(httpx.ReadTimeout("read timed out"))
    _install_client(monkeypatch, fake)
    with pytest.raises(SarvamUnavailable):
        asyncio.run(chat([{"role": "user", "content": "q"}], user_id=None))


def test_chat_client_not_initialised_raises_unavailable(monkeypatch):
    _install_client(monkeypatch, None)
    with pytest.raises(SarvamUnavailable):
        asyncio.run(chat([{"role": "user", "content": "q"}], user_id=None))


# ── 5. Per-user 30/mo cap ─────────────────────────────────────────────────
class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, int] = {}
        self.ttl: dict[str, int] = {}

    def incr(self, key):
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    def expire(self, key, ttl):
        self.ttl[key] = ttl


def test_per_user_cap_blocks_31st_call(monkeypatch):
    import deps

    fake_redis = _FakeRedis()
    monkeypatch.setattr(deps, "redis_client", fake_redis, raising=False)
    fake_client = _FakeClient(_FakeResp(200, _ok_body()))
    monkeypatch.setattr(deps, "sarvam_llm_client", fake_client, raising=False)
    monkeypatch.setattr(sarvam_mod, "PER_USER_MONTHLY_CAP", 30, raising=False)

    # 30 successful calls
    for _ in range(30):
        asyncio.run(chat([{"role": "user", "content": "q"}], user_id="u-1"))

    with pytest.raises(SarvamRateLimited) as exc_info:
        asyncio.run(chat([{"role": "user", "content": "q"}], user_id="u-1"))
    assert exc_info.value.reason == "per_user_monthly_cap"
    assert fake_client.calls and len(fake_client.calls) == 30


def test_per_user_cap_disabled_when_zero(monkeypatch):
    import deps

    fake_redis = _FakeRedis()
    monkeypatch.setattr(deps, "redis_client", fake_redis, raising=False)
    fake_client = _FakeClient(_FakeResp(200, _ok_body()))
    monkeypatch.setattr(deps, "sarvam_llm_client", fake_client, raising=False)
    monkeypatch.setattr(sarvam_mod, "PER_USER_MONTHLY_CAP", 0, raising=False)

    # 50 calls succeed
    for _ in range(50):
        asyncio.run(chat([{"role": "user", "content": "q"}], user_id="u-2"))
    assert len(fake_client.calls) == 50


def test_anonymous_user_skips_local_cap(monkeypatch):
    """``user_id=None`` means the edge worker is the canonical enforcer
    (anon-id keyed). The local backstop must be a no-op."""
    import deps

    fake_redis = _FakeRedis()
    monkeypatch.setattr(deps, "redis_client", fake_redis, raising=False)
    fake_client = _FakeClient(_FakeResp(200, _ok_body()))
    monkeypatch.setattr(deps, "sarvam_llm_client", fake_client, raising=False)

    asyncio.run(chat([{"role": "user", "content": "q"}], user_id=None))
    assert fake_redis.store == {}


# ── 6. Success-rate snapshot powers the admin tile + alert ────────────────
def test_success_rate_alert_floor(monkeypatch):
    fake_ok = _FakeResp(200, _ok_body())
    fake_err = _FakeResp(500, "boom")

    seq = [fake_ok] * 10 + [fake_err] * 11

    def _next():
        return seq.pop(0)

    fake_client = _FakeClient(_next)
    _install_client(monkeypatch, fake_client)

    for _ in range(10):
        asyncio.run(chat([{"role": "user", "content": "q"}], user_id=None))
    for _ in range(11):
        try:
            asyncio.run(chat([{"role": "user", "content": "q"}], user_id=None))
        except SarvamUnavailable:
            pass

    snap = success_rate_snapshot()
    assert snap["total"] == 21
    assert snap["ok"] == 10
    assert snap["err"] == 11
    # 10/21 = 0.476... ≪ 0.95 → alert fires
    assert snap["alert"] is True
    assert snap["success_rate"] < 0.95


def test_success_rate_no_alert_below_min_samples(monkeypatch):
    fake_client = _FakeClient(_FakeResp(500, "boom"))
    _install_client(monkeypatch, fake_client)
    for _ in range(5):
        try:
            asyncio.run(chat([{"role": "user", "content": "q"}], user_id=None))
        except SarvamUnavailable:
            pass
    snap = success_rate_snapshot()
    # 5 < min_samples (20) → must NOT alert even though success_rate=0
    assert snap["alert"] is False


def test_success_rate_empty_window_is_perfect():
    snap = success_rate_snapshot()
    assert snap["total"] == 0
    assert snap["success_rate"] == 1.0
    assert snap["alert"] is False
