import asyncio
import logging
import time as _time

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# --- Module-level token cache (Issue #1: mirrors vertex_client.py pattern) ---
_token_lock = asyncio.Lock()
_cached_token: str | None = None
_token_expiry: float = 0

# --- Module-level httpx client for connection reuse (Issue #3) ---
_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    """Return a module-level httpx.AsyncClient for connection pooling."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=3.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _http_client


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

    # Get access token (cached with 60s-before-expiry check)
    token = await _get_embedding_access_token()

    url = (
        f"https://{location}-aiplatform.googleapis.com/v1/"
        f"projects/{project_id}/locations/{location}/"
        f"publishers/google/models/text-embedding-005:predict"
    )

    payload = {
        "instances": [{"content": sanitized}],
    }

    client = _get_http_client()
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
    """Get OAuth2 access token for Vertex AI embedding API with caching and lock."""
    global _cached_token, _token_expiry

    if not settings.google_credentials:
        raise RuntimeError(
            "Google credentials not configured. "
            "Set GOOGLE_APPLICATION_CREDENTIALS or GOOGLE_APPLICATION_CREDENTIALS_JSON."
        )

    # Return cached token if still valid (with 60s buffer before expiry)
    if _cached_token and _time.time() < _token_expiry - 60:
        return _cached_token

    async with _token_lock:
        # Double-check after acquiring lock
        if _cached_token and _time.time() < _token_expiry - 60:
            return _cached_token

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

        _cached_token = creds.token
        # Token typically valid for 1 hour
        _token_expiry = _time.time() + 3600

        return _cached_token
