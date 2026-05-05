"""Task #412 — Embed-stack health watchdog alert tests.

Verifies the per-leg consecutive-failure semantics of
``metrics._check_embed_stack_health_once`` so a single transient blip
never pages on-call but ``threshold`` consecutive failures do.
"""
from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("CF_ACCOUNT_ID", "test-account")
os.environ.setdefault("CF_AI_GATEWAY_TOKEN", "test-token")

from tests._deps_stub import install_deps_stub  # noqa: E402

install_deps_stub()

import metrics as metrics_mod  # noqa: E402


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture(autouse=True)
def _reset_embed_stack_state():
    for leg in metrics_mod._EMBED_STACK_LEGS:
        metrics_mod._embed_stack_consecutive_failures[leg] = 0
        metrics_mod._embed_stack_was_firing[leg] = False
        metrics_mod._embed_stack_last_error[leg] = None
        metrics_mod._embed_stack_last_latency_ms[leg] = None
    metrics_mod._alert_last_fired.clear()
    yield
    for leg in metrics_mod._EMBED_STACK_LEGS:
        metrics_mod._embed_stack_consecutive_failures[leg] = 0
        metrics_mod._embed_stack_was_firing[leg] = False
    metrics_mod._alert_last_fired.clear()


def _patch_probe(per_leg_results):
    """Replace _probe_embed_stack_leg with a stub that returns
    ``per_leg_results[leg].pop(0)`` on each call."""
    queues = {leg: list(seq) for leg, seq in per_leg_results.items()}

    async def _stub(leg):
        return queues[leg].pop(0)

    return patch.object(metrics_mod, "_probe_embed_stack_leg", _stub)


