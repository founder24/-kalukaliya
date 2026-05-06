"""
Task #490 — Option-D automatic controller.

The trip/reset state machine in `embed_degraded_controller` is the
auto-failover layer that flips `is_degraded()` to True without an
operator manually setting `EMBED_DEGRADED_MODE=true`. These tests pin
the locked thresholds (3/5 failures, p95 > 2000ms, reset streak = 5)
so a future refactor of the rule set has to update them too.
"""
from __future__ import annotations

import pytest

import embed_degraded_controller as edc


@pytest.fixture(autouse=True)
def _reset_controller(monkeypatch):
    monkeypatch.delenv("EMBED_DEGRADED_MODE", raising=False)
    edc.reset_for_tests()
    yield
    edc.reset_for_tests()


def test_starts_not_degraded():
    assert edc.is_degraded() is False
    snap = edc.snapshot()
    assert snap["tripped"] is False
    assert snap["window_size"] == 0


def test_trips_on_three_failures_in_window_of_five():
    """3 failed probes within the 5-probe window must flip degraded=True."""
    states: list[tuple[bool, str]] = []
    edc.on_state_change(lambda s, r: states.append((s, r)))

    edc.record_probe(ok=True, latency_ms=80.0)
    edc.record_probe(ok=False, latency_ms=120.0)
    edc.record_probe(ok=False, latency_ms=120.0)
    edc.record_probe(ok=True, latency_ms=80.0)
    assert edc.is_degraded() is False, "must NOT trip on only 2 failures"

    edc.record_probe(ok=False, latency_ms=120.0)
    assert edc.is_degraded() is True
    snap = edc.snapshot()
    assert snap["tripped"] is True
    assert "failures=" in (snap["trip_reason"] or "")
    assert states and states[-1][0] is True


def test_trips_on_p95_latency_exceeding_2000ms():
    """All-success probes with p95 > 2000ms must still trip degraded mode."""
    for _ in range(4):
        edc.record_probe(ok=True, latency_ms=100.0)
    assert edc.is_degraded() is False
    edc.record_probe(ok=True, latency_ms=5000.0)
    assert edc.is_degraded() is True
    snap = edc.snapshot()
    assert "p95=" in (snap["trip_reason"] or "")


def test_resets_after_five_consecutive_ok_probes():
    """Reset edge: 5 consecutive ok probes with p95 <= threshold clears trip."""
    states: list[tuple[bool, str]] = []
    edc.on_state_change(lambda s, r: states.append((s, r)))

    for _ in range(5):
        edc.record_probe(ok=False, latency_ms=120.0)
    assert edc.is_degraded() is True

    for _ in range(5):
        edc.record_probe(ok=True, latency_ms=80.0)
    assert edc.is_degraded() is False
    snap = edc.snapshot()
    assert snap["tripped"] is False
    assert any(s is False for s, _ in states), "reset edge must fire listener with state=False"


def test_failure_during_recovery_resets_streak():
    """A single failure in the middle of the recovery streak must restart it."""
    for _ in range(5):
        edc.record_probe(ok=False, latency_ms=120.0)
    assert edc.is_degraded() is True

    for _ in range(4):
        edc.record_probe(ok=True, latency_ms=80.0)
    assert edc.is_degraded() is True, "must still be tripped after only 4 ok probes"

    edc.record_probe(ok=False, latency_ms=120.0)
    for _ in range(4):
        edc.record_probe(ok=True, latency_ms=80.0)
    assert edc.is_degraded() is True, "interrupted streak must NOT reset trip"


def test_env_override_forces_degraded(monkeypatch):
    """`EMBED_DEGRADED_MODE=true` env var must always report degraded
    regardless of in-process probe state."""
    monkeypatch.setenv("EMBED_DEGRADED_MODE", "true")
    assert edc.is_degraded() is True
    edc.record_probe(ok=True, latency_ms=50.0)
    assert edc.is_degraded() is True


def test_dispatch_uses_controller_for_auto_trip(monkeypatch):
    """End-to-end: tripping the controller (no env var) must route
    `call_embed_with_dispatch` through the SQS deferred-replay path
    and raise `EmbedDegradedMode`. This is the acceptance probe for
    "outage trips automatically before on-call flips the flag"."""
    import asyncio
    import llm
    import sqs_fanout
    from vertex_services import EmbedDegradedMode

    monkeypatch.delenv("EMBED_DEGRADED_MODE", raising=False)
    for _ in range(5):
        edc.record_probe(ok=False, latency_ms=120.0)
    assert edc.is_degraded() is True

    enqueued: list[dict] = []

    async def _fake_enqueue(queue: str, payload: dict) -> str:
        enqueued.append({"queue": queue, "payload": payload})
        return "msg-id-stub"

    monkeypatch.setattr(sqs_fanout, "enqueue", _fake_enqueue, raising=False)

    async def _go():
        return await llm.call_embed_with_dispatch("auto-tripped degraded probe", lang="en")

    with pytest.raises(EmbedDegradedMode):
        asyncio.run(_go())

    assert enqueued and enqueued[0]["queue"] == "reembed"
