"""Syrabit.ai — LLM knowledge-based responses (web search and RAG removed)."""
import re, asyncio, time, hashlib, logging
from typing import Optional, Dict, List
from datetime import datetime, timezone
from deps import db, is_mongo_available
from internal_user_agents import rag_fetch_headers as _rag_fetch_headers
from utils import _extract_keywords

logger = logging.getLogger(__name__)

__all__ = [
    "_HISTORY_MAX_TURNS", "_HISTORY_TOKEN_BUDGET",
    "_LATENCY_MAX", "_RAG_TELEM_MAX",
    "_chat_latencies",
    "_extract_relevant_sections",
    "split_into_sections", "merge_short_sections", "sentence_split_with_overlap",
    "_rag_telemetry", "_record_chat_latency",
    "_record_rag_event", "_sources_from_rag_ctx", "_trim_history",
    "build_rag_system_prompt",
    "get_vector_search_stats", "get_pipeline_stats", "record_pipeline_run",
    "_record_vector_search",
    "resolve_rag_context",
    "auto_chunk_content", "backfill_chunk_embeddings",
    "rag_search", "rechunk_chapter", "vector_rag_search",
    "syrabit_library_search",
    "_fetch_content_card", "_fetch_enrichment_blocks",
    "_fetch_internal_chapters",
    "_embed_and_store_chapter", "_embed_and_store_page", "_embed_cms_document",
    "web_search_with_fallback",
    "_sources_from_web_results",
]


def _record_vector_search(*args, **kwargs):
    pass

def get_vector_search_stats(window_seconds: int = 3600) -> dict:
    return {"total_searches": 0, "has_data": False, "info": "RAG removed — web search only"}

_PIPELINE_RUNS: list = []
_PIPELINE_RUNS_MAX = 500

def record_pipeline_run(action: str, subject: str, success: bool, chapters: int = 0, chunks: int = 0, embeddings: int = 0, duration_ms: float = 0, error: str = ""):
    _PIPELINE_RUNS.append({
        "ts": time.time(),
        "action": action,
        "subject": subject[:100],
        "success": success,
        "chapters": chapters,
        "chunks": chunks,
        "embeddings": embeddings,
        "duration_ms": round(duration_ms, 1),
        "error": error[:200],
    })
    if len(_PIPELINE_RUNS) > _PIPELINE_RUNS_MAX:
        del _PIPELINE_RUNS[:50]

def get_pipeline_stats(window_seconds: int = 86400) -> dict:
    cutoff = time.time() - window_seconds
    recent = [r for r in _PIPELINE_RUNS if r["ts"] >= cutoff]
    if not recent:
        return {"total_runs": 0, "has_data": False}
    successes = sum(1 for r in recent if r["success"])
    return {
        "total_runs": len(recent),
        "successes": successes,
        "failures": len(recent) - successes,
        "success_rate": round(successes / len(recent) * 100, 1),
        "total_chapters": sum(r["chapters"] for r in recent),
        "total_chunks": sum(r["chunks"] for r in recent),
        "total_embeddings": sum(r["embeddings"] for r in recent),
        "recent": recent[-10:],
        "has_data": True,
    }


async def auto_chunk_content(*args, **kwargs) -> list:
    return []

async def rechunk_chapter(*args, **kwargs) -> dict:
    return {"status": "skipped", "reason": "RAG removed"}

async def backfill_chunk_embeddings(*args, **kwargs) -> dict:
    return {"status": "skipped", "reason": "RAG removed"}

async def vector_rag_search(*args, **kwargs) -> list:
    return []

async def rag_search(*args, **kwargs) -> dict:
    return {"chunks": [], "chapters": [], "subjects": [], "source": "none", "quality": "none"}

async def _fetch_content_card(*args, **kwargs):
    return None

async def _fetch_enrichment_blocks(*args, **kwargs) -> str:
    return ""

async def _embed_and_store_chapter(*args, **kwargs):
    return True

async def _embed_and_store_page(*args, **kwargs):
    return True

async def _embed_cms_document(*args, **kwargs):
    return {}


_WEB_SEARCH_CACHE: dict[str, tuple[float, list]] = {}
_WEB_SEARCH_CACHE_TTL = 300
_WEB_SEARCH_CACHE_MAX = 256

_SYRABIT_DOMAINS = {"syrabit.ai", "www.syrabit.ai"}

_EDUCATIONAL_DOMAINS = {
    "ncert.nic.in", "byjus.com", "vedantu.com", "toppr.com",
    "learncbse.in", "geeksforgeeks.org", "shaalaa.com",
    "doubtnut.com", "askiitians.com", "meritnation.com",
    "brainly.in", "javatpoint.com", "studiestoday.com",
    "tutorialspoint.com", "mathsisfun.com", "khanacademy.org",
    "wikipedia.org", "brilliant.org", "unacademy.com",
}


def _build_search_query(
    query: str,
    board_name: str = "",
    class_name: str = "",
    subject_name: str = "",
    chapter_name: str = "",
) -> str:
    parts = [query]
    if board_name:
        b = board_name.strip().upper()
        if b in ("AHSEC", "SEBA", "DEGREE"):
            parts.append(b)
        else:
            parts.append(board_name.strip())
    if class_name:
        parts.append(class_name.strip())
    if subject_name and subject_name.lower() not in query.lower():
        parts.append(subject_name.strip())
    if chapter_name and chapter_name.lower() not in query.lower():
        parts.append(chapter_name.strip())
    return " ".join(parts)


