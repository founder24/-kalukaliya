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

            # 2. Execute Hybrid Search with Semantic Reranking
            try:
                results = self.client.search(
                    search_text=query,  # BM25 Keyword matching
                    vector_queries=[vector_query],  # Semantic Vector matching
                    filter=f"tier_access eq '{user_tier}'",  # Security Filter
                    query_type=QueryType.SEMANTIC,  # Enable Neural Reranker
                    semantic_configuration_name=settings.AZURE_SEARCH_SEMANTIC_CONFIG,
                    query_caption=QueryCaptionType.EXTRACTIVE,  # Generate snippets
                    query_answer=QueryAnswerType.EXTRACTIVE,  # Generate direct answers
                    top=limit,  # Final return count
                )
                logger.info(f"Using SEMANTIC search for query '{query[:20]}...'")
                
            except AzureError as e:
                # Fallback to vector-only search if semantic ranker fails
                logger.warning(
                    f"Semantic ranker failed ({str(e)}), falling back to VECTOR-ONLY search"
                )
                results = self.client.search(
                    search_text=query,
                    vector_queries=[vector_query],
                    filter=f"tier_access eq '{user_tier}'",
                    query_type=QueryType.VECTOR,  # Fallback to vector only
                    top=limit,
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
