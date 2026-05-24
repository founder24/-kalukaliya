"""
Embedder module - Azure AI Search Built-in Vectorization

Azure AI Search handles embeddings internally via integrated vectorization
(skillset-based). The backend sends raw text queries to the search service,
which vectorizes them using its configured vectorizer profile.

This module exists to maintain backward compatibility with callers that
previously relied on generate_embedding(). It now returns the raw text
for use with Azure Search's VectorizableTextQuery.
"""

import logging

logger = logging.getLogger(__name__)


async def generate_embedding(text: str) -> str:
    """
    Returns the input text for Azure Search integrated vectorization.

    Azure AI Search vectorizes queries internally using its configured
    vectorizer (skillset-based embedding). No external API call is needed.

    Args:
        text: Input text to be vectorized by the search service

    Returns:
        The input text (Azure Search handles vectorization internally)
    """
    if not text or not text.strip():
        raise ValueError("Cannot generate embedding for empty text")
    return text


async def close_http_client():
    """No-op kept for backward compatibility with app shutdown hooks."""
    pass
