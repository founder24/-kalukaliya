"""
Chat routing: Sarvam-105b is the primary model for both English and Assamese.
The system prompt instructs the model which language to respond in.

Fallback: when Sarvam AI billing is exhausted (402) or the service is
unavailable, traffic automatically falls back to Gemini 2.5 Flash via
``app.services.ai.gemini_fallback`` (generate_gemini / stream_gemini).
Gemini credentials are resolved from GEMINI_API_KEY (Google AI Studio) or
GOOGLE_SA_KEY / GOOGLE_APPLICATION_CREDENTIALS_JSON (Vertex AI).

Cloudflare Workers AI is NOT used for chat — it is only used for OCR endpoints
in chat.py.
"""

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
    Detect language and route to the appropriate LLM.

    Both English and Assamese now route to Sarvam AI.
    The system prompt (built in chat_service) instructs Sarvam which language
    to respond in.

    Returns:
        tuple: (language_code, model_name)
    """
    lang = detect_language(text)
    logger.info(f"Routing to Sarvam AI (lang={lang})")
    return lang, settings.SARVAM_MODEL


async def generate_response(
    system_prompt: str, user_message: str, model: str, stream: bool = False
) -> str:
    """
    Generate response using Sarvam AI.
    """
    from app.services.ai.sarvam_client import generate_with_sarvam

    return await generate_with_sarvam(
        system_prompt=system_prompt, user_message=user_message, stream=stream
    )


async def stream_response(
    system_prompt: str,
    user_message: str,
    model: str,
) -> AsyncGenerator[str, None]:
    """
    Stream response from Sarvam AI.

    All chat traffic (English and Assamese) routes here.
    Yields text chunks as they arrive.
    Raises RuntimeError on failure (caller handles dead-letter / error SSE).
    """
    from app.services.ai.sarvam_client import sarvam_client

    logger.info(f"Streaming from Sarvam AI (model={model})")
    async for chunk in sarvam_client.stream_generate_with_retry(
        system_prompt=system_prompt,
        user_message=user_message,
    ):
        yield chunk
