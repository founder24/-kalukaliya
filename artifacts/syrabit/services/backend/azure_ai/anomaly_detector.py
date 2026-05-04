"""Azure AI Anomaly Detector wrapper — credit / error / cost watchdog.

Polled by a Container Apps Job (``services/cron-jobs/azure_anomaly.py``)
on a 5-minute cadence. Three time series are checked:

* **Credit burn** — per-provider $-spent over the last 24 h, pulled
  from the unified billing view.
* **Error rate** — per-provider 5xx-rate from the gateway.
* **R2 cost** — class-A + class-B operation count, pulled from
  Cloudflare's analytics push.

Anomalies are surfaced two ways:

1. The cron job emits a ``ai_anomaly_detected`` Application Insights
   custom metric; the metric alert in ``infra/azure/ai-services.tf``
   fires the existing ops Slack action group.
2. The anomaly + offending series is appended to the admin
   intelligence panel under "Recent anomalies" so the on-call has
   full context, not just a Slack ping.

Per the task: this *parallels* the existing watchdogs (Slack alerts
from the gateway throttle counters, Sentry alerts on error budget) —
it does not replace them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from . import _resolver

API_VERSION = "v1.1"


@dataclass
class TimeSeriesPoint:
    timestamp: str  # ISO-8601
    value: float


@dataclass
class AnomalyVerdict:
    is_anomaly: bool
    expected_value: float
    upper_margin: float
    lower_margin: float
    severity: float  # 0..1, 0 = normal


def _token() -> str:
    return _resolver.get_credential().get_token(
        "https://cognitiveservices.azure.com/.default"
    ).token


def detect_last_point(
    series: Iterable[TimeSeriesPoint],
    *,
    granularity: str = "minutely",
    sensitivity: int = 90,
) -> AnomalyVerdict:
    """Run the *last point* detector — cheapest mode for streaming.

    The cron job calls this once per series per tick; the multivariate
    detector is reserved for the offline weekly review.
    """
    import requests

    points = [{"timestamp": p.timestamp, "value": p.value} for p in series]
    if len(points) < 12:
        # The univariate detector requires >= 12 points; below that
        # we report "normal" rather than fail the cron job.
        return AnomalyVerdict(
            is_anomaly=False,
            expected_value=points[-1]["value"] if points else 0.0,
            upper_margin=0.0,
            lower_margin=0.0,
            severity=0.0,
        )

    endpoint = _resolver.endpoint_for("anomaly_detector").rstrip("/")
    resp = requests.post(
        f"{endpoint}/anomalydetector/{API_VERSION}/timeseries/last/detect",
        json={
            "series": points,
            "granularity": granularity,
            "sensitivity": sensitivity,
            "imputeMode": "auto",
        },
        headers={
            "Authorization": f"Bearer {_token()}",
            "Content-Type": "application/json",
        },
        timeout=10,
    )
    if resp.status_code == 429:
        raise RuntimeError("azure-anomaly: throttled (429)")
    resp.raise_for_status()
    body = resp.json()

    # Severity isn't reported by Anomaly Detector directly; derive a
    # 0..1 score from how far the last point sits outside the
    # expected band so the Slack alert can rank concurrent anomalies.
    upper = float(body.get("upperMargin", 0.0))
    lower = float(body.get("lowerMargin", 0.0))
    expected = float(body.get("expectedValue", 0.0))
    actual = points[-1]["value"]
    if upper + lower > 0:
        severity = min(1.0, abs(actual - expected) / max(upper, lower, 1e-6))
    else:
        severity = 0.0

    return AnomalyVerdict(
        is_anomaly=bool(body.get("isAnomaly", False)),
        expected_value=expected,
        upper_margin=upper,
        lower_margin=lower,
        severity=severity,
    )
