"""Google Web Risk API client.

Wraps https://webrisk.googleapis.com/v1/uris:search to check whether a URL
is on Google's Safe Browsing lists (MALWARE, SOCIAL_ENGINEERING,
UNWANTED_SOFTWARE). Used to vet outbound links in generated SEO content
and user-submitted topic discoveries before publication.

Auth: GOOGLE_WEB_RISK_API_KEY (falls back to GOOGLE_KG_API_KEY).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

WEB_RISK_API_URL = "https://webrisk.googleapis.com/v1/uris:search"
_HTTP_TIMEOUT_S = 5.0
_DEFAULT_THREAT_TYPES = ("MALWARE", "SOCIAL_ENGINEERING", "UNWANTED_SOFTWARE")


def _api_key() -> str:
    return (
        (os.environ.get("GOOGLE_WEB_RISK_API_KEY") or "").strip()
        or (os.environ.get("GOOGLE_KG_API_KEY") or "").strip()
    )


def is_configured() -> bool:
    return bool(_api_key())


async def check_uri(
    uri: str,
    *,
    threat_types: Optional[List[str]] = None,
    timeout_s: float = _HTTP_TIMEOUT_S,
) -> Dict[str, Any]:
    """Check a single URL against Web Risk lists.

    Returns: {
        "status": "ok"|"disabled"|"error",
        "uri": str,
        "safe": bool,                # True iff no threats matched
        "matched_threats": [str, ...],
        "expire_time": ISO|None,
        "elapsed_ms": float,
        "error": Optional[str],
    }
    """
    u = (uri or "").strip()
    if not u:
        return {"status": "error", "uri": u, "safe": False,
                "matched_threats": [], "elapsed_ms": 0.0, "error": "empty_uri"}

    key = _api_key()
    if not key:
        return {"status": "disabled", "uri": u, "safe": False,
                "matched_threats": [], "elapsed_ms": 0.0,
                "error": "GOOGLE_WEB_RISK_API_KEY (or GOOGLE_KG_API_KEY) not configured"}

    if threat_types is None:
        types = list(_DEFAULT_THREAT_TYPES)
    elif isinstance(threat_types, str):
        types = [s.strip() for s in threat_types.split(",") if s.strip()]
    else:
        types = [str(s).strip() for s in threat_types if str(s).strip()]
    valid = {"MALWARE", "SOCIAL_ENGINEERING", "UNWANTED_SOFTWARE"}
    types = [t.upper() for t in types if t.upper() in valid]
    if not types:
        types = list(_DEFAULT_THREAT_TYPES)
    params: List[tuple] = [("uri", u), ("key", key)]
    for t in types:
        params.append(("threatTypes", t))

    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.get(WEB_RISK_API_URL, params=params)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        if r.status_code != 200:
            return {"status": "error", "uri": u, "safe": False,
                    "matched_threats": [], "elapsed_ms": elapsed_ms,
                    "error": f"HTTP {r.status_code}: {r.text[:200]}"}
        data = r.json() or {}
    except httpx.TimeoutException:
        return {"status": "error", "uri": u, "safe": False,
                "matched_threats": [],
                "elapsed_ms": (time.perf_counter() - t0) * 1000.0,
                "error": "timeout"}
    except Exception as exc:
        return {"status": "error", "uri": u, "safe": False,
                "matched_threats": [],
                "elapsed_ms": (time.perf_counter() - t0) * 1000.0,
                "error": f"{type(exc).__name__}: {str(exc)[:200]}"}

    threat = data.get("threat") or {}
    matched = list(threat.get("threatTypes") or [])
    return {
        "status": "ok",
        "uri": u,
        "safe": not matched,
        "matched_threats": matched,
        "expire_time": threat.get("expireTime"),
        "elapsed_ms": elapsed_ms,
        "error": None,
    }


async def batch_check(
    uris: List[str],
    *,
    threat_types: Optional[List[str]] = None,
    concurrency: int = 8,
    timeout_s: float = _HTTP_TIMEOUT_S,
) -> List[Dict[str, Any]]:
    """Check many URIs concurrently (sem-bounded)."""
    sem = asyncio.Semaphore(max(1, int(concurrency)))

    async def _one(u: str) -> Dict[str, Any]:
        async with sem:
            return await check_uri(u, threat_types=threat_types, timeout_s=timeout_s)

    return await asyncio.gather(*[_one(u) for u in uris])
