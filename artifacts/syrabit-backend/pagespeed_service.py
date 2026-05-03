"""Google PageSpeed Insights v5 API client + Core Web Vitals collector.

Wraps https://pagespeedonline.googleapis.com/pagespeedonline/v5/runPagespeed
to fetch Core Web Vitals (LCP, INP/FID, CLS, TTFB, FCP) plus the Lighthouse
performance score for any public URL. Used by the SEO admin panel to surface
which Syrabit pages are tanking organic ranking due to performance.

Auth: optional. The endpoint accepts an API key (via GOOGLE_PAGESPEED_API_KEY
or fallback GOOGLE_KG_API_KEY) which raises the per-day quota from 25k to
the project quota. Without a key, the API still works at a lower rate limit.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

PAGESPEED_API_URL = (
    "https://pagespeedonline.googleapis.com/pagespeedonline/v5/runPagespeed"
)
_HTTP_TIMEOUT_S = 60.0  # PSI runs a real Lighthouse audit; 60s is realistic.

VALID_STRATEGIES = ("mobile", "desktop")
VALID_CATEGORIES = ("performance", "accessibility", "best-practices", "seo", "pwa")


def _api_key() -> str:
    return (
        (os.environ.get("GOOGLE_PAGESPEED_API_KEY") or "").strip()
        or (os.environ.get("GOOGLE_KG_API_KEY") or "").strip()
    )


def is_configured() -> bool:
    return True


async def run_pagespeed(
    url: str,
    *,
    strategy: str = "mobile",
    categories: Optional[List[str]] = None,
    locale: str = "en",
    timeout_s: float = _HTTP_TIMEOUT_S,
) -> Dict[str, Any]:
    """Run a single PageSpeed Insights audit for `url`.

    Returns: {
        "status": "ok"|"error",
        "url": str,
        "strategy": str,
        "performance_score": float in [0,1] or None,
        "scores": {category: score},
        "core_web_vitals": {
            "lcp_ms": float|None,         # Largest Contentful Paint
            "inp_ms": float|None,         # Interaction to Next Paint
            "fid_ms": float|None,         # First Input Delay (legacy)
            "cls": float|None,            # Cumulative Layout Shift
            "ttfb_ms": float|None,        # Time to First Byte
            "fcp_ms": float|None,         # First Contentful Paint
        },
        "loading_experience": dict|None,  # field data (CrUX) when available
        "elapsed_ms": float,
        "fetched_at": ISO timestamp,
        "error": Optional[str],
    }
    """
    if not url:
        return {
            "status": "error", "url": url, "strategy": strategy,
            "elapsed_ms": 0.0, "error": "empty_url",
        }

    s = strategy.lower().strip()
    if s not in VALID_STRATEGIES:
        s = "mobile"

    cats = [c for c in (categories or ["performance"]) if c in VALID_CATEGORIES]
    if not cats:
        cats = ["performance"]

    params: List[tuple] = [
        ("url", url),
        ("strategy", s),
        ("locale", locale),
    ]
    for c in cats:
        params.append(("category", c))
    key = _api_key()
    if key:
        params.append(("key", key))

    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.get(PAGESPEED_API_URL, params=params)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        if r.status_code != 200:
            body = r.text[:300] if r.text else ""
            return {
                "status": "error", "url": url, "strategy": s,
                "elapsed_ms": elapsed_ms,
                "error": f"HTTP {r.status_code}: {body}",
            }
        data = r.json() or {}
    except httpx.TimeoutException:
        return {
            "status": "error", "url": url, "strategy": s,
            "elapsed_ms": (time.perf_counter() - t0) * 1000.0,
            "error": "timeout",
        }
    except Exception as exc:
        return {
            "status": "error", "url": url, "strategy": s,
            "elapsed_ms": (time.perf_counter() - t0) * 1000.0,
            "error": f"{type(exc).__name__}: {str(exc)[:200]}",
        }

    lighthouse = (data.get("lighthouseResult") or {})
    audits = lighthouse.get("audits") or {}
    cat_results = lighthouse.get("categories") or {}
    scores = {
        name.replace("-", "_"): (cat_results.get(name) or {}).get("score")
        for name in cat_results.keys()
    }
    perf_score = (cat_results.get("performance") or {}).get("score")

    def _audit_ms(key: str) -> Optional[float]:
        a = audits.get(key) or {}
        v = a.get("numericValue")
        return float(v) if isinstance(v, (int, float)) else None

    def _audit_num(key: str) -> Optional[float]:
        return _audit_ms(key)

    cwv = {
        "lcp_ms": _audit_ms("largest-contentful-paint"),
        "inp_ms": _audit_ms("interaction-to-next-paint"),
        "fid_ms": _audit_ms("max-potential-fid"),
        "cls": _audit_num("cumulative-layout-shift"),
        "ttfb_ms": _audit_ms("server-response-time"),
        "fcp_ms": _audit_ms("first-contentful-paint"),
    }

    from datetime import datetime, timezone
    return {
        "status": "ok",
        "url": url,
        "strategy": s,
        "performance_score": perf_score,
        "scores": scores,
        "core_web_vitals": cwv,
        "loading_experience": data.get("loadingExperience"),
        "origin_loading_experience": data.get("originLoadingExperience"),
        "fetch_time": lighthouse.get("fetchTime"),
        "elapsed_ms": elapsed_ms,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "error": None,
    }


async def batch_pagespeed(
    urls: List[str],
    *,
    strategy: str = "mobile",
    categories: Optional[List[str]] = None,
    concurrency: int = 3,
    timeout_s: float = _HTTP_TIMEOUT_S,
) -> List[Dict[str, Any]]:
    """Run PageSpeed audits for many URLs concurrently.

    `concurrency` is intentionally low (3) because PSI runs a real Lighthouse
    audit each time and aggressive parallelism gets throttled.
    """
    sem = asyncio.Semaphore(max(1, int(concurrency)))

    async def _one(u: str) -> Dict[str, Any]:
        async with sem:
            return await run_pagespeed(
                u, strategy=strategy, categories=categories, timeout_s=timeout_s
            )

    return await asyncio.gather(*[_one(u) for u in urls])


_CRUX_METRIC_KEYS = {
    "lcp": "LARGEST_CONTENTFUL_PAINT_MS",
    "inp": "INTERACTION_TO_NEXT_PAINT",
    "fid": "FIRST_INPUT_DELAY_MS",
    "cls": "CUMULATIVE_LAYOUT_SHIFT_SCORE",
    "ttfb": "EXPERIMENTAL_TIME_TO_FIRST_BYTE",
    "fcp": "FIRST_CONTENTFUL_PAINT_MS",
}

# Google's published CWV thresholds — see https://web.dev/articles/vitals (2024).
# CLS is reported by CrUX as integer * 100, so a 0.1 threshold becomes 10.
_FIELD_THRESHOLDS = {
    "lcp": (2500, 4000),
    "inp": (200, 500),
    "fid": (100, 300),
    "cls": (10, 25),  # CrUX scale (×100)
    "ttfb": (800, 1800),
    "fcp": (1800, 3000),
}

# Lab-only proxy thresholds (Lighthouse `max-potential-fid` and
# `server-response-time`). Documented but kept separate from true field
# CWV so dashboards don't conflate lab proxies with real-user metrics.
_LAB_THRESHOLDS = {
    "lcp": (2500, 4000),
    "cls": (0.1, 0.25),
    "fcp": (1800, 3000),
    "lab_fid_proxy": (130, 250),    # max-potential-fid (Lighthouse)
    "lab_ttfb_proxy": (800, 1800),  # server-response-time (Lighthouse)
}


def _bucket(value: Optional[float], good: float, poor: float) -> str:
    if value is None:
        return "n/a"
    if value <= good:
        return "good"
    if value <= poor:
        return "needs_improvement"
    return "poor"


def summarize_cwv(audit: Dict[str, Any]) -> Dict[str, Any]:
    """Classify Core Web Vitals against Google's published thresholds.

    Prefers CrUX field data (`loadingExperience.metrics`) when available
    because it reflects real users; falls back to Lighthouse lab metrics
    with `lab_*_proxy` labels for `fid` and `ttfb` so callers don't
    conflate lab proxies with field measurements.

    Returns {
        "source": "field"|"lab"|"none",
        "field": {metric: "good"|"needs_improvement"|"poor"|"n/a"},
        "lab":   {metric: bucket, ...incl lab_fid_proxy / lab_ttfb_proxy},
    }
    """
    field_buckets: Dict[str, str] = {}
    lab_buckets: Dict[str, str] = {}

    # Prefer CrUX field data when present.
    le = audit.get("loading_experience") or audit.get("origin_loading_experience") or {}
    metrics = (le or {}).get("metrics") or {}
    for short, crux_key in _CRUX_METRIC_KEYS.items():
        m = metrics.get(crux_key)
        if not isinstance(m, dict):
            continue
        percentile = m.get("percentile")
        if not isinstance(percentile, (int, float)):
            continue
        good, poor = _FIELD_THRESHOLDS[short]
        field_buckets[short] = _bucket(float(percentile), good, poor)

    # Always compute lab buckets too (when audit data is present).
    cwv = audit.get("core_web_vitals") or {}
    for key, (good, poor) in _LAB_THRESHOLDS.items():
        if key == "lab_fid_proxy":
            lab_buckets[key] = _bucket(cwv.get("fid_ms"), good, poor)
        elif key == "lab_ttfb_proxy":
            lab_buckets[key] = _bucket(cwv.get("ttfb_ms"), good, poor)
        else:
            lab_buckets[key] = _bucket(cwv.get(f"{key}_ms" if key != "cls" else "cls"),
                                        good, poor)

    if field_buckets:
        source = "field"
    elif any(v != "n/a" for v in lab_buckets.values()):
        source = "lab"
    else:
        source = "none"

    return {
        "source": source,
        "field": field_buckets,
        "lab": lab_buckets,
    }
