"""Authenticated client for the internal Cloudflare Workers AI generation API.

All server-side text generation must pass through the API Worker.  This keeps
model selection, retries, and provider access inside Cloudflare rather than
spreading third-party credentials across Cloud Run jobs and local scripts.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncGenerator

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class WorkersAIClient:
    """Call the API Worker's private, non-streaming generation endpoint."""

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=10.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    @property
    def endpoint(self) -> str:
        base = (settings.WORKERS_AI_INTERNAL_URL or settings.CF_WORKER_URL).rstrip("/")
        return f"{base}/api/v1/internal/generate"

    async def close(self) -> None:
        await self._client.aclose()

    async def generate(
        self,
        system_prompt: str,
        user_message: str,
        stream: bool = False,
        is_assamese: bool = False,
        max_tokens: int | None = None,
    ) -> str:
        """Generate text through Workers AI, retrying transient Worker errors once."""
        if not settings.EDGE_SHARED_SECRET:
            raise RuntimeError(
                "Workers AI generation is not configured: EDGE_SHARED_SECRET is required"
            )

        payload = {
            "system_prompt": system_prompt,
            "user_message": user_message,
            "max_output_tokens": max_tokens or settings.CF_AI_MAX_OUTPUT_TOKENS,
            "language": "as" if is_assamese else "en",
        }
        headers = {
            "Authorization": f"Bearer {settings.EDGE_SHARED_SECRET}",
            "Content-Type": "application/json",
        }

        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = await self._client.post(self.endpoint, headers=headers, json=payload)
                response.raise_for_status()
                body = response.json()
                text = str(body.get("text") or "").strip()
                if not text:
                    raise RuntimeError("Workers AI returned an empty generation")
                return text
            except (httpx.TimeoutException, httpx.HTTPStatusError, ValueError, RuntimeError) as exc:
                last_error = exc
                status = getattr(getattr(exc, "response", None), "status_code", None)
                retryable = status is None or status in (429, 500, 502, 503, 504)
                if attempt == 0 and retryable:
                    await asyncio.sleep(1.5)
                    continue
                break

        raise RuntimeError(f"Workers AI generation failed: {last_error}") from last_error

    async def stream_generate_with_retry(
        self,
        system_prompt: str,
        user_message: str,
        is_assamese: bool = False,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[str, None]:
        """Expose buffered internal generation as bounded chunks for legacy SSE users.

        Student chat is streamed natively by the D1 API Worker.  This adapter
        preserves the FastAPI endpoint's event contract while Cloud Run remains
        available for legacy routes.
        """
        text = await self.generate(
            system_prompt,
            user_message,
            is_assamese=is_assamese,
            max_tokens=max_tokens,
        )
        size = max(1, settings.STREAM_CHUNK_SIZE)
        for offset in range(0, len(text), size):
            yield text[offset : offset + size]


workers_ai_client = WorkersAIClient()


async def generate_with_workers_ai(
    system_prompt: str,
    user_message: str,
    stream: bool = False,
    is_assamese: bool = False,
    max_tokens: int | None = None,
) -> str:
    """Functional helper used by health probes and small background jobs."""
    return await workers_ai_client.generate(
        system_prompt,
        user_message,
        stream=stream,
        is_assamese=is_assamese,
        max_tokens=max_tokens,
    )