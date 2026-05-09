"""Task #9 — Admin observability tile for the verified-bot fast path.

Surfaces per-bucket bot traffic + per-family rDNS verification
miss-rate at ``GET /api/admin/health/bot-buckets`` so the AdminHealth
dashboard can render the same four-bucket roll-up that
``infra/bot-rules.yaml`` defines:

* ``verified_search``   — search engines on the high-RPM bucket
* ``citation_ai``       — answer engines on the high-RPM bucket
* ``training_ai``       — 403'd at the edge by ``AI_BOT_UA``
* ``abusive``           — held to the standard 120 RPM ceiling

Two independent signals are surfaced:

1. **Per-bucket hits** — projected from ``cf_bot_report.collect_recent_bot_hits``
   (existing CF Logpush-derived per-UA roll-up).
2. **Per-family rDNS verification miss-rate** — the worker's
   ``verifyBotIpWithKv`` writes ``bot:rdns_ctr:<day>:<family>:<outcome>``
   counters into Cloudflare KV (RATE_LIMIT namespace, 48 h TTL). We
   read them here and compute miss-rate = ``miss / (hit + miss)``,
   where ``miss`` means the verification did a live PTR/A round-trip
   (cache miss) and ``hit`` means the 24 h KV cache served the
   answer. A miss-rate >> 0 indicates KV churn / IP rotation; a
   sudden 100 % miss-rate indicates the KV cache itself is
   misbehaving.

Read-only and admin-gated; no writes, no external API calls. Safe to
poll on the ~30 s AdminHealth refresh interval.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends

from auth_deps import get_admin_user

logger = logging.getLogger(__name__)
router = APIRouter()

# Canonical registry path — single source of truth for which UA tokens
# belong to which bucket. Loaded lazily on first request so server
# startup doesn't pay the parse cost.
_RULES_PATH = Path(__file__).resolve().parents[3] / "infra" / "bot-rules.yaml"


def _load_registry() -> dict[str, list[str]]:
    """Return ``{bucket: [token, ...]}`` from the YAML registry. Uses
    the same tolerant mini-parser as ``scripts/gen_bot_regex.py`` so
    the backend doesn't acquire a hard dependency on PyYAML at the
    import boundary."""
    import sys
    repo_root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(repo_root / "scripts"))
    try:
        import gen_bot_regex  # type: ignore
        rules = gen_bot_regex._load_yaml()
        return gen_bot_regex.all_tokens(rules)
    except Exception as exc:  # noqa: BLE001
        logger.warning("bot_buckets: registry load failed: %s", exc)
        return {}


def _classify(ua_lower: str, by_bucket: dict[str, list[str]]) -> str | None:
    """Return the FIRST bucket whose token list matches the UA.

    Order matters: verified_search wins over training_ai for UAs
    listed in both (Google-Extended, Applebot-Extended). The dashboard
    reports them as verified_search because that's the bucket the
    high-RPM edge fast path puts them in; the training_ai listing is
    a robots.txt-only signal."""
    for bucket in ("verified_search", "citation_ai", "training_ai", "abusive"):
        for token in by_bucket.get(bucket, []):
            if token in ua_lower:
                return bucket
    return None


_RDNS_FAMILIES = (
    "googlebot", "bingbot", "duckduckbot", "applebot", "yandexbot",
    "baiduspider", "petalbot", "yeti", "seznambot", "yahoo-slurp",
    "perplexitybot", "openai-search",
)


def _read_rdns_counters() -> dict[str, dict[str, int]]:
    """Best-effort fetch of today's per-family rDNS counters from
    Upstash Redis. The edge worker writes the same keys directly to
    Cloudflare KV (RATE_LIMIT namespace) AND, when configured, to
    the same Upstash REST endpoint via the existing observability
    bridge (`workers/edge-proxy/src/redis-mirror.ts`). The backend
    reads via the shared sync `upstash_redis.Redis` client exposed
    as `deps.redis_client`. Returns
    ``{family: {hit_pos, hit_neg, miss_pos, miss_neg}}`` with zeros
    on any read failure — the dashboard renders an empty-state tile
    rather than 5xx-ing the whole admin health page."""
    from datetime import datetime, timezone
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out: dict[str, dict[str, int]] = {
        f: {"hit_pos": 0, "hit_neg": 0, "miss_pos": 0, "miss_neg": 0}
        for f in _RDNS_FAMILIES
    }
    try:
        from deps import redis_client  # type: ignore
        if redis_client is None:
            return out
        for family in _RDNS_FAMILIES:
            for outcome in ("hit_pos", "hit_neg", "miss_pos", "miss_neg"):
                key = f"bot:rdns_ctr:{day}:{family}:{outcome}"
                try:
                    val = redis_client.get(key)  # upstash_redis is sync
                    if val is not None:
                        out[family][outcome] = int(val)
                except Exception:  # noqa: BLE001
                    continue
    except Exception as exc:  # noqa: BLE001
        logger.info("bot_buckets: rdns counter read unavailable (%s)", exc)
    return out


def _compute_miss_rate(counts: dict[str, int]) -> dict[str, Any]:
    """Roll up a single family's counts into a tile-friendly summary."""
    hits = counts["hit_pos"] + counts["hit_neg"]
    misses = counts["miss_pos"] + counts["miss_neg"]
    total = hits + misses
    return {
        "hits": hits,
        "misses": misses,
        "total": total,
        # miss_rate = portion of verifications that required a live
        # PTR+A round-trip rather than a 24 h KV cache hit. 0 = ideal.
        "miss_rate": (misses / total) if total > 0 else 0.0,
        "verified_total": counts["hit_pos"] + counts["miss_pos"],
        "denied_total": counts["hit_neg"] + counts["miss_neg"],
    }


