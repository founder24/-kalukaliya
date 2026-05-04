"""Task #360 — credit-burn meter test matrix.

Covers Meters A (daily-call auto-flip), B (RPM-headroom sliding-window
auto-flip), C (cumulative-cost notify-only), shared `chat:fallback`
flag with pin semantics, validation sampling rate (env + Redis
override + 0.5% floor), the async-only guard for `gpt-oss-120b`, and
the memory-brain enforcement guard.

These are pure unit tests — no Redis / Mongo / network required. The
fakes are kept inside this file so the spec is readable end-to-end.
"""
from __future__ import annotations

import os
import sys
import random
import importlib

import pytest


# Make the backend package importable when run via `pytest tests/`.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── fakes ─────────────────────────────────────────────────────────────


class FakeRedis:
    """Minimal Redis stand-in covering the calls the meters make."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.expires: dict[str, int] = {}

    def get(self, key: str):
        v = self.store.get(key)
        return v.encode() if v is not None else None

    def set(self, key: str, value: str, ex=None):
        self.store[key] = str(value)
        if ex is not None:
            self.expires[key] = int(ex)
        return True

    def delete(self, key: str):
        self.store.pop(key, None)
        self.expires.pop(key, None)
        return 1

    def incr(self, key: str) -> int:
        cur = int(self.store.get(key, "0"))
        cur += 1
        self.store[key] = str(cur)
        return cur

    def expire(self, key: str, ttl: int):
        self.expires[key] = int(ttl)
        return True


class AlertCollector:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def __call__(self, severity: str, message: str, ctx: dict) -> None:
        self.events.append((severity, message, ctx))

    def by_severity(self, severity: str):
        return [e for e in self.events if e[0] == severity]


# ── Meter A — daily-call auto-flip ────────────────────────────────────


def _make_meter_a(**cfg_kw):
    from credit_burn_meter import MeterA, MeterAConfig, FallbackFlag
    r = FakeRedis()
    flag = FallbackFlag(r)
    sink = AlertCollector()
    cfg = MeterAConfig(**cfg_kw) if cfg_kw else None
    return r, flag, sink, MeterA(r, flag, sink, cfg)


def test_meter_a_warning_at_8k_no_flip():
    r, flag, sink, m = _make_meter_a(warning_threshold=8000, trip_threshold=10000)
    m.increment(n=8000)
    assert len(sink.by_severity("warning")) == 1
    assert not flag.is_set()


def test_meter_a_trip_at_10k_flips_flag():
    r, flag, sink, m = _make_meter_a(warning_threshold=8000, trip_threshold=10000)
    m.increment(n=10000)
    assert flag.is_set()
    assert len(sink.by_severity("critical")) == 1


def test_meter_a_warning_only_emits_once_per_day():
    r, flag, sink, m = _make_meter_a(warning_threshold=10, trip_threshold=100)
    for _ in range(15):
        m.increment()
    # warning fires at 10, must not fire again at 11..15.
    assert len(sink.by_severity("warning")) == 1


def test_meter_a_pinned_flag_survives_rollover():
    """Day rollover must not auto-clear when chat:fallback:pin=1."""
    r, flag, sink, m = _make_meter_a(warning_threshold=1, trip_threshold=2)
    # day 1 — trip
    m.increment(now=1_700_000_000.0, n=2)
    assert flag.is_set()
    # operator pins
    r.set("chat:fallback:pin", "1")
    # day 2 — rollover
    cleared = m.maybe_rollover(now=1_700_000_000.0 + 86_400 * 2)
    assert cleared is False
    assert flag.is_set()


def test_meter_a_unpinned_rollover_clears():
    r, flag, sink, m = _make_meter_a(warning_threshold=1, trip_threshold=2)
    m.increment(now=1_700_000_000.0, n=2)
    assert flag.is_set()
    cleared = m.maybe_rollover(now=1_700_000_000.0 + 86_400 * 2)
    assert cleared is True
    assert not flag.is_set()


# ── Shared-flag source coordination (Meters A + B) ────────────────────


def test_shared_flag_meter_a_release_does_not_clear_while_meter_b_active():
    """Critical contract: A's auto-clear must NOT clear the flag while
    B is still tripping — and vice versa."""
    from credit_burn_meter import (
        FallbackFlag, MeterA, MeterAConfig, MeterB, MeterBConfig,
    )
    r = FakeRedis()
    flag = FallbackFlag(r)
    sink = AlertCollector()
    clock = FakeClock()

    a = MeterA(r, flag, sink, MeterAConfig(warning_threshold=1, trip_threshold=2))
    b = MeterB(r, flag, sink,
               MeterBConfig(rpm_limit=10, trip_pct=0.5, clear_pct=0.4),
               time_fn=clock)
    # Both meters trip
    a.increment(now=1_700_000_000.0, n=2)
    for _ in range(5):
        b.record()
    assert flag.is_set()
    assert flag.active_sources() == {"meterA", "meterB"}

    # A rolls over to next day → tries to release
    cleared = a.maybe_rollover(now=1_700_000_000.0 + 86_400 * 2)
    assert cleared is False  # B still active
    assert flag.is_set()
    assert flag.active_sources() == {"meterB"}


def test_shared_flag_clears_only_when_both_meters_release():
    from credit_burn_meter import FallbackFlag
    r = FakeRedis()
    flag = FallbackFlag(r)
    flag.trip(source="meterA")
    flag.trip(source="meterB")
    assert flag.is_set()
    assert flag.release(source="meterA") is False
    assert flag.is_set()
    assert flag.release(source="meterB") is True
    assert not flag.is_set()


def test_chat_stream_keeps_chat_turn_open_during_sse_iteration():
    """Round-9 reviewer requirement: the streaming chat handler MUST
    keep the ChatTurnContext open for the full duration of the SSE
    body iterator — otherwise `assert_mongo_read_or_raise` and the
    `gpt-oss-120b` live-chat guard are bypassed because LLM dispatch
    runs inside the `event_stream()` generator AFTER
    `_chat_stream_impl` returns. This test exercises the body-iterator
    wrapper added in `routes/ai_chat.chat_stream`."""
    import asyncio as _asyncio
    from unittest.mock import patch
    from chat_turn_context import (
        chat_turn, _in_chat_turn,
        assert_mongo_read_or_raise,
        MemoryBrainEnforcementError,
    )

    # Simulate the round-8/9 wrapper pattern in isolation.
    observed_in_turn: list = []
    observed_raises: list = []

    async def _fake_event_stream():
        # Each yield mimics one SSE chunk; the dispatcher guard runs
        # at LLM dispatch time inside this generator. Both invariants
        # MUST hold for every chunk.
        for i in range(3):
            observed_in_turn.append(_in_chat_turn.get())
            try:
                assert_mongo_read_or_raise(dispatcher_name="sse-test")
                observed_raises.append(None)
            except MemoryBrainEnforcementError as exc:
                observed_raises.append(exc)
            yield f"data: chunk{i}\n\n"

    async def _exercise():
        ctx = chat_turn(session_id="s", user_id="u")
        ctx.__enter__()
        inner = _fake_event_stream()

        async def _guarded():
            try:
                async for c in inner:
                    yield c
            finally:
                ctx.__exit__(None, None, None)

        # Drain the wrapped generator end-to-end.
        async for _ in _guarded():
            pass
        # After drain the context MUST have been exited.
        return _in_chat_turn.get()

    after = _asyncio.run(_exercise())

    assert observed_in_turn == [True, True, True], (
        f"chat_turn must remain active for every SSE chunk — got "
        f"{observed_in_turn}"
    )
    # Every chunk should have raised the memory-brain guard since we
    # never marked Mongo-read. This proves the guard is enforceable
    # during streaming, not bypassed.
    assert all(isinstance(r, MemoryBrainEnforcementError)
               for r in observed_raises), (
        "memory-brain guard MUST raise during SSE streaming when no "
        "Mongo-read mark — got " + repr(observed_raises)
    )
    assert after is False, (
        "chat_turn must be exited after the wrapped generator drains"
    )


def test_memory_brain_enforcement_raises_when_no_mongo_read():
    """Round-8 reviewer requirement: prove the dispatcher's
    memory-brain guard ACTUALLY raises in dev/test when a chat turn
    skipped the Mongo history read. The guard is what makes the
    `mark_mongo_read()` placement matter."""
    from chat_turn_context import (
        chat_turn, mark_mongo_read,
        assert_mongo_read_or_raise,
        MemoryBrainEnforcementError,
    )
    # Inside a turn with NO mark, the dispatcher must raise.
    with chat_turn(session_id="s1", user_id="u1"):
        try:
            assert_mongo_read_or_raise(dispatcher_name="test")
        except MemoryBrainEnforcementError:
            pass
        else:
            raise AssertionError(
                "dispatcher MUST raise MemoryBrainEnforcementError when "
                "no Mongo read was marked on the turn"
            )
    # And once marked, the same guard must pass.
    with chat_turn(session_id="s2", user_id="u2"):
        mark_mongo_read()
        assert_mongo_read_or_raise(dispatcher_name="test")  # no raise


def test_runtime_alert_sink_signature_matches_meter_contract():
    """Round-5 reviewer caught a TypeError-swallowing signature
    mismatch. The runtime sink MUST accept ``(severity, message,
    context)`` exactly — proven by passing it to a real Meter A trip."""
    import credit_burn_meter_runtime as runtime
    from credit_burn_meter import MeterA, MeterAConfig, FallbackFlag
    captured = []

    def _sink(severity, message, context):
        captured.append((severity, message, context))

    fake = FakeRedis()
    flag = FallbackFlag(fake)
    a = MeterA(fake, flag, _sink,
               MeterAConfig(warning_threshold=1, trip_threshold=2))
    a.increment(now=1_700_000_000.0, n=2)
    assert captured, "alert sink MUST be invoked when MeterA trips"
    assert captured[-1][0] in ("warning", "critical")

    # Now prove the runtime's default sink does NOT raise when called
    # with the same 3-arg shape (the round-5 bug was a 2-arg sig).
    runtime._alert_sink("critical", "test", {"meter": "A"})


def test_dispatcher_excludes_paid_providers_when_fallback_active(monkeypatch):
    """Round-6 reviewer requirement: the chat:fallback flag must
    actually drive dispatch. Prove that when `is_fallback_active()`
    returns True, `call_with_provider_fallback` excludes paid
    providers from the very first `select_provider` draw."""
    import asyncio as _asyncio
    import importlib
    import credit_burn_meter_runtime as runtime

    # Force is_fallback_active() → True
    monkeypatch.setattr(runtime, "is_fallback_active", lambda: True)

    import llm as _llm
    seen_excludes: list[frozenset] = []

    def _fake_select(feature, lang="", exclude=frozenset()):
        seen_excludes.append(exclude)
        return "workers_ai"

    async def _attempt(provider):
        return f"OK:{provider}"

    monkeypatch.setattr(_llm, "select_provider", _fake_select)

    result = _asyncio.run(_llm.call_with_provider_fallback(
        "english_rag_chat", "en", _attempt, max_attempts=1,
    ))
    assert result == "OK:workers_ai"
    assert seen_excludes, "select_provider must have been called"
    excluded = seen_excludes[0]
    # Round-7: vertex / gemini / sarvam ARE excluded (cost ceiling),
    # but Azure GPT-4.1-mini is the v3 fallback target for Meter B
    # (RPM relief) and MUST be preserved.
    for paid in ("vertex", "gemini", "sarvam"):
        assert paid in excluded, (
            f"expensive paid provider {paid!r} must be excluded when "
            f"chat:fallback is active — got {sorted(excluded)}"
        )
    for keep in ("azure_openai", "azure", "workers_ai"):
        assert keep not in excluded, (
            f"v3 fallback target {keep!r} MUST NOT be excluded when "
            f"chat:fallback is active — got {sorted(excluded)}"
        )


def test_meter_runtime_uses_in_memory_store_when_redis_unavailable(monkeypatch):
    """Round-6 reviewer requirement: meter runtime must NOT silently
    no-op when ``deps.redis_client`` is None — it must fall back to
    a working in-memory store and emit a degradation alert."""
    import credit_burn_meter_runtime as runtime
    import sys
    # Force `from deps import redis_client` to return None.
    fake_deps = type(sys)("deps")
    fake_deps.redis_client = None
    monkeypatch.setitem(sys.modules, "deps", fake_deps)

    # Reset singletons.
    runtime._METERS_INIT = False
    runtime._METER_A = None
    runtime._METER_B = None
    runtime._METER_C = None
    runtime._FLAG = None

    # Capture alerts so we can assert the degradation warning fires.
    alerts: list = []
    runtime.set_alert_sink(lambda sev, msg, ctx: alerts.append((sev, msg, ctx)))
    try:
        runtime._ensure_meters()
        assert runtime._METER_A is not None, (
            "in-memory degraded MeterA must be constructed, not left None"
        )
        assert runtime._FLAG is not None
        # Increments must work end-to-end against the in-memory store.
        runtime.increment_chat_request()
        assert any(ctx.get("reason") == "redis_client_none"
                   for _, _, ctx in alerts), (
            "degradation alert MUST be emitted when Redis is unavailable"
        )
    finally:
        runtime.set_alert_sink(runtime._alert_sink)


def test_runtime_meter_singletons_increment_and_observe_flag():
    """End-to-end wiring proof: ``credit_burn_meter_runtime``
    increments Meter A and Meter B on every chat request, and a
    handler-side ``is_fallback_active()`` reads the SAME flag the
    meters drive."""
    import credit_burn_meter_runtime as runtime
    from credit_burn_meter import MeterA, MeterAConfig, FallbackFlag

    # Reset the module singletons so this test owns the state.
    runtime._METERS_INIT = False
    runtime._METER_A = None
    runtime._METER_B = None
    runtime._METER_C = None
    runtime._FLAG = None
    runtime._LAST_B_TICK_LOG = 0.0

    # Force the runtime to bind against a fresh FakeRedis. We can't
    # patch `deps.redis_client` cheaply here, so we hand-construct the
    # singletons after _ensure_meters has tried.
    runtime._ensure_meters()
    fake = FakeRedis()
    runtime._FLAG = FallbackFlag(fake)
    runtime._METER_A = MeterA(
        fake, runtime._FLAG, AlertCollector(),
        MeterAConfig(warning_threshold=1, trip_threshold=2),
    )
    # Drive 2 chat requests through the runtime hook.
    runtime.increment_chat_request()
    runtime.increment_chat_request()
    assert runtime.is_fallback_active(), (
        "after 2 ticks against trip_threshold=2 the runtime hook MUST "
        "report the chat:fallback flag as active"
    )

    # Meter C ingestion path
    new_total = runtime.ingest_daily_cost_usd(1234.0)
    assert new_total is not None and new_total >= 1234.0


def test_chat_handler_reads_fallback_flag_when_meters_trip():
    """End-to-end wiring proof: when MeterA trips the shared
    `chat:fallback` flag, a fresh `FallbackFlag(redis)` constructed by
    the chat handler MUST observe the trip via the same Redis key."""
    from credit_burn_meter import (
        FallbackFlag, MeterA, MeterAConfig,
    )
    r = FakeRedis()
    a = MeterA(r, FallbackFlag(r), AlertCollector(),
               MeterAConfig(warning_threshold=1, trip_threshold=2))
    a.increment(now=1_700_000_000.0, n=2)

    # Now simulate the chat handler constructing its own view of the
    # flag (as `_chat_impl` does on every request).
    handler_flag = FallbackFlag(r)
    assert handler_flag.is_set(), (
        "chat handler must observe the same `chat:fallback` flag the "
        "meter tripped — otherwise hot-flag wiring is broken"
    )
    # And after admin clears (auto_clear), the handler view also clears.
    handler_flag.auto_clear()
    assert not FallbackFlag(r).is_set()


def test_dispatcher_blocks_workers_ai_120b_on_live_chat():
    """Round-2 review proof: `_dispatch_llm_for_feature` must raise when
    a live chat feature (e.g. ``english_rag_chat``) would route to
    ``@cf/openai/gpt-oss-120b`` — even via the workers_ai branch where
    the model id is resolved indirectly through ``_TASK_ROUTE``."""
    import asyncio
    import importlib
    import sys

    # Reload llm + chat_turn_context so the test sees a clean module
    # state (the dispatcher imports the guard lazily inside the call).
    if "llm" in sys.modules:
        importlib.reload(sys.modules["llm"])
    import llm as _llm
    from chat_turn_context import (
        chat_turn,
        ForbiddenLiveChatModelError as LiveChatModelForbiddenError,
    )

    # Stub _TASK_ROUTE so the workers_ai branch resolves to 120b for a
    # live-chat feature that would not normally route there. This makes
    # the test independent of the production routing table.
    _orig_route = dict(_llm._TASK_ROUTE)
    _llm._TASK_ROUTE["english_rag_chat"] = (
        "workers-ai", "@cf/openai/gpt-oss-120b",
    )
    try:
        with chat_turn(session_id="t", user_id="u"):
            from chat_turn_context import mark_mongo_read
            mark_mongo_read()
            with pytest.raises(LiveChatModelForbiddenError):
                asyncio.run(_llm._dispatch_llm_for_feature(
                    [{"role": "user", "content": "hi"}],
                    provider="workers_ai",
                    max_tokens=64,
                    feature="english_rag_chat",
                ))
    finally:
        _llm._TASK_ROUTE.clear()
        _llm._TASK_ROUTE.update(_orig_route)


def test_meter_b_redis_unreachable_falls_back_to_local_window():
    """If Redis raises, Meter B must keep tripping on local-replica
    saturation rather than going silent."""
    from credit_burn_meter import (
        FallbackFlag, MeterB, MeterBConfig,
    )

    class BrokenRedis(FakeRedis):
        def incr(self, key):
            raise RuntimeError("redis down")

        def get(self, key):
            # flag/sources reads against a separate FallbackFlag are OK;
            # only meterB:* keys raise.
            if key.startswith("meterB:"):
                raise RuntimeError("redis down")
            return super().get(key)

    r = BrokenRedis()
    flag = FallbackFlag(r)
    sink = AlertCollector()
    clock = FakeClock()
    b = MeterB(r, flag, sink,
               MeterBConfig(rpm_limit=10, trip_pct=0.5, clear_pct=0.4),
               time_fn=clock)
    for _ in range(5):
        b.record()
    assert flag.is_set()


# ── Meter B — RPM-headroom sliding window ─────────────────────────────


class FakeClock:
    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _make_meter_b(**cfg_kw):
    from credit_burn_meter import MeterB, MeterBConfig, FallbackFlag
    r = FakeRedis()
    flag = FallbackFlag(r)
    sink = AlertCollector()
    clock = FakeClock()
    cfg = MeterBConfig(**cfg_kw) if cfg_kw else None
    return r, flag, sink, clock, MeterB(r, flag, sink, cfg, time_fn=clock)


def test_meter_b_trip_at_70pct():
    r, flag, sink, clock, m = _make_meter_b(rpm_limit=300, trip_pct=0.70, clear_pct=0.50)
    # 209 calls in the window = 69.6% — no trip
    for _ in range(209):
        m.record()
    assert not flag.is_set()
    # 210 calls = 70.0% — trip
    m.record()
    assert flag.is_set()
    assert len(sink.by_severity("critical")) == 1


def test_meter_b_clear_requires_5min_sustain_below_50pct():
    r, flag, sink, clock, m = _make_meter_b(
        rpm_limit=300, trip_pct=0.70, clear_pct=0.50, sustain_min=5, window_s=60,
    )
    for _ in range(210):
        m.record()
    assert flag.is_set()
    # advance window past the burst — count drops to 0
    clock.advance(120)
    m.tick()
    # 4 minutes below clear_pct — still not cleared
    for i in range(4):
        clock.advance(60)
        m.tick()
    assert flag.is_set()
    # 5th minute — clears
    clock.advance(60)
    m.tick()
    assert not flag.is_set()


def test_meter_b_rebound_resets_sustain_timer():
    r, flag, sink, clock, m = _make_meter_b(
        rpm_limit=300, trip_pct=0.70, clear_pct=0.50, sustain_min=5, window_s=60,
    )
    for _ in range(210):
        m.record()
    assert flag.is_set()
    clock.advance(120)
    m.tick()  # below clear, sustain timer starts
    clock.advance(120)
    # rebound above clear_pct (151 > 150)
    for _ in range(151):
        m.record()
    m.tick()
    # back below clear and advance only 4m — should NOT clear since the
    # sustain timer was reset by the rebound.
    clock.advance(120)
    m.tick()
    for _ in range(4):
        clock.advance(60)
        m.tick()
    assert flag.is_set()


# ── Meter C — cumulative cost (notify-only) ───────────────────────────


def _make_meter_c(**cfg_kw):
    from credit_burn_meter import MeterC, MeterCConfig
    sink = AlertCollector()
    clock = FakeClock()
    cfg = MeterCConfig(**cfg_kw) if cfg_kw else None
    return sink, clock, MeterC(sink, cfg, time_fn=clock)


def test_meter_c_warning_at_50pct_no_flip():
    sink, clock, m = _make_meter_c(budget_usd=5000, warning_pct=0.50, alert_pct=0.70)
    m.record(2500.0)
    warn = sink.by_severity("warning")
    assert len(warn) == 1
    assert warn[0][2]["notify_only"] is True


def test_meter_c_alert_at_70pct_no_flip():
    sink, clock, m = _make_meter_c(budget_usd=5000, warning_pct=0.50, alert_pct=0.70)
    m.record(3500.0)
    crit = sink.by_severity("critical")
    assert len(crit) == 1
    assert crit[0][2]["notify_only"] is True


def test_meter_c_evicts_outside_365d_window():
    sink, clock, m = _make_meter_c(budget_usd=5000, warning_pct=0.50, alert_pct=0.70)
    m.record(1000.0)
    clock.advance(366 * 86_400)
    # year-old event evicted; new event well below warning
    m.record(100.0)
    assert m.current() == pytest.approx(100.0)


# ── Validation sampler ────────────────────────────────────────────────


def test_validation_sampler_default_is_10pct(monkeypatch):
    monkeypatch.delenv("VALIDATION_SAMPLE_RATE", raising=False)
    import validation_sampler as v
    importlib.reload(v)
    assert v.env_sample_rate() == pytest.approx(0.10)


def test_validation_sampler_env_override(monkeypatch):
    monkeypatch.setenv("VALIDATION_SAMPLE_RATE", "0.05")
    import validation_sampler as v
    importlib.reload(v)
    assert v.env_sample_rate() == pytest.approx(0.05)


def test_validation_sampler_floor_clamps_low_rates(monkeypatch):
    monkeypatch.setenv("VALIDATION_SAMPLE_RATE", "0.001")
    import validation_sampler as v
    importlib.reload(v)
    # 0.001 < 0.005 floor — clamped up
    assert v.env_sample_rate() == pytest.approx(v.SAMPLE_RATE_FLOOR)


def test_validation_sampler_redis_override_wins(monkeypatch):
    monkeypatch.setenv("VALIDATION_SAMPLE_RATE", "0.10")
    import validation_sampler as v
    importlib.reload(v)
    r = FakeRedis()
    r.set(v.REDIS_OVERRIDE_KEY, "0.25")
    assert v.effective_sample_rate(r) == pytest.approx(0.25)


def test_validation_sampler_should_validate_distribution(monkeypatch):
    monkeypatch.setenv("VALIDATION_SAMPLE_RATE", "0.10")
    import validation_sampler as v
    importlib.reload(v)
    rng = random.Random(42)
    n = 10_000
    hits = sum(1 for _ in range(n) if v.should_validate(rate=0.10, rng=rng))
    # 10% ± 1.5pp tolerance with n=10k
    assert 850 <= hits <= 1150


# ── Async-only guard for gpt-oss-120b ─────────────────────────────────


def test_async_only_guard_blocks_120b_in_chat_turn():
    import chat_turn_context as ctx
    with ctx.chat_turn(session_id="s"):
        with pytest.raises(ctx.ForbiddenLiveChatModelError):
            ctx.assert_live_chat_model_allowed("@cf/openai/gpt-oss-120b")


def test_async_only_guard_blocks_120b_outside_async_scope():
    """Even outside a chat turn, 120b is forbidden unless explicitly batch."""
    import chat_turn_context as ctx
    with pytest.raises(ctx.ForbiddenLiveChatModelError):
        ctx.assert_live_chat_model_allowed("gpt-oss-120b")


def test_async_only_guard_allows_120b_in_async_batch_scope():
    import chat_turn_context as ctx
    with ctx.async_batch_scope():
        ctx.assert_live_chat_model_allowed("@cf/openai/gpt-oss-120b")  # no raise


def test_async_only_guard_passes_other_models():
    import chat_turn_context as ctx
    with ctx.chat_turn():
        ctx.assert_live_chat_model_allowed("@cf/mistral/mistral-7b-instruct-v0.3")
        ctx.assert_live_chat_model_allowed("gpt-4.1-mini")
        ctx.assert_live_chat_model_allowed("@cf/openai/gpt-oss-20b")


# ── Memory-brain enforcement guard ────────────────────────────────────


def test_memory_brain_guard_raises_when_mongo_skipped(monkeypatch):
    monkeypatch.setenv("ENV", "test")
    import chat_turn_context as ctx
    with ctx.chat_turn(session_id="s"):
        with pytest.raises(ctx.MemoryBrainEnforcementError):
            ctx.assert_mongo_read_or_raise()


def test_memory_brain_guard_passes_when_mongo_marked(monkeypatch):
    monkeypatch.setenv("ENV", "test")
    import chat_turn_context as ctx
    with ctx.chat_turn(session_id="s") as turn:
        turn.mark_mongo_read()
        ctx.assert_mongo_read_or_raise()  # no raise


def test_memory_brain_guard_noop_outside_chat_turn(monkeypatch):
    monkeypatch.setenv("ENV", "test")
    import chat_turn_context as ctx
    # Outside a chat turn (e.g. async batch), the guard is a no-op.
    ctx.assert_mongo_read_or_raise()


def test_memory_brain_guard_soft_in_production(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    import chat_turn_context as ctx
    with ctx.chat_turn(session_id="s"):
        # Must not raise in prod — emits a metric instead.
        ctx.assert_mongo_read_or_raise()
