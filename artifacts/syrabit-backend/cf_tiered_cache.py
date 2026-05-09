"""Smart Tiered Cache activation gate + ``purge_by_cache_tags`` helper
+ ``hit_ratio_snapshot`` that derives a zone hit ratio from CF GraphQL
analytics."""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def is_enabled() -> bool:
    """True iff CF_TIERED_CACHE_ON is set in the environment."""
    from config import CF_TIERED_CACHE_ON
    return bool(CF_TIERED_CACHE_ON)


async def apply_tiered_cache() -> dict[str, Any]:
    """Enable Smart Tiered Cache when the flag is on.

    No-op when ``CF_TIERED_CACHE_ON`` is unset.
    """
    if not is_enabled():
        return {"applied": False, "reason": "flag_off"}
    try:
        from cf_enterprise import tiered_cache_enable, tiered_cache_status
    except Exception as exc:
        return {"applied": False, "reason": f"import_failed: {exc}"}

    before = await tiered_cache_status()
    result = await tiered_cache_enable()
    after = await tiered_cache_status()
    return {
        "applied": result is not None,
        "before": (before or {}).get("value") if before else None,
        "after": (after or {}).get("value") if after else None,
    }


async def purge_by_cache_tags(tags: list[str]) -> dict[str, Any]:
    """Purge CF edge cache for all entries carrying any of ``tags``.

    Thin gated wrapper over ``cf_enterprise.purge_by_tags`` so callers
    can stay flag-aware without importing two modules. Returns
    ``{"purged": False, "reason": "flag_off"}`` when the flag is
    disabled (no-op so a rollback does not re-introduce stale
    invalidation traffic).
    """
    if not is_enabled():
        return {"purged": False, "reason": "flag_off", "tags": tags}
    if not tags:
        return {"purged": False, "reason": "no_tags", "tags": []}
    try:
        from cf_enterprise import purge_by_tags
    except Exception as exc:
        return {"purged": False, "reason": f"import_failed: {exc}", "tags": tags}
    result = await purge_by_tags(tags)
    return {"purged": result is not None, "tags": tags, "result": result}


