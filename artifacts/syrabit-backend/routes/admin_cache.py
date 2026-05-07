"""Task #571 — admin-only cache effectiveness panel.

`GET /api/health/cache` returns hit-ratio + cardinality + miss-reason
data for every cache layer Syrabit operates:

  * `ai_input_cache` — per-content-type counters from
    `ai_input_cache.snapshot()` (the canonical Task #571 source).
  * `ai_response_cache` — legacy LLM-response cache from
    `ai_cache.stats()` (kept on the panel because operators still
    page on its hit-rate during deploys).
  * `rag_cache` — Redis-backed retrieval cache hits/misses from
    the `rag:cache:*` counters in `rag_cache.py`.
  * `l1_inproc` — `cachetools.TTLCache` instances declared in
    `cache.py` (`_user_cache`, `_conv_cache`, `_content_cache`,
    `_rag_cache`, `_vector_rag_cache`, `_query_embed_cache`,
    `_embedding_cache`, `_content_card_cache`, `_syllabus_cache`).
    Cardinality only — `cachetools.TTLCache` does not expose hit/
    miss counters natively and instrumenting every accessor is out
    of scope for Task #571.
  * `edge_targets` — advisory hit-ratio targets parsed from
    `workers/edge-proxy/monitored-urls.json` so the panel can
    compare advisory vs. live numbers in one place.

Auth: `get_admin_user` — same dependency as `/admin/diagnostics`. The
nightly `lambda_batch.cache_effectiveness` shipper hits this endpoint
with an admin JWT minted from `ADMIN_JWT_SECRET` and emits the
per-layer numbers to the `Syrabit/Cache` CloudWatch namespace.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends

from auth_deps import get_admin_user
from ai_input_cache import snapshot as _ai_cache_snapshot

logger = logging.getLogger(__name__)
router = APIRouter()

# Repo-relative path to the edge worker's monitored-urls policy.
# parents resolution from this file:
#   parents[0] = artifacts/syrabit-backend/routes
#   parents[1] = artifacts/syrabit-backend
#   parents[2] = artifacts                   ← previous (buggy) target
#   parents[3] = repo root                   ← what we actually want
# Override via `MONITORED_URLS_PATH` for non-standard layouts (the
# Lambda image flattens the repo into a single deploy bundle and
# resolves this from $LAMBDA_TASK_ROOT).
_MONITORED_URLS_PATH = Path(
    os.environ.get(
        "MONITORED_URLS_PATH",
        str(Path(__file__).resolve().parents[3] / "workers" / "edge-proxy" / "monitored-urls.json"),
    )
)


def _load_edge_targets() -> list[dict[str, Any]]:
    try:
        if not _MONITORED_URLS_PATH.exists():
            return []
        data = json.loads(_MONITORED_URLS_PATH.read_text())
    except Exception as e:
        logger.debug("[admin_cache] monitored-urls load failed: %s", e)
        return []
    out: list[dict[str, Any]] = []
    # Schema: monitored-urls.json uses `backend_paths` (Task #887);
    # the previous "backend_routes" key was a typo that silently
    # produced an empty list.
    for entry in data.get("backend_paths", []):
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


def _ai_response_cache_stats() -> dict[str, Any]:
    """Pull legacy LLM-response cache stats. Best-effort; never raises."""
    try:
        import ai_cache as _ac
        s = _ac.stats()
        # Keep only the fields the panel actually renders so we do not
        # surface internal breaker / namespace plumbing on the public
        # contract.
        return {
            "hits": int(s.get("hits", 0)),
            "misses": int(s.get("misses", 0)),
            "hit_rate": float(s.get("hit_rate", 0.0)),
            "backend": s.get("backend"),
            "breaker_open": bool(s.get("breaker_open", False)),
        }
    except Exception as e:
        logger.debug("[admin_cache] ai_cache stats unavailable: %s", e)
        return {"hits": 0, "misses": 0, "hit_rate": 0.0, "available": False}


def _rag_cache_stats() -> dict[str, Any]:
    """Read `rag:cache:*` Redis counters. Returns zeros on outage."""
    try:
        from deps import redis_client
        if redis_client is None:
            return {"hits": 0, "misses": 0, "hit_rate": 0.0, "available": False}
        h_raw = redis_client.get("rag:cache:hits") or 0
        m_raw = redis_client.get("rag:cache:misses") or 0
        hits = int(h_raw if not isinstance(h_raw, (bytes, bytearray)) else h_raw.decode())
        misses = int(m_raw if not isinstance(m_raw, (bytes, bytearray)) else m_raw.decode())
    except Exception as e:
        logger.debug("[admin_cache] rag_cache counters unavailable: %s", e)
        return {"hits": 0, "misses": 0, "hit_rate": 0.0, "available": False}
    total = hits + misses
    return {
        "hits": hits,
        "misses": misses,
        "hit_rate": round(hits / total, 4) if total else 0.0,
    }


def _l1_inproc_stats() -> dict[str, Any]:
    """Snapshot of every `_InstrumentedTTLCache` ring in `cache.py`.

    Task #571 round-3: counters now come from `cache.l1_counters_snapshot()`
    (each `_InstrumentedTTLCache` increments hits/misses/sets on every read +
    write). Hit-rate is computed as `hits / (hits + misses)` — `None` only
    when the ring has not yet served any traffic (NOT zero, which would
    page false alarms on a freshly restarted pod)."""
    out: dict[str, Any] = {}
    try:
        import cache as _c
        counters = _c.l1_counters_snapshot()
        for name in (
            "_ai_response_cache", "_user_cache", "_conv_cache", "_content_cache",
            "_rag_cache", "_vector_rag_cache", "_query_embed_cache",
            "_embedding_cache", "_content_card_cache", "_syllabus_cache",
            "_hierarchy_cache",
        ):
            inst = getattr(_c, name, None)
            if inst is None:
                continue
            row = counters.get(name) or {"hits": 0, "misses": 0, "sets": 0}
            hits = int(row.get("hits", 0))
            misses = int(row.get("misses", 0))
            total = hits + misses
            out[name] = {
                "currsize": getattr(inst, "currsize", None),
                "maxsize": getattr(inst, "maxsize", None),
                "ttl_seconds": getattr(inst, "ttl", None),
                "hits": hits,
                "misses": misses,
                "sets": int(row.get("sets", 0)),
                "hit_rate": (round(hits / total, 4) if total else None),
            }
    except Exception as e:
        logger.debug("[admin_cache] l1 inproc cache stats unavailable: %s", e)
    return out


@router.get("/api/health/cache")
async def admin_cache_health(_admin: dict = Depends(get_admin_user)) -> dict[str, Any]:
    """Return per-layer cache stats + edge advisory targets.

    Shape (consumed by `/admin/observability` cache panel and the
    nightly `cache_effectiveness` Lambda):

        {
          "ai_input_cache":     { totals + per-content-type rows },
          "ai_response_cache":  { hits, misses, hit_rate, backend },
          "rag_cache":          { hits, misses, hit_rate },
          "l1_inproc":          { _user_cache: {currsize, maxsize, ttl_seconds, hit_rate}, ... },
          "edge_targets":       [ { path, ttl_seconds, cache_hit_ratio_target, user_keyed }, ... ],
          "alarm_thresholds":   { ai_cache_hit_ratio_floor, cardinality_multiplier }
        }
    """
    return {
        "ai_input_cache": _ai_cache_snapshot(),
        "ai_response_cache": _ai_response_cache_stats(),
        "rag_cache": _rag_cache_stats(),
        "l1_inproc": _l1_inproc_stats(),
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
