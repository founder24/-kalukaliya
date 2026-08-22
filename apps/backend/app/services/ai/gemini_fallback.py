"""Deprecated compatibility shim for the retired Gemini fallback."""

from typing import AsyncGenerator

from app.services.ai.workers_ai_client import generate_with_workers_ai, workers_ai_client


def _available() -> bool:
    """Workers AI is configured when the shared internal secret is present."""
    from app.config import settings
    return bool(settings.EDGE_SHARED_SECRET)


async def generate_gemini(
    system_prompt: str,
    user_message: str,
    timeout: float = 90.0,
    max_output_tokens: int = 2000,
) -> str:
    """Compatibility alias; generation is performed by Workers AI."""
    return await generate_with_workers_ai(
        system_prompt, user_message, max_tokens=max_output_tokens
    )


async def stream_gemini(
    system_prompt: str, user_message: str, timeout: float = 90.0
) -> AsyncGenerator[str, None]:
    """Compatibility alias; emit Cloudflare-generated chunks."""
    async for chunk in workers_ai_client.stream_generate_with_retry(
        system_prompt, user_message
    ):
        yield chunk