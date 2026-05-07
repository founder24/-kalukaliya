"""Task #571 — admin-only cache effectiveness panel.

`GET /api/health/cache` returns the per-content-type counters from
`ai_input_cache.snapshot()` so the admin Observability page can render
the "Cache hit-ratio" panel split by content type with `unique_keys/day`
cardinality and the miss-reason ranking. Also surfaces the edge-cache
TTL targets parsed from `workers/edge-proxy/monitored-urls.json` so
the panel can compare advisory vs. live numbers in one place.

Auth: `get_admin_user` — same dependency as `/admin/diagnostics`. The
nightly `lambda_batch.cache_effectiveness` shipper hits this endpoint
with an admin JWT minted from `ADMIN_JWT_SECRET`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends

from auth_deps import get_admin_user
from ai_input_cache import snapshot as _ai_cache_snapshot

router = APIRouter()

# Repo-relative path to the edge worker's monitored-urls policy. Loaded
# once at process start; absent file means we just don't include the
# `edge_targets` block in the response (panel still renders the AI-cache
# section). Never raises.
_MONITORED_URLS_PATH = Path(__file__).resolve().parents[2] / "workers" / "edge-proxy" / "monitored-urls.json"


def _load_edge_targets() -> list[dict[str, Any]]:
    try:
        if not _MONITORED_URLS_PATH.exists():
            return []
        data = json.loads(_MONITORED_URLS_PATH.read_text())
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for entry in data.get("backend_routes", []):
        ec = entry.get("edge_cache") or {}
        if ec.get("behavior") != "cacheable":
            continue
        out.append({
            "path": entry.get("path"),
            "ttl_seconds": ec.get("ttl_seconds"),
            "cache_hit_ratio_target": ec.get("cache_hit_ratio_target"),
            "user_keyed": bool(ec.get("user_keyed")),
        })
    return out


@router.get("/api/health/cache")
async def admin_cache_health(_admin: dict = Depends(get_admin_user)) -> dict[str, Any]:
    """Return the AI-cache snapshot + edge advisory targets.

    Shape (consumed by `/admin/observability` cache panel and the
    nightly `cache_effectiveness` Lambda):

        {
          "ai_input_cache": {
             "totals": {"hits", "misses", "sets", "hit_ratio", "unique_keys_24h"},
             "content_types": {
                "<ct>": {"hits", "misses", "sets", "hit_ratio",
                         "unique_keys_24h", "miss_reasons": {...}},
                ...
             }
          },
          "edge_targets": [{"path", "ttl_seconds",
                            "cache_hit_ratio_target", "user_keyed"}, ...],
          "alarm_thresholds": {
             "ai_cache_hit_ratio_floor": 0.30,
             "cardinality_multiplier": 3.0
          }
        }
    """
    return {
        "ai_input_cache": _ai_cache_snapshot(),
        "edge_targets": _load_edge_targets(),
        "alarm_thresholds": {
            # Surface the alarm thresholds so the admin panel can render
            # the same red lines the CloudWatch alarms enforce
            # (`Syrabit/Cache` namespace, declared in
            # `infra/aws/lambda-batch-jobs.tf`).
            "ai_cache_hit_ratio_floor": float(
                os.environ.get("CACHE_HIT_RATIO_FLOOR", "0.30")
            ),
            "cardinality_multiplier": float(
                os.environ.get("CACHE_CARDINALITY_MULTIPLIER", "3.0")
            ),
        },
    }
