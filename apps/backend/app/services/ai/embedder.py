import logging

logger = logging.getLogger(__name__)


async def generate_embedding(text: str) -> str:
    """
    Sanitize and return text for Vertex AI Search's built-in embedding.

    Vertex AI Search handles embedding internally via its serving configuration.
    This function simply returns the sanitized text.

    Args:
        text: Input text to embed

    Returns:
        The sanitized text string (Vertex AI Search handles vectorization)
    """
    # Strip excessive whitespace and normalize
    sanitized = " ".join(text.split())
    if not sanitized:
        raise ValueError("Cannot generate embedding for empty text")
    return sanitized
