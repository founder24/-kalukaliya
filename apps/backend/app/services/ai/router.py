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

    Assamese and Bengali share the same Unicode block, so this intentionally
    uses conservative script heuristics rather than treating a few characters
    in an otherwise English/code-heavy prompt as Assamese.
    """
    if not text:
        return "en"

    # Code comments and string literals are not reliable language signals.
    # Remove fenced and inline code before measuring the natural-language
    # script, while retaining the original text for code detection.
    natural_text = re.sub(r"```[\s\S]*?```", " ", text)
    natural_text = re.sub(r"`[^`]*`", " ", natural_text)
    assamese_pattern = re.compile(r"[\u0980-\u09FF]")
    assamese_chars = len(assamese_pattern.findall(natural_text))
    total_chars = len(re.sub(r"\s+", "", natural_text))

    if total_chars == 0:
        return "en"

    assamese_ratio = assamese_chars / total_chars
    if assamese_chars < 5 or assamese_ratio < 0.35:
        return "en"

    code_markers = re.compile(
        r"(?:\b(?:def|class|function|const|let|import|return|SELECT|FROM)\b|"
        r"=>|[{};]|</?[A-Za-z][^>]*>)"
    )
    if code_markers.search(text) and assamese_ratio < 0.55:
        return "en"
    return "as"


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
