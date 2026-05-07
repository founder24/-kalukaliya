"""routes.admin_aws_native — Admin status + toggle for the AWS-native features.

Backs the React ``AdminAwsNativePanel`` (Task #337). The panel polls
``GET /admin/aws-native/status`` and writes to ``POST /admin/aws-native/toggle``.

We intentionally keep the per-feature enable/disable in-process
(:data:`providers.aws_native.ENABLED_FLAGS`) — the runbook
(`docs/features/aws-native.md` §6) treats these toggles as fast-flips,
not durable config. CloudWatch + Cost Explorer remain the source of
truth for cross-replica spend / latency aggregates; the live throttle
and p95 numbers reported here are this replica's rolling window only,
which matches how ``AdminHealth`` already surfaces per-provider
telemetry from in-process counters.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth_deps import get_admin_user as require_admin
from providers import aws_native

logger = logging.getLogger("routes.admin_aws_native")

router = APIRouter(tags=["admin", "aws-native"])

# Cohere-only guardrail surfaced verbatim in the admin tile per
# cloud-allocation-plan §6 + §9.
_BEDROCK_GUARDRAIL = (
    "Cohere-only — Anthropic Claude / Meta Llama / Mistral / Amazon "
    "Titan / Amazon Nova on Bedrock are explicitly excluded. Azure "
    "OpenAI + Vertex Gemini cover those LLM roles."
)

# Stable runbook anchors so the admin tile can deep-link.
_RUNBOOK_ANCHORS: Dict[str, str] = {
    "polly":           "32-polly--third-tier-tts",
    "transcribe":      "33-transcribe--third-tier-stt",
    "textract":        "34-textract--structured-document-ocr",
    "rekognition":     "35-rekognition--image-moderation",
    "comprehend":      "36-comprehend--sampled-nlp-analytics",
    "translate":       "37-translate--fallback",
    "personalize":     "38-personalize--home-rail-recommendations",
    "fraud_detector":  "39-fraud-detector--risk-score-on-signup--payment",
}


def _dashboard_url(feature: str) -> str:
    region = aws_native._FEATURE_REGIONS.get(feature, aws_native.PRIMARY_REGION)
    return (
        f"https://{region}.console.aws.amazon.com/cloudwatch/home?"
        f"region={region}#dashboards:name=syrabit-aws-native-prod"
    )


def _health_label(snap: Dict[str, Any], enabled: bool) -> str:
    if not enabled:
        return "disabled"
    if not aws_native.is_configured():
        # boto3 missing in this replica — show degraded so the operator
        # knows the toggle is on but no AWS calls are reaching this box.
        return "degraded"
    if snap.get("invocations", 0) == 0:
        return "ok"  # nothing called yet — assume ok until proven otherwise
    failure_rate = snap.get("failures", 0) / max(snap.get("invocations", 1), 1)
    if failure_rate >= 0.5:
        return "failed"
    if failure_rate >= 0.05 or (snap.get("throttledPct") or 0) >= 0.05:
        return "degraded"
    return "ok"


# Per-feature 7-day spend hydrated from AWS Cost Explorer. The slow
# timer below refreshes once an hour (Cost Explorer charges per query,
# so we throttle aggressively). When the boto3 path raises we leave
# the previous values in place and surface a stale-marker on the
# response so the operator knows the value is from before the outage.
_SPEND_CACHE: Dict[str, Optional[float]] = {k: None for k in aws_native.FEATURE_KEYS}
_SPEND_LAST_AT: Optional[float] = None
_SPEND_REFRESH_SECONDS = 3600.0


def _refresh_spend_cache_if_stale() -> Optional[str]:
    """Pull Cost Explorer once an hour. Returns ``None`` on success,
    a short error label on failure (so the JSON response can carry it).
    """
    import time
    global _SPEND_LAST_AT
    now = time.monotonic()
    if _SPEND_LAST_AT is not None and (now - _SPEND_LAST_AT) < _SPEND_REFRESH_SECONDS:
        return None
    try:
        fresh = aws_native.fetch_cost_explorer_7d()
    except Exception as exc:
        logger.warning("Cost Explorer hydrate failed: %s", str(exc)[:200])
        return type(exc).__name__
    _SPEND_CACHE.update({k: fresh.get(k) for k in aws_native.FEATURE_KEYS})
    _SPEND_LAST_AT = now
    return None


@router.get("/admin/aws-native/status")
async def get_status(_admin: dict = Depends(require_admin)) -> Dict[str, Any]:
    """Render the per-feature tile contract consumed by AdminAwsNativePanel."""
    from datetime import datetime, timezone

    spend_err = _refresh_spend_cache_if_stale()
    snapshots = aws_native.telemetry_snapshot()
    features: List[Dict[str, Any]] = []
    for key in aws_native.FEATURE_KEYS:
        snap = snapshots.get(key, {})
        enabled = aws_native.is_enabled(key)
        features.append({
            "key": key,
            "enabled": enabled,
            "health": _health_label(snap, enabled),
            "throttledPct": snap.get("throttledPct") or 0.0,
            "p95LatencyMs": snap.get("p95LatencyMs"),
            "spendUsd7d": _SPEND_CACHE.get(key),
            "dashboardUrl": _dashboard_url(key),
            "runbookAnchor": _RUNBOOK_ANCHORS.get(key),
            "lastError": snap.get("lastError"),
        })

    return {
        "asOf": datetime.now(timezone.utc).isoformat(),
        "bedrockGuardrail": _BEDROCK_GUARDRAIL,
        "features": features,
        "boto3Available": aws_native.is_configured(),
        "spendHydrationError": spend_err,
        "spendHydratedAt": _SPEND_LAST_AT,
    }


class ToggleBody(BaseModel):
    key: str = Field(..., description="Feature key — see providers.aws_native.FEATURE_KEYS")
    enabled: bool = Field(..., description="Target enabled state")


@router.post("/admin/aws-native/toggle")
async def toggle_feature(body: ToggleBody, _admin: dict = Depends(require_admin)) -> Dict[str, Any]:
    """Flip a feature on or off. Persists in-process; clean restart re-enables defaults."""
    try:
        new_value = aws_native.set_enabled(body.key, body.enabled)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    logger.info("aws_native toggle: feature=%s enabled=%s", body.key, new_value)
    return {"key": body.key, "enabled": new_value}
