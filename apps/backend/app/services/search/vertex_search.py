"""
VertexSearchService - Vertex AI Search (Discovery Engine) for RAG retrieval.

Provides hybrid search with neural reranking via Google Cloud Discovery Engine.
Replaces the previous Azure Cognitive Search implementation.
"""

import asyncio
import hashlib
import json
import logging
import os
from typing import Optional

from app.config import settings
from app.core.circuit_breaker import vertex_search_circuit_breaker

logger = logging.getLogger(__name__)

VALID_USER_TIERS = {"free", "pro"}


class VertexSearchService:
    """
    Vertex AI Search (Discovery Engine) Service - Hybrid Search with Ranking.

    Features:
    - Uses Discovery Engine SearchServiceClient for retrieval
    - Graceful degradation when service is unavailable
    - Circuit breaker integration
    - Redis caching for repeat queries
    - Same return format as previous search service for compatibility
    """

    def __init__(self):
        self._client = None
        self._serving_config: Optional[str] = None
        self._initialized = False

        if (
            settings.VERTEX_PROJECT_ID
            and settings.VERTEX_SEARCH_DATASTORE_ID
            and (settings.GOOGLE_APPLICATION_CREDENTIALS_JSON or settings.GOOGLE_APPLICATION_CREDENTIALS or os.environ.get('K_SERVICE'))
        ):
            try:
                self._init_client()
            except Exception as e:
                logger.warning(f"Failed to initialize Vertex Search client: {e}")
        else:
            logger.warning(
                "Vertex AI Search not configured - RAG search will return empty results"
            )

    def is_available(self) -> bool:
        """Public check for whether the search service is initialized and ready."""
        return self._initialized

    def _init_client(self):
        """Initialize the Discovery Engine SearchServiceClient."""
        from google.cloud import discoveryengine_v1
        from google.oauth2 import service_account

        creds_info = settings.google_credentials
        if creds_info:
            credentials = service_account.Credentials.from_service_account_info(
                creds_info,
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
        elif os.environ.get('K_SERVICE'):
            # Running on Cloud Run with Workload Identity - use ADC
            import google.auth
            credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
        else:
            raise RuntimeError("No credentials available for Vertex Search client")

        self._client = discoveryengine_v1.SearchServiceClient(
            credentials=credentials,
        )

        # Build the serving config resource name
        self._serving_config = self._client.serving_config_path(
            project=settings.VERTEX_PROJECT_ID,
            location=settings.VERTEX_SEARCH_LOCATION,
            data_store=settings.VERTEX_SEARCH_DATASTORE_ID,
            serving_config=settings.VERTEX_SEARCH_SERVING_CONFIG,
        )
        self._initialized = True

    def _extract_score(self, result, content: str, index: int) -> float:
        """Extract relevance score from a Discovery Engine result.

        Uses model_scores when available, falls back to a heuristic based on
        content length.
        """
        score = None
        if hasattr(result, "model_scores") and result.model_scores:
            try:
                for key, score_val in result.model_scores.items():
                    if hasattr(score_val, "values") and score_val.values:
                        score = float(score_val.values[0])
                        break
            except (IndexError, TypeError, ValueError):
                pass

        if score is None:
            # Heuristic fallback based on content quality
            if len(content) > 50:
                score = 0.65
            else:
                score = 0.3
            logger.debug(
                f"Using heuristic score for result {index}: content_len={len(content)}, score={score}"
            )

        return score

    async def warm_up(self) -> None:
        """Warm up the search client connection during startup."""
        if not self._initialized:
            return
        try:
            # Minimal query to verify connectivity
            await asyncio.to_thread(self._execute_search, "*", None, 1)
            logger.info("Vertex Search warm-up successful")
        except Exception as e:
            logger.warning(f"Search warm-up failed (non-fatal): {e}")

    def _execute_search(
        self, query: str, filter_expr: Optional[str], limit: int
    ) -> list:
        """Execute a synchronous search call (to be run in executor)."""
        from google.cloud import discoveryengine_v1

        content_search_spec = discoveryengine_v1.SearchRequest.ContentSearchSpec(
            snippet_spec=discoveryengine_v1.SearchRequest.ContentSearchSpec.SnippetSpec(
                return_snippet=True,
            ),
            extractive_content_spec=discoveryengine_v1.SearchRequest.ContentSearchSpec.ExtractiveContentSpec(
                max_extractive_answer_count=1,
            ),
        )

        request = discoveryengine_v1.SearchRequest(
            serving_config=self._serving_config,
            query=query,
            page_size=limit,
            content_search_spec=content_search_spec,
        )

        if filter_expr:
            request.filter = filter_expr

        response = self._client.search(request=request)
        # Collect results from the pager
        results = []
        for result in response.results:
            results.append(result)
            if len(results) >= limit:
                break
        return results

    async def search_context(
        self, query: str, text: str, user_tier: str, limit: int = 5
    ) -> list[dict]:
        """
        Executes search via Vertex AI Search (Discovery Engine).

        Args:
            query: User's search query text
            text: Sanitized text for search (Discovery Engine handles embedding)
            user_tier: 'free' or 'pro' for content filtering
            limit: Number of results to return (default: 5)

        Returns:
            List of context chunks: [{id, title, content, score, reranker_score, url}]
        """
        if not self._initialized:
            logger.warning(
                "Vertex Search client not initialized - returning empty context"
            )
            return []

        # Redis cache - try cache first
        cache_key = None
        if settings.SEARCH_CACHE_ENABLED:
            try:
                from app.db.redis import get_redis

                cache_input = f"{query}:{text}:{user_tier}:{limit}"
                cache_key = (
                    f"search_cache:{hashlib.sha256(cache_input.encode()).hexdigest()}"
                )
                redis = get_redis()
                cached = await redis.get(cache_key)
                if cached:
                    logger.info(f"Search cache HIT for query '{query[:20]}...'")
                    return json.loads(cached)
            except (RuntimeError, Exception) as e:
                logger.debug(f"Search cache unavailable: {e}")

        try:
            # Circuit breaker check
            if not vertex_search_circuit_breaker.allow_request():
                logger.warning("Circuit breaker OPEN - returning empty results")
                return []

            # Validate user_tier
            if user_tier and user_tier not in VALID_USER_TIERS:
                logger.warning(f"Invalid user_tier value rejected: {user_tier}")
                filter_expr = None
            else:
                filter_expr = f'tier_access = "{user_tier}"' if user_tier else None

            # Execute search in thread pool (client is synchronous)
            try:
                results = await asyncio.wait_for(
                    asyncio.to_thread(self._execute_search, query, filter_expr, limit),
                    timeout=10.0,
                )
                logger.info(
                    f"Vertex Search returned {len(results)} results "
                    f"for query '{query[:20]}...'"
                )
            except asyncio.TimeoutError:
                logger.error(
                    f"Vertex Search timed out after 10s for query '{query[:20]}...'"
                )
                return []

            context_chunks = []
            for i, result in enumerate(results):
                doc_data = result.document
                struct_data = {}
                if doc_data.struct_data:
                    struct_data = dict(doc_data.struct_data)

                # Extract content from extractive answers or struct_data
                content = ""
                if (
                    hasattr(result, "document")
                    and hasattr(result.document, "derived_struct_data")
                    and result.document.derived_struct_data
                ):
                    derived = dict(result.document.derived_struct_data)
                    extractive_answers = derived.get("extractive_answers", [])
                    if extractive_answers:
                        content = extractive_answers[0].get("content", "")
                    if not content:
                        snippets = derived.get("snippets", [])
                        if snippets:
                            content = snippets[0].get("snippet", "")

                if not content:
                    content = struct_data.get("content", "")

                score = self._extract_score(result, content, i)

                chunk = {
                    "id": doc_data.id or f"result_{i}",
                    "title": struct_data.get("title", ""),
                    "content": content,
                    "score": round(score, 2),
                    "reranker_score": round(score, 2),
                    "url": struct_data.get("source_url", ""),
                }
                context_chunks.append(chunk)

            # Fallback for empty tier-filtered results
            if not context_chunks and user_tier:
                logger.warning(
                    f"No results with tier filter '{user_tier}', retrying without filter"
                )
                try:
                    fallback_results = await asyncio.wait_for(
                        asyncio.to_thread(self._execute_search, query, None, limit),
                        timeout=10.0,
                    )
                    for i, result in enumerate(fallback_results):
                        doc_data = result.document
                        struct_data = {}
                        if doc_data.struct_data:
                            struct_data = dict(doc_data.struct_data)

                        content = ""
                        if (
                            hasattr(result.document, "derived_struct_data")
                            and result.document.derived_struct_data
                        ):
                            derived = dict(result.document.derived_struct_data)
                            extractive_answers = derived.get("extractive_answers", [])
                            if extractive_answers:
                                content = extractive_answers[0].get("content", "")
                            if not content:
                                snippets = derived.get("snippets", [])
                                if snippets:
                                    content = snippets[0].get("snippet", "")

                        if not content:
                            content = struct_data.get("content", "")

                        score = self._extract_score(result, content, i)

                        chunk = {
                            "id": doc_data.id or f"result_{i}",
                            "title": struct_data.get("title", ""),
                            "content": content,
                            "score": round(score, 2),
                            "reranker_score": round(score, 2),
                            "url": struct_data.get("source_url", ""),
                            "unfiltered": True,
                        }
                        context_chunks.append(chunk)

                    if context_chunks:
                        logger.info(
                            f"Found {len(context_chunks)} results without tier filter"
                        )
                except asyncio.TimeoutError:
                    logger.error("Vertex Search fallback timed out")
                except Exception as e:
                    logger.error(f"Vertex Search fallback failed: {e}")

            logger.info(
                f"Retrieved {len(context_chunks)} chunks for query '{query[:20]}...'"
            )

            # Cache the result
            if settings.SEARCH_CACHE_ENABLED and cache_key and context_chunks:
                try:
                    from app.db.redis import get_redis

                    redis = get_redis()
                    await redis.set(cache_key, json.dumps(context_chunks), ex=300)
                except (RuntimeError, Exception) as e:
                    logger.debug(f"Failed to cache search results: {e}")

            vertex_search_circuit_breaker.record_success()
            return context_chunks

        except Exception as e:
            vertex_search_circuit_breaker.record_failure()
            logger.error(f"Vertex Search failed completely: {str(e)}")
            logger.warning("search_context returned empty due to error")
            return []


# Singleton instance
search_service = VertexSearchService()
