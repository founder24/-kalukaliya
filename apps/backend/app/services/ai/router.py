"""Legacy FastAPI chat adapter backed exclusively by Cloudflare Workers AI."""

import re
import logging
from typing import AsyncGenerator

from app.config import settings

logger = logging.getLogger(__name__)


def detect_language(text: str) -> str:
    """
    Detect language of input text.
    Returns 'as' for Assamese, 'en' for English.
    """
    assamese_pattern = re.compile(r"[\u0980-\u09FF]")
    assamese_chars = len(assamese_pattern.findall(text))
    total_chars = len(text.replace(" ", ""))

    if total_chars == 0:
        return "en"

    assamese_ratio = assamese_chars / total_chars
    if assamese_ratio > 0.3 and assamese_chars >= 3:
        return "as"
    return "en"


def detect_language_and_route(text: str) -> tuple[str, str]:
    """
    Detect language and route to the shared Workers AI text model.

    Returns:
        tuple: (language_code, model_name)
    """
    lang = detect_language(text)
    logger.info("Routing to Workers AI (lang=%s)", lang)
    return lang, settings.CF_AI_MODEL


async def generate_response(
    system_prompt: str, user_message: str, model: str, stream: bool = False
) -> str:
    """
    Generate a response through the internal Workers AI endpoint.
    """
    from app.services.ai.workers_ai_client import generate_with_workers_ai

    return await generate_with_workers_ai(
        system_prompt=system_prompt,
        user_message=user_message,
        stream=stream,
        is_assamese=detect_language(user_message) == "as",
    )


async def stream_response(
    system_prompt: str,
    user_message: str,
    model: str,
) -> AsyncGenerator[str, None]:
    """
    Stream the compatibility FastAPI path from Workers AI.
    """
    from app.services.ai.workers_ai_client import workers_ai_client

    logger.info("Streaming from Workers AI (model=%s)", model)
    async for chunk in workers_ai_client.stream_generate_with_retry(
        system_prompt=system_prompt,
        user_message=user_message,
        is_assamese=detect_language(user_message) == "as",
    ):
        yield chunk
