"""Task #404 — public ``/api/turnstile/config`` endpoint tests.

Mirrors ``test_cf_web_analytics.py`` for the new endpoint. The route
hands the React auth forms exactly enough info to mount the Turnstile
widget (``enabled`` + ``site_key``), and must hide the site key when
the flag is off so a half-configured rollout cannot leak the namespace
early.
"""
from __future__ import annotations

import importlib
import os

import pytest


_FLAGS = ("TURNSTILE_ON", "TURNSTILE_SITE_KEY")
_PRISTINE = {k: os.environ.get(k) for k in _FLAGS}


@pytest.fixture(autouse=True)
def _restore_env():
    yield
    for k, v in _PRISTINE.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    importlib.reload(importlib.import_module("config"))


def _reload_with(env: dict) -> None:
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    importlib.reload(importlib.import_module("config"))


def test_returns_disabled_when_flag_off():
    _reload_with({"TURNSTILE_ON": "false", "TURNSTILE_SITE_KEY": "1x00000000000000000000AA"})
    from turnstile import frontend_config
    cfg = frontend_config()
    assert cfg == {"enabled": False, "site_key": None}


def test_returns_disabled_when_site_key_missing():
    _reload_with({"TURNSTILE_ON": "true", "TURNSTILE_SITE_KEY": ""})
    from turnstile import frontend_config
    cfg = frontend_config()
    assert cfg == {"enabled": False, "site_key": None}


def test_returns_site_key_when_enabled():
    _reload_with({"TURNSTILE_ON": "true", "TURNSTILE_SITE_KEY": "1x00000000000000000000AA"})
    from turnstile import frontend_config
    cfg = frontend_config()
    assert cfg["enabled"] is True
    assert cfg["site_key"] == "1x00000000000000000000AA"


@pytest.mark.asyncio
async def test_route_handler_returns_payload():
    """The route handler returns the same payload as ``frontend_config()``."""
    _reload_with({"TURNSTILE_ON": "true", "TURNSTILE_SITE_KEY": "1x00000000000000000000AA"})
    from routes.turnstile_config import turnstile_config
    body = await turnstile_config()
    assert body == {"enabled": True, "site_key": "1x00000000000000000000AA"}


@pytest.mark.asyncio
async def test_route_handler_hides_key_when_disabled():
    _reload_with({"TURNSTILE_ON": "false", "TURNSTILE_SITE_KEY": "1x00000000000000000000AA"})
    from routes.turnstile_config import turnstile_config
    body = await turnstile_config()
    assert body == {"enabled": False, "site_key": None}
