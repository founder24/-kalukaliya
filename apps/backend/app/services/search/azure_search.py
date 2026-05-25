from azure.search.documents.aio import SearchClient
from azure.search.documents.models import (
    VectorizableTextQuery,
    QueryType,
    QueryCaptionType,
    QueryAnswerType,
)
from azure.core.exceptions import AzureError
from azure.core.credentials import AzureKeyCredential
from app.config import settings
import hashlib
import json
import logging

logger = logging.getLogger(__name__)


class AzureSearchService:
    """
    Azure Cognitive Search Service - Hybrid Search with Semantic Reranking
    Provides BM25 + Vector search with neural reranking for optimal RAG quality

    Features:
    - Native async client (no thread pool executor)
    - Graceful degradation to vector-only if semantic ranker fails
    - Circuit breaker integration
    - Detailed error logging
    """

    def __init__(self):
        self.client = None
        if settings.AZURE_SEARCH_ENDPOINT and settings.AZURE_SEARCH_QUERY_KEY:
            self.client = SearchClient(
                endpoint=settings.AZURE_SEARCH_ENDPOINT,
                index_name=settings.AZURE_SEARCH_INDEX_NAME,
                credential=AzureKeyCredential(settings.AZURE_SEARCH_QUERY_KEY),
            )
        else:
            logger.warning(
                "Azure Search not configured - RAG search will return empty results"
            )

    async def _async_search(
        self,
        query: str,
        vector_query,
        user_tier: str | None,
        limit: int,
        semantic: bool,
    ):
        """Async search using the native async client."""
        filter_expr = f"tier_access eq '{user_tier}'" if user_tier else None
        if semantic:
            kwargs = {
                "search_text": query,
                "vector_queries": [vector_query],
                "query_type": QueryType.SEMANTIC,
                "semantic_configuration_name": settings.AZURE_SEARCH_SEMANTIC_CONFIG,
                "query_caption": QueryCaptionType.EXTRACTIVE,
                "query_answer": QueryAnswerType.EXTRACTIVE,
                "top": limit,
            }
            if filter_expr:
                kwargs["filter"] = filter_expr
            results = self.client.search(**kwargs)
        else:
            kwargs = {
                "search_text": query,
                "vector_queries": [vector_query],
                "query_type": QueryType.VECTOR,
                "top": limit,
            }
            if filter_expr:
                kwargs["filter"] = filter_expr
            results = self.client.search(**kwargs)
        return [doc async for doc in results]

    async def search_context(
        self, query: str, text: str, user_tier: str, limit: int = 5
    ):
        """
        Executes Hybrid Search (Keyword + Vector) with Semantic Reranking.
        Falls back to vector-only search if semantic ranker is unavailable.

        Args:
            query: User's search query text
            text: Text to vectorize (Azure Search handles embedding via VectorizableTextQuery)
            user_tier: 'free' or 'pro' for content filtering
            limit: Number of results to return (default: 5)

        Returns:
            List of context chunks with scores and metadata
        """
        if not self.client:
            logger.warning(
                "Azure Search client not initialized - returning empty context"
            )
            return []

        # Redis cache - try cache first
        cache_key = None
        try:
            from app.db.redis import get_redis

            cache_input = f"{query}:{text}:{user_tier}"
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
            # 1. Define Vector Query using built-in vectorization
            vector_query = VectorizableTextQuery(
                text=text,
                k_nearest_neighbors=50,
                fields="content_vector",
            )

            # 2. Execute Hybrid Search with Semantic Reranking (native async)
            try:
                results = await self._async_search(
                    query, vector_query, user_tier, limit, True
                )
                logger.info(f"Using SEMANTIC search for query '{query[:20]}...'")
            except AzureError as e:
                logger.warning(
                    f"Semantic ranker failed ({str(e)}), falling back to VECTOR-ONLY search"
                )
                results = await self._async_search(
                    query, vector_query, user_tier, limit, False
                )

            context_chunks = []
            for doc in results:
                # Extract high-score fields
                chunk = {
                    "id": doc["id"],
                    "title": doc["title"],
                    "content": doc["content"],
                    "score": doc["@search.score"],
                    "reranker_score": doc.get("@search.reranker_score", 0),
                    "url": doc.get("source_url", ""),
                }
                context_chunks.append(chunk)

            # RAG-C2: Fallback for empty tier-filtered results
            if not context_chunks and user_tier:
                logger.warning(
                    f"No results with tier filter '{user_tier}', retrying without filter"
                )
                try:
                    fallback_results = await self._async_search(
                        query, vector_query, None, limit, True
                    )
                except AzureError:
                    fallback_results = await self._async_search(
                        query, vector_query, None, limit, False
                    )

                for doc in fallback_results:
                    chunk = {
                        "id": doc["id"],
                        "title": doc["title"],
                        "content": doc["content"],
                        "score": doc["@search.score"],
                        "reranker_score": doc.get("@search.reranker_score", 0),
                        "url": doc.get("source_url", ""),
                        "unfiltered": True,
                    }
                    context_chunks.append(chunk)

                if context_chunks:
                    logger.info(
                        f"Found {len(context_chunks)} results without tier filter"
                    )

            logger.info(
                f"Retrieved {len(context_chunks)} chunks for query '{query[:20]}...'"
            )

            # Cache the result
            if cache_key and context_chunks:
                try:
                    from app.db.redis import get_redis

                    redis = get_redis()
                    await redis.set(cache_key, json.dumps(context_chunks), ex=300)
                except (RuntimeError, Exception) as e:
                    logger.debug(f"Failed to cache search results: {e}")

            return context_chunks

        except Exception as e:
            logger.error(f"Azure Search failed completely: {str(e)}")
            logger.warning("search_context returned empty due to error")
            # Return empty list instead of raising to allow graceful degradation
            return []


# Singleton instance
search_service = AzureSearchService()
