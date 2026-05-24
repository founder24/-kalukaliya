import httpx
import logging

from app.config import settings

logger = logging.getLogger(__name__)


_http_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=30.0)
    return _http_client


async def close_http_client():
    global _http_client
    if _http_client:
        await _http_client.aclose()
        _http_client = None


async def generate_embedding(text: str) -> list[float]:
    """
    Generate embedding using Azure OpenAI text-embedding-3-large
    
    Args:
        text: Input text to embed
        
    Returns:
        List of 1536 floats representing the embedding vector
    """
    if not settings.AZURE_OPENAI_ENDPOINT:
        raise RuntimeError("AZURE_OPENAI_ENDPOINT not configured - cannot generate embeddings")

    try:
        # Use Azure OpenAI embedding endpoint
        # Note: This requires Azure OpenAI service configured
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider
        
        token_provider = get_bearer_token_provider(
            DefaultAzureCredential(),
            "https://cognitiveservices.azure.com/.default"
        )
        
        client = get_http_client()
        response = await client.post(
            f"{settings.AZURE_OPENAI_ENDPOINT}/openai/deployments/{settings.AZURE_EMBEDDING_MODEL}/embeddings?api-version=2024-02-15-preview",
            headers={
                "Authorization": f"Bearer {token_provider()}",
                "Content-Type": "application/json"
            },
            json={
                "input": text,
                "model": settings.AZURE_EMBEDDING_MODEL,
                "dimensions": settings.AZURE_EMBEDDING_DIMENSIONS
            }
        )
        response.raise_for_status()
        data = response.json()
        
        return data["data"][0]["embedding"]
            
    except RuntimeError:
        raise
    except Exception as e:
        logger.error(f"Embedding generation failed: {str(e)}")
        raise RuntimeError(f"Embedding service unavailable: {e}")
