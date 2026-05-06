"""USD -> INR FX helper for Syrabit.ai money-truth pipeline.

Single source of conversion truth used by every monetary write path
(Stripe webhook, AdSense sync, payment migration). The rate is fetched
once per UTC calendar day from Frankfurter (ECB rates, free, no key)
with open.er-api.com as fallback, cached in the ``fx_rates`` Mongo
collection so repeat calls within the same day cost zero network.

Design rules (Task #731):
  * NEVER default to 1.0 or 83 — fail loud with FxRateUnavailable so
    silent zero / wildly wrong totals can't sneak into revenue rollups.
  * Every cached row carries the source it came from + the exact UTC
    timestamp it was fetched, so admin UI captions like
    "USD->INR @ rate as of 2026-04-23 06:14 UTC (frankfurter)" are
    self-describing and auditable.
  * Cache document is upsert-keyed by ``USD_INR_YYYY-MM-DD`` so the
    same day is never re-fetched (and so a manual override row can
    be inserted by an admin without code changes).

Public API:
    rate_info = await get_usd_inr_rate()
    # -> {"rate": 93.86, "source": "frankfurter",
    #     "fetched_at": "2026-04-23T06:14:02+00:00",
    #     "as_of_date": "2026-04-23"}

    converted = await usd_to_inr(0.09)
    # -> {"inr": 8.45, "rate": 93.86, "source": "frankfurter",
    #     "fetched_at": "2026-04-23T06:14:02+00:00",
    #     "as_of_date": "2026-04-23"}
"""

from __future__ import annotations

import logging
import asyncio
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_COLLECTION = "fx_rates"
_KEY_PREFIX = "USD_INR_"

# Per-process in-memory cache so the very first call inside a single
# request still skips the Mongo round-trip on subsequent calls. Key is
# the YYYY-MM-DD UTC date string, value is the same dict shape returned
# from get_usd_inr_rate.
_memo: dict[str, dict] = {}
_memo_lock = asyncio.Lock()

# 6 second cap per source — backend serves user requests, can't hang.
_HTTP_TIMEOUT = 6.0


class FxRateUnavailable(RuntimeError):
    """Raised when every FX source is unreachable AND no cached rate
    exists for today. Callers MUST surface this — silently defaulting
    is exactly the bug Task #731 exists to fix."""


