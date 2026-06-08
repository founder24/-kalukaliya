"""
/api/v1/search/compare — Side-by-side benchmark: MongoDB vector search vs Vertex AI Search.

POST /api/v1/search/compare
Body: { "query": "...", "lang": "en"|"as", "limit": 5 }

Runs both retrieval paths in parallel and returns:
  - results from each source
  - per-source latency
  - a simple quality comparison (chunk count, top score, avg score)
"""

import asyncio
import time
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/search", tags=["search"])


class CompareRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    lang: str = Field("en", pattern="^(en|as)$")
    limit: int = Field(5, ge=1, le=10)


class SourceResult(BaseModel):
    source: str
    latency_ms: float
    chunk_count: int
    top_score: float
    avg_score: float
    chunks: list[dict]
    error: Optional[str] = None


class CompareResponse(BaseModel):
    query: str
    lang: str
    total_latency_ms: float
    embedding_latency_ms: float
    vertex: SourceResult
    mongodb: SourceResult
    winner: str
    notes: list[str]


@router.post("/compare", response_model=CompareResponse)
async def compare_search(req: CompareRequest):
    """
    Run MongoDB vector search and Vertex AI Search in parallel for the same query.
    Returns timing, chunk quality, and a recommended winner.
    """
    t_total = time.time()

    # ── 1. Generate shared embedding (used by MongoDB path; Vertex uses raw text) ──
    t_embed = time.time()
    query_embedding: Optional[list[float]] = None
    embed_latency_ms = 0.0
    embed_error: Optional[str] = None

    try:
        from app.services.ai.embedder import generate_embedding_vector
        query_embedding = await asyncio.wait_for(
            generate_embedding_vector(req.query), timeout=4.0
        )
        embed_latency_ms = (time.time() - t_embed) * 1000
    except asyncio.TimeoutError:
        embed_error = "embedding timed out (4s)"
        embed_latency_ms = 4000.0
    except Exception as e:
        embed_error = f"embedding failed: {e}"
        embed_latency_ms = (time.time() - t_embed) * 1000

    # ── 2. Run both search paths in parallel ────────────────────────────────────
    vertex_result = SourceResult(
        source="vertex", latency_ms=0, chunk_count=0,
        top_score=0, avg_score=0, chunks=[]
    )
    mongo_result = SourceResult(
        source="mongodb_vector", latency_ms=0, chunk_count=0,
        top_score=0, avg_score=0, chunks=[]
    )

    async def run_vertex():
        from app.services.search.vertex_search import search_service
        t0 = time.time()
        try:
            if not search_service.is_available():
                return SourceResult(
                    source="vertex", latency_ms=0, chunk_count=0,
                    top_score=0, avg_score=0, chunks=[],
                    error="Vertex Search not configured (VERTEX_SEARCH_DATASTORE_ID missing)"
                )
            chunks = await asyncio.wait_for(
                search_service.search_context(
                    query=req.query, text=req.query,
                    user_tier="free", limit=req.limit
                ),
                timeout=6.0
            )
            ms = (time.time() - t0) * 1000
            scores = [c.get("score", 0) for c in chunks]
            return SourceResult(
                source="vertex",
                latency_ms=round(ms, 1),
                chunk_count=len(chunks),
                top_score=round(max(scores, default=0), 3),
                avg_score=round(sum(scores) / len(scores) if scores else 0, 3),
                chunks=chunks,
            )
        except asyncio.TimeoutError:
            ms = (time.time() - t0) * 1000
            return SourceResult(
                source="vertex", latency_ms=round(ms, 1), chunk_count=0,
                top_score=0, avg_score=0, chunks=[],
                error="Vertex Search timed out (6s)"
            )
        except Exception as e:
            ms = (time.time() - t0) * 1000
            return SourceResult(
                source="vertex", latency_ms=round(ms, 1), chunk_count=0,
                top_score=0, avg_score=0, chunks=[],
                error=str(e)[:200]
            )

    async def run_mongo():
        from app.services.search.mongo_vector_search import mongo_vector_search
        t0 = time.time()
        try:
            if embed_error:
                return SourceResult(
                    source="mongodb_vector", latency_ms=0, chunk_count=0,
                    top_score=0, avg_score=0, chunks=[],
                    error=f"Skipped — {embed_error}"
                )
            chunks = await asyncio.wait_for(
                mongo_vector_search.search_with_embedding(
                    query_embedding, lang=req.lang, limit=req.limit
                ),
                timeout=4.0
            )
            ms = (time.time() - t0) * 1000
            scores = [c.get("score", 0) for c in chunks]
            return SourceResult(
                source="mongodb_vector",
                latency_ms=round(ms, 1),
                chunk_count=len(chunks),
                top_score=round(max(scores, default=0), 3),
                avg_score=round(sum(scores) / len(scores) if scores else 0, 3),
                chunks=chunks,
            )
        except asyncio.TimeoutError:
            ms = (time.time() - t0) * 1000
            return SourceResult(
                source="mongodb_vector", latency_ms=round(ms, 1), chunk_count=0,
                top_score=0, avg_score=0, chunks=[],
                error="MongoDB vector search timed out (4s)"
            )
        except Exception as e:
            ms = (time.time() - t0) * 1000
            return SourceResult(
                source="mongodb_vector", latency_ms=round(ms, 1), chunk_count=0,
                top_score=0, avg_score=0, chunks=[],
                error=str(e)[:200]
            )

    vertex_result, mongo_result = await asyncio.gather(run_vertex(), run_mongo())

    # ── 3. Determine winner and notes ───────────────────────────────────────────
    notes: list[str] = []

    if embed_error:
        notes.append(f"Embedding: {embed_error}")
    else:
        notes.append(f"Shared embedding latency: {embed_latency_ms:.0f}ms")

    if vertex_result.error:
        notes.append(f"Vertex error: {vertex_result.error}")
    if mongo_result.error:
        notes.append(f"MongoDB error: {mongo_result.error}")

    # Two latency perspectives:
    #
    # Standalone: MongoDB cost = embed (1.8s, Vertex REST) + chapter fetch (~120ms)
    #             Vertex cost  = search only (~220ms, embedding is internal to Vertex)
    # In-pipeline: The chat pipeline already generates an embedding for topic
    #              matching.  Reusing it means MongoDB cost = chapter fetch only.
    #              This makes MongoDB 2-10x faster than Vertex in that context.
    vertex_latency_ms = vertex_result.latency_ms
    mongo_standalone_ms = embed_latency_ms + mongo_result.latency_ms
    mongo_pipeline_ms = mongo_result.latency_ms  # embedding already computed

    notes.append(
        f"Standalone latency — Vertex: {vertex_latency_ms:.0f}ms | "
        f"MongoDB: {mongo_standalone_ms:.0f}ms (embed+fetch)"
    )
    notes.append(
        f"In-pipeline latency (embed reused) — Vertex: {vertex_latency_ms:.0f}ms | "
        f"MongoDB: {mongo_pipeline_ms:.0f}ms (fetch only)"
    )

    if vertex_result.chunk_count == 0 and mongo_result.chunk_count > 0:
        winner = "mongodb_vector"
        notes.append("MongoDB wins: Vertex returned no chunks")
    elif mongo_result.chunk_count == 0 and vertex_result.chunk_count > 0:
        winner = "vertex"
        notes.append("Vertex wins: MongoDB returned no chunks")
    elif vertex_result.chunk_count == 0 and mongo_result.chunk_count == 0:
        winner = "none"
        notes.append("No results from either source")
    else:
        # Winner judged on the in-pipeline scenario (embedding is a shared sunk cost).
        # Score quality × chunk count per ms of incremental retrieval time.
        vertex_value = (vertex_result.top_score * vertex_result.chunk_count) / max(vertex_latency_ms, 1)
        mongo_value = (mongo_result.top_score * mongo_result.chunk_count) / max(mongo_pipeline_ms, 1)

        if mongo_value >= vertex_value * 0.85:
            winner = "mongodb_vector"
            notes.append(
                f"MongoDB wins in-pipeline: {mongo_pipeline_ms:.0f}ms fetch, "
                f"score {mongo_result.top_score} vs Vertex {vertex_result.top_score} in {vertex_latency_ms:.0f}ms"
            )
        else:
            winner = "vertex"
            notes.append(
                f"Vertex wins: better score/latency ratio "
                f"(score {vertex_result.top_score} in {vertex_latency_ms:.0f}ms)"
            )

    total_ms = (time.time() - t_total) * 1000

    logger.info(
        f"search_compare: query='{req.query[:40]}' lang={req.lang} "
        f"vertex={vertex_result.latency_ms}ms/{vertex_result.chunk_count}chunks "
        f"mongo={mongo_result.latency_ms}ms/{mongo_result.chunk_count}chunks "
        f"winner={winner}"
    )

    return CompareResponse(
        query=req.query,
        lang=req.lang,
        total_latency_ms=round(total_ms, 1),
        embedding_latency_ms=round(embed_latency_ms, 1),
        vertex=vertex_result,
        mongodb=mongo_result,
        winner=winner,
        notes=notes,
    )
