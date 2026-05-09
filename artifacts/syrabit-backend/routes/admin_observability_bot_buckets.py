"""Task #9 — Admin observability tile for the verified-bot fast path.

Surfaces per-bucket bot traffic counters at
``GET /api/admin/health/bot-buckets`` so the AdminHealth dashboard
can render the same four-bucket roll-up that ``infra/bot-rules.yaml``
defines:

* ``verified_search``   — search engines on the high-RPM bucket
* ``citation_ai``       — answer engines on the high-RPM bucket
* ``training_ai``       — 403'd at the edge by ``AI_BOT_UA``
* ``abusive``           — held to the standard 120 RPM ceiling

The counters are populated by ``cf_bot_report.collect_recent_bot_hits``
(the existing CF Logpush-derived per-UA roll-up). This route simply
projects the per-UA counts onto the canonical registry's buckets so
the dashboard reads the same labels as the YAML and the worker
without an extra translation layer.

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

    return {
        "buckets": counters,
        "totals": {
            "hits_24h": total_hits,
            "buckets": len(bucket_names),
            "tokens": total_tokens,
        },
        "registry_path": "infra/bot-rules.yaml",
        "fast_path_rpm": 60000,
        # Operator hint — what to do when "unclassified" is non-zero.
        "operator_hint": (
            "Add unclassified UAs to infra/bot-rules.yaml under the "
            "appropriate bucket; the drift check will then enforce "
            "the four runtime regexes contain them."
        ),
    }