class TestConsecutiveFailureThreshold:
    def test_single_failure_does_not_page(self):
        dispatch = AsyncMock()
        results = {
            "embed":        [{"ok": False, "reason": "boom", "latency_ms": 12}],
            "rerank":       [{"ok": True,  "latency_ms": 5}],
            "memory_brain": [{"ok": True,  "latency_ms": 5}],
        }
        with _patch_probe(results), patch.object(metrics_mod, "_dispatch_alert", dispatch):
            snap = _run(metrics_mod._check_embed_stack_health_once(threshold=3))
        dispatch.assert_not_awaited()
        assert snap["embed"]["consecutive_failures"] == 1
        assert snap["embed"]["firing"] is False

    def test_two_failures_still_silent(self):
        dispatch = AsyncMock()
        results = {
            "embed":        [{"ok": False, "reason": "boom", "latency_ms": 11}] * 2,
            "rerank":       [{"ok": True,  "latency_ms": 5}] * 2,
            "memory_brain": [{"ok": True,  "latency_ms": 5}] * 2,
        }
        with _patch_probe(results), patch.object(metrics_mod, "_dispatch_alert", dispatch):
            _run(metrics_mod._check_embed_stack_health_once(threshold=3))
            _run(metrics_mod._check_embed_stack_health_once(threshold=3))
        dispatch.assert_not_awaited()
        assert metrics_mod._embed_stack_consecutive_failures["embed"] == 2

    def test_third_consecutive_failure_pages(self):
        dispatch = AsyncMock()
        results = {
            "embed":        [{"ok": False, "reason": "timeout", "latency_ms": 99}] * 3,
            "rerank":       [{"ok": True,  "latency_ms": 5}] * 3,
            "memory_brain": [{"ok": True,  "latency_ms": 5}] * 3,
        }
        with _patch_probe(results), patch.object(metrics_mod, "_dispatch_alert", dispatch):
            for _ in range(3):
                _run(metrics_mod._check_embed_stack_health_once(threshold=3))
        dispatch.assert_awaited_once()
        args = dispatch.await_args.args
        assert args[0] == "embed_stack_embed_unhealthy"
        snap = dispatch.await_args.kwargs.get("threshold_snapshot") or args[3]
        assert snap["leg"] == "embed"
        assert snap["actual"] == 3
        assert snap["latency_ms"] == 99
        assert snap["last_error"] == "timeout"
        assert "timeout" in args[2]
        assert "99" in args[2]

    def test_success_between_failures_resets_counter(self):
        dispatch = AsyncMock()
        results = {
            "embed": [
                {"ok": False, "reason": "x", "latency_ms": 1},
                {"ok": False, "reason": "x", "latency_ms": 1},
                {"ok": True,  "latency_ms": 5},        # reset
                {"ok": False, "reason": "x", "latency_ms": 1},
                {"ok": False, "reason": "x", "latency_ms": 1},
            ],
            "rerank":       [{"ok": True, "latency_ms": 5}] * 5,
            "memory_brain": [{"ok": True, "latency_ms": 5}] * 5,
        }
        with _patch_probe(results), patch.object(metrics_mod, "_dispatch_alert", dispatch):
            for _ in range(5):
                _run(metrics_mod._check_embed_stack_health_once(threshold=3))
        dispatch.assert_not_awaited()
        assert metrics_mod._embed_stack_consecutive_failures["embed"] == 2

    def test_does_not_repage_while_firing(self):
        dispatch = AsyncMock()
        results = {
            "embed":        [{"ok": False, "reason": "x", "latency_ms": 1}] * 5,
            "rerank":       [{"ok": True,  "latency_ms": 5}] * 5,
            "memory_brain": [{"ok": True,  "latency_ms": 5}] * 5,
        }
        with _patch_probe(results), patch.object(metrics_mod, "_dispatch_alert", dispatch):
            for _ in range(5):
                _run(metrics_mod._check_embed_stack_health_once(threshold=3))
        # 1 page on the 3rd failure, no re-page on tick 4 / 5.
        assert dispatch.await_count == 1

    def test_recovery_alert_fires_after_paging_then_green(self):
        dispatch = AsyncMock()
        results = {
            "embed": [
                {"ok": False, "reason": "x", "latency_ms": 1},
                {"ok": False, "reason": "x", "latency_ms": 1},
                {"ok": False, "reason": "x", "latency_ms": 1},  # pages
                {"ok": True,  "latency_ms": 5},                  # recovery
            ],
            "rerank":       [{"ok": True, "latency_ms": 5}] * 4,
            "memory_brain": [{"ok": True, "latency_ms": 5}] * 4,
        }
        with _patch_probe(results), patch.object(metrics_mod, "_dispatch_alert", dispatch):
            for _ in range(4):
                _run(metrics_mod._check_embed_stack_health_once(threshold=3))
        types = [c.args[0] for c in dispatch.await_args_list]
        assert types == ["embed_stack_embed_unhealthy", "embed_stack_embed_recovered"]

    def test_each_leg_has_its_own_counter(self):
        dispatch = AsyncMock()
        results = {
            "embed":        [{"ok": False, "reason": "e", "latency_ms": 1}] * 3,
            "rerank":       [{"ok": True,  "latency_ms": 5}] * 3,
            "memory_brain": [{"ok": False, "reason": "m", "latency_ms": 2}] * 3,
        }
        with _patch_probe(results), patch.object(metrics_mod, "_dispatch_alert", dispatch):
            for _ in range(3):
                _run(metrics_mod._check_embed_stack_health_once(threshold=3))
        types = sorted(c.args[0] for c in dispatch.await_args_list)
        assert types == [
            "embed_stack_embed_unhealthy",
            "embed_stack_memory_brain_unhealthy",
        ]
        assert metrics_mod._embed_stack_consecutive_failures["rerank"] == 0

    def test_threshold_zero_disables_watchdog(self):
        dispatch = AsyncMock()
        # Even with all-failure stubs, a 0 threshold must not page.
        results = {
            "embed":        [{"ok": False, "reason": "x"}] * 3,
            "rerank":       [{"ok": False, "reason": "x"}] * 3,
            "memory_brain": [{"ok": False, "reason": "x"}] * 3,
        }
        with _patch_probe(results), patch.object(metrics_mod, "_dispatch_alert", dispatch):
            for _ in range(3):
                _run(metrics_mod._check_embed_stack_health_once(threshold=0))
        dispatch.assert_not_awaited()


class TestThresholdRegistered:
    def test_default_threshold_present(self):
        assert (
            metrics_mod._ALERT_THRESHOLDS_DEFAULT.get(
                "embed_stack_consecutive_failures_threshold"
            )
            == 3
        )