@router.get("/admin/health/bot-buckets")
async def bot_buckets_health(_admin: dict = Depends(get_admin_user)) -> dict[str, Any]:
    """Per-bucket bot traffic projection over the most recent
    ``cf_bot_report`` window. Shape:

        {
          "buckets": {
            "verified_search": {"hits_24h": 12345, "uas": ["googlebot", ...]},
            "citation_ai":     {...},
            "training_ai":     {...},
            "abusive":         {...},
            "unclassified":    {...},
          },
          "totals": {"hits_24h": 67890, "buckets": 5, "tokens": 48},
          "registry_path": "infra/bot-rules.yaml",
          "fast_path_rpm": 60000,
        }

    ``unclassified`` is intentional — UAs the registry doesn't
    recognise (new browsers, new bot families) accumulate here so an
    operator can decide whether to add them to the YAML.
    """
    by_bucket = _load_registry()
    bucket_names = ("verified_search", "citation_ai", "training_ai", "abusive", "unclassified")
    counters: dict[str, dict[str, Any]] = {
        b: {"hits_24h": 0, "uas": []} for b in bucket_names
    }

    # cf_bot_report exposes a rolling-window dict of {ua_lower: count}.
    # If it isn't available (cold start, missing CF API token) we
    # surface zeroes rather than 5xx — the dashboard tile renders an
    # empty state.
    try:
        from cf_bot_report import collect_recent_bot_hits  # type: ignore
        per_ua = await collect_recent_bot_hits(window_h=24) or {}
    except Exception as exc:  # noqa: BLE001
        logger.info("bot_buckets: cf_bot_report unavailable (%s)", exc)
        per_ua = {}

    for ua_lower, count in per_ua.items():
        bucket = _classify(ua_lower, by_bucket) or "unclassified"
        counters[bucket]["hits_24h"] += int(count)
        # Cap the surfaced per-bucket UA list at 25 to keep the
        # response small even for noisy unclassified buckets.
        if len(counters[bucket]["uas"]) < 25:
            counters[bucket]["uas"].append({"ua": ua_lower, "hits_24h": int(count)})

    total_hits = sum(b["hits_24h"] for b in counters.values())
    total_tokens = sum(len(v) for v in by_bucket.values())

    # rDNS verification miss-rate per family (Task #9 — surfaces the
    # forward-confirmed PTR cache health from the edge worker).
    rdns_raw = _read_rdns_counters()
    rdns_per_family = {f: _compute_miss_rate(c) for f, c in rdns_raw.items()}
    rdns_total = {"hits": 0, "misses": 0, "verified_total": 0, "denied_total": 0, "total": 0}
    for s in rdns_per_family.values():
        for k in ("hits", "misses", "verified_total", "denied_total", "total"):
            rdns_total[k] += s[k]
    rdns_total["miss_rate"] = (
        rdns_total["misses"] / rdns_total["total"] if rdns_total["total"] > 0 else 0.0
    )

    return {
        "buckets": counters,
        "totals": {
            "hits_24h": total_hits,
            "buckets": len(bucket_names),
            "tokens": total_tokens,
        },
        # Per-family rDNS verification health (24 h window, today UTC).
        # Each entry: {hits, misses, total, miss_rate, verified_total,
        # denied_total}. miss_rate near 0 means the 24 h KV cache is
        # serving most verifications; sustained miss_rate ~1 indicates
        # KV cache churn or IP-rotation — investigate.
        "rdns_verification": {
            "per_family": rdns_per_family,
            "total": rdns_total,
            "window": "today_utc",
            "cache_ttl_s": 86400,
        },
        "registry_path": "infra/bot-rules.yaml",
        "fast_path_rpm": 60000,
        # Operator hint — what to do when "unclassified" is non-zero.
        "operator_hint": (
            "Add unclassified UAs to infra/bot-rules.yaml under the "
            "appropriate bucket; the drift check will then enforce "
            "the four runtime regexes contain them. A sustained "
            "rdns_verification.total.miss_rate near 1.0 indicates "
            "the KV cache is not being read — check RATE_LIMIT KV "
            "namespace binding in the edge worker."
        ),
    }
