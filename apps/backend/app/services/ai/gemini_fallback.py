"""
Gemini 2.5 Flash — chat fallback when Sarvam AI is unavailable.

Used only when sarvam_client.stream_generate_with_retry() raises an exception.
Credentials are read from GOOGLE_SA_KEY (Replit secret) or
GOOGLE_APPLICATION_CREDENTIALS_JSON (Cloud Run SM secret).

The project_id is extracted from the service-account JSON itself, so no
separate VERTEX_PROJECT_ID env var is needed.
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


def _load_creds() -> tuple[str, str]:
    """
    Returns (project_id, creds_json_string).
    Tries GOOGLE_SA_KEY (Replit) first, then
    GOOGLE_APPLICATION_CREDENTIALS_JSON (Cloud Run SM).
    Returns ("", "") if neither is available.
    """
    for raw in (
        os.environ.get("GOOGLE_SA_KEY", ""),
        os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON", ""),
    ):
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
    """Quick check — True if Gemini credentials are present."""
    project_id, creds = _load_creds()
    return bool(project_id and creds)


def _stream_sync(system_prompt: str, user_message: str) -> list[str]:
    """
    Run Gemini streaming synchronously inside asyncio.to_thread.
    Returns the accumulated list of text chunks.
    Raises RuntimeError on any failure.
    """
    project_id, creds_json = _load_creds()
    if not project_id:
        raise RuntimeError("Gemini fallback: no Google credentials available")

    from google import genai as google_genai
    from google.genai.types import GenerateContentConfig

    # Write SA credentials to a temp file so google-auth can pick them up.
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
        combined = f"{system_prompt}\n\n{user_message}"
        chunks: list[str] = []
        for chunk in client.models.generate_content_stream(
            model=_GEMINI_MODEL,
            contents=[{"role": "user", "parts": [{"text": combined}]}],
            config=GenerateContentConfig(
                temperature=0.3,
                max_output_tokens=2000,
                thinking_config={"thinking_budget": 0},
            ),
        ):
            if chunk.text:
                chunks.append(chunk.text)
        return chunks
    finally:
        try:
            os.unlink(tf.name)
        except Exception:
            pass


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
        raise RuntimeError("Gemini fallback: Google credentials not configured")

    logger.info("gemini_fallback: activating (Sarvam AI unavailable)")
    try:
        chunks = await asyncio.wait_for(
            asyncio.to_thread(_stream_sync, system_prompt, user_message),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        raise RuntimeError(f"Gemini fallback timed out after {timeout}s")
    except Exception as e:
        raise RuntimeError(f"Gemini fallback failed: {e}")

    for chunk in chunks:
        yield chunk