async def _ddg_search(query: str, max_results: int = 5) -> list:
    try:
        from ddgs import DDGS
        def _do_search():
            with DDGS() as ddgs:
                return list(ddgs.text(query, max_results=max_results, region="in-en"))
        results = await asyncio.wait_for(
            asyncio.to_thread(_do_search),
            timeout=1.5,
        )
        return results or []
    except asyncio.TimeoutError:
        logger.warning(f"[WEB_SEARCH] DDG search timed out for: {query[:60]}")
        return []
    except Exception as e:
        logger.warning(f"[WEB_SEARCH] DDG search failed: {e}")
        return []


def _is_safe_scheme(url: str) -> bool:
    """Cheap scheme/host-shape filter.

    The full SSRF rule set (private-IP, hard-deny, DNS-resolved-to-private,
    per-redirect-hop re-checks) lives in
    ``url_safety.validate_host_for_ssrf`` / ``safe_get_with_redirects``
    and is applied in :func:`_fetch_page_content`. This helper only weeds
    out obviously-malformed URLs before we spend cycles on them.
    """
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        return bool(parsed.hostname)
    except Exception:
        return False

_httpx_client = None

def _get_httpx_client():
    """Shared async client for grounded-answer web fetches.

    Uses ``follow_redirects=False`` because we walk the redirect chain
    manually via ``url_safety.safe_get_with_redirects`` to re-validate
    every hop against the SSRF rule set (private-IP, hard-deny, DNS
    resolution). Trusting httpx's auto-follow here would let an upstream
    302 walk us into a private IP address.
    """
    global _httpx_client
    if _httpx_client is None:
        import httpx
        _httpx_client = httpx.AsyncClient(
            timeout=3.0,
            follow_redirects=False,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
    return _httpx_client

async def _fetch_page_content(url: str, max_chars: int = 3000) -> str:
    # Delegate to the shared SSRF guards so this path uses the same
    # rules as `edu_reader.fetch_and_extract` (DNS-to-private rejection
    # on the initial host + per-hop redirect re-checks).
    from url_safety import validate_host_for_ssrf, safe_get_with_redirects
    if not url or not _is_safe_scheme(url):
        return ""
    try:
        from urllib.parse import urlparse as _urlparse
        host = (_urlparse(url).hostname or "").lower()
        host_ok, _why = await validate_host_for_ssrf(host)
        if not host_ok:
            return ""
        client = _get_httpx_client()
        resp, final_url, redirect_reason = await safe_get_with_redirects(
            client, url, headers=_rag_fetch_headers(),
        )
        if redirect_reason != "ok" or resp is None:
            return ""
        # Defensive: re-validate the post-redirect host. `safe_get_with_redirects`
        # already SSRF-checks every hop, but if a future change ever lets a
        # non-redirect response slip through with a different effective host
        # (e.g. via client-side proxy), this catches it.
        if not _is_safe_scheme(final_url):
            return ""
        final_host = (_urlparse(final_url).hostname or "").lower()
        final_ok, _final_why = await validate_host_for_ssrf(final_host)
        if not final_ok:
            return ""
        if resp.status_code != 200:
            return ""
        html = resp.text
        text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<nav[^>]*>.*?</nav>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<footer[^>]*>.*?</footer>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<header[^>]*>.*?</header>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'&[a-zA-Z]+;', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        if len(text) < 50:
            return ""
        return text[:max_chars]
    except Exception:
        return ""


async def web_search_with_fallback(
    query: str,
    *,
    num_results: int = 8,
    board_name: str = "",
    class_name: str = "",
    subject_name: str = "",
    chapter_name: str = "",
    enrich_top_n: int = 2,
) -> list:
    cache_key = hashlib.md5(f"{query}:{board_name}:{class_name}:{subject_name}:{chapter_name}".lower().encode()).hexdigest()
    cached = _WEB_SEARCH_CACHE.get(cache_key)
    if cached:
        ts, results = cached
        if time.time() - ts < _WEB_SEARCH_CACHE_TTL:
            logger.info(f"[WEB_SEARCH] Cache HIT for '{query[:40]}' ({len(results)} results)")
            return results

    t0 = time.perf_counter()
    all_results: list = []
    seen_urls: set = set()

    scoped_query = _build_search_query(query, board_name, class_name, subject_name, chapter_name)

    _search_rag_task = None
    try:
        from llm import call_search_rag_with_dispatch, select_provider
        _search_provider = select_provider("search_rag", lang="en")
        if _search_provider == "exa_ai":
            _search_rag_task = asyncio.ensure_future(
                call_search_rag_with_dispatch(scoped_query, feature="search_rag", lang="en")
            )
    except Exception:
        _search_rag_task = None

    web_raw = await _ddg_search(scoped_query, max_results=num_results)

    if isinstance(web_raw, list):
        edu_results = []
        other_results = []
        for r in web_raw:
            url = r.get("href", r.get("url", ""))
            if url and url not in seen_urls:
                seen_urls.add(url)
                entry = {
                    "title": r.get("title", ""),
                    "snippet": r.get("body", r.get("snippet", "")),
                    "url": url,
                    "_layer": "base",
                }
                try:
                    from urllib.parse import urlparse
                    domain = urlparse(url).netloc.lower().lstrip("www.")
                    if domain in _EDUCATIONAL_DOMAINS:
                        edu_results.append(entry)
                    else:
                        other_results.append(entry)
                except Exception:
                    other_results.append(entry)
        all_results.extend(edu_results)
        all_results.extend(other_results)
    else:
        logger.warning(f"[WEB_SEARCH] Web layer failed: {web_raw}")

    if _search_rag_task is not None:
        try:
            _exa_results = await asyncio.wait_for(_search_rag_task, timeout=2.0)
            for r in _exa_results:
                url = r.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_results.append({
                        "title": r.get("title", ""),
                        "snippet": r.get("text", r.get("snippet", ""))[:400],
                        "url": url,
                        "_layer": "exa",
                    })
        except Exception as _ex_err:
            logger.debug("[WEB_SEARCH] exa search_rag concurrent task non-fatal: %s", _ex_err)

    if not all_results:
        logger.warning(f"[WEB_SEARCH] No results for: {query[:60]}")
        return []

    dur_ms = (time.perf_counter() - t0) * 1000
    logger.info(
        f"[WEB_SEARCH] '{query[:50]}' → {len(all_results)} results "
        f"(syrabit={sum(1 for r in all_results if r.get('_layer')=='syrabit')}, "
        f"edu={sum(1 for r in all_results if r.get('_layer')=='base')}) "
        f"in {dur_ms:.0f}ms"
    )

    if len(_WEB_SEARCH_CACHE) >= _WEB_SEARCH_CACHE_MAX:
        oldest = min(_WEB_SEARCH_CACHE, key=lambda k: _WEB_SEARCH_CACHE[k][0])
        del _WEB_SEARCH_CACHE[oldest]
    _WEB_SEARCH_CACHE[cache_key] = (time.time(), all_results)

    return all_results


async def syrabit_library_search(query: str, *, board_name: str = "", class_name: str = "", subject_name: str = "", limit: int = 10, **kwargs) -> list:
    try:
        results = await web_search_with_fallback(
            query, num_results=limit,
            board_name=board_name, class_name=class_name,
            subject_name=subject_name,
        )
        return [
            {"title": r.get("title", ""), "snippet": r.get("snippet", ""), "url": r.get("url", ""), "source": r.get("_layer", "web")}
            for r in results
        ]
    except Exception as e:
        logger.warning(f"syrabit_library_search web fallback failed: {e}")
        return []


# ─────────────────────────────────────────────
# Public chunking adapter (heading split / short-section merge / sentence
# overlap). The heavy ingestion pipeline that USED to call these was
# retired (see module docstring). The pure helpers are kept as a stable
# public adapter so any future ingestion / re-chunking work has a tested
# entry point and so the chunking quality contract is enforced by CI.
# ─────────────────────────────────────────────

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


def split_into_sections(content: str) -> List[Dict[str, str]]:
    """Split a markdown document on `#`-prefixed headings.

    Returns a list of `{"heading": str, "text": str}` records. Text that
    appears before the first heading is captured as a leading section
    with `heading == ""`. Empty / whitespace-only input returns `[]`.
    """
    if not content or not content.strip():
        return []
    matches = list(_HEADING_RE.finditer(content))
    if not matches:
        return [{"heading": "", "text": content.strip()}]
    sections: List[Dict[str, str]] = []
    leading = content[: matches[0].start()].strip()
    if leading:
        sections.append({"heading": "", "text": leading})
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        body = content[m.end():end].strip()
        sections.append({"heading": m.group(2).strip(), "text": body})
    return sections


def merge_short_sections(
    sections: List[Dict[str, str]],
    target: int = 600,
) -> List[Dict[str, str]]:
    """Coalesce consecutive sections whose combined text is below `target`.

    Sections already at or above `target` chars are kept as-is. Headings
    of merged sections are joined with " / ". Empty input → `[]`.
    """
    if not sections:
        return []
    merged: List[Dict[str, str]] = []
    for sec in sections:
        if merged and len(merged[-1]["text"]) < target:
            prev = merged[-1]
            heading = " / ".join(h for h in (prev["heading"], sec["heading"]) if h)
            prev["heading"] = heading
            prev["text"] = (prev["text"] + "\n\n" + sec["text"]).strip()
        else:
            merged.append({"heading": sec.get("heading", ""), "text": sec.get("text", "")})
    return merged


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def sentence_split_with_overlap(
    text: str,
    target: int = 600,
    max_len: int = 1000,
    overlap: int = 1,
) -> List[str]:
    """Split text into sentence-aligned chunks with N-sentence overlap.

    - Each chunk grows to roughly `target` chars, hard-capped at `max_len`.
    - Successive chunks share the trailing `overlap` sentences of the
      previous chunk so context isn't lost at boundaries.
    - Single-sentence input returns a single chunk.
    - Empty / whitespace input returns `[]`.
    - The loop is bounded (advances by at least one sentence per iteration)
      so pathological `overlap >= chunk_size` configs cannot hang.
    """
    if not text or not text.strip():
        return []
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(text.strip()) if s.strip()]
    if not sentences:
        return []
    if len(sentences) == 1:
        return [sentences[0]]
    chunks: List[str] = []
    i = 0
    n = len(sentences)
    safety = 0
    while i < n and safety < n * 4:
        safety += 1
        cur: List[str] = []
        cur_len = 0
        j = i
        while j < n:
            s = sentences[j]
            extra = (1 if cur else 0) + len(s)
            if cur and cur_len + extra > max_len:
                break
            cur.append(s)
            cur_len += extra
            j += 1
            if cur_len >= target:
                break
        if not cur:
            break
        chunks.append(" ".join(cur))
        if j >= n:
            break
        # Step forward by at least 1 sentence; clamp overlap so we always advance.
        step = max(1, len(cur) - max(0, overlap))
        i += step
    return chunks


