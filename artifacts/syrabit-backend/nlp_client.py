"""Google Cloud Natural Language API client (API-key mode).

Wraps:
  POST https://language.googleapis.com/v1/documents:analyzeSentiment
  POST https://language.googleapis.com/v1/documents:analyzeEntities
  POST https://language.googleapis.com/v1/documents:classifyText
  POST https://language.googleapis.com/v1/documents:annotateText  (combined)

Used to grade generated educational notes: detect negative-tone errors,
extract canonical entities for cross-linking, and classify content into
Google's IAB taxonomy for content-targeted SEO.

Auth: GOOGLE_NLP_API_KEY (falls back to GOOGLE_KG_API_KEY).
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

NLP_BASE = "https://language.googleapis.com/v1/documents:"
_HTTP_TIMEOUT_S = 10.0


def _api_key() -> str:
    return (
        (os.environ.get("GOOGLE_NLP_API_KEY") or "").strip()
        or (os.environ.get("GOOGLE_KG_API_KEY") or "").strip()
    )


def is_configured() -> bool:
    return bool(_api_key())


def _document(content: str, language: Optional[str] = None) -> Dict[str, Any]:
    doc: Dict[str, Any] = {"type": "PLAIN_TEXT", "content": content}
    if language:
        doc["language"] = language
    return doc


async def _post(method: str, body: Dict[str, Any], timeout_s: float) -> Dict[str, Any]:
    key = _api_key()
    if not key:
        return {"status": "disabled",
                "error": "GOOGLE_NLP_API_KEY (or GOOGLE_KG_API_KEY) not configured"}
    url = f"{NLP_BASE}{method}?key={key}"
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.post(url, json=body)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        if r.status_code != 200:
            return {"status": "error", "elapsed_ms": elapsed_ms,
                    "error": f"HTTP {r.status_code}: {r.text[:200]}"}
        return {"status": "ok", "elapsed_ms": elapsed_ms, "data": r.json() or {}}
    except httpx.TimeoutException:
        return {"status": "error",
                "elapsed_ms": (time.perf_counter() - t0) * 1000.0,
                "error": "timeout"}
    except Exception as exc:
        return {"status": "error",
                "elapsed_ms": (time.perf_counter() - t0) * 1000.0,
                "error": f"{type(exc).__name__}: {str(exc)[:200]}"}


async def analyze_sentiment(
    content: str, *, language: Optional[str] = None,
    timeout_s: float = _HTTP_TIMEOUT_S,
) -> Dict[str, Any]:
    """Return overall + per-sentence sentiment scores."""
    if not (content or "").strip():
        return {"status": "error", "error": "empty_content"}
    out = await _post(
        "analyzeSentiment",
        {"document": _document(content, language), "encodingType": "UTF8"},
        timeout_s,
    )
    if out.get("status") != "ok":
        return out
    data = out["data"]
    doc_sent = data.get("documentSentiment") or {}
    sentences = []
    for s in data.get("sentences") or []:
        st = s.get("sentiment") or {}
        sentences.append({
            "text": (s.get("text") or {}).get("content"),
            "score": st.get("score"),
            "magnitude": st.get("magnitude"),
        })
    return {
        "status": "ok",
        "elapsed_ms": out["elapsed_ms"],
        "language": data.get("language"),
        "document_score": doc_sent.get("score"),
        "document_magnitude": doc_sent.get("magnitude"),
        "sentences": sentences,
    }


async def analyze_entities(
    content: str, *, language: Optional[str] = None,
    timeout_s: float = _HTTP_TIMEOUT_S,
) -> Dict[str, Any]:
    """Extract entities with types + salience + Wikipedia/MID metadata."""
    if not (content or "").strip():
        return {"status": "error", "error": "empty_content"}
    out = await _post(
        "analyzeEntities",
        {"document": _document(content, language), "encodingType": "UTF8"},
        timeout_s,
    )
    if out.get("status") != "ok":
        return out
    data = out["data"]
    entities = []
    for e in data.get("entities") or []:
        meta = e.get("metadata") or {}
        entities.append({
            "name": e.get("name"),
            "type": e.get("type"),
            "salience": e.get("salience"),
            "wikipedia_url": meta.get("wikipedia_url"),
            "mid": meta.get("mid"),
            "mention_count": len(e.get("mentions") or []),
        })
    return {
        "status": "ok",
        "elapsed_ms": out["elapsed_ms"],
        "language": data.get("language"),
        "entities": entities,
        "count": len(entities),
    }


async def classify_text(
    content: str, *, language: Optional[str] = None,
    timeout_s: float = _HTTP_TIMEOUT_S,
) -> Dict[str, Any]:
    """Classify into Google's IAB content taxonomy. Requires >=20 tokens."""
    if not (content or "").strip():
        return {"status": "error", "error": "empty_content"}
    out = await _post(
        "classifyText",
        {"document": _document(content, language)},
        timeout_s,
    )
    if out.get("status") != "ok":
        return out
    data = out["data"]
    return {
        "status": "ok",
        "elapsed_ms": out["elapsed_ms"],
        "categories": [
            {"name": c.get("name"), "confidence": c.get("confidence")}
            for c in (data.get("categories") or [])
        ],
    }
