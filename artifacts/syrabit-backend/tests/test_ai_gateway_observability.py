"""Task #383 — tests for the AI Gateway header parser + counters."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_counters():
    from ai_gateway_observability import reset_for_tests
    reset_for_tests()
    yield
    reset_for_tests()


def test_parses_cache_hit_headers():
    from ai_gateway_observability import parse_aig_response_headers
    out = parse_aig_response_headers({
        "cf-aig-cache-status": "HIT",
        "cf-aig-cache-ttl": "300",
        "cf-aig-log-id": "log-abc",
        "cf-aig-event-id": "evt-1",
    })
    assert out["present"] is True
    assert out["cache_status"] == "hit"
    assert out["cache_ttl_s"] == 300
    assert out["log_id"] == "log-abc"
    assert out["event_id"] == "evt-1"
    assert out["guardrail"]["action"] is None


def test_parses_miss_and_bypass():
    from ai_gateway_observability import parse_aig_response_headers
    miss = parse_aig_response_headers({"cf-aig-cache-status": "MISS"})
    bypass = parse_aig_response_headers({"cf-aig-cache-status": "BYPASS"})
    assert miss["cache_status"] == "miss"
    assert bypass["cache_status"] == "bypass"


def test_unknown_status_falls_back_to_bypass():
    from ai_gateway_observability import parse_aig_response_headers
    out = parse_aig_response_headers({"cf-aig-cache-status": "STALE"})
    # Unknown but non-empty values are normalised to bypass so we don't
    # silently drop telemetry when CF adds new statuses.
    assert out["cache_status"] == "bypass"


def test_no_headers_returns_present_false():
    from ai_gateway_observability import parse_aig_response_headers
    out = parse_aig_response_headers({})
    assert out["present"] is False
    assert out["cache_status"] is None


def test_record_increments_cache_counters(monkeypatch):
    monkeypatch.setattr("ai_gateway_observability.CF_AIGW_OBS_ON", True)
    from ai_gateway_observability import record_aig_response, snapshot
    record_aig_response({"cf-aig-cache-status": "HIT"}, provider="vertex")
    record_aig_response({"cf-aig-cache-status": "HIT"}, provider="vertex")
    record_aig_response({"cf-aig-cache-status": "MISS"}, provider="azure")
    snap = snapshot()
    assert snap["counters"]["aig_cache_hits"] == 2
    assert snap["counters"]["aig_cache_misses"] == 1
    assert snap["cache_hit_ratio"] == pytest.approx(2 / 3, rel=1e-3)
    assert len(snap["recent_samples"]) == 3


def test_record_skipped_when_flag_off(monkeypatch):
    monkeypatch.setattr("ai_gateway_observability.CF_AIGW_OBS_ON", False)
    from ai_gateway_observability import record_aig_response, snapshot
    record_aig_response({"cf-aig-cache-status": "HIT"})
    snap = snapshot()
    assert snap["counters"]["aig_cache_hits"] == 0
    assert snap["enabled"] is False


def test_guardrail_block_counter(monkeypatch):
    monkeypatch.setattr("ai_gateway_observability.CF_AIGW_OBS_ON", True)
    from ai_gateway_observability import record_aig_response, snapshot
    record_aig_response({
        "cf-aig-cache-status": "MISS",
        "cf-aig-guardrail-action": "block",
        "cf-aig-guardrail-category": "pii",
    })
    record_aig_response({
        "cf-aig-cache-status": "MISS",
        "cf-aig-guardrail-action": "allow",
    })
    snap = snapshot()
    assert snap["counters"]["aig_guardrails_blocked"] == 1
    assert snap["counters"]["aig_guardrails_allowed"] == 1
    assert snap["guardrail_block_ratio"] == pytest.approx(0.5, rel=1e-3)


def test_record_returns_summary_when_disabled(monkeypatch):
    monkeypatch.setattr("ai_gateway_observability.CF_AIGW_OBS_ON", False)
    from ai_gateway_observability import record_aig_response
    out = record_aig_response({"cf-aig-cache-status": "HIT"})
    assert out["cache_status"] == "hit"  # parse still works


# ──────────────────────────────────────────────────────────────────────
# Task #403 — integration test: a live chat call through the
# providers/cloudflare_ai.py path (Workers AI via CF AI Gateway) must
# bump aig_responses_total by 1. Uses httpx.MockTransport to shim the
# upstream call so the counters move without any real network traffic.
# ──────────────────────────────────────────────────────────────────────


def test_workers_ai_chat_records_aig_response_headers(monkeypatch):
    """One non-stream chat through providers.cloudflare_ai.chat() must
    feed the cf-aig-* response headers into record_aig_response() exactly
    once, bumping aig_responses_total by 1."""
    import asyncio
    import importlib

    import httpx

    monkeypatch.setattr("ai_gateway_observability.CF_AIGW_OBS_ON", True)
    from ai_gateway_observability import reset_for_tests, snapshot
    reset_for_tests()

    monkeypatch.setenv("CF_AI_GATEWAY_ACCOUNT_ID", "acct-test")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "cf-token-test")
    monkeypatch.setenv("CF_AI_GATEWAY_ID", "gw-test")

    from providers import cloudflare_ai
    importlib.reload(cloudflare_ai)

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "cf-aig-cache-status": "MISS",
                "cf-aig-log-id": "log-int-1",
                "cf-aig-event-id": "evt-int-1",
            },
            json={"result": {"response": "hello"}, "success": True},
        )

    transport = httpx.MockTransport(_handler)
    fake_client = httpx.AsyncClient(transport=transport)
    monkeypatch.setattr(cloudflare_ai, "_http_client", fake_client)

    before = snapshot()["counters"]["aig_responses_total"]

    text = asyncio.run(cloudflare_ai.chat(
        [{"role": "user", "content": "hi"}],
        model_key="chat_fast",
        max_tokens=4,
    ))
    assert text == "hello"

    after_snap = snapshot()
    assert after_snap["counters"]["aig_responses_total"] == before + 1, after_snap
    assert after_snap["counters"]["aig_cache_misses"] >= 1, after_snap
    samples = after_snap["recent_samples"]
    assert samples and samples[-1]["log_id"] == "log-int-1", samples


def test_workers_ai_stream_records_aig_response_headers(monkeypatch):
    """One streaming chat through providers.cloudflare_ai.chat_stream() must
    feed the cf-aig-* headers into record_aig_response() exactly once,
    even if zero tokens are emitted before the stream closes."""
    import asyncio
    import importlib

    import httpx

    monkeypatch.setattr("ai_gateway_observability.CF_AIGW_OBS_ON", True)
    from ai_gateway_observability import reset_for_tests, snapshot
    reset_for_tests()

    monkeypatch.setenv("CF_AI_GATEWAY_ACCOUNT_ID", "acct-test")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "cf-token-test")
    monkeypatch.setenv("CF_AI_GATEWAY_ID", "gw-test")

    from providers import cloudflare_ai
    importlib.reload(cloudflare_ai)

    sse_body = b'data: {"response": "hi"}\n\ndata: [DONE]\n\n'

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "cf-aig-cache-status": "HIT",
                "cf-aig-log-id": "log-stream-1",
            },
            content=sse_body,
        )

    transport = httpx.MockTransport(_handler)
    fake_client = httpx.AsyncClient(transport=transport)
    monkeypatch.setattr(cloudflare_ai, "_http_client", fake_client)

    async def _drive() -> list[str]:
        out: list[str] = []
        async for chunk in cloudflare_ai.chat_stream(
            [{"role": "user", "content": "hi"}],
            model_key="chat_fast",
        ):
            out.append(chunk)
        return out

    before = snapshot()["counters"]["aig_responses_total"]
    chunks = asyncio.run(_drive())
    assert chunks == ["hi"]

    after_snap = snapshot()
    assert after_snap["counters"]["aig_responses_total"] == before + 1, after_snap
    assert after_snap["counters"]["aig_cache_hits"] >= 1, after_snap
