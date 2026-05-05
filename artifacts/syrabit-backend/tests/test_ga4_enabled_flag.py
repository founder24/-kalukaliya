"""Task #383 — GA4_ENABLED gate cuts the GA4 client off."""
from __future__ import annotations

import pytest

from config import Configurator


@pytest.fixture(autouse=True)
def _restore_ga4_env():
    """Snapshot + restore the GA4-related runtime overrides so a test
    can flip the flag without bleeding into the next."""
    keys = ("GA4_ENABLED", "GA4_PROPERTY_ID",
            "GOOGLE_OAUTH_CLIENT_SECRET", "GA4_REFRESH_TOKEN")
    saved = {k: Configurator._overrides.get(k, None) for k in keys}
    yield
    for k, v in saved.items():
        if v is None:
            Configurator._overrides.pop(k, None)
        else:
            Configurator._overrides[k] = v


def test_ga4_enabled_flag_blocks_configured_check():
    Configurator.set_runtime_env("GA4_PROPERTY_ID", "props/123")
    Configurator.set_runtime_env("GOOGLE_OAUTH_CLIENT_SECRET", "secret")
    Configurator.set_runtime_env("GA4_REFRESH_TOKEN", "token")
    Configurator.set_runtime_env("GA4_ENABLED", "0")
    import ga4_client
    assert ga4_client._is_configured() is False


def test_ga4_enabled_flag_allows_when_on():
    Configurator.set_runtime_env("GA4_PROPERTY_ID", "props/123")
    Configurator.set_runtime_env("GOOGLE_OAUTH_CLIENT_SECRET", "secret")
    Configurator.set_runtime_env("GA4_REFRESH_TOKEN", "token")
    Configurator.set_runtime_env("GA4_ENABLED", "1")
    import ga4_client
    assert ga4_client._is_configured() is True


@pytest.mark.asyncio
async def test_run_report_returns_none_when_disabled():
    Configurator.set_runtime_env("GA4_PROPERTY_ID", "props/123")
    Configurator.set_runtime_env("GOOGLE_OAUTH_CLIENT_SECRET", "secret")
    Configurator.set_runtime_env("GA4_REFRESH_TOKEN", "token")
    Configurator.set_runtime_env("GA4_ENABLED", "0")
    import ga4_client
    out = await ga4_client.run_report(["country"], ["activeUsers"], [{}])
    assert out is None
