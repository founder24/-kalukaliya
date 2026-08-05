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


async def generate_gemini(
    system_prompt: str,
    user_message: str,
    timeout: float = 90.0,
) -> str:
    """
    Generate a complete (non-streaming) response from Gemini 2.5 Flash.
    Returns the full response string.
    Raises RuntimeError on timeout or failure.
    """
    if not _available():
        raise RuntimeError("Gemini fallback: Google credentials not configured")

    logger.info("gemini_fallback.generate: activating (Sarvam AI unavailable)")
    try:
        chunks = await asyncio.wait_for(
            asyncio.to_thread(_stream_sync, system_prompt, user_message),
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
        err = str(e)
        if "403" in err or "PERMISSION_DENIED" in err:
            logger.error(
                "Gemini fallback 403 PERMISSION_DENIED — service account lacks "
                "'aiplatform.endpoints.predict'. Fix: grant roles/aiplatform.user to "
                "cloudflare-edge-invoker@blissful-acumen-495019-t6.iam.gserviceaccount.com "
                "at https://console.cloud.google.com/iam-admin/iam"
            )
        raise RuntimeError(f"Gemini fallback failed: {e}")

    for chunk in chunks:
        yield chunk
