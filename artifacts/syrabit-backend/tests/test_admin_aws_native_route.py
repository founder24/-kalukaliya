"""Task #337 — admin /aws-native/{status,toggle} route contract.

Hermetic: bypasses the auth dependency and directly exercises the
router functions so the suite never needs a TestClient + DB.
"""
from __future__ import annotations

import asyncio

import pytest

from providers import aws_native
from routes import admin_aws_native


@pytest.fixture(autouse=True)
def _reset_state():
    aws_native.reset_telemetry()
    for k in aws_native.FEATURE_KEYS:
        aws_native.ENABLED_FLAGS[k] = True
    yield
    aws_native.reset_telemetry()


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_status_returns_one_tile_per_feature():
    payload = _run(admin_aws_native.get_status({"sub": "admin"}))
    feats = payload["features"]
    assert {f["key"] for f in feats} == set(aws_native.FEATURE_KEYS)
    assert "Cohere-only" in payload["bedrockGuardrail"]
    assert payload["asOf"]
    for f in feats:
        assert "dashboardUrl" in f
        assert "runbookAnchor" in f
        assert f["health"] in {"ok", "degraded", "failed", "disabled"}


def test_status_marks_disabled_feature():
    aws_native.set_enabled("polly", False)
    payload = _run(admin_aws_native.get_status({"sub": "admin"}))
    polly = next(f for f in payload["features"] if f["key"] == "polly")
    assert polly["enabled"] is False
    assert polly["health"] == "disabled"


def test_status_health_degraded_on_failures():
    for _ in range(20):
        aws_native.record_outcome("translate", False, 50.0, RuntimeError("boom"))
    payload = _run(admin_aws_native.get_status({"sub": "admin"}))
    translate = next(f for f in payload["features"] if f["key"] == "translate")
    assert translate["health"] == "failed"


def test_toggle_endpoint_round_trip():
    body = admin_aws_native.ToggleBody(key="rekognition", enabled=False)
    out = _run(admin_aws_native.toggle_feature(body, {"sub": "admin"}))
    assert out == {"key": "rekognition", "enabled": False}
    assert aws_native.is_enabled("rekognition") is False


def test_toggle_endpoint_rejects_unknown_key():
    from fastapi import HTTPException
    body = admin_aws_native.ToggleBody(key="totally-fake", enabled=True)
    with pytest.raises(HTTPException) as exc:
        _run(admin_aws_native.toggle_feature(body, {"sub": "admin"}))
    assert exc.value.status_code == 400


def test_runbook_anchors_cover_every_feature():
    """Each feature must have a stable runbook deep link for the admin tile."""
    assert set(admin_aws_native._RUNBOOK_ANCHORS.keys()) == set(aws_native.FEATURE_KEYS)
