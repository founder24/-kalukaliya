"""Task #558 — the Sentry SDK must initialize from ``SENTRY_DSN`` and
no other env var.

Picking Sentry Developer free over GlitchTip self-hosted (rejected
option captured in ADR-0003) keeps the DSN env-var name unchanged so
the SDK stays wire-compatible if we ever flip back. The test asserts:

  1. With ``SENTRY_DSN`` set, ``init_sentry()`` returns True and the
     loaded DSN comes from that env var only.
  2. With ``SENTRY_DSN`` empty but ``GLITCHTIP_DSN`` (or any other
     candidate) set, ``init_sentry()`` returns False — we do NOT
     silently fall back to a different env var (V4 §12).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _reset(setup) -> None:
    setup._INITIALIZED = False
    setup._INIT_DETAILS = {
        "enabled": False, "dsn_loaded": False,
        "environment": None, "release": None, "reason": None,
    }


def test_dsn_loaded_from_sentry_dsn(monkeypatch):
    dsn = "https://abc@o0.ingest.example/42"
    monkeypatch.setenv("SENTRY_DSN", dsn)
    monkeypatch.setenv("SENTRY_ENVIRONMENT", "production")
    monkeypatch.delenv("GLITCHTIP_DSN", raising=False)

    import observability.sentry_setup as setup
    _reset(setup)
    assert setup.init_sentry() is True

    import sentry_sdk
    client = sentry_sdk.Hub.current.client
    assert client is not None
    loaded_dsn = client.options.get("dsn")
    assert loaded_dsn == dsn, (
        f"sentry_sdk.init must load DSN from SENTRY_DSN; got {loaded_dsn!r}"
    )

    health = setup.get_sentry_health()
    assert health["environment"] == "production"
    assert health["dsn_loaded"] is True


def test_no_silent_fallback_to_glitchtip_dsn(monkeypatch):
    """When SENTRY_DSN is absent we MUST NOT silently fall back to a
    GLITCHTIP_DSN env var — the operator has to make the swap
    explicit. Mirrors the V4 §12 no-silent-fallbacks policy.
    """
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.setenv("GLITCHTIP_DSN", "https://gt@example/1")

    import observability.sentry_setup as setup
    _reset(setup)
    assert setup.init_sentry() is False

    health = setup.get_sentry_health()
    assert health["enabled"] is False
    assert health["dsn_loaded"] is False
    assert "SENTRY_DSN" in (health.get("reason") or "")