def _extract_relevant_sections(document_text: str, query: str, char_limit: int = 3000) -> str:
    keywords = _extract_keywords(query)
    lines = [l.strip() for l in document_text.split('\n') if l.strip()]
    scored = []
    for i, line in enumerate(lines):
        score = sum(1 for kw in keywords if kw in line.lower())
        scored.append((score, i, line))
    scored.sort(key=lambda x: -x[0])
    selected_indices = set()
    for score, idx, _ in scored[:8]:
        if score > 0:
            for j in range(max(0, idx - 1), min(len(lines), idx + 3)):
                selected_indices.add(j)
    if selected_indices:
        relevant = "\n".join(lines[i] for i in sorted(selected_indices))
        return relevant[:char_limit]
    return document_text[:char_limit]


async def _fetch_chunks_semantic(
    query: str,
    limit: int = 10,
    subject_id: Optional[str] = None,
    lang: str = "en",
) -> list:
    """Semantic retrieval driven by PROVIDER_PRIORITY['vector_search'].

    Dispatch order (PROVIDER_PRIORITY['vector_search']):
      pinecone_ai (500) → mongodb_atlas (0, weight-0 fallback) → vertex (2k) → workers_ai (0)

    select_provider("vector_search") draws a weighted provider.  Each provider maps to:
      - pinecone_ai:   Pinecone serverless $vectorSearch (syrabit-ahsec index)
      - mongodb_atlas: Atlas $vectorSearch aggregation (weight-0, only when Pinecone unavailable)
      - vertex:        vertex_services.embed_text + Atlas $vectorSearch (Gemini embed space)
      - workers_ai:    no vector endpoint — returns [] immediately

    Returns chapter dicts in the same shape as the keyword-search path so they
    can be deduplicated and reranked together downstream.
    """
    try:
        from llm import select_provider as _select_vs_provider
        _vs_provider = _select_vs_provider("vector_search", lang=lang)
        logger.debug("[VECTOR_SEARCH] dispatch selected provider=%r for query='%s'", _vs_provider, query[:40])
    except Exception:
        _vs_provider = "pinecone_ai"

    # ── Query embedding ──────────────────────────────────────────────────────
    # Embed using the provider appropriate for the selected vector store.
    # Cohere (1024-dim) is required when Pinecone's syrabit-ahsec index was
    # built with Cohere vectors — do NOT fall back to Pinecone Inference
    # (768-dim), which causes a dimension mismatch.
    try:
        q_vec: Optional[list] = None

        if _vs_provider in ("pinecone_ai", "mongodb_atlas"):
            try:
                from providers.cohere import embed_query as _cohere_embed_query, ENABLED as _cohere_on
                if _cohere_on:
                    q_vec = await asyncio.wait_for(_cohere_embed_query(query), timeout=4.0)
            except Exception as _ce:
                logger.debug("[VECTOR_SEARCH] Cohere query embed failed: %s", _ce)

        elif _vs_provider == "vertex":
            try:
                import vertex_services as _vs
                q_vec = await asyncio.wait_for(
                    _vs.embed_text(query, task_type="RETRIEVAL_QUERY"), timeout=5.0
                )
            except Exception as _ve:
                logger.debug("[VECTOR_SEARCH] Vertex query embed failed: %s", _ve)

        if not q_vec:
            logger.debug("[VECTOR_SEARCH] Query embedding unavailable for provider=%r — skipping semantic", _vs_provider)
            return []

        # ── Primary: Pinecone serverless vector search ────────────────────────
        pc_filter: Optional[dict] = None
        if subject_id:
            pc_filter = {"subject_id": {"$eq": subject_id}}

        raw: list = []

        if _vs_provider == "pinecone_ai":
            try:
                from retrievers.pinecone_vector import PineconeVectorRetriever
                _pc_retriever = PineconeVectorRetriever()
                if _pc_retriever.is_configured():
                    matches = await asyncio.wait_for(
                        _pc_retriever.query(
                            q_vec,
                            top_k=limit,
                            metadata_filter=pc_filter,
                            return_metadata=True,
                        ),
                        timeout=5.0,
                    )
                    raw = [
                        {
                            "chapter_id":    m["metadata"].get("chapter_id", ""),
                            "chapter_title": m["metadata"].get("chapter_title", ""),
                            "subject_id":    m["metadata"].get("subject_id", ""),
                            "_vs_score":     m["score"],
                        }
                        for m in matches
                        if m.get("metadata", {}).get("chapter_id")
                    ]
                    logger.debug("[VECTOR_SEARCH] pinecone_ai: %d matches for '%s'", len(raw), query[:50])
            except Exception as _pc_err:
                logger.debug("[VECTOR_SEARCH] pinecone_ai failed — will try Atlas fallback: %s", _pc_err)

        # ── Fallback: MongoDB Atlas $vectorSearch ─────────────────────────────
        # Runs when: (a) pinecone_ai selected but returned 0 results or failed,
        # (b) mongodb_atlas explicitly selected (weight-0 provider), or
        # (c) vertex selected (embed via Vertex, search via Atlas $vectorSearch).
        import os as _os
        _atlas_fallback_enabled = _os.environ.get(
            "PINECONE_ATLAS_FALLBACK", "true"
        ).strip().lower() not in ("0", "false", "no")

        if not raw and (_vs_provider in ("mongodb_atlas", "vertex") or _atlas_fallback_enabled):
            try:
                vs_filter: dict = {}
                if subject_id:
                    vs_filter = {"subject_id": {"$eq": subject_id}}

                pipeline: list = [
                    {
                        "$vectorSearch": {
                            "index": "vector_index",
                            "path": "embedding",
                            "queryVector": q_vec,
                            "numCandidates": min(limit * 15, 500),
                            "limit": limit,
                            **({"filter": vs_filter} if vs_filter else {}),
                        }
                    },
                    {"$addFields": {"_vs_score": {"$meta": "vectorSearchScore"}}},
                    {"$project": {
                        "_id": 0,
                        "chapter_id": 1,
                        "chapter_title": 1,
                        "subject_id": 1,
                        "_vs_score": 1,
                    }},
                ]
                raw = await db.chunks.aggregate(pipeline).to_list(length=limit)
                if raw:
                    logger.debug(
                        "[INTERNAL_RAG] Atlas $vectorSearch fallback: %d hits for '%s'",
                        len(raw), query[:50],
                    )
            except Exception as _atlas_err:
                logger.debug("[INTERNAL_RAG] Atlas $vectorSearch fallback also failed: %s", _atlas_err)

        if not raw:
            return []

        chapter_ids = list({r["chapter_id"] for r in raw if r.get("chapter_id")})
        if not chapter_ids:
            return []

        ch_docs = await db.chapters.find(
            {"id": {"$in": chapter_ids}, "status": "published"},
            {"_id": 0, "id": 1, "title": 1, "content": 1, "content_as": 1,
             "slug": 1, "subject_id": 1, "description": 1},
        ).to_list(length=len(chapter_ids))
        chapters_map = {ch["id"]: ch for ch in ch_docs}

        seen: set = set()
        result = []
        for r in raw:
            cid = r.get("chapter_id", "")
            if cid in seen or cid not in chapters_map:
                continue
            seen.add(cid)
            result.append(chapters_map[cid])

        logger.debug(
            "[INTERNAL_RAG] Semantic: %d chunk hits → %d unique chapters for '%s'",
            len(raw), len(result), query[:50],
        )
        return result
    except Exception as _se:
        logger.debug("[INTERNAL_RAG] Semantic search failed: %s", _se)
        return []


