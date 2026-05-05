"""Task #383 — Turnstile siteverify tests."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture(autouse=True)
def _reset_state():
    from turnstile import reset_for_tests
    reset_for_tests()
    yield
    reset_for_tests()


def _fake_client_factory(payload: dict, status: int = 200,
                         content_type: str = "application/json"):
    class _Resp:
        status_code = status
        headers = {"content-type": content_type}

        def json(self):
            return payload

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, data=None):
            self.last_url = url
            self.last_data = data
            return _Resp()

    return _Client


@pytest.mark.asyncio
async def test_returns_bypass_when_flag_off(monkeypatch):
    monkeypatch.setattr("turnstile.TURNSTILE_ON", False)
    from turnstile import verify_turnstile_token
    out = await verify_turnstile_token("any-token")
    assert out.ok is True
    assert out.action == "bypass-flag-off"


@pytest.mark.asyncio
async def test_missing_token_fails_when_flag_on(monkeypatch):
    monkeypatch.setattr("turnstile.TURNSTILE_ON", True)
    monkeypatch.setattr("turnstile.TURNSTILE_SECRET_KEY", "topsecret")
    from turnstile import verify_turnstile_token, snapshot
    out = await verify_turnstile_token("")
    assert out.ok is False
    assert "missing-input-response" in out.error_codes
    assert snapshot()["verify_missing_token"] == 1


@pytest.mark.asyncio
async def test_misconfigured_when_secret_unset(monkeypatch):
    monkeypatch.setattr("turnstile.TURNSTILE_ON", True)
    monkeypatch.setattr("turnstile.TURNSTILE_SECRET_KEY", "")
    from turnstile import verify_turnstile_token, snapshot
    out = await verify_turnstile_token("token")
    assert out.ok is False
    assert "secret-not-configured" in out.error_codes
    assert snapshot()["verify_misconfigured"] == 1


@pytest.mark.asyncio
async def test_successful_verify(monkeypatch):
    monkeypatch.setattr("turnstile.TURNSTILE_ON", True)
    monkeypatch.setattr("turnstile.TURNSTILE_SECRET_KEY", "topsecret")
    from turnstile import verify_turnstile_token, snapshot
    factory = _fake_client_factory({"success": True, "hostname": "syrabit.ai"})
    out = await verify_turnstile_token("ok-token", "1.2.3.4",
                                       http_client_factory=factory)
    assert out.ok is True
    assert out.hostname == "syrabit.ai"
    assert snapshot()["verify_passes"] == 1


@pytest.mark.asyncio
async def test_rejected_verify(monkeypatch):
    monkeypatch.setattr("turnstile.TURNSTILE_ON", True)
    monkeypatch.setattr("turnstile.TURNSTILE_SECRET_KEY", "topsecret")
    from turnstile import verify_turnstile_token, snapshot
    factory = _fake_client_factory({"success": False,
                                    "error-codes": ["invalid-input-response"]})
    out = await verify_turnstile_token("bad-token",
                                       http_client_factory=factory)
    assert out.ok is False
    assert "invalid-input-response" in out.error_codes
    assert snapshot()["verify_fails"] == 1


@pytest.mark.asyncio
async def test_network_error_handled(monkeypatch):
    monkeypatch.setattr("turnstile.TURNSTILE_ON", True)
    monkeypatch.setattr("turnstile.TURNSTILE_SECRET_KEY", "topsecret")

    class _BoomClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            raise RuntimeError("connection refused")

    from turnstile import verify_turnstile_token, snapshot
    out = await verify_turnstile_token("token",
                                       http_client_factory=_BoomClient)
    assert out.ok is False
    assert "network-error" in out.error_codes
    assert snapshot()["verify_unreachable"] == 1


@pytest.mark.asyncio
async def test_dependency_raises_403_on_missing_token(monkeypatch):
    monkeypatch.setattr("turnstile.TURNSTILE_ON", True)
    monkeypatch.setattr("turnstile.TURNSTILE_SECRET_KEY", "topsecret")
    from fastapi import HTTPException
    from turnstile import require_turnstile

    request = MagicMock()
    request.headers = {}
    request.json = AsyncMock(side_effect=ValueError("no body"))
    request.client = None

    with pytest.raises(HTTPException) as exc_info:
        await require_turnstile(request)
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == "turnstile_required"


@pytest.mark.asyncio
async def test_dependency_passes_when_token_valid(monkeypatch):
    monkeypatch.setattr("turnstile.TURNSTILE_ON", True)
    monkeypatch.setattr("turnstile.TURNSTILE_SECRET_KEY", "topsecret")
    from turnstile import require_turnstile, verify_turnstile_token  # noqa
    import turnstile

    async def _fake_verify(token, remote_ip, *, http_client_factory=None):
        from turnstile import VerifyResult
        return VerifyResult(ok=True, action="login")

    monkeypatch.setattr(turnstile, "verify_turnstile_token", _fake_verify)
    request = MagicMock()
    request.headers = {"x-turnstile-token": "good"}
    request.client = MagicMock(host="1.2.3.4")
    out = await require_turnstile(request)
    assert out.ok is True
