"""Google Discovery Engine (Vertex AI Search) client (SA-OAuth).

Wraps https://discoveryengine.googleapis.com/v1/{servingConfig}:search.
Used to query a managed semantic-search index (an alternative to the
Pinecone retriever for grounded-recall and topic discovery).

Auth: requires GOOGLE_APPLICATION_CREDENTIALS_JSON. The serving config
path is built from env vars:
  GCP_DISCOVERY_LOCATION   default: global
  GCP_DISCOVERY_COLLECTION default: default_collection
  GCP_DISCOVERY_DATA_STORE (required for search)
  GCP_DISCOVERY_SERVING_CONFIG default: default_search
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx

import gcp_auth

logger = logging.getLogger(__name__)

BASE = "https://discoveryengine.googleapis.com/v1"
_HTTP_TIMEOUT_S = 12.0


def is_configured() -> bool:
    return gcp_auth.is_configured()


def _serving_config(
    *, project: Optional[str] = None, location: Optional[str] = None,
    collection: Optional[str] = None, data_store: Optional[str] = None,
    serving_config: Optional[str] = None,
) -> Optional[str]:
    p = (project or gcp_auth.project_id() or "").strip()
    ds = (data_store or os.environ.get("GCP_DISCOVERY_DATA_STORE") or "").strip()
    if not p or not ds:
        return None
    loc = (location or os.environ.get("GCP_DISCOVERY_LOCATION") or "global").strip()
    coll = (collection or os.environ.get("GCP_DISCOVERY_COLLECTION") or
            "default_collection").strip()
    sc = (serving_config or os.environ.get("GCP_DISCOVERY_SERVING_CONFIG") or
          "default_search").strip()
    return (f"projects/{p}/locations/{loc}/collections/{coll}"
            f"/dataStores/{ds}/servingConfigs/{sc}")


async def search(
    query: str,
    *,
    page_size: int = 10,
    project: Optional[str] = None,
    location: Optional[str] = None,
    collection: Optional[str] = None,
    data_store: Optional[str] = None,
    serving_config: Optional[str] = None,
    timeout_s: float = _HTTP_TIMEOUT_S,
) -> Dict[str, Any]:
    """Run a Discovery Engine search query."""
    q = (query or "").strip()
    if not q:
        return {"status": "error", "query": q, "results": [], "error": "empty_query"}

    headers = gcp_auth.auth_header()
    if not headers:
        return gcp_auth.disabled_payload({"query": q, "results": []})

    sc = _serving_config(
        project=project, location=location, collection=collection,
        data_store=data_store, serving_config=serving_config,
    )
    if not sc:
        return {
            "status": "disabled", "query": q, "results": [],
            "error": ("GCP_DISCOVERY_DATA_STORE not configured "
                      "(set env var pointing at the data store ID)."),
        }

    url = f"{BASE}/{sc}:search"
    body: Dict[str, Any] = {
        "query": q,
        "pageSize": max(1, min(50, int(page_size))),
    }
    headers = {**headers, "Content-Type": "application/json"}

    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.post(url, headers=headers, json=body)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        if r.status_code >= 400:
            return {"status": "error", "query": q, "results": [],
                    "elapsed_ms": elapsed_ms,
                    "error": f"HTTP {r.status_code}: {r.text[:300]}"}
        data = r.json() or {}
    except httpx.TimeoutException:
        return {"status": "error", "query": q, "results": [],
                "elapsed_ms": (time.perf_counter() - t0) * 1000.0,
                "error": "timeout"}
    except Exception as exc:
        return {"status": "error", "query": q, "results": [],
                "elapsed_ms": (time.perf_counter() - t0) * 1000.0,
                "error": f"{type(exc).__name__}: {str(exc)[:200]}"}

    results: List[Dict[str, Any]] = []
    for r_item in (data.get("results") or []):
        doc = r_item.get("document") or {}
        struct_data = doc.get("structData") or doc.get("derivedStructData") or {}
        results.append({
            "id": doc.get("id"),
            "name": doc.get("name"),
            "uri": doc.get("uri") or struct_data.get("link"),
            "title": struct_data.get("title"),
            "snippet": struct_data.get("snippet") or struct_data.get("snippets"),
            "struct_data": struct_data,
        })
    return {
        "status": "ok",
        "query": q,
        "results": results,
        "count": len(results),
        "total_size": data.get("totalSize"),
        "next_page_token": data.get("nextPageToken"),
        "serving_config": sc,
        "elapsed_ms": elapsed_ms,
    }