async def _fetch_internal_chapters(
    query: str,
    subject_id: Optional[str] = None,
    subject_name: Optional[str] = None,
    limit: int = 3,
    max_content_chars: int = 4000,
) -> list:
    """Hybrid keyword + semantic retrieval with Pinecone reranking.

    Pipeline:
      1. Keyword regex search on MongoDB chapters (title + content + content_as)
      2. Semantic vector search on MongoDB chunks via Atlas $vectorSearch  [parallel]
      3. Deduplicate by chapter id
      4. Pinecone bge-reranker-v2-m3 reranks (multilingual, handles Assamese)
      5. Return top-K chapters within char budget
    """
    if not await is_mongo_available():
        return []
    try:
        keywords = _extract_keywords(query)
        if not keywords:
            return []

        filters: dict = {"status": "published"}
        # Use subject_id when explicitly provided.
        # Do NOT filter by subject_name alone — the AHSEC subject stubs (sub1/sub2…)
        # have no content yet; filtering to them returns zero results.
        if subject_id:
            filters["subject_id"] = subject_id

        try:
            from providers.pinecone_ai import ENABLED as _pinecone_enabled
        except Exception:
            _pinecone_enabled = False

        # Fetch more candidates when reranker is available
        fetch_limit = min(limit * 5, 25) if _pinecone_enabled else limit

        # ── 1. Keyword search: title + content + content_as (Assamese field) ──
        regex_pattern = "|".join(re.escape(kw) for kw in keywords[:6])
        filters["$or"] = [
            {"title":      {"$regex": regex_pattern, "$options": "i"}},
            {"content":    {"$regex": regex_pattern, "$options": "i"}},
            {"content_as": {"$regex": regex_pattern, "$options": "i"}},
        ]
        keyword_task = db.chapters.find(
            filters,
            {"_id": 0, "id": 1, "title": 1, "content": 1, "content_as": 1,
             "slug": 1, "subject_id": 1, "description": 1},
        ).limit(fetch_limit).to_list(length=fetch_limit)

        # ── 2. Semantic vector search (parallel with keyword) ─────────────────
        if _pinecone_enabled:
            kw_chapters, sem_chapters = await asyncio.gather(
                keyword_task,
                _fetch_chunks_semantic(query, limit=fetch_limit, subject_id=subject_id),
                return_exceptions=True,
            )
            if isinstance(kw_chapters, Exception):
                logger.debug("[INTERNAL_RAG] Keyword search error: %s", kw_chapters)
                kw_chapters = []
            if isinstance(sem_chapters, Exception):
                logger.debug("[INTERNAL_RAG] Semantic search error: %s", sem_chapters)
                sem_chapters = []
        else:
            kw_chapters = await keyword_task
            sem_chapters = []

        # ── 3. Merge + deduplicate by chapter id ──────────────────────────────
        seen_ids: set = set()
        all_chapters = []
        for ch in (kw_chapters + sem_chapters):
            cid = ch.get("id", "")
            if cid not in seen_ids:
                seen_ids.add(cid)
                all_chapters.append(ch)

        logger.debug(
            "[INTERNAL_RAG] keyword=%d semantic=%d merged=%d for '%s'",
            len(kw_chapters), len(sem_chapters), len(all_chapters), query[:50],
        )

        # ── 4. Build candidate dicts ───────────────────────────────────────────
        candidates = []
        for ch in all_chapters:
            content = (ch.get("content") or ch.get("description") or "").strip()
            if not content or len(content) < 30:
                continue
            content_as = (ch.get("content_as") or "").strip()
            candidates.append({
                "title":      ch.get("title", ""),
                "content":    content,
                "content_as": content_as,
                "slug":       ch.get("slug", ""),
                "subject_id": ch.get("subject_id", ""),
                "type":       "chapter",
            })

        # ── 5. Pinecone reranking (bge-reranker-v2-m3, multilingual) ──────────
        if _pinecone_enabled and len(candidates) > 1:
            try:
                from providers.pinecone_ai import rerank_items as _pc_rerank

                def _rerank_text(c: dict) -> str:
                    # Bilingual scoring: include Assamese content when available so
                    # the multilingual reranker can match Assamese queries.
                    en_part = c["content"][:600]
                    as_part = c["content_as"][:300] if c["content_as"] else ""
                    return f"{c['title']}\n\n{en_part}" + (f"\n\n{as_part}" if as_part else "")

                candidates = await asyncio.wait_for(
                    _pc_rerank(query, candidates, _rerank_text, top_k=limit),
                    timeout=8.0,
                )
                logger.info(
                    "[INTERNAL_RAG] Pinecone reranked → top %d for '%s'",
                    len(candidates), query[:50],
                )
            except Exception as _rr_err:
                logger.debug("[INTERNAL_RAG] Pinecone rerank skipped: %s", _rr_err)
                candidates = candidates[:limit]
        else:
            candidates = candidates[:limit]

        # ── 6. Apply total-char budget ─────────────────────────────────────────
        result = []
        total_chars = 0
        for ch in candidates:
            content = ch["content"]
            if total_chars + len(content) > max_content_chars:
                content = content[:max(500, max_content_chars - total_chars)]
            total_chars += len(content)
            result.append({**ch, "content": content})
            if total_chars >= max_content_chars:
                break

        if result:
            logger.info(
                "[INTERNAL_RAG] Returning %d chapter(s) for '%s' (subject=%s, %d chars)",
                len(result), query[:50], subject_id or "any", total_chars,
            )
        return result
    except Exception as e:
        logger.warning("[INTERNAL_RAG] Chapter fetch failed: %s", e)
        return []


