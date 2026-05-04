"""Syrabit.ai — Grounded answer pipeline.

Single backend orchestrator for the educational browser's "ask Syra"
flow. Given a user query plus optional page/chapter context, this
module:

  1. Pulls internal RAG context (existing chapter index).
  2. Pulls fresh web grounding via `rag.web_search_with_fallback`.
  3. Optionally pulls the rendered text of a "current page" the user
     is looking at (via `edu_reader.fetch_and_extract`).
  4. Filters all external content through the kid-safe filter.
  5. Builds a stable, deduped, **numbered** citation list — every
     source gets the *same* index across reruns of the same query/url
     set so the streaming answer can reference `[3]` and the frontend
     can resolve it back to the source card.
  6. Streams the LLM answer via the existing `call_llm_api_stream`
     pipeline, yielding SSE events that include cancellation-safe
     message IDs so frontends can resume / cancel cleanly.

The orchestrator does not own any HTTP routes — `routes/edu_browser.py`
mounts a thin SSE endpoint that calls into here. Keeping it route-free
makes it easy to unit-test and to reuse from other entry points
(websocket, internal cron, smoke tests).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import uuid
from typing import AsyncIterator, Optional
from urllib.parse import urlparse

from rag import (
    web_search_with_fallback,
    _fetch_internal_chapters,
    build_rag_system_prompt,
)
from edu_reader import fetch_and_extract
from guardrails.web_safety import filter_web_results, score_text_kid_safety
from guardrails.prompt_safety import evaluate_prompt_safety, validate_llm_output
from llm import call_llm_api_stream
from cache import (
    REDIS_AI_CACHE_TTL,
    build_ai_cache_key, ai_cache_aget, ai_cache_aset,
    ai_cache_record_hit_saved_latency, ai_cache_expected_saved_ms,
)

logger = logging.getLogger(__name__)

GROUNDED_CACHE_PREFIX = "grounded_answer:"

# Per-process metrics
_pipeline_metrics = {
    "runs": 0,
    "cache_hits": 0,
    "cancelled": 0,
    "safety_blocked_query": 0,
    "safety_dropped_web": 0,
    "safety_blocked_output": 0,
}


def get_grounded_pipeline_stats() -> dict:
    return dict(_pipeline_metrics)


# ───────────────────────── Citation building ─────────────────────────

_GROUNDING_STOPWORDS = frozenset({
    "the","a","an","is","are","was","were","be","been","being","of","in","on","to",
    "for","and","or","but","with","from","by","at","as","that","this","these","those",
    "it","its","what","why","how","who","whom","when","where","which","do","does","did",
    "can","could","should","would","will","may","might","must","not","no","i","you","he",
    "she","they","we","my","your","our","their","me","him","her","us","them","there",
    "than","then","so","if","into","about","also","over","under","up","down","just",
    "very","more","most","some","any","all","each","every","such","only","own","same",
    "too","one","two","three",
})

# Sentence boundary: punctuation followed by whitespace and a capital / quote / paren.
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(])")


def _query_keywords(query: str) -> set[str]:
    toks = re.findall(r"[A-Za-z][A-Za-z0-9\-']{2,}", (query or "").lower())
    return {t for t in toks if t not in _GROUNDING_STOPWORDS}


def _extract_page_spans(text: str, query: str, max_spans: int = 5) -> list[str]:
    """Pick up to ``max_spans`` sentences from ``text`` that best match ``query``.

    Uses a cheap lexical-overlap score (count of distinct query keywords
    appearing in the sentence). Returns sentences in the order they appear
    in the article so the frontend can highlight them in reading order.
    Returns an empty list when no useful match exists.
    """
    if not text or not query:
        return []
    keywords = _query_keywords(query)
    if not keywords:
        return []
    if len(text) > 60_000:
        text = text[:60_000]
    sents = _SENTENCE_RE.split(text)
    scored: list[tuple[int, int, str]] = []
    for idx, raw in enumerate(sents):
        s = raw.strip()
        if not (20 <= len(s) <= 400):
            continue
        slow = s.lower()
        hits = sum(1 for kw in keywords if kw in slow)
        if hits == 0:
            continue
        scored.append((hits, idx, s))
    if not scored:
        return []
    top = sorted(scored, key=lambda x: (-x[0], x[1]))[:max_spans]
    top.sort(key=lambda x: x[1])
    return [s for _, _, s in top]


def _extract_overlap_spans(page_text: str, snippet: str, max_spans: int = 3) -> list[str]:
    """Find sentences in ``page_text`` that contain verbatim multi-word
    phrases also present in ``snippet``.

    Used so that web/internal citations whose body text overlaps the
    article on the open page can be linked to the same in-article
    highlight UI as the page-type citation. We scan the snippet for
    4-6 word windows that contain at least one non-stopword, check
    each window for a case-insensitive match in the page text, and
    return the unique containing sentences (in reading order).
    Returns an empty list when no useful overlap exists.
    """
    if not page_text or not snippet:
        return []
    if len(page_text) > 60_000:
        page_text = page_text[:60_000]
    page_lower = page_text.lower()
    words = re.findall(r"[A-Za-z][A-Za-z0-9\-']*", snippet)
    if len(words) < 4:
        return []
    seen_phrase: set[str] = set()
    candidates: list[str] = []
    # Prefer longer (more specific) windows first.
    for n in (6, 5, 4):
        if len(words) < n:
            continue
        for i in range(len(words) - n + 1):
            window = words[i:i + n]
            if not any(w.lower() not in _GROUNDING_STOPWORDS for w in window):
                continue
            phrase = " ".join(window)
            plow = phrase.lower()
            if plow in seen_phrase:
                continue
            seen_phrase.add(plow)
            if plow in page_lower:
                candidates.append(phrase)
    if not candidates:
        return []
    sents = _SENTENCE_RE.split(page_text)
    chosen: list[tuple[int, str]] = []
    chosen_idxs: set[int] = set()
    for cand in candidates:
        clow = cand.lower()
        for idx, raw in enumerate(sents):
            if idx in chosen_idxs:
                continue
            s = raw.strip()
            if not (20 <= len(s) <= 400):
                continue
            if clow in s.lower():
                chosen.append((idx, s))
                chosen_idxs.add(idx)
                break
        if len(chosen) >= max_spans:
            break
    chosen.sort(key=lambda x: x[0])
    return [s for _, s in chosen]


def _stable_citation_key(item: dict) -> str:
    """Deterministic key for de-duping citations across reruns."""
    if "url" in item and item["url"]:
        return f"url:{item['url']}"
    if "chapter_id" in item and item["chapter_id"]:
        return f"chapter:{item['chapter_id']}"
    title = (item.get("title") or "").strip().lower()
    return "title:" + hashlib.md5(title.encode()).hexdigest()[:10]


def _build_citations(
    web_results: list[dict],
    internal_chapters: list[dict],
    page_context: Optional[dict],
    query: str = "",
) -> list[dict]:
    """Return a list of `{"index": N, "type": ..., "title": ..., ...}` dicts.

    Indices start at 1 and are assigned in a fixed priority order:
    page_context → internal chapters → web results. Repeats are
    suppressed via `_stable_citation_key`. The output is what the
    frontend renders as the visible citation tray and what the LLM
    is told to use when emitting `[N]` markers in its answer.
    """
    citations: list[dict] = []
    seen: dict[str, int] = {}
    next_idx = 1

    # Cache the open-page text once so we can also surface in-article
    # overlap highlights for web/internal citations whose snippets quote
    # the page verbatim.
    page_text_for_overlap = ""
    if page_context and page_context.get("ok") and page_context.get("text"):
        page_text_for_overlap = page_context.get("text") or ""
        key = _stable_citation_key(page_context)
        seen[key] = next_idx
        domain = page_context.get("domain") or ""
        spans = _extract_page_spans(page_text_for_overlap, query) if query else []
        citations.append({
            "index": next_idx,
            "type": "page",
            "title": page_context.get("title", domain or "Current page"),
            "url": page_context.get("url", ""),
            "domain": domain,
            "snippet": page_text_for_overlap[:240],
            "spans": spans,
            "anchor": "",
        })
        next_idx += 1

    for ch in internal_chapters or []:
        key = _stable_citation_key({
            "chapter_id": ch.get("subject_id", "") + ":" + (ch.get("slug", "") or ch.get("title", "")),
            "title": ch.get("title", ""),
        })
        if key in seen:
            continue
        seen[key] = next_idx
        snippet = (ch.get("content") or "")[:240]
        overlap = (
            _extract_overlap_spans(page_text_for_overlap, ch.get("content") or "")
            if page_text_for_overlap else []
        )
        citations.append({
            "index": next_idx,
            "type": "chapter",
            "title": ch.get("title", "Untitled chapter"),
            "url": f"/learn/{ch.get('slug', '')}" if ch.get("slug") else "",
            "domain": "syrabit.ai",
            "snippet": snippet,
            "spans": overlap,
            "anchor": ch.get("slug", ""),
        })
        next_idx += 1

    for r in web_results or []:
        url = r.get("url", "")
        if not url:
            continue
        key = _stable_citation_key({"url": url})
        if key in seen:
            continue
        seen[key] = next_idx
        domain = ""
        try:
            domain = (urlparse(url).hostname or "").lower()
            if domain.startswith("www."):
                domain = domain[4:]
        except Exception:
            pass
        web_snippet = (r.get("snippet") or r.get("body") or "")
        overlap = (
            _extract_overlap_spans(page_text_for_overlap, web_snippet)
            if page_text_for_overlap else []
        )
        citations.append({
            "index": next_idx,
            "type": "web",
            "title": r.get("title", domain or "Web result"),
            "url": url,
            "domain": domain,
            "snippet": web_snippet[:240],
            "spans": overlap,
            "anchor": "",
        })
        next_idx += 1

    return citations


def _build_citation_prompt(citations: list[dict]) -> str:
    if not citations:
        return ""
    lines = [
        "",
        "─── CITATIONS ───",
        "Cite sources inline using [N] where N is the source number below. "
        "Do NOT invent new numbers. Reuse the same number when citing the "
        "same source again. Do NOT include URLs in the answer body — only "
        "the bracketed number.",
        "",
    ]
    for c in citations:
        domain = c.get("domain") or ""
        title = c.get("title") or "Source"
        snippet = (c.get("snippet") or "").strip().replace("\n", " ")
        if len(snippet) > 280:
            snippet = snippet[:280] + "…"
        lines.append(f"[{c['index']}] {title} — {domain}\n    {snippet}")
    lines.append("─────────────────")
    return "\n".join(lines)


# ───────────────────────── Cache helpers ─────────────────────────

def _query_cache_key(
    query: str,
    page_url: str,
    subject_id: str,
    chapter_name: str,
    *,
    model: str = "grounded",
    response_lang: str = "",
) -> str:
    """Deterministic AI-cache key for grounded answers.

    Delegates to the shared `build_ai_cache_key` helper so that grounded
    responses live in the same Memorystore namespace as chat responses
    (Task #609) and benefit from the same purge/stats/circuit-breaker
    machinery. The page URL + subject + chapter together act as the
    retrieval fingerprint for this pipeline.
    """
    retrieval_fp = f"{page_url.strip().lower()}|{subject_id}|{chapter_name.strip().lower()}"
    return build_ai_cache_key(
        model=model or "grounded",
        prompt=query,
        retrieval=retrieval_fp,
        language=response_lang or "",
        scope="grounded",
    )


# ───────────────────────── Public streaming API ─────────────────────────

async def stream_grounded_answer(
    *,
    query: str,
    page_url: str = "",
    subject_id: str = "",
    subject_name: str = "",
    chapter_name: str = "",
    board_name: str = "",
    class_name: str = "",
    response_lang: str = "en",
    model: str = "",
    max_tokens: int = 1024,
    actor: str = "",
    ip_hash: str = "",
    message_id: Optional[str] = None,
    cancel_event: Optional[asyncio.Event] = None,
) -> AsyncIterator[str]:
    """Yield SSE-formatted strings for a grounded answer.

    Each yielded string is a complete `data: {...}\\n\\n` block. The
    first event is a `meta` frame containing `message_id`, `citations`,
    and `from_cache`. Subsequent frames carry `content` deltas. The
    final frame is `{"event": "syrabit_done", ...}` followed by
    `data: [DONE]\\n\\n`.

    If `cancel_event` is set during streaming, the iterator emits a
    `{"event": "cancelled"}` frame and stops cleanly.
    """
    _pipeline_metrics["runs"] += 1
    t0 = time.perf_counter()
    mid = message_id or f"msg_{uuid.uuid4().hex[:16]}"

    # Task #362 §1 — Tier-1 recall-intent classifier. Detects "earlier
    # you said…" style turns so an upstream summary-vector lookup can
    # be gated. Fires a counter for calibration; downstream consumers
    # read the flag from the chat-turn context (no signature change).
    _is_recall_turn = False
    try:
        from recall_intent import is_recall_intent as _is_recall
        _is_recall_turn = _is_recall(query)
        if _is_recall_turn:
            _pipeline_metrics["recall_intent_hits"] = (
                _pipeline_metrics.get("recall_intent_hits", 0) + 1
            )
            logger.debug("[grounded_answer] recall-intent detected: %s", query[:120])
    except Exception:
        pass

    # 1. Prompt safety
    safe_prompt, fallback_msg, guardrail_tag = evaluate_prompt_safety(query)
    if fallback_msg:
        _pipeline_metrics["safety_blocked_query"] += 1
        yield _sse({
            "event": "meta", "message_id": mid,
            "guardrail_blocked": True, "guardrail_tag": guardrail_tag,
            "citations": [], "from_cache": False,
        })
        for i in range(0, len(fallback_msg), 60):
            yield _sse({"message_id": mid, "content": fallback_msg[i:i + 60]})
            await asyncio.sleep(0.005)
        yield _sse({"event": "syrabit_done", "message_id": mid, "guardrail_tag": guardrail_tag})
        yield "data: [DONE]\n\n"
        return

    # 2. Cache check (very cheap repeat-question optimisation)
    cache_key = _query_cache_key(
        query, page_url, subject_id, chapter_name,
        model=model or "grounded", response_lang=response_lang or "",
    )
    _grounded_cache_t0 = time.perf_counter()
    cached_blob = await ai_cache_aget(cache_key)
    if cached_blob:
        ai_cache_record_hit_saved_latency(ai_cache_expected_saved_ms())
        try:
            cached = json.loads(cached_blob if isinstance(cached_blob, str) else cached_blob.decode("utf-8"))
            _pipeline_metrics["cache_hits"] += 1
            yield _sse({
                "event": "meta", "message_id": mid,
                "citations": cached.get("citations", []),
                "from_cache": True,
            })
            answer = cached.get("answer", "")
            for i in range(0, len(answer), 280):
                if cancel_event and cancel_event.is_set():
                    _pipeline_metrics["cancelled"] += 1
                    yield _sse({"event": "cancelled", "message_id": mid})
                    return
                yield _sse({"message_id": mid, "content": answer[i:i + 280]})
                await asyncio.sleep(0)
            yield _sse({
                "event": "syrabit_done", "message_id": mid,
                "from_cache": True, "elapsed_ms": int((time.perf_counter() - t0) * 1000),
                "citations": cached.get("citations", []),
            })
            yield "data: [DONE]\n\n"
            return
        except Exception as e:
            logger.debug(f"[grounded_answer] cache decode failed: {e}")

    # 3. Gather grounding context in parallel
    async def _maybe_fetch_page():
        if not page_url:
            return None
        return await fetch_and_extract(page_url, actor=actor, ip_hash=ip_hash)

    async def _maybe_internal():
        if not (subject_id or subject_name):
            return []
        try:
            return await _fetch_internal_chapters(query, subject_id=subject_id, subject_name=subject_name)
        except Exception as e:
            logger.debug(f"[grounded_answer] internal fetch failed: {e}")
            return []

    async def _maybe_web():
        try:
            return await web_search_with_fallback(
                query, board_name=board_name, class_name=class_name,
                subject_name=subject_name, chapter_name=chapter_name,
            )
        except Exception as e:
            logger.debug(f"[grounded_answer] web search failed: {e}")
            return []

    page_ctx, internal, web_raw = await asyncio.gather(
        _maybe_fetch_page(), _maybe_internal(), _maybe_web(),
        return_exceptions=False,
    )

    # 4. Kid-safe filter on web grounding & page context
    web_kept, web_dropped = filter_web_results(web_raw or [])
    if web_dropped:
        _pipeline_metrics["safety_dropped_web"] += len(web_dropped)
        logger.info(f"[grounded_answer] kid-safe filter dropped {len(web_dropped)} web result(s)")

    page_payload: Optional[dict] = None
    if page_ctx and page_ctx.get("ok"):
        safe, _density, _hits = score_text_kid_safety(page_ctx.get("text", ""))
        if safe:
            page_payload = page_ctx
        else:
            logger.info(f"[grounded_answer] kid-safe filter rejected page {page_ctx.get('url','')}")
            _pipeline_metrics["safety_dropped_web"] += 1

    citations = _build_citations(web_kept, internal, page_payload, query=query)

    # 5. Build prompt (reuse existing builder + append citation block)
    rag_ctx = {
        "chunks": internal, "chapters": internal,
        "subjects": [], "vector_hits": [],
        "source": ("internal" if internal else ("page" if page_payload else "web" if web_kept else "none")),
        "quality": ("tier1" if (internal or page_payload) else ("tier2" if web_kept else "none")),
        "_general_knowledge_fallback": not (internal or page_payload or web_kept),
    }
    if page_payload:
        rag_ctx["document_text"] = page_payload.get("text", "")[:6000]
        rag_ctx["source"] = "library" if internal else "document"

    system_prompt = build_rag_system_prompt(
        {
            "board_name": board_name, "class_name": class_name,
            "subject_name": subject_name, "subject_id": subject_id,
            "chapter_name": chapter_name, "stream_name": "",
        },
        rag_ctx,
        user_info={"name": "", "board_name": board_name, "class_name": class_name, "stream_name": "", "plan": "free"},
        query=query, syllabus=None,
        web_results=web_kept or None,
        resolved_intent="notes",
        response_lang=response_lang,
    )
    system_prompt += _build_citation_prompt(citations)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": query},
    ]

    # 6. Emit meta frame so the client can render the citation tray
    #    immediately, before any model tokens arrive.
    yield _sse({
        "event": "meta", "message_id": mid,
        "citations": citations,
        "rag_source": rag_ctx["source"],
        "web_results_kept": len(web_kept),
        "web_results_dropped": len(web_dropped),
        "from_cache": False,
    })

    # 7. Stream tokens. Wrap the underlying LLM stream so we can apply
    #    output safety validation, support cancellation, and accumulate
    #    the answer for the cache write at the end.
    answer_parts: list[str] = []
    try:
        async for chunk in call_llm_api_stream(
            messages, model=model or "", max_tokens=max_tokens,
            intent="notes", response_lang=response_lang,
        ):
            if cancel_event and cancel_event.is_set():
                _pipeline_metrics["cancelled"] += 1
                yield _sse({"event": "cancelled", "message_id": mid})
                return
            # `call_llm_api_stream` already yields SSE strings of the
            # form "data: {...}\n\n" with a "content" key. Tap into that
            # so we can mirror it as our own message_id-tagged frames
            # and accumulate full text for caching.
            if not chunk or not chunk.startswith("data:"):
                continue
            try:
                payload = json.loads(chunk.split("data:", 1)[1].strip())
            except Exception:
                continue
            content = payload.get("content")
            if content is None:
                continue
            ok, _violation = validate_llm_output(content)
            if not ok:
                _pipeline_metrics["safety_blocked_output"] += 1
                yield _sse({"event": "safety_break", "message_id": mid})
                break
            answer_parts.append(content)
            yield _sse({"message_id": mid, "content": content})
    except asyncio.CancelledError:
        _pipeline_metrics["cancelled"] += 1
        yield _sse({"event": "cancelled", "message_id": mid})
        return
    except Exception as e:
        logger.warning(f"[grounded_answer] LLM stream failed: {e}")
        yield _sse({"event": "error", "message_id": mid, "error": "llm_failed", "detail": str(e)[:200]})

    answer = "".join(answer_parts).strip()
    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    if answer:
        try:
            await ai_cache_aset(
                cache_key,
                json.dumps({"answer": answer, "citations": citations}),
                REDIS_AI_CACHE_TTL,
                saved_ms=float(elapsed_ms),
            )
        except Exception as e:
            logger.debug(f"[grounded_answer] cache write failed: {e}")

        # Task #361 §1 — RAG result cache (shadow-mode write). The
        # cache is in shadow until the operator flips
        # `cache:rag_serve_enabled = "1"` in Redis. Soft-fail: a write
        # failure here never affects the user-visible answer.
        try:
            from rag_cache import record_rag_result as _rag_cache_set
            _curr_ver = os.environ.get("CURRICULUM_VERSION", "v0")
            _rag_cache_set(
                query,
                {
                    "answer": answer,
                    "citations": citations,
                    "rag_source": rag_ctx["source"],
                    "elapsed_ms": elapsed_ms,
                },
                retriever=rag_ctx.get("source") or "default",
                top_k=len(citations or []),
                lang=(response_lang or "en"),
                curriculum_version=_curr_ver,
            )
        except Exception as e:
            logger.debug(f"[grounded_answer] rag_cache write failed: {e}")

    yield _sse({
        "event": "syrabit_done", "message_id": mid,
        "elapsed_ms": elapsed_ms,
        "rag_source": rag_ctx["source"],
        "citations": citations,
        "word_count": len(answer.split()) if answer else 0,
    })
    yield "data: [DONE]\n\n"


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


__all__ = [
    "GROUNDED_CACHE_PREFIX",
    "stream_grounded_answer", "get_grounded_pipeline_stats",
]
