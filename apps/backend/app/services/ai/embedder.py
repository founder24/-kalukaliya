import logging

logger = logging.getLogger(__name__)


async def generate_embedding(text: str) -> str:
    """
    Sanitize and return text for Azure Search's built-in vectorization.

    Azure Search uses VectorizableTextQuery which handles embedding internally
    via a configured skillset. This function simply returns the sanitized text.

    Args:
        text: Input text to embed

    Returns:
        The sanitized text string (Azure Search handles vectorization)
    """
    # Strip excessive whitespace and normalize
    sanitized = " ".join(text.split())
    if not sanitized:
        raise ValueError("Cannot generate embedding for empty text")
    return sanitized
