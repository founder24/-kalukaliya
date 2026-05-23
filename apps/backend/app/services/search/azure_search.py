import asyncio
from functools import partial
from azure.search.documents import SearchClient
from azure.search.documents.models import (
    VectorizedQuery,
    QueryType,
    QueryCaptionType,
    QueryAnswerType,
)
from azure.core.exceptions import AzureError
from azure.core.credentials import AzureKeyCredential
from app.config import settings
import logging

logger = logging.getLogger(__name__)


class AzureSearchService:
    """
    Azure Cognitive Search Service - Hybrid Search with Semantic Reranking
    Provides BM25 + Vector search with neural reranking for optimal RAG quality
    
    Features:
    - Graceful degradation to vector-only if semantic ranker fails
    - Circuit breaker integration
    - Detailed error logging
    """

    def __init__(self):
        self.client = SearchClient(
            endpoint=settings.AZURE_SEARCH_ENDPOINT,
            index_name=settings.AZURE_SEARCH_INDEX_NAME,
            credential=AzureKeyCredential(settings.AZURE_SEARCH_QUERY_KEY),
        )

    def _sync_search(self, query: str, vector_query, user_tier: str, limit: int, semantic: bool):
        """Synchronous search - runs in thread pool executor."""
        if semantic:
            return list(self.client.search(
                search_text=query,
                vector_queries=[vector_query],
                filter=f"tier_access eq '{user_tier}'",
                query_type=QueryType.SEMANTIC,
                semantic_configuration_name=settings.AZURE_SEARCH_SEMANTIC_CONFIG,
                query_caption=QueryCaptionType.EXTRACTIVE,
                query_answer=QueryAnswerType.EXTRACTIVE,
                top=limit,
            ))
        else:
            return list(self.client.search(
                search_text=query,
                vector_queries=[vector_query],
                filter=f"tier_access eq '{user_tier}'",
                query_type=QueryType.VECTOR,
                top=limit,
            ))

    async def search_context(
        self, query: str, embedding: list[float], user_tier: str, limit: int = 5
    ):
        """
        Executes Hybrid Search (Keyword + Vector) with Semantic Reranking.
        Falls back to vector-only search if semantic ranker is unavailable.
        
        Args:
            query: User's search query text
            embedding: 1536-dimensional vector from embedding model
            user_tier: 'free' or 'pro' for content filtering
            limit: Number of results to return (default: 5)
            
        Returns:
            List of context chunks with scores and metadata
        """
        try:
            # 1. Define Vector Query
            vector_query = VectorizedQuery(
                vector=embedding,
                k_nearest_neighbors=50,  # Retrieve larger candidate set for reranking
                fields="content_vector",
                exhaustive=True,  # Ensure accuracy over speed for small sets
            )

            # 2. Execute Hybrid Search with Semantic Reranking via thread pool
            loop = asyncio.get_event_loop()
            try:
                results = await loop.run_in_executor(
                    None,
                    partial(self._sync_search, query, vector_query, user_tier, limit, True)
                )
                logger.info(f"Using SEMANTIC search for query '{query[:20]}...'")
            except AzureError as e:
                logger.warning(
                    f"Semantic ranker failed ({str(e)}), falling back to VECTOR-ONLY search"
                )
                results = await loop.run_in_executor(
                    None,
                    partial(self._sync_search, query, vector_query, user_tier, limit, False)
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

            logger.info(
                f"Retrieved {len(context_chunks)} chunks for query '{query[:20]}...'"
            )
            return context_chunks

        except Exception as e:
            logger.error(f"Azure Search failed completely: {str(e)}")
            # Return empty list instead of raising to allow graceful degradation
            return []


# Singleton instance
search_service = AzureSearchService()