async def resolve_rag_context(
    query: str,
    subject_id: Optional[str] = None,
    subject_name: Optional[str] = None,
    document_text: Optional[str] = None,
    intent: Optional[str] = None,
    db_category: Optional[str] = None,
    pre_syl_match=None,
    topic_metadata: Optional[dict] = None,
    prefetched_chapters: Optional[list] = None,
) -> dict:
    if document_text and document_text.strip():
        relevant = _extract_relevant_sections(document_text, query)
        return {
            "chunks": [], "chapters": [], "subjects": [],
            "document_text": relevant,
            "document_full": document_text[:5000],
            "source": "document", "quality": "tier0",
            "intent": intent or "general",
        }
    if intent not in ("casual", "general") and (subject_id or subject_name):
        internal_chapters = prefetched_chapters if prefetched_chapters is not None else await _fetch_internal_chapters(query, subject_id=subject_id, subject_name=subject_name)
        if internal_chapters:
            return {
                "chunks": internal_chapters, "chapters": internal_chapters, "subjects": [],
                "vector_hits": [], "source": "internal", "quality": "tier1",
                "intent": intent or "notes",
                "_has_internal_content": True,
            }
    return {
        "chunks": [], "chapters": [], "subjects": [],
        "vector_hits": [], "source": "none", "quality": "none",
        "intent": intent or "general",
        "_general_knowledge_fallback": True,
    }


