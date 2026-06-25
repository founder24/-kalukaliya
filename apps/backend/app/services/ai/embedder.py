"""
CF Workers AI Embedder — replaces Vertex AI text-embedding-005.

Uses @cf/baai/bge-m3 (1024-dim, multilingual, supports Assamese + English).
Auth: CF_WORKER_AI_TOKEN ?? CF_API_TOKEN (already in settings).

Latency profile:
  CF bge-m3   ~100-200 ms  (REST API, global CF network)
  Vertex      ~150-300 ms  (gRPC, GCP regional)

All callers receive a list[float] of length 1024 — same interface as before,
only the dimension changed from 768 → 1024.
"""

import asyncio
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_EMBED_DIM = 1024
_CF_MODEL = "@cf/baai/bge-m3"

_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=5.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _http_client


def _cf_embed_url() -> str:
    account_id = settings.CF_ACCOUNT_ID
    if not account_id:
        raise RuntimeError(
            "CF_ACCOUNT_ID is not set. Cannot call CF Workers AI embedding API."
        )
    return (
        f"https://api.cloudflare.com/client/v4/accounts/"
        f"{account_id}/ai/run/{_CF_MODEL}"
    )


def _cf_auth_token() -> str:
    token = getattr(settings, "CF_WORKER_AI_TOKEN", None) or settings.CF_API_TOKEN
    if not token:
        raise RuntimeError(
            "CF_WORKER_AI_TOKEN (or CF_API_TOKEN) is not set. "
            "Cannot authenticate with CF Workers AI."
        )
    return token


async def generate_embedding(text: str) -> str:
    """Sanitize and return text (legacy helper used by admin tools)."""
    sanitized = " ".join(text.split())
    if not sanitized:
        raise ValueError("Cannot generate embedding for empty text")
    return sanitized


async def generate_embedding_vector(text: str) -> list[float]:
    """
    Generate a 1024-dimension embedding vector using CF Workers AI bge-m3.

    Supports both English and Assamese text — bge-m3 is trained on the
    mC4 multilingual corpus which includes Assamese (ISO code: as).

    Args:
        text: Input text to embed (topic title, user query, chunk content).

    Returns:
        A list of 1024 floats (cosine-normalized via Atlas vector index).

    Raises:
        RuntimeError: If CF credentials are missing or the API call fails.
        ValueError: If the input text is empty after sanitization.
    """
    sanitized = " ".join(text.split())
    if not sanitized:
        raise ValueError("Cannot generate embedding for empty text")

    return (await embed_batch([sanitized]))[0]


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """
    Embed a batch of texts in a single CF Workers AI call.

    CF bge-m3 accepts up to 100 texts per request.
    Returns a list of 1024-dim vectors in the same order as input.

    Args:
        texts: List of strings to embed (max 100 per call).

    Returns:
        List of 1024-dim float vectors.
    """
    if not texts:
        return []

    sanitized = [" ".join(t.split()) for t in texts]
    empty_indices = [i for i, t in enumerate(sanitized) if not t]
    if empty_indices:
        raise ValueError(f"Empty text at indices {empty_indices} in batch")

    if len(sanitized) > 100:
        raise ValueError(
            f"CF bge-m3 batch limit is 100 texts; got {len(sanitized)}. "
            "Split into smaller batches using embed_batch_chunked()."
        )

    url = _cf_embed_url()
    token = _cf_auth_token()

    payload = {"text": sanitized if len(sanitized) > 1 else sanitized[0]}

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
            f"CF bge-m3 API error: {response.status_code} — {response.text[:300]}"
        )
        raise RuntimeError(
            f"CF Workers AI embedding API returned HTTP {response.status_code}"
        )

    data = response.json()
    if not data.get("success"):
        errors = data.get("errors", [])
        raise RuntimeError(f"CF Workers AI embedding API failed: {errors}")

    raw = data["result"]["data"]

    if isinstance(raw[0], float):
        return [raw]
    return raw


async def embed_batch_chunked(
    texts: list[str], batch_size: int = 50
) -> list[list[float]]:
    """
    Embed an arbitrarily large list of texts by splitting into batches.

    Uses asyncio.gather for concurrent batches (up to CF rate limits).

    Args:
        texts: Any number of strings to embed.
        batch_size: Number of texts per CF API call (default 50, max 100).

    Returns:
        List of 1024-dim float vectors, same order as input.
    """
    batches = [texts[i : i + batch_size] for i in range(0, len(texts), batch_size)]
    results = await asyncio.gather(*[embed_batch(b) for b in batches])
    merged: list[list[float]] = []
    for r in results:
        merged.extend(r)
    return merged
