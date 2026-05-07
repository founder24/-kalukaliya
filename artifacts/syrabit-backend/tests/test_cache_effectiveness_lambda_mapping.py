"""Task #571 round-7 follow-up — Lambda snapshot→CW mapping pin.

Architect approved-with-comments review flagged that
`lambda_batch.cache_effectiveness` was only emitting the lifetime
`MissReason` series, not the panel-aligned `MissReason24h`. This test
imports the Lambda module + drives `handler()` against a fake
`/api/health/cache` payload and a fake CloudWatch client, then asserts
that BOTH metric series are emitted with the correct dimensions.

Prevents future drift between the panel (which reads
`top_miss_reasons_24h`) and CloudWatch (which the alarms read).
"""
from __future__ import annotations

import importlib
import json
import sys
import types
from pathlib import Path


def _load_lambda_module():
    # The lambda lives at artifacts/syrabit/services/backend/lambda_batch/
    root = Path(__file__).resolve().parents[2]
    lam_dir = root / "syrabit" / "services" / "backend" / "lambda_batch"
    sys.path.insert(0, str(lam_dir))
    if "cache_effectiveness" in sys.modules:
        del sys.modules["cache_effectiveness"]
    return importlib.import_module("cache_effectiveness")


class _FakeCW:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def put_metric_data(self, Namespace: str, MetricData: list[dict]) -> None:
        self.calls.append({"namespace": Namespace, "data": MetricData})


def test_handler_emits_both_lifetime_and_24h_miss_reason_series(monkeypatch) -> None:
    mod = _load_lambda_module()
    fake_cw = _FakeCW()

    snapshot = {
        "ai_input_cache": {
            "totals": {
                "hits": 100, "misses": 30, "sets": 50,
                "hit_ratio": 0.769, "unique_keys_24h": 42,
                "miss_reasons_24h": {"cold": 20, "ttl_expiry": 10},
                "hits_24h": 40, "misses_24h": 10, "hit_ratio_24h": 0.8,
            },
            "content_types": {
                "mcq": {
                    "hits": 50, "misses": 10, "sets": 20,
                    "hit_ratio": 0.833, "unique_keys_24h": 21,
                    "miss_reasons": {"cold": 100, "ttl_expiry": 50},
                    "miss_reasons_24h": {"cold": 5, "ttl_expiry": 5},
                    "hits_24h": 18, "misses_24h": 2, "hit_ratio_24h": 0.9,
                },
            },
        },
        "ai_response_cache": {"hits": 0, "misses": 0, "hit_rate": 0.0},
        "rag_cache": {"hits": 0, "misses": 0, "hit_rate": 0.0},
        "l1_inproc": {},
        "edge_targets": [],
    }

    monkeypatch.setattr(mod, "_fetch_snapshot", lambda: snapshot)
    monkeypatch.setattr(mod, "_fetch_cf_edge_hit_rates", lambda paths: {})

    fake_boto3 = types.SimpleNamespace(client=lambda name: fake_cw)
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

    out = mod.handler({}, None)
    assert out["ok"] is True

    # Flatten every emitted metric across all calls.
    all_metrics: list[dict] = []
    for call in fake_cw.calls:
        assert call["namespace"] == "Syrabit/Cache"
        all_metrics.extend(call["data"])

    names = {m["MetricName"] for m in all_metrics}
    assert "MissReason" in names, "lifetime MissReason series must still ship"
    assert "MissReason24h" in names, "24h MissReason24h series must ship (round-7)"
    # Round-8 — fleet-wide rolling 24h hit-ratio is what the alarm uses.
    assert "HitRatio24h" in names, "rolling 24h HitRatio24h must ship (round-8)"
    assert "Hits24h" in names and "Misses24h" in names

    # The Total row's HitRatio24h must equal the snapshot value.
    hr24_total = [
        m for m in all_metrics
        if m["MetricName"] == "HitRatio24h"
        and any(d["Name"] == "ContentType" and d["Value"] == "Total" for d in m["Dimensions"])
    ]
    assert hr24_total and hr24_total[0]["Value"] == 0.8

    # Verify the 24h series carries the expected reasons + counts.
    mr24 = [m for m in all_metrics if m["MetricName"] == "MissReason24h"]
    by_key = {}
    for m in mr24:
        ct = next(d["Value"] for d in m["Dimensions"] if d["Name"] == "ContentType")
        reason = next(d["Value"] for d in m["Dimensions"] if d["Name"] == "Reason")
        by_key[(ct, reason)] = m["Value"]
    assert by_key.get(("Total", "cold")) == 20.0
    assert by_key.get(("Total", "ttl_expiry")) == 10.0
    assert by_key.get(("mcq", "cold")) == 5.0
    assert by_key.get(("mcq", "ttl_expiry")) == 5.0
