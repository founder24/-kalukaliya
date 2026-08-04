"""
Web search service using DuckDuckGo (duckduckgo-search package, no API key required).

Used as the 20% web-search slice in the RAG pipeline:
  RAG 50% + Web 20% + LLM logic 30%

If RAG is unavailable:
  Web 50% + LLM logic 50%

Queries are automatically augmented with board/syllabus keywords to keep
results aligned with Assam Board (AHSEC/SEBA) curriculum.

Replaced the old DDG Instant-Answers API (api.duckduckgo.com/?format=json)
which only returned Wikipedia zero-click abstracts and was empty for almost
all specific academic questions.  duckduckgo-search scrapes real DDG results
and reliably returns 3-5 web snippets per query.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Educational keywords appended to every query so results stay syllabus-aligned
_EDU_SUFFIX = "AHSEC SEBA CBSE Assam board syllabus"

_MAX_RESULTS = 4   # cap at 4 web snippets — matches old budget
_TIMEOUT_S  = 5.0  # generous: DDGS is synchronous, runs in a thread


def _build_query(user_query: str, lang: str) -> str:
    """Append curriculum keywords so DDG returns educational content."""
    return f"{user_query.strip()} {_EDU_SUFFIX}"


def _ddgs_sync(query: str, max_results: int) -> list[dict]:
    """
    Synchronous DDG text search.  Runs inside asyncio.to_thread so it
    never blocks the event loop.

    Returns a list of raw DDGS result dicts:
      { title, href, body }
    """
    try:
        from ddgs import DDGS  # lazy import — only used here (package: ddgs)
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results))
    except Exception as e:
        logger.warning(f"_ddgs_sync error: {type(e).__name__}: {e}")
        return []


async def web_search(
    query: str,
    lang: str = "en",
    timeout: float = _TIMEOUT_S,
) -> list[dict]:
    """
    Fetch educational web snippets for the query.

    Returns a list of chunk dicts (same shape as RAG chunks) on success,
    or [] on any error / timeout so the caller's pipeline degrades gracefully.
    """
    augmented = _build_query(query, lang)

    try:
        raw = await asyncio.wait_for(
            asyncio.to_thread(_ddgs_sync, augmented, _MAX_RESULTS),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning("web_search: timed out (%.1fs)", timeout)
        return []
    except Exception as e:
        logger.warning(f"web_search: failed ({type(e).__name__}: {e})")
        return []

    chunks: list[dict] = []
    for i, r in enumerate(raw):
        body  = (r.get("body") or "").strip()
        title = (r.get("title") or "Web Result").strip()
        url   = (r.get("href") or "").strip()
        if body and len(body) > 30:
            chunks.append(
                {
                    "id":      f"web_{i}",
                    "title":   title,
                    "content": body[:700],   # cap each snippet at ~140 words
                    "score":   0.70,
                    "url":     url,
                    "source":  "web_search",
                }
            )

    logger.info(
        f"web_search: query_len={len(query)} lang={lang} chunks={len(chunks)}"
    )
    return chunks[:_MAX_RESULTS]