_HISTORY_TOKEN_BUDGET = 1500
_HISTORY_MAX_TURNS = 8


def _trim_history(messages: list, token_budget: int = _HISTORY_TOKEN_BUDGET, max_turns: int = _HISTORY_MAX_TURNS) -> list:
    capped = messages[-(max_turns * 2):]
    while capped:
        total_chars = sum(len(m.get("content", "")) for m in capped)
        if total_chars // 4 <= token_budget:
            break
        capped = capped[2:]
    return capped


def _sources_from_rag_ctx(
    rag_ctx: dict,
    board_slug: str = "",
    class_slug: str = "",
    subject_slug: str = "",
) -> list:
    """Build the inline-citation `sources` list the frontend uses to
    resolve `[PAGE: ...]` markers inside the answer body and the source
    card under the bubble.

    When ``board_slug``/``class_slug``/``subject_slug`` are supplied (the
    routes layer resolves them right before emitting ``syrabit_done``),
    chapter entries get a deep-link URL of the form
    ``/{board}/{class}/{subject}/{chapter_slug}`` so the frontend can
    navigate straight to the source chapter and trigger the topic
    highlight.  Without slugs we still emit a chapter source (slug +
    title) so the markdown lookup can match titles deterministically.
    """
    seen_chapters: set[str] = set()
    seen_subjects: set[str] = set()
    sources: list[dict] = []

    _cc_meta = rag_ctx.get("content_card_meta")
    if _cc_meta and (_cc_meta.get("card_name") or _cc_meta.get("lesson_name")):
        _cc_slug = _cc_meta.get("card_slug", "")
        _cc_url = f"/learn/{_cc_slug}" if _cc_slug and not _cc_slug.startswith("chapter/") else ""
        sources.append({
            "type":         "content_card",
            "card_name":    _cc_meta.get("card_name", ""),
            "lesson_name":  _cc_meta.get("lesson_name", ""),
            "subject_name": _cc_meta.get("subject_name", ""),
            "board_name":   _cc_meta.get("board_name", ""),
            "class_name":   _cc_meta.get("class_name", ""),
            "slug":         _cc_slug,
            "title":        _cc_meta.get("card_name", "") or _cc_meta.get("lesson_name", ""),
            "url":          _cc_url,
        })

    # Internal chapters retrieved by RAG (the dominant source for
    # library/cache answers).  These show up under both ``chapters`` and
    # ``chunks`` depending on the retrieval path; dedup by slug.
    _chap_base = ""
    if board_slug and class_slug and subject_slug:
        _chap_base = f"/{board_slug}/{class_slug}/{subject_slug}"
    for ch in (rag_ctx.get("chapters") or rag_ctx.get("chunks") or []):
        if not isinstance(ch, dict):
            continue
        c_slug = (ch.get("slug") or "").strip()
        c_title = (ch.get("title") or "").strip()
        if not c_slug and not c_title:
            continue
        key = c_slug or c_title.lower()
        if key in seen_chapters:
            continue
        seen_chapters.add(key)
        url = f"{_chap_base}/{c_slug}" if (_chap_base and c_slug) else ""
        sources.append({
            "type":  "chapter",
            "slug":  c_slug,
            "title": c_title,
            "url":   url,
        })

    for subj in rag_ctx.get("subjects", []):
        slug = subj.get("slug", "")
        if slug and slug not in seen_subjects:
            seen_subjects.add(slug)
            url = subj.get("url", "")
            if not url and _chap_base and slug == subject_slug:
                url = _chap_base
            sources.append({"slug": slug, "title": subj.get("name", ""), "url": url})

    return sources


