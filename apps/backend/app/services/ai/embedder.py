import logging

import httpx

from app.config import settings

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


async def generate_embedding_vector(text: str) -> list[float]:
    """
    Generate a 768-dimension embedding vector using Vertex AI text-embedding-005.

    Calls the Vertex AI REST API directly (same httpx + OAuth2 pattern as vertex_client.py).
    The resulting vector is stored in MongoDB for cosine similarity matching.

    Args:
        text: Input text to embed (topic title, user query, etc.)

    Returns:
        A list of 768 floats representing the text embedding.

    Raises:
        RuntimeError: If the API call fails or credentials are missing.
    """
    sanitized = " ".join(text.split())
    if not sanitized:
        raise ValueError("Cannot generate embedding for empty text")

    project_id = settings.VERTEX_PROJECT_ID
    location = settings.VERTEX_LOCATION

    if not project_id:
        raise RuntimeError("VERTEX_PROJECT_ID is not configured")

    # Get access token (same pattern as vertex_client.py)
    token = await _get_embedding_access_token()

    url = (
        f"https://{location}-aiplatform.googleapis.com/v1/"
        f"projects/{project_id}/locations/{location}/"
        f"publishers/google/models/text-embedding-005:predict"
    )

    payload = {
        "instances": [{"content": sanitized}],
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0)) as client:
        response = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=payload,
        )

        if response.status_code != 200:
            logger.error(
                f"Embedding API error: {response.status_code} - {response.text[:200]}"
            )
            raise RuntimeError(
                f"Vertex AI embedding API failed with status {response.status_code}"
            )

        data = response.json()

    try:
        embedding = data["predictions"][0]["embeddings"]["values"]
    except (KeyError, IndexError) as e:
        logger.error(f"Unexpected embedding response structure: {e}")
        raise RuntimeError("Failed to parse embedding response from Vertex AI")

    return embedding


async def _get_embedding_access_token() -> str:
    """Get OAuth2 access token for Vertex AI embedding API."""
    import asyncio

    if not settings.google_credentials:
        raise RuntimeError(
            "Google credentials not configured. "
            "Set GOOGLE_APPLICATION_CREDENTIALS or GOOGLE_APPLICATION_CREDENTIALS_JSON."
        )

    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_info(
        settings.google_credentials,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )

    try:
        from google.auth.transport._aiohttp_requests import Request as AiohttpRequest

        aiohttp_request = AiohttpRequest()
        try:
            await creds.refresh(aiohttp_request)
        finally:
            await aiohttp_request.close()
    except (ImportError, AttributeError):
        import google.auth.transport.requests

        request = google.auth.transport.requests.Request()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, creds.refresh, request)

    return creds.token
