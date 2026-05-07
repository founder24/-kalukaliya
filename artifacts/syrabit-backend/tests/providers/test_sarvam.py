"""tests.providers.test_sarvam — Task #553.

VCR-style hermetic unit tests for ``providers.sarvam.chat``.

The four required wire-level cases (success / 429 / 500 / timeout) are
driven from JSON cassette files in ``tests/providers/cassettes/``,
loaded into an ``httpx.MockTransport`` and bound to a real
``httpx.AsyncClient`` that we patch into ``deps.sarvam_llm_client``.
The cassettes carry the recorded HTTP exchange (status, headers,
body) so adding a new case is "drop a JSON file + add a test stub" —
no inline mock plumbing.

Higher-level facade contract tests (per-user cap, anon skip, cap=0
override, success-rate snapshot, Sentry alert flip) use the same
cassette infrastructure to stay consistent.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Optional

import httpx
import pytest

from providers import sarvam as sarvam_mod
from providers.sarvam import (
    ChatResponse,
    SarvamRateLimited,
    SarvamUnavailable,
    chat,
)

CASSETTE_DIR = Path(__file__).parent / "cassettes"


def _load_cassette(name: str) -> dict:
    """Load a JSON cassette by stem (no `.json`)."""
    path = CASSETTE_DIR / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _cassette_handler(cassette: dict):
    """Build an ``httpx.MockTransport`` handler that replays one
    recorded response. Tracks the inbound request on the closure so
    tests can assert on it."""
    captured: dict = {"calls": []}
    resp_spec = cassette["response"]

    def _handler(request: httpx.Request) -> httpx.Response:
        body_bytes = request.content or b""
        try:
            body_json = json.loads(body_bytes.decode("utf-8")) if body_bytes else None
        except Exception:
            body_json = None
        captured["calls"].append({
            "method": request.method,
            "url": str(request.url),
            "json": body_json,
        })
        if "raise" in resp_spec:
            kind = resp_spec["raise"]
            msg = resp_spec.get("message", "simulated transport error")
            if kind.endswith("ReadTimeout"):
                raise httpx.ReadTimeout(msg)
            if kind.endswith("ConnectError"):
                raise httpx.ConnectError(msg)
            raise httpx.TransportError(msg)
        body = resp_spec.get("body", {})
        return httpx.Response(
            status_code=int(resp_spec["status_code"]),
            headers=resp_spec.get("headers") or {},
            json=body if isinstance(body, (dict, list)) else None,
            content=body if isinstance(body, (str, bytes)) else None,
        )

    return _handler, captured


def _install_cassette(monkeypatch, cassette_name: Optional[str]):
    """Wire a real ``httpx.AsyncClient`` driven by the cassette into
    ``deps.sarvam_llm_client``. Returns the captured-calls dict."""
    import deps

    if cassette_name is None:
        monkeypatch.setattr(deps, "sarvam_llm_client", None, raising=False)
        return {"calls": []}

    cassette = _load_cassette(cassette_name)
    handler, captured = _cassette_handler(cassette)
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="https://api.sarvam.ai")
    monkeypatch.setattr(deps, "sarvam_llm_client", client, raising=False)
    return captured


@pytest.fixture(autouse=True)
def _reset_metrics_state():
    """Drop the in-process Sarvam event ring + alert throttle between
    tests so the rolling snapshot is deterministic."""
    import metrics

    metrics._SARVAM_CHAT_EVENTS.clear()
    metrics._SARVAM_LAST_ALERT_TS = 0.0
    yield
    metrics._SARVAM_CHAT_EVENTS.clear()
    metrics._SARVAM_LAST_ALERT_TS = 0.0


# ── 1. VCR — success cassette ─────────────────────────────────────────────
def test_chat_success_cassette_returns_chat_response(monkeypatch):
    captured = _install_cassette(monkeypatch, "sarvam_success")

    out = asyncio.run(
        chat([{"role": "user", "content": "Greet me briefly."}], user_id=None, max_tokens=80)
    )

    assert isinstance(out, ChatResponse)
    assert out.provider == "sarvam"
    assert out.model == "sarvam-m"
    # `<think>...</think>` reasoning stripped from cassette body
    assert "নমস্কাৰ" in out.text
    assert "<think>" not in out.text
    assert out.usage["prompt_tokens"] == 18
    assert out.usage["completion_tokens"] == 24
    assert out.usage["total_tokens"] == 42
    # Real cost derived from the upstream usage block
    assert out.cost_usd > 0
    # The recorded payload was actually sent (not a fake)
    assert captured["calls"], "cassette transport did not see the request"
    sent = captured["calls"][0]
    assert sent["method"] == "POST"
    assert sent["url"].endswith("/v1/chat/completions")
    assert sent["json"]["model"] == "sarvam-m"
    assert sent["json"]["response_language"] == "as-IN"  # `language="as"` mapping


def test_chat_default_language_is_assamese(monkeypatch):
    captured = _install_cassette(monkeypatch, "sarvam_success")
    asyncio.run(chat([{"role": "user", "content": "hi"}], user_id=None))
    assert captured["calls"][0]["json"]["response_language"] == "as-IN"


# ── 2. VCR — upstream 429 cassette ────────────────────────────────────────
def test_chat_upstream_429_cassette_raises_rate_limited(monkeypatch):
    _install_cassette(monkeypatch, "sarvam_429")

    with pytest.raises(SarvamRateLimited) as exc_info:
        asyncio.run(chat([{"role": "user", "content": "q"}], user_id=None))

    assert exc_info.value.reason == "upstream_429"
    assert exc_info.value.retry_after == 12

    from metrics import sarvam_chat_snapshot
    snap = sarvam_chat_snapshot()
    assert snap["err"] == 1
    assert snap["last_error"] == "upstream_429"


# ── 3. VCR — upstream 500 cassette ────────────────────────────────────────
def test_chat_upstream_500_cassette_raises_unavailable(monkeypatch):
    _install_cassette(monkeypatch, "sarvam_500")
    with pytest.raises(SarvamUnavailable):
        asyncio.run(chat([{"role": "user", "content": "q"}], user_id=None))

    from metrics import sarvam_chat_snapshot
    snap = sarvam_chat_snapshot()
    assert snap["err"] == 1
    assert "upstream 500" in snap["last_error"]


# ── 4. VCR — transport timeout cassette ───────────────────────────────────
def test_chat_timeout_cassette_raises_unavailable(monkeypatch):
    _install_cassette(monkeypatch, "sarvam_timeout")
    with pytest.raises(SarvamUnavailable):
        asyncio.run(chat([{"role": "user", "content": "q"}], user_id=None))

    from metrics import sarvam_chat_snapshot
    snap = sarvam_chat_snapshot()
    assert snap["err"] == 1
    assert "transport" in snap["last_error"].lower()


def test_chat_client_not_initialised_raises_unavailable(monkeypatch):
    _install_cassette(monkeypatch, None)
    with pytest.raises(SarvamUnavailable):
        asyncio.run(chat([{"role": "user", "content": "q"}], user_id=None))


# ── 5. cost_caps interceptor: per-user 30/mo cap ──────────────────────────
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
    """Cap is enforced via ``cost_caps.record_sarvam_user_call`` — the
    canonical interceptor — so a 31st call raises
    ``SarvamRateLimited("per_user_monthly_cap")`` and never reaches
    the upstream."""
    import cost_caps
    import deps

    fake_redis = _FakeRedis()
    monkeypatch.setattr(deps, "redis_client", fake_redis, raising=False)
    captured = _install_cassette(monkeypatch, "sarvam_success")
    monkeypatch.setattr(cost_caps, "SARVAM_PER_USER_MONTHLY_CAP", 30, raising=False)

    for _ in range(30):
        asyncio.run(chat([{"role": "user", "content": "q"}], user_id="u-1"))

    with pytest.raises(SarvamRateLimited) as exc_info:
        asyncio.run(chat([{"role": "user", "content": "q"}], user_id="u-1"))
    assert exc_info.value.reason == "per_user_monthly_cap"
    # 31st call did NOT hit the upstream
    assert len(captured["calls"]) == 30


def test_per_user_cap_disabled_when_zero(monkeypatch):
    import cost_caps
    import deps

    fake_redis = _FakeRedis()
    monkeypatch.setattr(deps, "redis_client", fake_redis, raising=False)
    captured = _install_cassette(monkeypatch, "sarvam_success")
    monkeypatch.setattr(cost_caps, "SARVAM_PER_USER_MONTHLY_CAP", 0, raising=False)

    for _ in range(50):
        asyncio.run(chat([{"role": "user", "content": "q"}], user_id="u-2"))
    assert len(captured["calls"]) == 50


def test_anonymous_user_skips_local_cap(monkeypatch):
    """``user_id=None`` means the edge worker is the canonical enforcer
    (anon-id keyed). The local backstop must be a no-op."""
    import deps

    fake_redis = _FakeRedis()
    monkeypatch.setattr(deps, "redis_client", fake_redis, raising=False)
    _install_cassette(monkeypatch, "sarvam_success")

    asyncio.run(chat([{"role": "user", "content": "q"}], user_id=None))
    assert fake_redis.store == {}


# ── 6. metrics + Sentry alert hook ────────────────────────────────────────
def test_metrics_records_real_token_cost(monkeypatch):
    """Every successful call must write ``prompt_tokens`` / ``completion_tokens``
    / ``cost_usd`` into ``metrics._SARVAM_CHAT_EVENTS`` so the
    AdminHealth tile can show "Cost / 24h" + "tokens_24h"."""
    import metrics

    _install_cassette(monkeypatch, "sarvam_success")
    asyncio.run(chat([{"role": "user", "content": "q"}], user_id=None))

    snap = metrics.sarvam_chat_snapshot()
    assert snap["ok"] == 1
    assert snap["tokens_24h"] == 18 + 24
    assert snap["cost_usd_24h"] > 0
    # p50/p95 are populated even with a single sample
    assert snap["p50_latency_ms"] >= 0
    assert snap["p95_latency_ms"] >= 0


def test_success_rate_alert_floor_emits_sentry(monkeypatch):
    """When success-rate drops below 95 % over 1 h with ≥ 20 samples,
    ``metrics.maybe_emit_sarvam_alert`` must fire a Sentry warning."""
    import sys
    import types
    import metrics

    captured: list[tuple[str, str]] = []
    fake_sentry = types.SimpleNamespace(
        capture_message=lambda msg, level="warning": captured.append((msg, level)),
        set_tag=lambda *a, **kw: None,
    )
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake_sentry)

    # Seed 21 events: 10 ok, 11 err → 47.6 % success rate
    for _ in range(10):
        metrics.record_sarvam_chat(success=True, latency_ms=50.0,
                                   prompt_tokens=10, completion_tokens=10)
    for _ in range(11):
        metrics.record_sarvam_chat(success=False, latency_ms=100.0,
                                   error="upstream 500: boom")

    snap = metrics.sarvam_chat_snapshot()
    assert snap["alert"] is True
    assert snap["success_rate"] < 0.95

    fired = metrics.maybe_emit_sarvam_alert()
    assert fired is True
    assert captured, "expected exactly one Sentry capture_message"
    msg, level = captured[0]
    assert level == "warning"
    assert "sarvam_chat_below_floor" in msg

    # Throttle: a second call within the throttle window must NOT fire.
    fired_again = metrics.maybe_emit_sarvam_alert()
    assert fired_again is False


def test_success_rate_no_alert_below_min_samples():
    import metrics

    for _ in range(5):
        metrics.record_sarvam_chat(success=False, latency_ms=10.0, error="boom")
    snap = metrics.sarvam_chat_snapshot()
    # 5 < min_samples (20) → must NOT alert even though success_rate=0
    assert snap["alert"] is False


def test_success_rate_empty_window_is_perfect():
    from providers.sarvam import success_rate_snapshot

    snap = success_rate_snapshot()
    assert snap["total"] == 0
    assert snap["success_rate"] == 1.0
    assert snap["alert"] is False
