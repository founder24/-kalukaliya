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
from ai_input_cache import per_region_snapshot as _ai_cache_per_region_snapshot

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


# ── Cloudflare edge hit-rate ────────────────────────────────────────────────
# Pulled from CF Analytics GraphQL on demand (and cached in-process for
# 60 s so a tab refresh cannot DoS the panel into the CF rate limits).
# Best-effort: missing CF_API_TOKEN / CF_ZONE_ID returns {} silently so
# the panel still renders the AI-cache + L1 rows.
import time as _time
import urllib.request as _ur

_CF_EDGE_CACHE: dict[str, Any] = {"ts": 0.0, "value": {}}
_CF_EDGE_TTL_S = 60.0


def _fetch_cf_edge_hit_rates(paths: list[str]) -> dict[str, float]:
    if not paths:
        return {}
    now = _time.monotonic()
    if now - float(_CF_EDGE_CACHE.get("ts", 0.0)) < _CF_EDGE_TTL_S:
        return dict(_CF_EDGE_CACHE.get("value") or {})
    token = os.environ.get("CF_API_TOKEN", "").strip()
    zone = os.environ.get("CF_ZONE_ID", "").strip()
    if not (token and zone):
        return {}
    end = _time.gmtime()
    start_t = _time.gmtime(_time.time() - 86_400)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    query = (
        "query($zone:String!,$start:Time!,$end:Time!){"
        " viewer{ zones(filter:{zoneTag:$zone}){"
        "  httpRequestsAdaptiveGroups(limit:1000,"
        "   filter:{datetime_geq:$start,datetime_leq:$end},"
        "   orderBy:[clientRequestPath_ASC]){"
        "    count sum{cachedRequests}"
        "    dimensions{clientRequestPath}"
        "  } } }"
        "}"
    )
    body = json.dumps({
        "query": query,
        "variables": {
            "zone": zone,
            "start": _time.strftime(fmt, start_t),
            "end": _time.strftime(fmt, end),
        },
    }).encode("utf-8")
    req = _ur.Request(
        "https://api.cloudflare.com/client/v4/graphql",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    try:
        with _ur.urlopen(req, timeout=4.0) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.debug("[admin_cache] CF GraphQL fetch failed: %s", e)
        return {}
    out: dict[str, tuple[int, int]] = {}
    try:
        groups = (
            payload.get("data", {}).get("viewer", {})
            .get("zones", [{}])[0].get("httpRequestsAdaptiveGroups", [])
        )
        wanted = set(paths)
        for g in groups:
            cf_path = (g.get("dimensions") or {}).get("clientRequestPath", "")
            count = int(g.get("count") or 0)
            cached = int((g.get("sum") or {}).get("cachedRequests") or 0)
            for w in wanted:
                if cf_path == w or cf_path.startswith(w):
                    h = out.get(w, (0, 0))
                    out[w] = (h[0] + cached, h[1] + count)
        rates = {p: (c / t if t else 0.0) for p, (c, t) in out.items()}
    except Exception as e:
        logger.debug("[admin_cache] CF GraphQL parse failed: %s", e)
        rates = {}
    _CF_EDGE_CACHE["ts"] = now
    _CF_EDGE_CACHE["value"] = rates
    return rates


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
    edge_targets = _load_edge_targets()
    edge_paths = [t["path"] for t in edge_targets if t.get("path")]
    edge_hit_rates = _fetch_cf_edge_hit_rates(edge_paths)
    # Decorate edge_targets with the live hit-rate so the panel can
    # render advisory + actual side-by-side without a second pass.
    for t in edge_targets:
        rate = edge_hit_rates.get(t.get("path"))
        if rate is not None:
            t["live_hit_rate"] = rate
    # Task #2 — Assamese-aware per-region tile. Rolls per-region
    # counters from every cache layer that supports the new `region`
    # arg (ai_input_cache, kv_cache, cf_tiered_cache) into a single
    # `per_region` field the admin panel renders side-by-side.
    per_region: dict[str, Any] = {
        "ai_input_cache": _ai_cache_per_region_snapshot(),
    }
    try:
        from kv_cache import default_cache as _kv_default
        per_region["kv_cache"] = _kv_default().per_region_snapshot()
    except Exception as e:
        logger.debug("[admin_cache] kv_cache per-region unavailable: %s", e)
    try:
        from cf_tiered_cache import per_region_snapshot as _cf_per_region
        per_region["cf_tiered_cache"] = _cf_per_region()
    except Exception as e:
        logger.debug("[admin_cache] cf_tiered_cache per-region unavailable: %s", e)
    return {
        "ai_input_cache": _ai_cache_snapshot(),
        "ai_response_cache": _ai_response_cache_stats(),
        "rag_cache": _rag_cache_stats(),
        "l1_inproc": _l1_inproc_stats(),
        "edge_targets": edge_targets,
        "edge_hit_rates_cf": edge_hit_rates,
        "per_region": per_region,
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
