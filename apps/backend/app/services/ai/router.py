"""
Chat routing uses Cloudflare Workers AI (English) and Sarvam AI (Assamese).
Vertex AI / Gemini is no longer used for chat routing.
"""

import re
import logging
from typing import AsyncGenerator

from app.config import settings

logger = logging.getLogger(__name__)


def detect_language(text: str) -> str:
    """
    Detect language of input text.
    Returns 'as' for Assamese, 'en' for English
    """
    # Assamese Unicode range: U+0980 to U+09FF
    assamese_pattern = re.compile(r"[\u0980-\u09FF]")

    # Count Assamese characters
    assamese_chars = len(assamese_pattern.findall(text))
    total_chars = len(text.replace(" ", ""))

    if total_chars == 0:
        return "en"  # Default to English

    # If >30% Assamese characters, consider it Assamese
    assamese_ratio = assamese_chars / total_chars

    if assamese_ratio > 0.3 and assamese_chars >= 3:
        return "as"
    return "en"


def detect_language_and_route(text: str) -> tuple[str, str]:
    """
    Detect language and route to appropriate LLM.

    Returns:
        tuple: (language_code, model_name)
    """
    lang = detect_language(text)

    if lang == "as":
        # Route to Sarvam for Assamese
        logger.info("Routing to Sarvam AI for Assamese content")
        return "as", settings.SARVAM_MODEL
    else:
        # Route to Cloudflare Workers AI for English
        logger.info(f"Routing to Cloudflare Workers AI for English content (model={settings.CF_AI_MODEL})")
        return "en", settings.CF_AI_MODEL


def _is_cf_model(model: str) -> bool:
    """True when the model string is a Cloudflare Workers AI model path."""
    return model.startswith("@cf/")


async def generate_response(
    system_prompt: str, user_message: str, model: str, stream: bool = False
) -> str:
    """
    Generate response using appropriate AI client based on model.
    """
    if "sarvam" in model.lower() or "openhathi" in model.lower():
        from app.services.ai.sarvam_client import generate_with_sarvam

        return await generate_with_sarvam(
            system_prompt=system_prompt, user_message=user_message, stream=stream
        )
    elif _is_cf_model(model):
        from app.services.ai.cloudflare_client import cloudflare_client

        logger.info(f"Generating via Cloudflare Workers AI (model={model})")
        return await cloudflare_client.generate(
            system_prompt=system_prompt, user_message=user_message, stream=stream
        )
    elif "gemini" in model.lower() or "vertex" in model.lower():
        from app.services.ai.vertex_client import generate_with_vertex

        return await generate_with_vertex(
            system_prompt=system_prompt,
            user_message=user_message,
            model=model,
            stream=stream,
        )
    else:
        raise RuntimeError(
            f"Unknown model '{model}': supported providers are Cloudflare Workers AI (@cf/...), "
            f"Sarvam AI (sarvam/openhathi), and Vertex AI (gemini/vertex)."
        )


async def stream_response(
    system_prompt: str,
    user_message: str,
    model: str,
) -> AsyncGenerator[str, None]:
    """
    Stream response from the appropriate AI client based on model name.

    Routes to:
    - Sarvam AI (with retry) for Assamese models (sarvam, openhathi, saaras)
    - Cloudflare Workers AI for English models (@cf/...)
    - Vertex AI as fallback for legacy gemini/vertex model names

    Yields text chunks as they arrive from the provider.
    Raises RuntimeError on failure (caller handles fallback).
    """
    if (
        "sarvam" in model.lower()
        or "openhathi" in model.lower()
        or "saaras" in model.lower()
    ):
        from app.services.ai.sarvam_client import sarvam_client

        logger.info(f"Streaming from Sarvam AI (model={model})")
        async for chunk in sarvam_client.stream_generate_with_retry(
            system_prompt=system_prompt,
            user_message=user_message,
        ):
            yield chunk
    elif _is_cf_model(model):
        from app.services.ai.cloudflare_client import cloudflare_client

        logger.info(f"Streaming from Cloudflare Workers AI (model={model})")
        async for chunk in cloudflare_client.stream_generate(
            system_prompt=system_prompt,
            user_message=user_message,
        ):
            yield chunk
    elif "gemini" in model.lower() or "vertex" in model.lower():
        from app.services.ai.vertex_client import vertex_client

        logger.info(f"Streaming from Vertex AI (model={model})")
        async for chunk in vertex_client.stream_generate_with_retry(
            system_prompt=system_prompt,
            user_message=user_message,
        ):
            yield chunk
    else:
        raise RuntimeError(
            f"Unknown model '{model}': supported providers are Cloudflare Workers AI (@cf/...), "
            f"Sarvam AI (openhathi/sarvam/saaras), and Vertex AI (gemini/vertex)."
        )
