"""retrieval_first — Task #581 §L5 retrieval-first dispatch hook.

Before any LLM call, the chat dispatcher MUST attempt to satisfy the
question from local stores in this order:

  1. ``ai_input_cache`` — deterministic-input cache (Task #571). Hit
     ratio for repeated definition / mcq / explanation prompts is in
     the 30-40 % range during exam mode.
  2. ``rag_cache`` — chunk-level retrieval cache (BM25 + vector pass
     short-circuit).
  3. Mongo materialized stores — `mcqs`, `definitions`, `pyqs`,
     `flashcards` collections, keyed by topic-slug + question-hash.
     These are the bulk of "answerable from local data" hits because
     the admin pre-gen pipeline pre-computes the long tail.

Returns ``None`` on miss so the caller can proceed with the LLM
dispatch (or, in the §L4 ``tier=="retrieval_only"`` bucket, paywall).

Pure-async, no FastAPI imports — safe to call from any dispatcher.
The confidence threshold defaults to 0.85 per the Task #581 spec; the
Mongo materialized-store tier always returns 1.0 (exact-key lookup),
the ai_input_cache tier returns 1.0 (deterministic key), the rag_cache
tier returns the upstream similarity score.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_CONFIDENCE_THRESHOLD = 0.85


@dataclass
class RetrievalHit:
    """Result of a successful retrieval-first lookup."""
    answer: str
    source: str           # "ai_input_cache" | "rag_cache" | "mongo:<collection>"
    confidence: float     # >= threshold for the caller to use it
    content_type: str     # "definition" | "mcq" | "explanation" | "pyq" | ...
    metadata: dict


def _normalized_key(query: str, content_type: str, lang: str) -> str:
    """Stable hash key for cross-tier deduplication."""
    raw = f"{(content_type or '').lower()}|{(lang or 'en').lower()}|{(query or '').strip().lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


async def _try_ai_input_cache(
    query: str, content_type: str, lang: str
) -> Optional[RetrievalHit]:
    """Tier 1: deterministic ai_input_cache lookup.

    Best-effort — returns None on any error so the dispatcher continues
    down the ladder rather than failing the whole turn.
    """
    try:
        from ai_input_cache import get_response  # type: ignore
    except Exception:
        return None
    try:
        cached = get_response(
            content_type=content_type,
            template_version=f"retrieval_first_v1",
            prompt=query,
            model="*",
            max_tokens=0,
        )
    except Exception:
        return None
    if not cached:
        return None
    return RetrievalHit(
        answer=str(cached),
        source="ai_input_cache",
        confidence=1.0,
        content_type=content_type,
        metadata={"key": _normalized_key(query, content_type, lang)},
    )


async def _try_rag_cache(
    query: str, content_type: str, lang: str
) -> Optional[RetrievalHit]:
    """Tier 2: chunk-level retrieval cache.

    Uses ``rag_cache.lookup`` if available. Returns None when the
    upstream similarity score is below the threshold or when the
    module isn't wired (best-effort).
    """
    try:
        from rag_cache import lookup as _rag_lookup  # type: ignore
    except Exception:
        return None
    try:
        hit = await _rag_lookup(query=query, lang=lang, content_type=content_type)  # type: ignore[misc]
    except TypeError:
        try:
            hit = _rag_lookup(query)  # type: ignore[misc]
        except Exception:
            return None
    except Exception:
        return None
    if not hit:
        return None
    answer = (hit.get("answer") if isinstance(hit, dict) else None) or ""
    score = float(hit.get("score", 0.0)) if isinstance(hit, dict) else 0.0
    if not answer or score < DEFAULT_CONFIDENCE_THRESHOLD:
        return None
    return RetrievalHit(
        answer=answer,
        source="rag_cache",
        confidence=score,
        content_type=content_type,
        metadata=hit if isinstance(hit, dict) else {},
    )


async def _try_mongo_materialized(
    query: str, content_type: str, lang: str
) -> Optional[RetrievalHit]:
    """Tier 3: Mongo materialized stores (mcqs / definitions / pyqs / flashcards).

    Looks up the stable-hash key in the per-content-type collection.
    Returns None if Mongo isn't reachable or the row doesn't exist.
    """
    try:
        from db import db as _mongo  # type: ignore
    except Exception:
        return None
    if _mongo is None:
        return None
    coll_map = {
        "definition":      "definitions",
        "mcq":             "mcqs",
        "mcq_explanation": "mcqs",
        "pyq":             "pyqs",
        "pyq_answer":      "pyqs",
        "flashcard":       "flashcards",
    }
    coll_name = coll_map.get((content_type or "").lower())
    if not coll_name:
        return None
    try:
        coll = _mongo[coll_name]
    except Exception:
        return None
    key = _normalized_key(query, content_type, lang)
    try:
        doc = await coll.find_one({"retrieval_key": key, "lang": lang})  # type: ignore[union-attr]
    except TypeError:
        try:
            doc = coll.find_one({"retrieval_key": key, "lang": lang})
        except Exception:
            return None
    except Exception:
        return None
    if not doc:
        return None
    answer = doc.get("answer") or doc.get("body") or doc.get("text") or ""
    if not answer:
        return None
    return RetrievalHit(
        answer=str(answer),
        source=f"mongo:{coll_name}",
        confidence=1.0,
        content_type=content_type,
        metadata={"_id": str(doc.get("_id", "")), "key": key},
    )


async def try_resolve(
    query: str,
    *,
    content_type: str,
    lang: str = "en",
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> Optional[RetrievalHit]:
    """Run the three-tier retrieval-first ladder. Return the first hit
    with ``confidence >= confidence_threshold``, or None on full miss.

    Caller contract:
      * On hit, ship the answer directly — DO NOT invoke the LLM. Emit
        ``free_tier_dispatch.record(...)`` with ``source=hit.source``.
      * On miss, the caller may either invoke the LLM (normal /
        cheap / tight bucket) or 402 (retrieval_only bucket).
    """
    if not (query or "").strip():
        return None
    threshold = max(0.0, min(1.0, float(confidence_threshold or 0.0)))
    for fn in (_try_ai_input_cache, _try_rag_cache, _try_mongo_materialized):
        try:
            hit = await fn(query, content_type, lang)
        except Exception as exc:
            logger.debug("[retrieval-first] %s raised: %s", fn.__name__, exc)
            hit = None
        if hit and hit.confidence >= threshold:
            return hit
    return None


__all__ = ["RetrievalHit", "try_resolve", "DEFAULT_CONFIDENCE_THRESHOLD"]
