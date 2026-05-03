"""Google Knowledge Graph Search API client.

Reusable wrapper around https://kgsearch.googleapis.com/v1/entities:search.
Looks up canonical Google entity records (kg_id, name, description, types,
detailedDescription, image, url) for a free-text query.

Auth: requires the GOOGLE_KG_API_KEY env var (a standard Google API key with
Knowledge Graph Search API enabled on the project).

Returns a structured dict with status in {ok, missing, disabled, error} so
callers (chat post-processing, admin endpoints, entity-SEO collectors) can
react without exception handling.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

KG_API_URL = "https://kgsearch.googleapis.com/v1/entities:search"
_HTTP_TIMEOUT_S = 5.0


def is_configured() -> bool:
    return bool((os.environ.get("GOOGLE_KG_API_KEY") or "").strip())


async def search_entities(
    query: str,
    *,
    limit: int = 5,
    languages: Optional[List[str]] = None,
    types: Optional[List[str]] = None,
    indent: bool = False,
    timeout_s: float = _HTTP_TIMEOUT_S,
) -> Dict[str, Any]:
    """Search the Google Knowledge Graph for entities matching `query`.

    Args:
        query: Free-text search term (e.g. "Rabindranath Tagore").
        limit: Max number of entities to return (1-20).
        languages: Optional BCP-47 language codes to bias results
            (e.g. ["en", "hi", "as"]).
        types: Optional schema.org type filters (e.g. ["Person", "Place"]).
        indent: Pretty-print JSON in response (debug only).
        timeout_s: Per-call HTTP timeout.

    Returns: {
        "status": "ok"|"missing"|"disabled"|"error",
        "query": str,
        "results": [{"kg_id", "name", "description", "types",
                     "detailed_description", "url", "image", "score"}, ...],
        "count": int,
        "elapsed_ms": float,
        "error": Optional[str],
    }
    """
    q = (query or "").strip()
    if not q:
        return {
            "status": "error", "query": q, "results": [], "count": 0,
            "elapsed_ms": 0.0, "error": "empty_query",
        }

    api_key = (os.environ.get("GOOGLE_KG_API_KEY") or "").strip()
    if not api_key:
        return {
            "status": "disabled", "query": q, "results": [], "count": 0,
            "elapsed_ms": 0.0,
            "error": "GOOGLE_KG_API_KEY not configured",
        }

    params: List[tuple] = [
        ("query", q),
        ("limit", str(max(1, min(20, int(limit))))),
        ("key", api_key),
    ]
    if indent:
        params.append(("indent", "true"))
    for lang in (languages or []):
        params.append(("languages", lang))
    for t in (types or []):
        params.append(("types", t))

    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.get(KG_API_URL, params=params)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        if r.status_code != 200:
            return {
                "status": "error", "query": q, "results": [], "count": 0,
                "elapsed_ms": elapsed_ms,
                "error": f"HTTP {r.status_code}: {r.text[:200]}",
            }
        items = (r.json() or {}).get("itemListElement") or []
    except httpx.TimeoutException:
        return {
            "status": "error", "query": q, "results": [], "count": 0,
            "elapsed_ms": (time.perf_counter() - t0) * 1000.0,
            "error": "timeout",
        }
    except Exception as exc:
        return {
            "status": "error", "query": q, "results": [], "count": 0,
            "elapsed_ms": (time.perf_counter() - t0) * 1000.0,
            "error": f"{type(exc).__name__}: {str(exc)[:200]}",
        }

    results: List[Dict[str, Any]] = []
    for item in items:
        result = item.get("result") or {}
        detailed = result.get("detailedDescription") or {}
        image = result.get("image") or {}
        result_types = result.get("@type")
        if isinstance(result_types, str):
            result_types = [result_types]
        results.append({
            "kg_id": result.get("@id"),
            "name": result.get("name"),
            "description": result.get("description"),
            "types": result_types or [],
            "detailed_description": detailed.get("articleBody"),
            "detailed_url": detailed.get("url"),
            "license": detailed.get("license"),
            "url": result.get("url"),
            "image": image.get("contentUrl") or image.get("url"),
            "score": item.get("resultScore"),
        })

    if not results:
        return {
            "status": "missing", "query": q, "results": [], "count": 0,
            "elapsed_ms": elapsed_ms, "error": None,
        }
    return {
        "status": "ok", "query": q, "results": results, "count": len(results),
        "elapsed_ms": elapsed_ms, "error": None,
    }


async def lookup_first(query: str, **kwargs) -> Optional[Dict[str, Any]]:
    """Convenience: return the highest-scoring entity for `query`, or None."""
    out = await search_entities(query, limit=1, **kwargs)
    if out.get("status") != "ok":
        return None
    results = out.get("results") or []
    return results[0] if results else None
