"""Task #565 — chat-credit-runway publisher + selector wiring tests.

Two halves:

* Selector half: prove that `cost_caps._projected_chat_runway_days`
  honours the new Redis key `chat:credit_runway_days` between the
  operator env override and the env-derived fallback, and that the
  resolution order matches the documented contract.
* Lambda half: hermetic coverage of the pure compute helper
  (`compute_runway_days`) for every branch the handler can hit
  (healthy, exhausted, no-burn).

The Lambda's Sentry / BigQuery / Upstash side-effects are intentionally
NOT exercised here — they live in the AWS-side integration test that
runs against a billing-export sandbox.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Task #565 — `lambda_batch` lives in artifacts/syrabit/services/backend so
# tests can import the handler without an installed package.
_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)
sys.path.insert(0, os.path.join(_REPO_ROOT, "artifacts", "syrabit", "services", "backend"))

from cost_caps import (  # noqa: E402
    _CHAT_CHAIN_DEFAULT,
    _CHAT_CHAIN_FLIPPED,
    _RUNWAY_REDIS_KEY,
    _projected_chat_runway_days,
    _reset_chat_primary_cache,
    _select_chat_primary,
)


def _clear_runway(monkeypatch):
    monkeypatch.delenv("CHAT_CREDIT_RUNWAY_DAYS", raising=False)
    monkeypatch.delenv("GCP_CREDITS_REMAINING_USD", raising=False)
    monkeypatch.delenv("CHAT_PRIMARY_OVERRIDE", raising=False)
    _reset_chat_primary_cache()


# ── Selector — Redis read path ───────────────────────────────────────────────
def test_runway_reads_from_redis_when_no_env_override(monkeypatch):
    _clear_runway(monkeypatch)
    fake_redis = MagicMock()
    fake_redis.get.return_value = "42"
    monkeypatch.setitem(sys.modules, "deps", SimpleNamespace(redis_client=fake_redis))
    assert _projected_chat_runway_days() == 42.0
    fake_redis.get.assert_called_once_with(_RUNWAY_REDIS_KEY)


def test_runway_redis_flips_chain_when_below_threshold(monkeypatch):
    _clear_runway(monkeypatch)
    fake_redis = MagicMock()
    fake_redis.get.return_value = b"30"  # bytes path — Upstash REST returns str, but be safe
    monkeypatch.setitem(sys.modules, "deps", SimpleNamespace(redis_client=fake_redis))
    chain = _select_chat_primary()
    assert chain == list(_CHAT_CHAIN_FLIPPED), (
        f"redis-published runway 30d (≤90d threshold) must flip the chain; got {chain}"
    )


def test_runway_redis_keeps_default_when_healthy(monkeypatch):
    _clear_runway(monkeypatch)
    fake_redis = MagicMock()
    fake_redis.get.return_value = "180"
    monkeypatch.setitem(sys.modules, "deps", SimpleNamespace(redis_client=fake_redis))
    chain = _select_chat_primary()
    assert chain == list(_CHAT_CHAIN_DEFAULT)


def test_env_override_beats_redis(monkeypatch):
    """`CHAT_CREDIT_RUNWAY_DAYS` env wins over the Redis-published value
    so an operator can pin the chain in an emergency without waiting
    for the Lambda to roll a new value."""
    _clear_runway(monkeypatch)
    fake_redis = MagicMock()
    fake_redis.get.return_value = "5"  # would normally flip
    monkeypatch.setitem(sys.modules, "deps", SimpleNamespace(redis_client=fake_redis))
    monkeypatch.setenv("CHAT_CREDIT_RUNWAY_DAYS", "180")
    _reset_chat_primary_cache()
    assert _projected_chat_runway_days() == 180.0
    assert _select_chat_primary() == list(_CHAT_CHAIN_DEFAULT)
    fake_redis.get.assert_not_called()


def test_redis_missing_falls_through_to_env_pool(monkeypatch):
    """Redis returning None (key expired / not yet published) must let
    the legacy `GCP_CREDITS_REMAINING_USD` path take over — the new
    code path is additive, not a replacement."""
    _clear_runway(monkeypatch)
    fake_redis = MagicMock()
    fake_redis.get.return_value = None
    monkeypatch.setitem(sys.modules, "deps", SimpleNamespace(redis_client=fake_redis))
    # No GCP_CREDITS_REMAINING_USD either → returns None.
    assert _projected_chat_runway_days() is None


def test_redis_unparseable_value_falls_through(monkeypatch):
    _clear_runway(monkeypatch)
    fake_redis = MagicMock()
    fake_redis.get.return_value = "garbage"
    monkeypatch.setitem(sys.modules, "deps", SimpleNamespace(redis_client=fake_redis))
    assert _projected_chat_runway_days() is None


def test_redis_client_none_does_not_raise(monkeypatch):
    _clear_runway(monkeypatch)
    monkeypatch.setitem(sys.modules, "deps", SimpleNamespace(redis_client=None))
    assert _projected_chat_runway_days() is None


def test_redis_get_raises_does_not_break_dispatch(monkeypatch):
    """Upstash hiccup must not break the chat hot path — Redis errors
    are swallowed and the selector falls through to the env path."""
    _clear_runway(monkeypatch)
    fake_redis = MagicMock()
    fake_redis.get.side_effect = RuntimeError("upstash 503")
    monkeypatch.setitem(sys.modules, "deps", SimpleNamespace(redis_client=fake_redis))
    assert _projected_chat_runway_days() is None
    # And the selector still returns a 2-position list, not a crash:
    assert _select_chat_primary() == list(_CHAT_CHAIN_DEFAULT)


# ── Lambda — pure compute helper ─────────────────────────────────────────────
def test_compute_runway_healthy():
    from lambda_batch.chat_credit_runway import compute_runway_days

    # $3000 pool, $2000 burned → $1000 remaining; $300 over 30d → $10/d → 100d.
    assert compute_runway_days(
        total_credits_usd=3000.0,
        cumulative_cost_usd=2000.0,
        cost_30d_usd=300.0,
    ) == 100


def test_compute_runway_exhausted_pool_returns_zero():
    from lambda_batch.chat_credit_runway import compute_runway_days

    # Cumulative > total → remaining negative → flip immediately.
    assert compute_runway_days(
        total_credits_usd=1000.0,
        cumulative_cost_usd=1500.0,
        cost_30d_usd=200.0,
    ) == 0


def test_compute_runway_zero_burn_returns_none():
    """No traffic = no signal → selector keeps the default chain."""
    from lambda_batch.chat_credit_runway import compute_runway_days

    assert compute_runway_days(
        total_credits_usd=1000.0,
        cumulative_cost_usd=10.0,
        cost_30d_usd=0.0,
    ) is None


def test_compute_runway_at_threshold_rounds_down(monkeypatch):
    """Boundary — 90.0 days exactly. Selector treats <=90 as the flip,
    so the integer 90 must still trigger the flipped chain."""
    from lambda_batch.chat_credit_runway import compute_runway_days

    # 900 remaining, $300/30d burn ($10/d) → 90 days.
    assert compute_runway_days(
        total_credits_usd=1000.0,
        cumulative_cost_usd=100.0,
        cost_30d_usd=300.0,
    ) == 90


def test_freshness_handler_alerts_on_missing_key(monkeypatch):
    """Freshness probe must Sentry-capture when the Redis key is gone
    so on-call sees stale-runway within ~1h even if the publisher Lambda
    failed to invoke (independent detector — V4 §12 fail-loud)."""
    import lambda_batch.chat_credit_runway as mod  # type: ignore

    monkeypatch.setattr(mod, "_db", SimpleNamespace(bootstrap_env=lambda: None))
    monkeypatch.setattr(mod, "_init_sentry", lambda: None)
    monkeypatch.setattr(mod, "_redis_get", lambda key: None)
    monkeypatch.setattr(mod, "_redis_pttl", lambda key: -2)
    captured: list[tuple] = []
    monkeypatch.setattr(mod, "_sentry_capture",
                        lambda msg, level="error", extra=None: captured.append((msg, level, extra)))
    out = mod.freshness_handler({}, None)
    assert out == {"ok": False, "stale": True, "reason": "missing"}
    assert captured and "missing" in captured[0][0].lower()


def test_freshness_handler_alerts_on_stale_value(monkeypatch):
    import lambda_batch.chat_credit_runway as mod  # type: ignore

    monkeypatch.setattr(mod, "_db", SimpleNamespace(bootstrap_env=lambda: None))
    monkeypatch.setattr(mod, "_init_sentry", lambda: None)
    monkeypatch.setattr(mod, "_redis_get", lambda key: "120")
    # publish TTL = 48h, remaining = 20h → age = 28h → stale
    publish_ttl_s = mod.DEFAULT_REDIS_TTL_S
    remaining_ms = 20 * 3600 * 1000
    monkeypatch.setattr(mod, "_redis_pttl", lambda key: remaining_ms)
    captured: list[tuple] = []
    monkeypatch.setattr(mod, "_sentry_capture",
                        lambda msg, level="error", extra=None: captured.append((msg, level, extra)))
    out = mod.freshness_handler({}, None)
    assert out["stale"] is True
    assert out["age_s"] >= 24 * 3600
    assert captured


def test_freshness_handler_silent_when_fresh(monkeypatch):
    import lambda_batch.chat_credit_runway as mod  # type: ignore

    monkeypatch.setattr(mod, "_db", SimpleNamespace(bootstrap_env=lambda: None))
    monkeypatch.setattr(mod, "_init_sentry", lambda: None)
    monkeypatch.setattr(mod, "_redis_get", lambda key: "120")
    # remaining 47h → age ~1h → fresh
    monkeypatch.setattr(mod, "_redis_pttl", lambda key: 47 * 3600 * 1000)
    captured: list[tuple] = []
    monkeypatch.setattr(mod, "_sentry_capture",
                        lambda msg, level="error", extra=None: captured.append((msg, level, extra)))
    out = mod.freshness_handler({}, None)
    assert out["ok"] is True
    assert out["stale"] is False
    assert not captured


def test_lambda_handler_skips_when_credits_unconfigured(monkeypatch):
    """Operator misconfig must surface loudly (Sentry capture inside
    the handler) and return an explicit error payload — no silent
    no-op (V4 §12)."""
    monkeypatch.delenv("GCP_TOTAL_CREDITS_USD", raising=False)
    # Stub `_db.bootstrap_env` so we don't try to hit Secrets Manager.
    import lambda_batch.chat_credit_runway as mod  # type: ignore

    monkeypatch.setattr(mod, "_db", SimpleNamespace(bootstrap_env=lambda: None))
    monkeypatch.setattr(mod, "_init_sentry", lambda: None)
    captured: list[tuple] = []
    monkeypatch.setattr(mod, "_sentry_capture",
                        lambda msg, level="error", extra=None: captured.append((msg, level)))
    out = mod.handler({}, None)
    assert out["ok"] is False
    assert "GCP_TOTAL_CREDITS_USD" in out["error"]
    assert captured, "missing-config branch must capture a Sentry event"