def _today_key() -> str:
    """UTC calendar day key, matches what the providers consider 'today'.
    Using UTC (not server local) keeps the cache key stable across
    Azure Container Apps region migrations (e.g. eastus2 → westus3 DR)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def _fetch_frankfurter(client: httpx.AsyncClient) -> Optional[dict]:
    """Frankfurter wraps ECB reference rates. No key, no rate limits."""
    try:
        r = await client.get(
            "https://api.frankfurter.dev/v1/latest",
            params={"from": "USD", "to": "INR"},
            timeout=_HTTP_TIMEOUT,
        )
        r.raise_for_status()
        body = r.json()
        rate = body.get("rates", {}).get("INR")
        as_of = body.get("date")
        if isinstance(rate, (int, float)) and rate > 0:
            return {"rate": float(rate), "source": "frankfurter", "as_of_date": as_of or _today_key()}
    except Exception as e:
        logger.warning("fx.frankfurter fetch failed: %s", e)
    return None


async def _fetch_open_er_api(client: httpx.AsyncClient) -> Optional[dict]:
    """Open exchangerate API — community-funded, free, no key."""
    try:
        r = await client.get(
            "https://open.er-api.com/v6/latest/USD",
            timeout=_HTTP_TIMEOUT,
        )
        r.raise_for_status()
        body = r.json()
        if body.get("result") != "success":
            return None
        rate = body.get("rates", {}).get("INR")
        if isinstance(rate, (int, float)) and rate > 0:
            return {"rate": float(rate), "source": "open_er_api", "as_of_date": _today_key()}
    except Exception as e:
        logger.warning("fx.open_er_api fetch failed: %s", e)
    return None


async def _load_cached(db, key: str) -> Optional[dict]:
    try:
        doc = await db[_COLLECTION].find_one({"_id": key})
        if doc and isinstance(doc.get("rate"), (int, float)) and doc["rate"] > 0:
            return {
                "rate": float(doc["rate"]),
                "source": doc.get("source", "cache"),
                "fetched_at": doc.get("fetched_at"),
                "as_of_date": doc.get("as_of_date") or key.replace(_KEY_PREFIX, ""),
            }
    except Exception as e:
        logger.warning("fx.cache read failed: %s", e)
    return None


async def _store_cache(db, key: str, rate_info: dict) -> None:
    try:
        await db[_COLLECTION].update_one(
            {"_id": key},
            {"$set": {
                "rate": rate_info["rate"],
                "source": rate_info["source"],
                "fetched_at": rate_info["fetched_at"],
                "as_of_date": rate_info.get("as_of_date"),
            }},
            upsert=True,
        )
    except Exception as e:
        # Cache failure must NOT break the request — we still have the
        # live rate in hand. Log and move on.
        logger.warning("fx.cache write failed: %s", e)


async def get_usd_inr_rate() -> dict:
    """Return today's USD->INR rate dict. Raises FxRateUnavailable if
    no source AND no cache produced a usable rate."""
    today = _today_key()

    async with _memo_lock:
        cached_mem = _memo.get(today)
    if cached_mem:
        return cached_mem

    # Local import so this module stays importable in unit-style
    # smoke tests that don't bring up the full deps graph.
    try:
        from deps import db
    except Exception:
        db = None

    key = f"{_KEY_PREFIX}{today}"

    if db is not None:
        cached_db = await _load_cached(db, key)
        if cached_db:
            async with _memo_lock:
                _memo[today] = cached_db
            return cached_db

    async with httpx.AsyncClient() as client:
        for fetcher in (_fetch_frankfurter, _fetch_open_er_api):
            res = await fetcher(client)
            if res:
                res["fetched_at"] = datetime.now(timezone.utc).isoformat()
                if db is not None:
                    await _store_cache(db, key, res)
                async with _memo_lock:
                    _memo[today] = res
                logger.info(
                    "fx: fetched USD->INR=%.4f from %s (as_of %s)",
                    res["rate"], res["source"], res.get("as_of_date"),
                )
                return res

    # Last-resort: try yesterday's cached rate. Stale by 1 day is much
    # better than failing a payment write — a < 1% drift is acceptable;
    # silent ₹0 is not.
    if db is not None:
        try:
            cursor = db[_COLLECTION].find().sort("_id", -1).limit(1)
            async for doc in cursor:
                if isinstance(doc.get("rate"), (int, float)) and doc["rate"] > 0:
                    stale = {
                        "rate": float(doc["rate"]),
                        "source": f"{doc.get('source', 'cache')}_stale",
                        "fetched_at": doc.get("fetched_at"),
                        "as_of_date": doc.get("as_of_date") or doc.get("_id", "").replace(_KEY_PREFIX, ""),
                    }
                    logger.warning(
                        "fx: live sources unreachable; using stale cached rate %.4f from %s",
                        stale["rate"], stale.get("as_of_date"),
                    )
                    async with _memo_lock:
                        _memo[today] = stale
                    return stale
        except Exception as e:
            logger.warning("fx.cache stale lookup failed: %s", e)

    raise FxRateUnavailable(
        "USD->INR FX rate unavailable: every live source failed and no cached rate exists. "
        "Refusing to default — fix the FX provider before proceeding."
    )


async def usd_to_inr(usd: float) -> dict:
    """Convert a USD amount to INR using today's cached rate.

    Returns a dict with both the converted amount AND the rate metadata
    so callers can persist the rate they actually used (audit trail)."""
    if not isinstance(usd, (int, float)):
        raise ValueError(f"usd_to_inr: amount must be numeric, got {type(usd).__name__}")
    rate_info = await get_usd_inr_rate()
    inr = round(float(usd) * rate_info["rate"], 2)
    return {
        "inr": inr,
        "rate": rate_info["rate"],
        "source": rate_info["source"],
        "fetched_at": rate_info["fetched_at"],
        "as_of_date": rate_info.get("as_of_date"),
    }


def reset_memo_for_tests() -> None:
    """Clear the per-process memo. Test-only entry point."""
    _memo.clear()
