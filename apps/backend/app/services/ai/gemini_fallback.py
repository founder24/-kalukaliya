"""
Gemini 2.5 Flash — chat fallback when Sarvam AI is unavailable.

Authentication priority (first that works wins):
  1. GEMINI_API_KEY env var / settings field — Google AI Studio
     (generativelanguage.googleapis.com, no IAM required, just the key)
  2. GOOGLE_SA_KEY / GOOGLE_APPLICATION_CREDENTIALS_JSON — Vertex AI
     (requires roles/aiplatform.user on the service account)

Using the API-key path avoids Vertex AI IAM entirely and is the recommended
path for production fallback.

Assamese output quality
=======================
Gemini 2.5 Flash responds directly in the language specified by the system
prompt — no post-processing extraction is needed.  This is different from
the Sarvam path, where the reasoning model embeds Assamese lines inside an
English chain-of-thought, requiring ``_extract_assamese_answer()`` /
``_extract_assamese_translation()`` to recover the clean output.

For Assamese requests, the caller should pass an Assamese (or bilingual)
system prompt instructing the model to respond in Assamese.  The response
will contain Assamese script directly; there is no need to call any of
the Sarvam-specific extraction helpers.

The ``/health/chat-pipeline`` endpoint runs an Assamese probe that verifies
this assumption on every CI deploy.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from typing import AsyncGenerator

logger = logging.getLogger(__name__)

_GEMINI_MODEL = "gemini-2.5-flash"


# ── Auth helpers ──────────────────────────────────────────────────────────────

def _get_api_key() -> str:
    """Return GEMINI_API_KEY from env or settings, empty string if absent."""
    from app.config import settings  # imported here to avoid circular imports
    return (
        os.environ.get("GEMINI_API_KEY", "")
        or getattr(settings, "GEMINI_API_KEY", None)
        or ""
    )


def _load_vertex_creds() -> tuple[str, str]:
    """
    Returns (project_id, creds_json_string) for Vertex AI auth.

    Priority (first non-empty wins):
      1. GOOGLE_SA_KEY env var  — set by Replit secrets in dev
      2. settings.GOOGLE_APPLICATION_CREDENTIALS_JSON — loaded by Secret
         Manager during FastAPI lifespan in Cloud Run (not an env var)
      3. GOOGLE_APPLICATION_CREDENTIALS_JSON env var — fallback for
         environments that export it directly

    Returns ("", "") if none of the above is usable.
    """
    from app.config import settings  # imported here to avoid circular imports

    candidates = [
        os.environ.get("GOOGLE_SA_KEY", ""),
        getattr(settings, "GOOGLE_APPLICATION_CREDENTIALS_JSON", "") or "",
        os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON", ""),
    ]
    for raw in candidates:
        if not raw:
            continue
        try:
            data = json.loads(raw)
            project_id = data.get("project_id", "")
            if project_id:
                return project_id, raw
        except Exception:
            continue
    return "", ""


def _available() -> bool:
    """Quick check — True if any Gemini credentials are present."""
    if _get_api_key():
        return True
    project_id, creds = _load_vertex_creds()
    return bool(project_id and creds)


# ── Sync worker (runs in asyncio.to_thread) ───────────────────────────────────

def _stream_sync(
    system_prompt: str,
    user_message: str,
    max_output_tokens: int = 2000,
) -> list[str]:
    """
    Run Gemini streaming synchronously inside asyncio.to_thread.
    Returns the accumulated list of text chunks.
    Raises RuntimeError on any failure.

    Tries GEMINI_API_KEY (Google AI Studio) first; if the project is blocked
    (403 PERMISSION_DENIED) or the model is unavailable (404 NOT_FOUND), falls
    through to Vertex AI service-account credentials automatically.
    """
    from google import genai as google_genai

    api_key = _get_api_key()

    if api_key:
        # ── Path 1: Google AI Studio (API key) ────────────────────────────
        logger.debug("gemini_fallback: trying Google AI Studio (API key)")
        client = google_genai.Client(api_key=api_key)
        try:
            return _call_model(client, system_prompt, user_message, max_output_tokens)
        except Exception as e:
            err = str(e)
            if "403" in err or "PERMISSION_DENIED" in err or "404" in err or "NOT_FOUND" in err:
                logger.warning(
                    "gemini_fallback: API-key path failed (%s) — falling through to Vertex AI",
                    err[:120],
                )
                # Fall through to Vertex AI below
            else:
                raise

    # ── Path 2: Vertex AI (service-account credentials) ───────────────────
    project_id, creds_json = _load_vertex_creds()
    if not project_id:
        raise RuntimeError("Gemini fallback: no credentials available "
                           "(set GEMINI_API_KEY or GOOGLE_SA_KEY)")
    logger.debug("gemini_fallback: using Vertex AI (service account)")
    tf = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    try:
        tf.write(creds_json)
        tf.close()
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = tf.name
        client = google_genai.Client(
            vertexai=True,
            project=project_id,
            location="us-central1",
        )
        return _call_model(client, system_prompt, user_message, max_output_tokens)
    finally:
        try:
            os.unlink(tf.name)
        except Exception:
            pass


def _call_model(
    client,
    system_prompt: str,
    user_message: str,
    max_output_tokens: int = 2000,
) -> list[str]:
    """Execute the streaming generate call and collect chunks."""
    from google.genai.types import GenerateContentConfig

    chunks: list[str] = []
    for chunk in client.models.generate_content_stream(
        model=_GEMINI_MODEL,
        contents=[{"role": "user", "parts": [{"text": user_message}]}],
        config=GenerateContentConfig(
            # Pass system prompt via system_instruction so Gemini treats it as
            # model context rather than user content — prevents the model from
            # echoing instructions back verbatim in its response.
            system_instruction=system_prompt,
            temperature=0.3,
            max_output_tokens=max_output_tokens,
            thinking_config={"thinking_budget": 0},
        ),
    ):
        if chunk.text:
            chunks.append(chunk.text)
    return chunks


# ── Public async API ──────────────────────────────────────────────────────────

async def generate_gemini(
    system_prompt: str,
    user_message: str,
    timeout: float = 90.0,
    max_output_tokens: int = 2000,
) -> str:
    """
    Generate a complete (non-streaming) response from Gemini 2.5 Flash.
    Returns the full response string.
    Raises RuntimeError on timeout or failure.

    max_output_tokens: upper bound on generated tokens. Raise this for long-form
    content (e.g. chapter notes) where 2000 tokens would truncate the output.
    """
    if not _available():
        raise RuntimeError("Gemini fallback: no credentials configured "
                           "(set GEMINI_API_KEY)")

    logger.info(
        "gemini_fallback.generate: activating (Sarvam AI unavailable)",
        extra={"max_output_tokens": max_output_tokens},
    )
    try:
        chunks = await asyncio.wait_for(
            asyncio.to_thread(_stream_sync, system_prompt, user_message, max_output_tokens),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        raise RuntimeError(f"Gemini fallback timed out after {timeout}s")
    except Exception as e:
        raise RuntimeError(f"Gemini fallback failed: {e}")

    return "".join(chunks)


async def stream_gemini(
    system_prompt: str,
    user_message: str,
    timeout: float = 90.0,
) -> AsyncGenerator[str, None]:
    """
    Stream a Gemini 2.5 Flash response as the fallback LLM.

    Runs the blocking google-genai streaming call in a thread pool so it
    never blocks the event loop.  Collects all chunks in the thread then
    yields them one by one to the caller.

    Raises RuntimeError on timeout or any Gemini API failure.
    """
    if not _available():
        raise RuntimeError("Gemini fallback: no credentials configured "
                           "(set GEMINI_API_KEY)")

    logger.info("gemini_fallback: activating (Sarvam AI unavailable)")
    try:
        chunks = await asyncio.wait_for(
            asyncio.to_thread(_stream_sync, system_prompt, user_message),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        raise RuntimeError(f"Gemini fallback timed out after {timeout}s")
    except Exception as e:
        err = str(e)
        if "403" in err or "PERMISSION_DENIED" in err:
            logger.error(
                "Gemini fallback 403 PERMISSION_DENIED — service account lacks "
                "'aiplatform.endpoints.predict'. Switch to API-key auth: set "
                "GEMINI_API_KEY in Replit secrets and GCP Secret Manager "
                "(gemini-api-key) to bypass IAM entirely."
            )
        raise RuntimeError(f"Gemini fallback failed: {e}")

    for chunk in chunks:
        yield chunk
