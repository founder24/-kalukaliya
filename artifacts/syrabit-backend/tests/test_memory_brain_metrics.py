"""Task #417 — unit tests for memory_brain_metrics + the chat-side
instrumentation in memory_brain_chat. Verifies:

  * record_event aggregates by op + kind, computes failure_rate_pct,
    surfaces top failure reasons, and trims to the rolling window;
  * get_hourly_buckets always returns the requested number of buckets
    (so the dashboard sparkline has a stable x-axis);
  * the best-effort wrappers in memory_brain_chat record success on
    the happy path AND record a failure (with classified reason) when
    the underlying provider raises;
  * a feature-disabled wrapper does NOT record an event (we don't
    want a "disabled" deploy to look like a healthy hot path on the
    tile).
"""
from __future__ import annotations

import asyncio
import importlib
import os
import sys
import time

import pytest


@pytest.fixture(autouse=True)
def _isolate_metrics(monkeypatch):
    # Re-import so each test starts with an empty deque even if a
    # previous test left state behind (the module is process-global).
    import memory_brain_metrics as _m
    _m.reset()
    yield
    _m.reset()


def test_record_event_aggregates_by_op_and_kind():
    import memory_brain_metrics as _m
    _m.record_event("write", kind="qa",   ok=True)
    _m.record_event("write", kind="qa",   ok=True)
    _m.record_event("write", kind="fact", ok=False, reason="voyage_error")
    _m.record_event("read",  kind="query", ok=True)
    _m.record_event("read",  kind="query", ok=False, reason="timeout")

    s = _m.get_stats()
    assert s["total"] == 5
    assert s["failures"] == 2
    assert s["failure_rate_pct"] == 40.0
    assert s["by_op"]["write"] == {"ok": 2, "fail": 1, "total": 3}
    assert s["by_op"]["read"]  == {"ok": 1, "fail": 1, "total": 2}
    assert s["by_kind"]["qa"]["ok"] == 2
    assert s["by_kind"]["fact"]["fail"] == 1
    reasons = {r["reason"]: r["count"] for r in s["top_failure_reasons"]}
    assert reasons == {"voyage_error": 1, "timeout": 1}


def test_invalid_op_is_ignored():
    import memory_brain_metrics as _m
    _m.record_event("delete", kind="qa", ok=True)  # not a valid op
    assert _m.get_stats()["total"] == 0


def test_window_filter_excludes_old_events(monkeypatch):
    import memory_brain_metrics as _m
    # Inject an event 2h in the past, then one fresh event. With a
    # 1h window the old one should be excluded from the aggregate.
    now = time.time()
    with _m._lock:
        _m._events.append((now - 7200, "write", "qa", True, None))
    _m.record_event("write", kind="qa", ok=True)
    s = _m.get_stats(window_seconds=3600)
    assert s["total"] == 1


def test_hourly_buckets_returns_requested_length():
    import memory_brain_metrics as _m
    _m.record_event("write", kind="qa", ok=True)
    _m.record_event("read",  kind="query", ok=False, reason="timeout")
    buckets = _m.get_hourly_buckets(hours=12)
    assert len(buckets) == 12
    # All bucket entries have the four counter keys (so the chart's
    # data binding never crashes on an undefined field).
    for b in buckets:
        for k in ("writes_ok", "writes_fail", "reads_ok", "reads_fail"):
            assert k in b
    # Aggregate sum across buckets should equal the per-op totals.
    total_writes = sum(b["writes_ok"] + b["writes_fail"] for b in buckets)
    total_reads  = sum(b["reads_ok"]  + b["reads_fail"]  for b in buckets)
    assert total_writes == 1
    assert total_reads == 1


# ── memory_brain_chat instrumentation ───────────────────────────────


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_chat_write_records_success_when_provider_ok(monkeypatch):
    import memory_brain_metrics as _m
    import memory_brain_chat as mbc

    async def fake_write(user_id, text, *, kind, metadata):
        return "deadbeef"

    fake_mod = type(sys)("providers.memory_brain")
    fake_mod.write_memory = fake_write
    monkeypatch.setitem(sys.modules, "providers.memory_brain", fake_mod)
    monkeypatch.setenv("MEMORY_BRAIN_CHAT_ENABLED", "1")

    asyncio.get_event_loop().run_until_complete(
        mbc.write_chat_turn_memory("u1", "hello", "world")
    )
    s = _m.get_stats()
    assert s["by_op"]["write"]["ok"] == 1
    assert s["by_op"]["write"]["fail"] == 0


def test_chat_write_records_failure_with_classified_reason(monkeypatch):
    import memory_brain_metrics as _m
    import memory_brain_chat as mbc

    async def boom(user_id, text, *, kind, metadata):
        raise RuntimeError("voyage rate limit exceeded")

    fake_mod = type(sys)("providers.memory_brain")
    fake_mod.write_memory = boom
    monkeypatch.setitem(sys.modules, "providers.memory_brain", fake_mod)
    monkeypatch.setenv("MEMORY_BRAIN_CHAT_ENABLED", "1")

    asyncio.get_event_loop().run_until_complete(
        mbc.write_chat_turn_memory("u1", "hello", "world")
    )
    s = _m.get_stats()
    assert s["by_op"]["write"]["fail"] == 1
    reasons = {r["reason"] for r in s["top_failure_reasons"]}
    assert "voyage_error" in reasons


def test_chat_query_timeout_records_failure(monkeypatch):
    import memory_brain_metrics as _m
    import memory_brain_chat as mbc

    async def slow(user_id, query, *, top_k):
        await asyncio.sleep(2)
        return []

    fake_mod = type(sys)("providers.memory_brain")
    fake_mod.query_memory = slow
    monkeypatch.setitem(sys.modules, "providers.memory_brain", fake_mod)
    monkeypatch.setenv("MEMORY_BRAIN_CHAT_ENABLED", "1")

    asyncio.get_event_loop().run_until_complete(
        mbc.query_user_memories("u1", "what is X?", timeout_s=0.05)
    )
    s = _m.get_stats()
    assert s["by_op"]["read"]["fail"] == 1
    reasons = {r["reason"] for r in s["top_failure_reasons"]}
    assert "timeout" in reasons


def test_disabled_feature_does_not_record(monkeypatch):
    import memory_brain_metrics as _m
    import memory_brain_chat as mbc

    monkeypatch.setenv("MEMORY_BRAIN_CHAT_ENABLED", "0")
    asyncio.get_event_loop().run_until_complete(
        mbc.write_chat_turn_memory("u1", "hello", "world")
    )
    asyncio.get_event_loop().run_until_complete(
        mbc.query_user_memories("u1", "x")
    )
    assert _m.get_stats()["total"] == 0
