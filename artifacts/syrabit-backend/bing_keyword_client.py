"""Bing Webmaster Keyword Research API client (Plan 11, Task #333).

Calls Bing's free Keyword Research endpoints (`GetKeyword`,
`GetRelatedKeywords`, `GetKeywordStats`) using the same
`BING_WEBMASTER_API_KEY` we already use for `bing_submit_client`. Results
are cached in Mongo (`bing_keyword_cache`) for 30 days per
`(seed, country, language)` so a monthly background refresh stays well
inside Bing's free quota.

Used by:
  * `routes/bot_discovery._bing_keyword_refresh_loop` — monthly chapter
    title refresh task (background, leader-elected via Mongo CAS lock).
  * `routes/content.get_chapter_by_slug` — projects `bing_keywords` so
    `ChapterPage.jsx` can populate `<meta keywords>` with what students
    actually search for instead of a static template.
  * `routes/cms_sarvam_health.BotRenderMiddleware` — bot HTML fallback
    emits the same Bing-derived keyword list when present.

Docs: https://learn.microsoft.com/en-us/bingwebmaster/keyword-research-api
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import os
import httpx

logger = logging.getLogger(__name__)

BING_KEYWORD_BASE_URL = "https://ssl.bing.com/webmaster/api.svc/json"
BING_KEYWORD_TIMEOUT_S = 30.0
BING_KEYWORD_DEFAULT_COUNTRY = "IN"
BING_KEYWORD_DEFAULT_LANGUAGE = "en-IN"
BING_KEYWORD_CACHE_COLLECTION = "bing_keyword_cache"
BING_KEYWORD_CACHE_TTL_DAYS = 30
BING_KEYWORD_TOP_N = 20


def _cache_key(seed: str, country: str, language: str) -> str:
    return f"{(seed or '').strip().lower()}|{country.lower()}|{language.lower()}"


async def _bing_get(
    api_key: str,
    path: str,
    params: Dict[str, Any],
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[dict]:
    """GET a Bing Keyword Research endpoint and return parsed JSON or None."""
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=BING_KEYWORD_TIMEOUT_S)
    try:
        url = f"{BING_KEYWORD_BASE_URL}/{path}"
        resp = await client.get(url, params={**params, "apikey": api_key})
        if resp.status_code != 200:
            logger.debug(
                "Bing keyword %s returned %d: %s",
                path, resp.status_code, (resp.text or "")[:200],
            )
            return None
        try:
            return resp.json()
        except Exception:
            return None
    except Exception as exc:
        logger.debug("Bing keyword %s transport error: %s", path, exc)
        return None
    finally:
        if owns_client:
            await client.aclose()


async def get_keyword(
    api_key: str,
    q: str,
    *,
    country: str = BING_KEYWORD_DEFAULT_COUNTRY,
    language: str = BING_KEYWORD_DEFAULT_LANGUAGE,
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[dict]:
    """Volume stats for the seed itself: `{Keyword, Broad, Phrase, Exact}`."""
    body = await _bing_get(
        api_key, "GetKeyword",
        {"q": q, "country": country, "language": language},
        client=client,
    )
    if not body:
        return None
    return body.get("d") or None


async def get_related_keywords(
    api_key: str,
    q: str,
    *,
    country: str = BING_KEYWORD_DEFAULT_COUNTRY,
    language: str = BING_KEYWORD_DEFAULT_LANGUAGE,
    client: Optional[httpx.AsyncClient] = None,
) -> List[dict]:
    """List of related queries Bing serves for the same intent."""
    body = await _bing_get(
        api_key, "GetRelatedKeywords",
        {"q": q, "country": country, "language": language},
        client=client,
    )
    if not body:
        return []
    d = body.get("d") or []
    return d if isinstance(d, list) else []


async def get_keyword_stats(
    api_key: str,
    q: str,
    *,
    country: str = BING_KEYWORD_DEFAULT_COUNTRY,
    language: str = BING_KEYWORD_DEFAULT_LANGUAGE,
    client: Optional[httpx.AsyncClient] = None,
) -> List[dict]:
    """Time-series search-volume rows for the seed."""
    body = await _bing_get(
        api_key, "GetKeywordStats",
        {"q": q, "country": country, "language": language},
        client=client,
    )
    if not body:
        return []
    d = body.get("d") or []
    return d if isinstance(d, list) else []


def _normalize_related(related: List[dict], top_n: int) -> List[dict]:
    """Flatten Bing's related-keyword payload into a sorted, deduped list of
    `{"keyword": str, "impressions": int}` capped at `top_n`."""
    out: List[dict] = []
    for r in related or []:
        if not isinstance(r, dict):
            continue
        kw = (r.get("Query") or r.get("Keyword") or "").strip()
        if not kw:
            continue
        try:
            impressions = int(r.get("Impressions") or r.get("Broad") or 0)
        except (TypeError, ValueError):
            impressions = 0
        out.append({"keyword": kw, "impressions": impressions})
    out.sort(key=lambda x: x["impressions"], reverse=True)
    seen = set()
    dedup: List[dict] = []
    for r in out:
        k = r["keyword"].lower()
        if k in seen:
            continue
        seen.add(k)
        dedup.append(r)
        if len(dedup) >= top_n:
            break
    return dedup


def _is_cache_fresh(cached_at, ttl_days: int, now: datetime) -> bool:
    if not cached_at:
        return False
    if isinstance(cached_at, str):
        try:
            cached_at = datetime.fromisoformat(cached_at.replace("Z", "+00:00"))
        except Exception:
            return False
    if not isinstance(cached_at, datetime):
        return False
    if cached_at.tzinfo is None:
        cached_at = cached_at.replace(tzinfo=timezone.utc)
    return (now - cached_at) < timedelta(days=ttl_days)


def _format_cached_at(cached_at) -> Optional[str]:
    if cached_at is None:
        return None
    if isinstance(cached_at, datetime):
        return cached_at.isoformat()
    return str(cached_at)


async def fetch_top_keywords(
    api_key: str,
    seed: str,
    *,
    db: Any = None,
    country: str = BING_KEYWORD_DEFAULT_COUNTRY,
    language: str = BING_KEYWORD_DEFAULT_LANGUAGE,
    top_n: int = BING_KEYWORD_TOP_N,
    ttl_days: int = BING_KEYWORD_CACHE_TTL_DAYS,
    client: Optional[httpx.AsyncClient] = None,
    force: bool = False,
    now: Optional[datetime] = None,
) -> dict:
    """Cached fetch of Bing's top related keywords for `seed`.

    Returns a dict shaped:
        {seed, country, language, keywords, primary, cached, fetched_at, source}
    where `source` is one of `cache`, `api`, `cache_stale`, `api_empty`,
    `cache_stale_fallback`, or `empty`.

    `client` allows the caller (the monthly refresh loop) to share a
    single `httpx.AsyncClient` across many seeds rather than opening one
    per call — a small but meaningful win when refreshing dozens of
    chapters in a single window.
    """
    seed = (seed or "").strip()
    now = now or datetime.now(timezone.utc)
    key = _cache_key(seed, country, language)

    cached_doc: Optional[dict] = None
    if db is not None and not force:
        try:
            cached_doc = await db[BING_KEYWORD_CACHE_COLLECTION].find_one({"_id": key})
        except Exception:
            cached_doc = None
        if cached_doc and _is_cache_fresh(cached_doc.get("cached_at"), ttl_days, now):
            return {
                "seed": seed,
                "country": country,
                "language": language,
                "keywords": cached_doc.get("keywords") or [],
                "primary": cached_doc.get("primary"),
                "cached": True,
                "fetched_at": _format_cached_at(cached_doc.get("cached_at")),
                "source": "cache",
            }

    if not api_key or not seed:
        if cached_doc:
            return {
                "seed": seed,
                "country": country,
                "language": language,
                "keywords": cached_doc.get("keywords") or [],
                "primary": cached_doc.get("primary"),
                "cached": True,
                "fetched_at": _format_cached_at(cached_doc.get("cached_at")),
                "source": "cache_stale",
            }
        return {
            "seed": seed,
            "country": country,
            "language": language,
            "keywords": [],
            "primary": None,
            "cached": False,
            "fetched_at": None,
            "source": "empty",
        }

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=BING_KEYWORD_TIMEOUT_S)
    try:
        related = await get_related_keywords(
            api_key, seed, country=country, language=language, client=client,
        )
        primary = await get_keyword(
            api_key, seed, country=country, language=language, client=client,
        )
    finally:
        if owns_client:
            await client.aclose()

    keywords = _normalize_related(related, top_n)

    if not keywords and not primary:
        if cached_doc:
            return {
                "seed": seed,
                "country": country,
                "language": language,
                "keywords": cached_doc.get("keywords") or [],
                "primary": cached_doc.get("primary"),
                "cached": True,
                "fetched_at": _format_cached_at(cached_doc.get("cached_at")),
                "source": "cache_stale_fallback",
            }
        return {
            "seed": seed,
            "country": country,
            "language": language,
            "keywords": [],
            "primary": None,
            "cached": False,
            "fetched_at": now.isoformat(),
            "source": "api_empty",
        }

    doc = {
        "_id": key,
        "seed": seed,
        "country": country,
        "language": language,
        "keywords": keywords,
        "primary": primary,
        "cached_at": now,
    }
    if db is not None:
        try:
            await db[BING_KEYWORD_CACHE_COLLECTION].update_one(
                {"_id": key}, {"$set": doc}, upsert=True,
            )
        except Exception as exc:
            logger.debug("bing keyword cache write failed: %s", exc)

    return {
        "seed": seed,
        "country": country,
        "language": language,
        "keywords": keywords,
        "primary": primary,
        "cached": False,
        "fetched_at": now.isoformat(),
        "source": "api",
    }


# ─── Task #332 — SQS consumer entrypoint ─────────────────────────────────────
#
# `services/backend/sqs_consumers/bing_keyword.py` invokes this when an
# `bing-keyword-refresh` SQS message lands. It is a thin batching wrapper
# around `fetch_top_keywords` so the consumer body stays declarative.
async def refresh_keywords(*, site: str | None = None,
                           keywords: list[str] | None = None,
                           db: Any = None) -> dict:
    """Refresh the cached related-keywords doc for each seed.

    Args:
      site: optional site identifier (logged only — Bing's Keyword
        Research API operates per-seed, not per-site).
      keywords: list of seed keywords whose cache rows to refresh.
      db: optional Mongo handle. If None, the module-level default
        (resolved at runtime via `deps.db`) is used.

    Returns: dict with per-seed status + counts.
    """
    seeds = [s for s in (keywords or []) if isinstance(s, str) and s.strip()]
    if not seeds:
        return {"site": site, "refreshed": 0, "skipped": "no-seeds"}
    if db is None:
        try:
            from deps import db as _db_handle  # type: ignore
            db = _db_handle
        except Exception:
            db = None
    api_key = os.environ.get("BING_KEYWORD_API_KEY", "").strip()
    if not api_key:
        return {"site": site, "refreshed": 0, "skipped": "missing-api-key"}
    # Task #332 — collect errors and RAISE at the end if any seed
    # failed, so the SQS Lambda consumer surfaces the failure to
    # AWS for retry + DLQ. Returning a partial-result dict would
    # ack the message and silently drop the failed seeds.
    out: dict = {"site": site, "refreshed": 0, "results": []}
    failures: list[tuple[str, str]] = []
    async with httpx.AsyncClient(timeout=15.0) as client:
        for seed in seeds:
            try:
                row = await fetch_top_keywords(
                    api_key, seed, db=db, client=client, force=True,
                )
                out["refreshed"] += 1
                out["results"].append({"seed": seed, "source": row.get("source")})
            except Exception as exc:
                err = repr(exc)[:200]
                out["results"].append({"seed": seed, "error": err})
                failures.append((seed, err))
    if failures:
        raise RuntimeError(
            f"bing-keyword-refresh: {len(failures)}/{len(seeds)} seeds failed: "
            + "; ".join(f"{s}={e}" for s, e in failures[:5])
        )
    return out
