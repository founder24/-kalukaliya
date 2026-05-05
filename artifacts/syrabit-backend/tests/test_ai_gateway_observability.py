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
