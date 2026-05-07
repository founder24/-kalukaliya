"""free_tier_dispatch — Task #581 §L8 observability counters.

Per-tier counters for the chat dispatch path so the admin Observability
panel + the >5% paid-escalation alarm can show "where did free turns
land": cache / rag / mongo / cheap / tight / retrieval_only / paywall /
paid-escalation.

Counters are tracked on a rolling 24h window in Redis (1-hour buckets,
24 entries per content_type) so an isolated bad-traffic spike doesn't
permanently skew the breakdown. All Redis ops are best-effort — no
exception escapes; on Redis outage the counters degrade to in-memory.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

_REDIS_KEY_PREFIX = "free_tier_dispatch:v1"
_BUCKET_SEC = 3600
_HISTORY_BUCKETS = 24

# Canonical tier names — keep stable, the admin panel + the alarm
# query hard-key on these.
TIER_CACHE_HIT     = "cache_hit"
TIER_RAG_HIT       = "rag_hit"
TIER_MONGO_HIT     = "mongo_hit"
TIER_CHEAP         = "cheap"
TIER_TIGHT         = "tight"
TIER_RETRIEVAL_ONLY = "retrieval_only"
TIER_PAYWALL       = "paywall"
TIER_PAID_ESCALATE = "paid_escalation"

ALL_TIERS = (
    TIER_CACHE_HIT, TIER_RAG_HIT, TIER_MONGO_HIT,
    TIER_CHEAP, TIER_TIGHT, TIER_RETRIEVAL_ONLY,
    TIER_PAYWALL, TIER_PAID_ESCALATE,
)

_INPROC_LOCK = threading.Lock()
_INPROC: dict[str, int] = {}  # bucket_key -> count


def _bucket_key(tier: str, lang: str, ts: Optional[float] = None) -> str:
    bucket = int((ts if ts is not None else time.time()) // _BUCKET_SEC)
    return f"{_REDIS_KEY_PREFIX}:{(lang or 'en').lower()}:{tier}:{bucket}"


def _redis():
    try:
        from deps import redis_client  # type: ignore
        return redis_client
    except Exception:
        return None


def record(tier: str, *, lang: str = "en", n: int = 1) -> None:
    """Bump the rolling-window counter for `tier` / `lang`.

    Tier MUST be one of `ALL_TIERS`. Unknown tiers are dropped (no
    raise) to keep the dispatch hot path safe from typos.
    """
    if tier not in ALL_TIERS:
        return
    key = _bucket_key(tier, lang)
    rc = _redis()
    if rc is not None:
        try:
            pipe = rc.pipeline()
            pipe.incrby(key, int(n))
            pipe.expire(key, _BUCKET_SEC * (_HISTORY_BUCKETS + 1))
            pipe.execute()
            return
        except Exception:
            pass
    with _INPROC_LOCK:
        _INPROC[key] = _INPROC.get(key, 0) + int(n)


def snapshot(*, lang: str = "en") -> dict:
    """Return the rolling-24h counter breakdown for one language.

    Shape:
        {
          "lang": "en",
          "window_hours": 24,
          "counts": {tier: int, ...},
          "totals": {"all": int, "free_llm": int, "free_no_llm": int,
                     "paid_escalation_pct": float},
        }

    `paid_escalation_pct` is the target the >5% alarm watches.
    """
    rc = _redis()
    now = time.time()
    counts: dict[str, int] = {t: 0 for t in ALL_TIERS}
    for tier in ALL_TIERS:
        for i in range(_HISTORY_BUCKETS):
            key = _bucket_key(tier, lang, ts=now - (i * _BUCKET_SEC))
            v = 0
            if rc is not None:
                try:
                    raw = rc.get(key)
                    if raw is not None:
                        v = int(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
                except Exception:
                    v = 0
            if v == 0:
                v = _INPROC.get(key, 0)
            counts[tier] += v

    total = sum(counts.values())
    free_no_llm = (
        counts[TIER_CACHE_HIT] + counts[TIER_RAG_HIT] + counts[TIER_MONGO_HIT]
        + counts[TIER_PAYWALL] + counts[TIER_RETRIEVAL_ONLY]
    )
    free_llm = counts[TIER_CHEAP] + counts[TIER_TIGHT]
    pct = (counts[TIER_PAID_ESCALATE] / total) if total > 0 else 0.0
    return {
        "lang": lang,
        "window_hours": _HISTORY_BUCKETS,
        "counts": counts,
        "totals": {
            "all": total,
            "free_llm": free_llm,
            "free_no_llm": free_no_llm,
            "paid_escalation_pct": round(pct, 4),
        },
    }


__all__ = [
    "record", "snapshot",
    "TIER_CACHE_HIT", "TIER_RAG_HIT", "TIER_MONGO_HIT",
    "TIER_CHEAP", "TIER_TIGHT", "TIER_RETRIEVAL_ONLY",
    "TIER_PAYWALL", "TIER_PAID_ESCALATE",
    "ALL_TIERS",
]
