"""Task #476 — Staging-vs-production embed-worker drift watchdog tests.

Pins the comparison logic and per-tick behaviour of
``metrics._check_embed_stack_drift_once`` so a single staging deploy
blip never pages, but ``threshold`` consecutive drift observations do —
and only via the Slack channel (no email, no browser push).
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
def _reset_drift_state():
    metrics_mod._embed_stack_drift_consecutive = 0
    metrics_mod._embed_stack_drift_was_firing = False
    metrics_mod._embed_stack_drift_last_payload = None
    metrics_mod._alert_last_fired.clear()
    yield
    metrics_mod._embed_stack_drift_consecutive = 0
    metrics_mod._embed_stack_drift_was_firing = False
    metrics_mod._embed_stack_drift_last_payload = None
    metrics_mod._alert_last_fired.clear()


def _patch_envs(env_seq):
    """Replace ``providers.workers_embed.health_check_environments`` so
    each call returns ``env_seq.pop(0)``."""
    queue = list(env_seq)

    async def _stub():
        return queue.pop(0)

    import providers.workers_embed as we
    return patch.object(we, "health_check_environments", _stub)


def _prod_ok(model_version="gemma-300m+qwen3-0.6b", dims=1024):
    return {
        "env": "production", "label": "Production", "configured": True,
        "ok": True, "url": "https://embed.syrabit.ai",
        "model_version": model_version, "dims": dims, "pages": True,
    }


def _staging_ok(model_version="gemma-300m+qwen3-0.6b", dims=1024):
    return {
        "env": "staging", "label": "Staging", "configured": True,
        "ok": True, "url": "https://embed-staging.syrabit.ai",
        "model_version": model_version, "dims": dims, "pages": False,
    }


class TestComparisonLogic:
    def test_matching_envs_no_drift(self):
        out = metrics_mod._compare_embed_environments(
            _prod_ok(), _staging_ok()
        )
        assert out["comparable"] is True
        assert out["drift"] is False
        assert out["drift_fields"] == []

    def test_model_version_drift(self):
        out = metrics_mod._compare_embed_environments(
            _prod_ok(model_version="gemma-300m+qwen3-0.6b"),
            _staging_ok(model_version="gemma-300m+qwen3-1.7b"),
        )
        assert out["comparable"] is True
        assert out["drift"] is True
        assert out["drift_fields"] == ["model_version"]

    def test_dims_drift(self):
        out = metrics_mod._compare_embed_environments(
            _prod_ok(dims=1024), _staging_ok(dims=768)
        )
        assert out["drift"] is True
        assert out["drift_fields"] == ["dims"]

    def test_both_fields_drift(self):
        out = metrics_mod._compare_embed_environments(
            _prod_ok("a", 1024), _staging_ok("b", 768)
        )
        assert out["drift_fields"] == ["model_version", "dims"]

    def test_unconfigured_staging_not_comparable(self):
        out = metrics_mod._compare_embed_environments(
            _prod_ok(),
            {"env": "staging", "configured": False, "ok": False},
        )
        assert out["comparable"] is False
        assert out["drift"] is False

    def test_unhealthy_staging_not_comparable(self):
        stg = _staging_ok()
        stg["ok"] = False
        out = metrics_mod._compare_embed_environments(_prod_ok(), stg)
        assert out["comparable"] is False


class TestDriftWatchdogTicks:
    def test_single_drift_does_not_alert(self):
        dispatch = AsyncMock()
        envs = [[_prod_ok(), _staging_ok(model_version="other")]]
        with _patch_envs(envs), patch.object(metrics_mod, "_dispatch_alert", dispatch):
            snap = _run(metrics_mod._check_embed_stack_drift_once(threshold=3))
        dispatch.assert_not_awaited()
        assert snap["consecutive"] == 1
        assert snap["firing"] is False

    def test_two_consecutive_still_silent(self):
        dispatch = AsyncMock()
        envs = [
            [_prod_ok(), _staging_ok(model_version="other")],
            [_prod_ok(), _staging_ok(model_version="other")],
        ]
        with _patch_envs(envs), patch.object(metrics_mod, "_dispatch_alert", dispatch):
            _run(metrics_mod._check_embed_stack_drift_once(threshold=3))
            _run(metrics_mod._check_embed_stack_drift_once(threshold=3))
        dispatch.assert_not_awaited()
        assert metrics_mod._embed_stack_drift_consecutive == 2

    def test_third_consecutive_drift_pages_slack(self):
        dispatch = AsyncMock()
        envs = [
            [_prod_ok(dims=1024), _staging_ok(dims=768)],
        ] * 3
        with _patch_envs(envs), patch.object(metrics_mod, "_dispatch_alert", dispatch):
            for _ in range(3):
                _run(metrics_mod._check_embed_stack_drift_once(threshold=3))
        dispatch.assert_awaited_once()
        args = dispatch.await_args.args
        assert args[0] == "embed_stack_staging_drift"
        snap = dispatch.await_args.kwargs.get("threshold_snapshot") or {}
        assert snap["actual"] == 3
        assert snap["drift_fields"] == ["dims"]
        assert snap["production"]["dims"] == 1024
        assert snap["staging"]["dims"] == 768
        assert metrics_mod._embed_stack_drift_was_firing is True

    def test_recovery_resets_counter_without_alert(self):
        dispatch = AsyncMock()
        envs = [
            [_prod_ok(), _staging_ok(model_version="other")],
            [_prod_ok(), _staging_ok(model_version="other")],
            [_prod_ok(), _staging_ok()],   # drift gone
        ]
        with _patch_envs(envs), patch.object(metrics_mod, "_dispatch_alert", dispatch):
            for _ in range(3):
                _run(metrics_mod._check_embed_stack_drift_once(threshold=3))
        dispatch.assert_not_awaited()
        assert metrics_mod._embed_stack_drift_consecutive == 0

    def test_does_not_repage_while_firing(self):
        dispatch = AsyncMock()
        envs = [
            [_prod_ok(), _staging_ok(model_version="other")],
        ] * 5
        with _patch_envs(envs), patch.object(metrics_mod, "_dispatch_alert", dispatch):
            for _ in range(5):
                _run(metrics_mod._check_embed_stack_drift_once(threshold=3))
        # 1 page on the 3rd drift, no re-page on tick 4 / 5.
        assert dispatch.await_count == 1

    def test_recovery_alert_fires_after_paging_then_realigned(self):
        dispatch = AsyncMock()
        envs = [
            [_prod_ok(), _staging_ok(model_version="other")],
            [_prod_ok(), _staging_ok(model_version="other")],
            [_prod_ok(), _staging_ok(model_version="other")],  # pages
            [_prod_ok(), _staging_ok()],                        # recovery
        ]
        with _patch_envs(envs), patch.object(metrics_mod, "_dispatch_alert", dispatch):
            for _ in range(4):
                _run(metrics_mod._check_embed_stack_drift_once(threshold=3))
        types = [c.args[0] for c in dispatch.await_args_list]
        assert types == [
            "embed_stack_staging_drift",
            "embed_stack_staging_drift_recovered",
        ]
        assert metrics_mod._embed_stack_drift_was_firing is False

    def test_unconfigured_staging_resets_streak(self):
        dispatch = AsyncMock()
        envs = [
            [_prod_ok(), _staging_ok(model_version="other")],   # +1
            [_prod_ok(), _staging_ok(model_version="other")],   # +2
            [_prod_ok(), {"env": "staging", "configured": False, "ok": False}],
        ]
        with _patch_envs(envs), patch.object(metrics_mod, "_dispatch_alert", dispatch):
            for _ in range(3):
                _run(metrics_mod._check_embed_stack_drift_once(threshold=3))
        dispatch.assert_not_awaited()
        assert metrics_mod._embed_stack_drift_consecutive == 0

    def test_threshold_zero_disables_watchdog(self):
        dispatch = AsyncMock()
        envs = [[_prod_ok(), _staging_ok(model_version="other")]] * 3
        with _patch_envs(envs), patch.object(metrics_mod, "_dispatch_alert", dispatch):
            for _ in range(3):
                _run(metrics_mod._check_embed_stack_drift_once(threshold=0))
        dispatch.assert_not_awaited()


class TestThresholdRegistered:
    def test_default_threshold_present(self):
        assert (
            metrics_mod._ALERT_THRESHOLDS_DEFAULT.get(
                "embed_stack_drift_consecutive_threshold"
            )
            == 3
        )

    def test_drift_alert_types_are_slack_only(self):
        assert "embed_stack_staging_drift" in metrics_mod._SLACK_ONLY_ALERT_TYPES
        assert (
            "embed_stack_staging_drift_recovered"
            in metrics_mod._SLACK_ONLY_ALERT_TYPES
        )
