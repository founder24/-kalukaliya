"""Discovery Engine document ingest helpers (Phase 4 capstone).

Posts structured documents into the Discovery Engine data store so search
results are richer than the website crawl. Used together with Cloud Tasks
to fan out a "publish all topics" job without blocking the API workers.

Each Syrabit topic is mapped onto a Discovery Engine document with:
    id          = topic._id (string)
    structData  = {title, description, slug, url, language, syllabus, ...}
    content     = optional markdown body for unstructured/blended search
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
_HTTP_TIMEOUT_S = 15.0


def _parent(
    *, project: Optional[str] = None, location: Optional[str] = None,
    collection: Optional[str] = None, data_store: Optional[str] = None,
) -> Optional[str]:
    p = (project or gcp_auth.project_id() or "").strip()
    ds = (data_store or os.environ.get("GCP_DISCOVERY_DATA_STORE") or "").strip()
    if not p or not ds:
        return None
    loc = (location or os.environ.get("GCP_DISCOVERY_LOCATION") or "global").strip()
    coll = (collection or os.environ.get("GCP_DISCOVERY_COLLECTION") or
            "default_collection").strip()
    return (f"projects/{p}/locations/{loc}/collections/{coll}"
            f"/dataStores/{ds}/branches/default_branch")


def topic_to_document(topic: Dict[str, Any]) -> Dict[str, Any]:
    """Map a Syrabit topic dict onto a Discovery Engine document body."""
    tid = str(topic.get("_id") or topic.get("id") or topic.get("slug") or "").strip()
    if not tid:
        raise ValueError("topic has no _id/id/slug")
    slug = topic.get("slug") or tid
    title = topic.get("title") or topic.get("name") or slug
    url = topic.get("canonical_url") or f"https://syrabit.ai/topics/{slug}"
    struct = {
        "title": title,
        "description": (topic.get("meta_description") or topic.get("summary") or "")[:1000],
        "slug": slug,
        "url": url,
        "language": topic.get("language") or "en",
        "syllabus": topic.get("syllabus"),
        "subject": topic.get("subject"),
        "tags": topic.get("tags") or [],
        "updated_at": topic.get("updated_at") or topic.get("last_modified"),
    }
    content = topic.get("body_md") or topic.get("content") or topic.get("text") or ""
    out: Dict[str, Any] = {
        "id": tid,
        "structData": {k: v for k, v in struct.items() if v is not None},
    }
    if content:
        out["content"] = {
            "mimeType": "text/plain",
            "rawBytes": _b64(content[:64_000]),
        }
    return out


def _b64(s: str) -> str:
    import base64
    return base64.b64encode(s.encode("utf-8", errors="replace")).decode("ascii")


async def upsert_documents(
    documents: List[Dict[str, Any]],
    *, timeout_s: float = _HTTP_TIMEOUT_S,
) -> Dict[str, Any]:
    """Upsert a batch of pre-formatted documents (max ~100/req recommended)."""
    if not documents:
        return {"status": "ok", "imported": 0, "skipped": True}
    headers = gcp_auth.auth_header()
    if not headers:
        return gcp_auth.disabled_payload({"imported": 0})
    parent = _parent()
    if not parent:
        return {"status": "disabled", "imported": 0,
                "error": "GCP_DISCOVERY_DATA_STORE not configured"}

    url = f"{BASE}/{parent}/documents:import"
    body = {
        "inlineSource": {"documents": documents},
        "reconciliationMode": "INCREMENTAL",
    }
    headers = {**headers, "Content-Type": "application/json"}
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.post(url, headers=headers, json=body)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        if r.status_code >= 400:
            return {"status": "error", "imported": 0, "elapsed_ms": elapsed_ms,
                    "error": f"HTTP {r.status_code}: {r.text[:400]}"}
        data = r.json() or {}
        return {"status": "ok", "imported": len(documents),
                "elapsed_ms": elapsed_ms, "operation": data.get("name")}
    except httpx.TimeoutException:
        return {"status": "error", "imported": 0,
                "elapsed_ms": (time.perf_counter() - t0) * 1000.0,
                "error": "timeout"}
    except Exception as exc:
        return {"status": "error", "imported": 0,
                "elapsed_ms": (time.perf_counter() - t0) * 1000.0,
                "error": f"{type(exc).__name__}: {str(exc)[:200]}"}


async def upsert_topic(topic: Dict[str, Any]) -> Dict[str, Any]:
    try:
        doc = topic_to_document(topic)
    except Exception as exc:
        return {"status": "error", "imported": 0, "error": repr(exc)}
    return await upsert_documents([doc])


# ─── Task #332 — SQS consumer entrypoint ─────────────────────────────────────
#
# `services/backend/sqs_consumers/discovery_engine.py` invokes this when a
# `discovery-engine-ingest` SQS message lands. The message body carries
# `{target: "topic"|"page"|"documents", payload: {...}}`. We dispatch to
# the existing per-target upsert helpers so the consumer call site stays
# a one-liner.
async def ingest(*, target: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch a Discovery-Engine ingest message to the right helper.

    Supported `target` values:
      * "topic"      → `upsert_topic(payload)`
      * "page"       → `upsert_topic(payload)` (alias — pages are
                       represented as topic documents in the index)
      * "documents"  → `upsert_documents(payload["documents"])`
    """
    # Task #332 — RAISE on operational failures so the SQS Lambda
    # consumer reports failure to the runtime; SQS will retry and
    # eventually move the message to the DLQ. Returning an error
    # dict would have the consumer ack the message and silently
    # drop the work.
    if target in ("topic", "page"):
        result = await upsert_topic(payload)
        if isinstance(result, dict) and result.get("status") == "error":
            raise RuntimeError(f"discovery-engine upsert failed: {result.get('error')}")
        return result
    if target == "documents":
        docs = payload.get("documents") or []
        if not isinstance(docs, list):
            raise ValueError("documents payload must be a list")
        result = await upsert_documents(docs)
        if isinstance(result, dict) and result.get("status") == "error":
            raise RuntimeError(f"discovery-engine upsert failed: {result.get('error')}")
        return result
    raise ValueError(f"unknown discovery-engine target {target!r}")
