"""Task #337 — provider + admin route guards for AWS-native features.

These tests stay hermetic — they monkeypatch ``providers.aws_native``
internals so the suite never calls live AWS. The failover-chain
behaviour is asserted at the contract level (toggles disable, errors
advance, deterministic fallback always returns).
"""
from __future__ import annotations

import pytest

from providers import aws_native


@pytest.fixture(autouse=True)
def _reset_native_state():
    aws_native.reset_telemetry()
    for k in aws_native.FEATURE_KEYS:
        aws_native.ENABLED_FLAGS[k] = True
    yield
    aws_native.reset_telemetry()


def test_feature_keys_are_canonical():
    assert set(aws_native.FEATURE_KEYS) == {
        "bedrock_cohere", "polly", "transcribe", "textract", "rekognition",
        "comprehend", "translate", "personalize", "fraud_detector",
    }


def test_set_enabled_round_trip():
    assert aws_native.set_enabled("polly", False) is False
    assert aws_native.is_enabled("polly") is False
    assert aws_native.set_enabled("polly", True) is True
    assert aws_native.is_enabled("polly") is True


def test_set_enabled_rejects_unknown_feature():
    with pytest.raises(ValueError):
        aws_native.set_enabled("nonexistent", True)


def test_env_var_disable_takes_precedence(monkeypatch):
    monkeypatch.setenv("AWS_NATIVE_REKOGNITION_DISABLED", "1")
    assert aws_native.is_enabled("rekognition") is False


def test_disabled_feature_short_circuits(monkeypatch):
    aws_native.set_enabled("polly", False)
    with pytest.raises(RuntimeError, match="disabled"):
        aws_native.synthesize_polly("hi")


def test_bedrock_embed_rejects_non_cohere_model():
    """Cloud-allocation §6+§9 guardrail: Bedrock is Cohere-only."""
    with pytest.raises(RuntimeError, match="Cohere-only"):
        aws_native.bedrock_embed(["a"], model_id="anthropic.claude-3-5-sonnet-20241022-v2:0")


def test_bedrock_rerank_rejects_non_cohere_model():
    with pytest.raises(RuntimeError, match="Cohere-only"):
        aws_native.bedrock_rerank("q", ["a"], model_id="amazon.titan-text-premier-v1:0")


def test_telemetry_records_outcomes():
    aws_native.record_outcome("polly", True, 12.0)
    aws_native.record_outcome("polly", False, 30.0, RuntimeError("boom"))
    snap = aws_native.telemetry_snapshot()["polly"]
    assert snap["invocations"] == 2
    assert snap["failures"] == 1
    assert snap["lastError"] == "RuntimeError"


def test_deterministic_recommendations_orders_by_score():
    items = [
        {"id": "low",  "popularity_score": 0.1, "recency_score": 0.0, "subject_affinity": 0.0},
        {"id": "high", "popularity_score": 1.0, "recency_score": 1.0, "subject_affinity": 1.0},
        {"id": "mid",  "popularity_score": 0.5, "recency_score": 0.5, "subject_affinity": 0.5},
    ]
    out = aws_native.deterministic_recommendations(items, num_results=2)
    assert out == ["high", "mid"]


def test_moderation_threshold_constants_match_runbook():
    """Runbook §3.5 documents the exact label set + threshold."""
    assert "Explicit Nudity" in aws_native.MODERATION_BLOCK_LABELS
    assert "Violence" in aws_native.MODERATION_BLOCK_LABELS
    assert aws_native.MODERATION_DEFAULT_THRESHOLD == 70.0


def test_polly_uses_secondary_region_only_for_bedrock():
    """Region map mirrors infra/aws/aws-native-features.tf."""
    assert aws_native._FEATURE_REGIONS["bedrock_cohere"] == aws_native.SECONDARY_REGION
    assert aws_native._FEATURE_REGIONS["polly"] == aws_native.PRIMARY_REGION
    assert aws_native._FEATURE_REGIONS["rekognition"] == aws_native.PRIMARY_REGION


def test_moderate_image_classifies_flagged(monkeypatch):
    """Rekognition wrapper flags when a blocked label clears the threshold."""

    class _FakeClient:
        def detect_moderation_labels(self, **_kwargs):
            return {
                "ModerationLabels": [
                    {"Name": "Explicit Nudity", "ParentName": "", "Confidence": 92.0},
                    {"Name": "Suggestive",      "ParentName": "", "Confidence": 60.0},
                ]
            }

    monkeypatch.setattr(aws_native, "_HAS_BOTO3", True, raising=False)
    monkeypatch.setattr(aws_native, "_client", lambda *_a, **_kw: _FakeClient())

    out = aws_native.moderate_image(b"\x00\x01")
    assert out["flagged"] is True
    assert out["max_confidence"] >= 90.0


def test_moderate_image_passes_clean(monkeypatch):
    class _FakeClient:
        def detect_moderation_labels(self, **_kwargs):
            return {"ModerationLabels": []}

    monkeypatch.setattr(aws_native, "_HAS_BOTO3", True, raising=False)
    monkeypatch.setattr(aws_native, "_client", lambda *_a, **_kw: _FakeClient())

    out = aws_native.moderate_image(b"\x00")
    assert out["flagged"] is False


def test_get_recommendations_falls_back_when_too_few(monkeypatch):
    """Personalize returning < 3 items must be treated as cold-start."""

    class _FakeClient:
        def get_recommendations(self, **_kwargs):
            return {"itemList": [{"itemId": "only-one"}]}

    monkeypatch.setattr(aws_native, "_HAS_BOTO3", True, raising=False)
    monkeypatch.setattr(aws_native, "_client", lambda *_a, **_kw: _FakeClient())
    out = aws_native.get_recommendations(campaign_arn="arn:fake", user_id="u1")
    assert out == []


def test_translate_text_returns_translated(monkeypatch):
    class _FakeClient:
        def translate_text(self, **_kwargs):
            return {"TranslatedText": "hello"}

    monkeypatch.setattr(aws_native, "_HAS_BOTO3", True, raising=False)
    monkeypatch.setattr(aws_native, "_client", lambda *_a, **_kw: _FakeClient())
    assert aws_native.translate_text("नमस्ते", source_lang="hi", target_lang="en") == "hello"


def test_telemetry_snapshot_shape():
    aws_native.record_outcome("translate", True, 5.0)
    snap = aws_native.telemetry_snapshot()
    assert set(snap.keys()) == set(aws_native.FEATURE_KEYS)
    for key in aws_native.FEATURE_KEYS:
        assert {"invocations", "failures", "throttledPct", "p95LatencyMs", "lastError"} <= set(snap[key].keys())
