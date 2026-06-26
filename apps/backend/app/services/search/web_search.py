"""
Web search service using DuckDuckGo Instant Answers API (no API key required).

Used as the 20% web-search slice in the RAG pipeline:
  RAG 50% + Web 20% + LLM logic 30%

If RAG is unavailable:
  Web 50% + LLM logic 50%

Queries are automatically augmented with board/syllabus keywords to keep
results aligned with Assam Board (AHSEC/SEBA) curriculum.
"""

from __future__ import annotations

import asyncio
import logging
import urllib.parse
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_DDG_URL = "https://api.duckduckgo.com/"
_TIMEOUT = 5.0  # seconds — must not block the critical LLM path

# Educational keywords appended to every query so results stay syllabus-aligned
_EDU_SUFFIX = "AHSEC SEBA CBSE Assam board syllabus"


def _build_query(user_query: str, lang: str) -> str:
    """Append curriculum keywords so DDG returns educational content."""
    stripped = user_query.strip()
    if lang == "as":
        # Assamese queries: transliterate context is English, keep suffix English
        return f"{stripped} {_EDU_SUFFIX}"
    return f"{stripped} {_EDU_SUFFIX}"


def _parse_ddg_response(data: dict) -> list[dict]:
    """
    Extract text snippets from DuckDuckGo Instant Answers JSON.

    Precedence:
      1. Abstract (summary paragraph)
      2. RelatedTopics texts (up to 4 items)
    """
    chunks: list[dict] = []

    abstract = data.get("AbstractText", "").strip()
    abstract_source = data.get("AbstractSource", "Web")
    abstract_url = data.get("AbstractURL", "")
    if abstract and len(abstract) > 40:
        chunks.append(
            {
                "id": "web_abstract",
                "title": data.get("Heading", abstract_source),
                "content": abstract[:800],
                "score": 0.72,
                "url": abstract_url,
                "source": "web_search",
            }
        )

    for i, topic in enumerate(data.get("RelatedTopics", [])[:4]):
        text = topic.get("Text", "").strip()
        first_url = topic.get("FirstURL", "")
        if text and len(text) > 30:
            chunks.append(
                {
                    "id": f"web_topic_{i}",
                    "title": topic.get("Name", "Related"),
                    "content": text[:600],
                    "score": 0.65,
                    "url": first_url,
                    "source": "web_search",
                }
            )

    return chunks[:4]  # cap at 4 web snippets


async def web_search(
    query: str,
    lang: str = "en",
    timeout: float = _TIMEOUT,
) -> list[dict]:
    """
    Fetch educational web snippets for the query.

    Returns a list of chunk dicts (same shape as RAG chunks) on success,
    or [] on any error / timeout so the caller's pipeline degrades gracefully.
    """
    augmented = _build_query(query, lang)
    params = {
        "q": augmented,
        "format": "json",
        "no_html": "1",
        "skip_disambig": "1",
        "t": "syrabit",
    }
    url = f"{_DDG_URL}?{urllib.parse.urlencode(params)}"

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(
                url,
                headers={"User-Agent": "Syrabit-Educational-AI/3.0"},
            )
            resp.raise_for_status()
            data = resp.json()
            chunks = _parse_ddg_response(data)
            logger.info(
                f"web_search: query_len={len(query)} lang={lang} "
                f"chunks={len(chunks)}"
            )
            return chunks
    except asyncio.TimeoutError:
        logger.warning("web_search: timed out")
        return []
    except Exception as e:
        logger.warning(f"web_search: failed ({type(e).__name__}: {e})")
        return []