async def hit_ratio_snapshot(window_minutes: int = 60) -> dict[str, Any]:
    """Best-effort cache-hit-ratio snapshot for the last ``window_minutes``.

    Uses the Cloudflare GraphQL analytics endpoint. Requires the same
    ``CF_ANALYTICS_API_TOKEN`` already used by ``cf_web_analytics``
    plus ``CF_ZONE_ID``. When either is missing the snapshot reports
    ``configured=False`` and the route renders a "not configured"
    pill rather than failing the whole panel.
    """
    token = (
        os.environ.get("CF_ANALYTICS_API_TOKEN", "").strip()
        or os.environ.get("CLOUDFLARE_ANALYTICS_TOKEN", "").strip()
        or os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    )
    zone = os.environ.get("CF_ZONE_ID", "").strip()
    if not (token and zone):
        return {"configured": False, "reason": "missing_token_or_zone"}

    # Window boundaries in CF's GraphQL ISO8601 format. We don't import
    # datetime at module top level so this stays cheap on cold start.
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc).replace(microsecond=0)
    start = (now - timedelta(minutes=window_minutes)).isoformat()
    end = now.isoformat()

    # CF returns one group per 1-minute bucket; we sum across the full
    # window in Python rather than asking GraphQL for a single bucket.
    # `limit` is the per-zone group cap; CF caps it at 10000 and a 1440
    # ceiling comfortably covers a 24h window of 1-minute buckets.
    query = """
    query Hit($zone: String!, $start: Time!, $end: Time!) {
      viewer {
        zones(filter: {zoneTag: $zone}) {
          httpRequests1mGroups(
            limit: 1440
            filter: {datetime_geq: $start, datetime_lt: $end}
          ) {
            sum {
              cachedRequests
              requests
              cachedBytes
              bytes
            }
          }
        }
      }
    }
    """
    payload = {
        "query": query,
        "variables": {"zone": zone, "start": start, "end": end},
    }
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.post(
                "https://api.cloudflare.com/client/v4/graphql",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        return {"configured": True, "ok": False, "reason": f"{type(exc).__name__}: {exc}"}

    try:
        groups = (
            data["data"]["viewer"]["zones"][0]["httpRequests1mGroups"]
        )
    except Exception:
        return {"configured": True, "ok": False, "reason": "no_data"}

    if not groups:
        return {"configured": True, "ok": True, "window_minutes": window_minutes,
                "hit_ratio": None, "samples": 0}

    requests = cached = bytes_total = bytes_cached = 0
    for grp in groups:
        sums = (grp or {}).get("sum") or {}
        requests += int(sums.get("requests") or 0)
        cached += int(sums.get("cachedRequests") or 0)
        bytes_total += int(sums.get("bytes") or 0)
        bytes_cached += int(sums.get("cachedBytes") or 0)
    return {
        "configured": True,
        "ok": True,
        "window_minutes": window_minutes,
        "samples": len(groups),
        "requests": requests,
        "cached_requests": cached,
        "hit_ratio": (cached / requests) if requests else None,
        "byte_hit_ratio": (bytes_cached / bytes_total) if bytes_total else None,
    }


# Task #2 — Assamese-aware per-region tag counters. The edge proxy
# stamps the inbound region in `X-Cache-Region` (default "global";
# "ne-india" for Assam-served requests). We track hit / miss counts in
# process so `routes/admin_cache.py` can render a per-region tile
# alongside the global zone hit-ratio.
_REGION_TAG_COUNTERS: dict[str, dict[str, int]] = {}


def colo_bias_for_region(region: str) -> tuple[str, ...]:
    """Task #2 — return the Cloudflare colo bias intended for a given
    cache region. `ne-india` resolves to the two AP-South colos closest
    to Assam (Mumbai = BOM, Chennai = MAA); every other region is
    served via the global tier. The edge proxy stamps these as
    `X-Backend-Colo-Bias` so the backend Ops Console can flag requests
    that landed outside the intended bias."""
    if (region or "global").strip().lower() == "ne-india":
        return ("BOM", "MAA")
    return ("global",)


def tier_cache_tag_for(region: str) -> str:
    """Task #2 — the CF cache tag stamped on every entry written for
    this region. Cloudflare Tiered Cache + Argo route requests for a
    given tag-prefix consistently to the same upper-tier colo, so by
    using `tier:ne-india` for Assamese reads we bias all upper-tier
    fetches into the AP-South (BOM/MAA) topology that already serves
    those edge POPs. `tier:global` for everyone else."""
    r = (region or "global").strip().lower()
    if r == "ne-india":
        return "tier:ne-india"
    return "tier:global"


def kv_namespace_for_region(region: str) -> str:
    """Task #2 — namespace prefix used by the backend cache when
    writing into Cloudflare KV. ne-india entries land in
    `ai_response_cache:v1:ne-india:` so they can be routed to the
    AP-South KV replica without touching the global namespace."""
    r = (region or "global").strip().lower()
    return r if r in ("ne-india",) else "global"


def record_region_event(region: str, hit: bool) -> None:
    region = region or "global"
    row = _REGION_TAG_COUNTERS.setdefault(region, {"hits": 0, "misses": 0})
    row["hits" if hit else "misses"] += 1


def per_region_snapshot() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for region, row in _REGION_TAG_COUNTERS.items():
        hits = int(row.get("hits", 0))
        misses = int(row.get("misses", 0))
        total = hits + misses
        out[region] = {
            "hits": hits,
            "misses": misses,
            "hit_ratio": (round(hits / total, 4) if total else None),
        }
    return out


async def snapshot() -> dict[str, Any]:
    """Aggregate snapshot for ``/admin/cf-health``."""
    enabled = is_enabled()
    out: dict[str, Any] = {"enabled": enabled, "status": None, "hit_ratio": None,
                           "per_region": per_region_snapshot()}
    if enabled:
        try:
            from cf_enterprise import tiered_cache_status
            status = await tiered_cache_status()
            out["status"] = (status or {}).get("value") if status else None
            out["configured"] = status is not None
        except Exception as exc:
            out["status"] = None
            out["error"] = f"{type(exc).__name__}: {exc}"
    out["hit_ratio"] = await hit_ratio_snapshot()
    return out
