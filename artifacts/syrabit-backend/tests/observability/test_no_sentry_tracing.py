"""Task #558 — Sentry must initialize with tracing fully off.

Asserts the canonical "errors-only" contract: when ``init_sentry()``
is called with a populated DSN, the resulting Hub options carry
``traces_sample_rate=0`` and no ``enable_tracing`` / ``traces_sampler``
positive setting. Boots only the observability package — no FastAPI
lifespan needed.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def test_init_sentry_locks_traces_to_zero(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://public@o0.ingest.example/0")
    monkeypatch.setenv("SENTRY_ENVIRONMENT", "test")
    monkeypatch.setenv("SENTRY_RELEASE", "task-558-test")

    # Reset the idempotency flag so the test is order-independent.
    import observability.sentry_setup as setup
    setup._INITIALIZED = False
    setup._INIT_DETAILS = {
        "enabled": False, "dsn_loaded": False,
        "environment": None, "release": None, "reason": None,
    }

    ok = setup.init_sentry()
    assert ok is True, setup.get_sentry_health()

    import sentry_sdk

    client = sentry_sdk.Hub.current.client
    assert client is not None, "sentry init reported success but no client on hub"
    opts = client.options

    rate = opts.get("traces_sample_rate")
    assert rate == 0, f"traces_sample_rate must be 0, got {rate!r}"
    assert opts.get("enable_tracing") in (None, False), (
        "enable_tracing must be None/False — Task #558 errors-only contract"
    )
    sampler = opts.get("traces_sampler")
    assert sampler is None, f"traces_sampler must be None, got {sampler!r}"

    health = setup.get_sentry_health()
    assert health["enabled"] is True
    assert health["dsn_loaded"] is True
    assert health["traces_sample_rate"] == 0


def test_init_sentry_skips_when_dsn_unset(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    import observability.sentry_setup as setup
    setup._INITIALIZED = False
    setup._INIT_DETAILS = {
        "enabled": False, "dsn_loaded": False,
        "environment": None, "release": None, "reason": None,
    }
    assert setup.init_sentry() is False
    health = setup.get_sentry_health()
    assert health["enabled"] is False
    assert health["dsn_loaded"] is False


def test_before_send_drops_known_noise():
    from observability.sentry_setup import before_send_filter

    # ResizeObserver loop — drop.
    assert before_send_filter(
        {"message": "ResizeObserver loop limit exceeded"}, None,
    ) is None
    # AbortError — drop.
    assert before_send_filter(
        {"exception": {"values": [{"type": "AbortError"}]}}, None,
    ) is None
    # Expected 4xx tagged event — drop.
    assert before_send_filter(
        {"tags": {"status_code": 404}}, None,
    ) is None
    # Real server error — keep.
    real = {
        "exception": {"values": [{
            "type": "ValueError",
            "stacktrace": {"frames": [{"abs_path": "/app/routes/foo.py"}]},
        }]},
    }
    assert before_send_filter(real, None) is real
