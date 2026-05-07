"""Task #571 — nightly shipper for cache-effectiveness telemetry.

EventBridge cron `cron(15 3 * * ? *)` (daily 03:15 UTC, after the
Assamese translation backfill at 03:00 has finished and before any
human admin shows up). The handler:

  1. Mints a short-lived admin JWT from `ADMIN_JWT_SECRET` (sourced
     from Secrets Manager).
  2. Calls `GET https://<backend>/api/health/cache` and parses the
     `ai_input_cache` block.
  3. Publishes the per-content-type counters to the `Syrabit/Cache`
     CloudWatch namespace as PutMetricData with dimensions
     `(ContentType=<ct>)` and the totals as `(ContentType=Total)`.

The CloudWatch alarms (`infra/aws/lambda-batch-jobs.tf`):

  * `cache-ai-hitratio-low`  — Total HitRatio < 0.30 for 1 day.
  * `cache-cardinality-spike` — Total UniqueKeys24h > 3× the trailing
    7-day moving average; uses CW Metric Math.

Both fire to the existing `ops_alerts` SNS topic.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger("lambda_batch.cache_effectiveness")
logger.setLevel(logging.INFO)

NAMESPACE = "Syrabit/Cache"
BACKEND_URL = os.environ.get(
    "BACKEND_URL",
    "https://syrabit-backend.lemonstone-ce3c87e1.eastus.azurecontainerapps.io",
)
ADMIN_JWT_SECRET_ARN = os.environ.get("ADMIN_JWT_SECRET_ARN", "")


def _load_admin_jwt_secret() -> str:
    direct = os.environ.get("ADMIN_JWT_SECRET", "").strip()
    if direct:
        return direct
    if not ADMIN_JWT_SECRET_ARN:
        raise RuntimeError("ADMIN_JWT_SECRET / ADMIN_JWT_SECRET_ARN not set")
    import boto3  # type: ignore
    sm = boto3.client("secretsmanager")
    raw = (sm.get_secret_value(SecretId=ADMIN_JWT_SECRET_ARN).get("SecretString") or "").strip()
    if raw.startswith("{"):
        return json.loads(raw).get("secret", raw)
    return raw


def _mint_admin_jwt() -> str:
    import jwt  # type: ignore
    secret = _load_admin_jwt_secret()
    payload = {
        "sub": "lambda-cache-effectiveness",
        "role": "admin",
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def _fetch_snapshot() -> dict[str, Any]:
    import urllib.request as _ur
    token = _mint_admin_jwt()
    req = _ur.Request(
        f"{BACKEND_URL.rstrip('/')}/api/health/cache",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    with _ur.urlopen(req, timeout=10.0) as resp:
        if resp.status != 200:
            raise RuntimeError(f"cache health returned {resp.status}")
        return json.loads(resp.read().decode("utf-8"))


def _emit(cw, dims_ct: str, *, hits: int, misses: int, sets: int,
          hit_ratio: float, unique_keys: int, miss_reasons: dict[str, int]) -> None:
    """Publish one ContentType row to CloudWatch."""
    base = [{"Name": "ContentType", "Value": dims_ct}]
    metrics = [
        {"MetricName": "Hits",          "Value": float(hits),          "Unit": "Count",   "Dimensions": base},
        {"MetricName": "Misses",        "Value": float(misses),        "Unit": "Count",   "Dimensions": base},
        {"MetricName": "Sets",          "Value": float(sets),          "Unit": "Count",   "Dimensions": base},
        {"MetricName": "HitRatio",      "Value": float(hit_ratio),     "Unit": "None",    "Dimensions": base},
        {"MetricName": "UniqueKeys24h", "Value": float(unique_keys),   "Unit": "Count",   "Dimensions": base},
    ]
    for reason, n in (miss_reasons or {}).items():
        metrics.append({
            "MetricName": "MissReason",
            "Value": float(n),
            "Unit": "Count",
            "Dimensions": base + [{"Name": "Reason", "Value": reason}],
        })
    # CloudWatch caps PutMetricData at 1000 metrics / call — we are well under.
    cw.put_metric_data(Namespace=NAMESPACE, MetricData=metrics)


def _emit_layer(cw, layer: str, *, hits: int, misses: int, hit_rate: float) -> None:
    """Emit a single (Layer=ai_response_cache|rag_cache|...) row.

    Lets the admin panel render every layer on the same chart and lets
    the alarm namespace stay flat (no second namespace per layer)."""
    base = [{"Name": "Layer", "Value": layer}]
    cw.put_metric_data(Namespace=NAMESPACE, MetricData=[
        {"MetricName": "LayerHits",     "Value": float(hits),     "Unit": "Count", "Dimensions": base},
        {"MetricName": "LayerMisses",   "Value": float(misses),   "Unit": "Count", "Dimensions": base},
        {"MetricName": "LayerHitRate",  "Value": float(hit_rate), "Unit": "None",  "Dimensions": base},
    ])


def _emit_l1(cw, name: str, *, currsize: int, maxsize: int) -> None:
    base = [{"Name": "L1Cache", "Value": name}]
    cw.put_metric_data(Namespace=NAMESPACE, MetricData=[
        {"MetricName": "L1Currsize", "Value": float(currsize), "Unit": "Count", "Dimensions": base},
        {"MetricName": "L1Capacity", "Value": float(maxsize),  "Unit": "Count", "Dimensions": base},
        # Saturation = currsize / maxsize. Helps tune layer sizes without
        # reading the dashboard math.
        {"MetricName": "L1Saturation",
         "Value": float(currsize) / float(maxsize) if maxsize else 0.0,
         "Unit": "None", "Dimensions": base},
    ])


def _emit_edge_route(cw, path: str, *, hit_rate: float) -> None:
    """Emit one (EdgeRoute=<path>) row from CF Analytics. Best-effort —
    we only call this when CF API token is configured + the GraphQL
    response actually has data for the path."""
    base = [{"Name": "EdgeRoute", "Value": path}]
    cw.put_metric_data(Namespace=NAMESPACE, MetricData=[
        {"MetricName": "EdgeHitRate", "Value": float(hit_rate), "Unit": "None", "Dimensions": base},
    ])


def _fetch_cf_edge_hit_rates(paths: list[str]) -> dict[str, float]:
    """Pull per-path edge cache hit-rate from Cloudflare Analytics
    (GraphQL httpRequestsAdaptiveGroups). Returns `{path: hit_rate}`.

    Best-effort: missing CF_API_TOKEN / CF_ZONE_ID returns {} silently
    so the alarm flow does not block on optional credentials. Errors
    are logged and downgraded to {} so a CF outage cannot fail the
    nightly job (the AI-cache rows above still ship)."""
    token = os.environ.get("CF_API_TOKEN", "").strip()
    zone = os.environ.get("CF_ZONE_ID", "").strip()
    if not (token and zone and paths):
        logger.info("CF edge hit-rate skipped (CF_API_TOKEN/CF_ZONE_ID/paths missing)")
        return {}
    import urllib.request as _ur
    # 24h trailing window (matches the AI-cache UniqueKeys24h period).
    end = time.gmtime()
    start_t = time.gmtime(time.time() - 86_400)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    query = """
      query($zone:String!,$start:Time!,$end:Time!){
        viewer{ zones(filter:{zoneTag:$zone}){
          httpRequestsAdaptiveGroups(
            limit:1000,
            filter:{datetime_geq:$start,datetime_leq:$end},
            orderBy:[clientRequestPath_ASC]
          ){
            count
            sum{ cachedRequests:cachedRequests, edgeResponseBytes:edgeResponseBytes }
            dimensions{ clientRequestPath:clientRequestPath }
          }
        } }
      }
    """
    body = json.dumps({
        "query": query,
        "variables": {
            "zone": zone,
            "start": time.strftime(fmt, start_t),
            "end": time.strftime(fmt, end),
        },
    }).encode("utf-8")
    req = _ur.Request(
        "https://api.cloudflare.com/client/v4/graphql",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    try:
        with _ur.urlopen(req, timeout=10.0) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.warning("CF GraphQL edge hit-rate fetch failed: %s", e)
        return {}
    out: dict[str, float] = {}
    try:
        groups = (
            payload.get("data", {})
            .get("viewer", {})
            .get("zones", [{}])[0]
            .get("httpRequestsAdaptiveGroups", [])
        )
        wanted = set(paths)
        # Aggregate by exact path-match. Prefix-match entries (e.g.
        # /api/content/) are handled by summing every CF row whose
        # clientRequestPath startswith the prefix.
        for g in groups:
            cf_path = (g.get("dimensions") or {}).get("clientRequestPath", "")
            count = int(g.get("count") or 0)
            cached = int((g.get("sum") or {}).get("cachedRequests") or 0)
            for w in wanted:
                if cf_path == w or cf_path.startswith(w):
                    h = out.get(w, (0, 0))
                    out[w] = (h[0] + cached, h[1] + count)
        # Resolve to hit-rate.
        return {p: (c / t) if t else 0.0 for p, (c, t) in out.items()}
    except Exception as e:
        logger.warning("CF GraphQL parse failed: %s", e)
        return {}


def handler(event, context):  # noqa: ARG001
    logger.info("cache_effectiveness invoked: event=%s", json.dumps(event)[:300])
    snapshot = _fetch_snapshot()

    import boto3  # type: ignore
    cw = boto3.client("cloudwatch")

    # ── 1. AI-input cache (per-content-type) ────────────────────────
    aic = snapshot.get("ai_input_cache") or {}
    totals = aic.get("totals") or {}
    cts = aic.get("content_types") or {}
    _emit(
        cw, "Total",
        hits=int(totals.get("hits", 0)),
        misses=int(totals.get("misses", 0)),
        sets=int(totals.get("sets", 0)),
        hit_ratio=float(totals.get("hit_ratio", 0.0)),
        unique_keys=int(totals.get("unique_keys_24h", 0)),
        miss_reasons={},
    )
    for ct, row in cts.items():
        _emit(
            cw, ct,
            hits=int(row.get("hits", 0)),
            misses=int(row.get("misses", 0)),
            sets=int(row.get("sets", 0)),
            hit_ratio=float(row.get("hit_ratio", 0.0)),
            unique_keys=int(row.get("unique_keys_24h", 0)),
            miss_reasons=row.get("miss_reasons") or {},
        )

    # ── 2. Other backend layers (Layer dimension) ───────────────────
    arc = snapshot.get("ai_response_cache") or {}
    _emit_layer(cw, "ai_response_cache",
                hits=int(arc.get("hits", 0)),
                misses=int(arc.get("misses", 0)),
                hit_rate=float(arc.get("hit_rate", 0.0)))
    rag = snapshot.get("rag_cache") or {}
    _emit_layer(cw, "rag_cache",
                hits=int(rag.get("hits", 0)),
                misses=int(rag.get("misses", 0)),
                hit_rate=float(rag.get("hit_rate", 0.0)))

    # ── 3. L1 in-process cachetools rings (cardinality only) ────────
    for name, row in (snapshot.get("l1_inproc") or {}).items():
        _emit_l1(cw, name,
                 currsize=int(row.get("currsize") or 0),
                 maxsize=int(row.get("maxsize") or 0))

    # ── 4. Cloudflare edge hit-rate per cacheable route (optional) ──
    edge_targets = snapshot.get("edge_targets") or []
    edge_paths = [t["path"] for t in edge_targets if t.get("path")]
    edge_rates = _fetch_cf_edge_hit_rates(edge_paths)
    for path, rate in edge_rates.items():
        _emit_edge_route(cw, path, hit_rate=rate)

    summary = {
        "totals": totals,
        "content_types_emitted": list(cts.keys()),
        "layers_emitted": ["ai_response_cache", "rag_cache"],
        "l1_emitted": list((snapshot.get("l1_inproc") or {}).keys()),
        "edge_routes_emitted": list(edge_rates.keys()),
    }
    logger.info("cache_effectiveness summary: %s", json.dumps(summary, default=str)[:800])
    return {"ok": True, "summary": summary}