def _sources_from_web_results(web_results: list) -> list:
    sources = []
    seen = set()
    for r in (web_results or []):
        url = r.get("url", "")
        if url and url not in seen:
            seen.add(url)
            sources.append({
                "type": "web",
                "title": r.get("title", ""),
                "url": url,
                "layer": r.get("_layer", "web"),
            })
    return sources[:6]


def build_rag_system_prompt(
    context: dict,
    rag_context: dict,
    user_info: dict = None,
    query: str = "",
    syllabus: dict = None,
    web_results: list = None,
    resolved_intent: str = "",
    response_lang: str = "",
) -> str:
    from prompts import build_system_prompt, classify_intent, _format_board_label as _fbl, get_intent_extraction_rules
    base_prompt = build_system_prompt(
        context, user_info=user_info, query=query,
        resolved_intent=resolved_intent, response_lang=response_lang,
    )
    source = rag_context.get("source", "none")

    _intent = resolved_intent if resolved_intent else (classify_intent(query)[0] if query else "notes")
    _is_casual = _intent in ("casual", "general")

    _board_raw = (context.get("board_name", "") or "").strip().upper()
    _board_label = _fbl(_board_raw) if _board_raw else "AssamBoard"
    _curriculum_label = f"{_board_label} Curriculum"

    _content_intents = {"notes", "important_questions", "pyq"}

    base_prompt += (
        "\n\nSPEED RULE: Be extremely concise. "
        "Use bullet points, not paragraphs. 50-100 words default. "
        "No filler, no introductions, no 'Great question!'. Start with the answer directly."
    )

    if _intent in _content_intents:
        base_prompt += (
            "\n\nCONTENT RULE: Answer from your training knowledge. "
            "Be accurate and curriculum-aligned. Accuracy over completeness."
        )
    if _intent == "pyq":
        base_prompt += (
            "\n\nPYQ RULE: The student is asking for previous year questions. "
            "Generate likely exam questions based on your knowledge of the curriculum and common exam patterns. "
            "Present them with realistic marks allocation."
        )

    if not _is_casual:
        _src_subject = context.get("subject_name", "")
        _src_course  = (context.get("stream_name", "") or "").strip()
        _src_board   = _board_label or "AssamBoard"
        _source_parts = []
        if _src_subject:
            _source_parts.append(f"{_src_subject} (subject name)")
        if _src_course:
            _source_parts.append(f"{_src_course} (course name)")
        _source_parts.append(f"{_src_board} (board name)")
        _source_line = " · ".join(_source_parts)
        base_prompt += (
            f"\n\nSOURCE: At the very end of your response, add on its own line:\n"
            f"SOURCE : {_source_line}"
        )

    grounding = ""

    if syllabus and syllabus.get("content") and not _is_casual:
        syllabus_content = syllabus.get("content", "")
        syllabus_topics = ", ".join(syllabus.get("topics", [])[:10])
        grounding = (
            "\n\n---\n"
            f"**CURRICULUM ({_curriculum_label}):**\n"
            f"The student's curriculum covers the following topics. "
            f"Keep your answer within this academic scope:\n\n"
            f"{syllabus_content}\n\n"
        )
        if syllabus_topics:
            grounding += f"**Key topics:** {syllabus_topics}\n\n"
        grounding += "---\n"

    _syl_chapters_list = rag_context.get("_syllabus_chapters", [])
    if _syl_chapters_list:
        _subject_name_for_syl = context.get("subject_name", "")
        grounding += (
            "\n\n---\n"
            f"**SUBJECT CHAPTERS ({_subject_name_for_syl}):**\n"
            "The following is the EXACT chapter list from the database. "
            "Display these chapters EXACTLY as shown — do NOT rename, split, merge, or reformat them.\n\n"
        )
        for _idx, _ch in enumerate(_syl_chapters_list, 1):
            _ch_title = _ch.get("title", f"Chapter {_idx}")
            _ch_desc = _ch.get("description", "")
            grounding += f"**{_ch_title}**"
            if _ch_desc:
                grounding += f" — {_ch_desc}"
            grounding += "\n"
        grounding += "\n---\n"

    def _cap_and_return(bp: str, gr: str) -> str:
        _CAP = 10_000
        total = len(bp) + len(gr)
        if total > _CAP:
            budget = _CAP - len(bp)
            if budget > 500:
                gr = gr[:budget]
                logger.info(f"[PROMPT] Grounding trimmed to {budget} chars (source={source})")
            else:
                bp = bp[:_CAP]
                gr = ""
                logger.info(f"[PROMPT] Base prompt capped at {_CAP} chars (source={source})")
        return bp + gr

    if source == "document":
        document_text = rag_context.get("document_text", "")
        if document_text:
            doc_budget = max(2000, 8000 - len(grounding))
            grounding += (
                "\n\n---\n"
                "**GROUNDING CONTEXT (Uploaded Study Document):**\n"
                "The student is asking about content from a specific uploaded study document. "
                "Base your answer **exclusively** on this document. Quote directly when possible.\n\n"
                "**Document content:**\n"
                f"{document_text[:doc_budget]}\n\n"
                "---\n"
                "*INSTRUCTION: Answer ONLY from the document above. "
                "If the question cannot be answered from this document, say so clearly "
                "and offer to answer from general knowledge instead.*"
            )
            return _cap_and_return(base_prompt, grounding)

    if source == "library":
        document_text = rag_context.get("document_text", "")
        if document_text:
            lib_budget = max(2000, 8000 - len(grounding))
            grounding += (
                "\n\n---\n"
                "**GROUNDING CONTEXT (Subject Library Context):**\n"
                "The student opened AI chat from a specific subject in the Syrabit library. "
                "Use this syllabus and chapter context to give accurate, curriculum-aligned answers.\n\n"
                "**Subject & syllabus:**\n"
                f"{document_text[:lib_budget]}\n\n"
                "---\n"
            )
            return _cap_and_return(base_prompt, grounding)

    if source == "internal":
        _internal_chapters = rag_context.get("chapters", [])
        if _internal_chapters:
            _int_budget = max(2000, 8000 - len(grounding))
            grounding += (
                "\n\n---\n"
                "**REFERENCE MATERIAL (Internal Chapter Content):**\n"
                "The following chapter excerpts are from the student's curriculum. "
                "Use them as your primary factual base. Supplement with your knowledge for explanations.\n\n"
            )
            _int_chars = 0
            for _ic in _internal_chapters:
                _ic_title = _ic.get("title", "")
                _ic_content = _ic.get("content", "")
                if _ic_title:
                    grounding += f"**{_ic_title}:**\n"
                if _ic_content and _int_chars < _int_budget:
                    _remaining = _int_budget - _int_chars
                    grounding += f"{_ic_content[:_remaining]}\n\n"
                    _int_chars += min(len(_ic_content), _remaining)
            grounding += "---\n"
            return _cap_and_return(base_prompt, grounding)

    _extraction_rules = get_intent_extraction_rules(_intent)
    if _extraction_rules and not _is_casual:
        base_prompt += f"\n\n{_extraction_rules}"

    if web_results:
        syrabit_results = [r for r in web_results if r.get("_layer") == "syrabit"]
        base_results   = [r for r in web_results if r.get("_layer") == "base"]
        polish_results = [r for r in web_results if r.get("_layer") == "polish"]

        web_block = "\n\n---\n"
        web_block += (
            "**WEB SEARCH RESULTS — PRIMARY SOURCE:**\n"
            "Use the following web search results as your primary factual base to answer the student's question. "
            "Supplement with your own knowledge for deeper explanations and examples.\n\n"
        )

        _idx = 1
        for r in syrabit_results:
            title   = r.get("title", "")
            url     = r.get("url", "")
            content = r.get("full_content") or r.get("snippet", "")
            _tag = "[Full Content]" if r.get("_enriched") else "[Snippet]"
            web_block += f"[Syrabit {_idx}] {_tag} {title}\n{content}\nSource: {url}\n\n"
            _idx += 1
        for r in base_results + polish_results:
            title   = r.get("title", "")
            url     = r.get("url", "")
            content = r.get("full_content") or r.get("snippet", "")
            _tag = "[Full Content]" if r.get("_enriched") else "[Snippet]"
            web_block += f"[Web {_idx}] {_tag} {title}\n{content}\nSource: {url}\n\n"
            _idx += 1

        web_block += "---\n"
        grounding += web_block
    elif not _is_casual:
        _s1_subject_hint = rag_context.get("_stage1_subject", "")
        if _s1_subject_hint:
            grounding += (
                f"\n\nThe student appears to be asking about {_s1_subject_hint}. "
                f"Answer from your general knowledge about this subject. "
                f"Be accurate, educational, and helpful."
            )

    _PROMPT_CAP = 10_000
    if grounding:
        total_len = len(base_prompt) + len(grounding)
        if total_len > _PROMPT_CAP:
            grounding_budget = _PROMPT_CAP - len(base_prompt)
            if grounding_budget > 500:
                grounding = grounding[:grounding_budget]
                logger.info(f"[PROMPT] Grounding trimmed to {grounding_budget} chars (base_prompt={len(base_prompt)} chars)")
            else:
                grounding = ""
                base_prompt = base_prompt[:_PROMPT_CAP]
                logger.info(f"[PROMPT] Base prompt too large, capped at {_PROMPT_CAP} chars")
        return base_prompt + grounding
    elif len(base_prompt) > _PROMPT_CAP:
        base_prompt = base_prompt[:_PROMPT_CAP]
        logger.info(f"[PROMPT] Base prompt capped at {_PROMPT_CAP} chars")
    return base_prompt


_rag_telemetry: list = []
_RAG_TELEM_MAX = 20_000
_chat_latencies: list = []
_LATENCY_MAX = 10_000

def _record_rag_event(quality: str, latency_ms: float, query: str = "", intent: str = ""):
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "quality": quality,
        "latency_ms": round(latency_ms, 1),
        "query": query[:200],
    }
    if intent:
        event["intent"] = intent
    _rag_telemetry.append(event)
    if len(_rag_telemetry) > _RAG_TELEM_MAX:
        _rag_telemetry.pop(0)

def _record_chat_latency(latency_ms: float):
    _chat_latencies.append({
        "ts": datetime.now(timezone.utc).isoformat(),
        "latency_ms": round(latency_ms, 1),
    })
    if len(_chat_latencies) > _LATENCY_MAX:
        _chat_latencies.pop(0)
