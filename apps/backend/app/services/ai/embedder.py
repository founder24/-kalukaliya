import httpx
import logging

from app.config import settings

logger = logging.getLogger(__name__)


async def generate_embedding(text: str) -> list[float]:
    """
    Generate embedding using Azure OpenAI text-embedding-3-large
    
    Args:
        text: Input text to embed
        
    Returns:
        List of 1536 floats representing the embedding vector
    """
    try:
        # Use Azure OpenAI embedding endpoint
        # Note: This requires Azure OpenAI service configured
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider
        
        token_provider = get_bearer_token_provider(
            DefaultAzureCredential(),
            "https://cognitiveservices.azure.com/.default"
        )
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{settings.AZURE_SEARCH_ENDPOINT}/openai/deployments/{settings.AZURE_EMBEDDING_MODEL}/embeddings?api-version=2024-02-15-preview",
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
            
    except Exception as e:
        logger.error(f"Embedding generation failed: {str(e)}")
        raise RuntimeError(f"Embedding service unavailable: {e}")
