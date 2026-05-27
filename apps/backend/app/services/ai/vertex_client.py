import asyncio
import httpx
import json
import logging
from typing import AsyncGenerator

from app.config import settings
from app.core.circuit_breaker import (
    vertex_circuit_breaker,
    CircuitBreakerError,
    CircuitState,
)

logger = logging.getLogger(__name__)


class VertexAIClient:
    """Vertex AI Gemini Client for English content"""

    def __init__(self):
        self.project_id = settings.VERTEX_PROJECT_ID
        self.location = settings.VERTEX_LOCATION
        self.model = settings.VERTEX_GEMINI_MODEL
        self.base_url = f"https://{self.location}-aiplatform.googleapis.com/v1/projects/{self.project_id}/locations/{self.location}/publishers/google/models"
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=5.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
        self._token_lock = asyncio.Lock()
        self._cached_token: str | None = None
        self._token_expiry: float = 0

    async def close(self):
        """Close the HTTP client (call on app shutdown)"""
        await self._client.aclose()

    async def generate(
        self, system_prompt: str, user_message: str, stream: bool = False
    ) -> str:
        """Generate response using Gemini"""
        try:

            async def _do_generate():
                response = await self._client.post(
                    f"{self.base_url}/{self.model}:generateContent",
                    headers={
                        "Authorization": f"Bearer {await self._get_access_token()}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "systemInstruction": {"parts": [{"text": system_prompt}]},
                        "contents": [
                            {"role": "user", "parts": [{"text": user_message}]}
                        ],
                        "generationConfig": {
                            "temperature": 0.7,
                            "maxOutputTokens": 512,
                        },
                    },
                )
                response.raise_for_status()
                data = response.json()

                # Extract response text
                if "candidates" in data and len(data["candidates"]) > 0:
                    return data["candidates"][0]["content"]["parts"][0]["text"]
                return "I couldn't generate a response. Please try again."

            result = await vertex_circuit_breaker.call(_do_generate)
            return result
        except CircuitBreakerError as e:
            raise RuntimeError(f"Vertex AI unavailable: {e}")
        except Exception as e:
            logger.error(f"Vertex AI error: {str(e)}")
            raise RuntimeError(f"Vertex AI service failed: {e}")

    async def _get_access_token(self) -> str:
        """Get OAuth2 access token for Vertex AI with caching and lock."""
        import time as _time

        if not settings.google_credentials:
            raise RuntimeError(
                "Vertex AI credentials not configured (GOOGLE_APPLICATION_CREDENTIALS_JSON is empty)"
            )

        # Return cached token if still valid (with 60s buffer)
        if self._cached_token and _time.time() < self._token_expiry - 60:
            return self._cached_token

        async with self._token_lock:
            # Double-check after acquiring lock
            if self._cached_token and _time.time() < self._token_expiry - 60:
                return self._cached_token

            from google.oauth2 import service_account

            creds = service_account.Credentials.from_service_account_info(
                settings.google_credentials,
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )

            # Try native async refresh via aiohttp transport first
            try:
                from google.auth.transport._aiohttp_requests import (
                    Request as AiohttpRequest,
                )

                aiohttp_request = AiohttpRequest()
                try:
                    await creds.refresh(aiohttp_request)
                finally:
                    await aiohttp_request.close()
            except (ImportError, AttributeError):
                # aiohttp transport not available, fall back to executor pattern
                import google.auth.transport.requests

                request = google.auth.transport.requests.Request()
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, creds.refresh, request)

            self._cached_token = creds.token
            # Token typically valid for 1 hour
            self._token_expiry = _time.time() + 3600

            return self._cached_token

    async def stream_generate(
        self,
        system_prompt: str,
        user_message: str,
    ) -> AsyncGenerator[str, None]:
        """
        Stream response using Gemini streamGenerateContent endpoint.

        Yields text chunks as they arrive via SSE.
        Uses ?alt=sse to get Server-Sent Events format from Vertex AI.
        """
        if vertex_circuit_breaker.state == CircuitState.OPEN:
            raise RuntimeError("Vertex AI unavailable (circuit open)")

        url = f"{self.base_url}/{self.model}:streamGenerateContent?alt=sse"
        headers = {
            "Authorization": f"Bearer {await self._get_access_token()}",
            "Content-Type": "application/json",
        }
        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_message}]}],
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 2048,
            },
        }

        try:
            async with self._client.stream(
                "POST", url, headers=headers, json=payload
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    raw = line[6:].strip()
                    if not raw:
                        continue
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    # Extract text from Gemini SSE response
                    candidates = data.get("candidates", [])
                    if not candidates:
                        continue
                    content = candidates[0].get("content", {})
                    parts = content.get("parts", [])
                    for part in parts:
                        text = part.get("text", "")
                        if text:
                            yield text
            vertex_circuit_breaker._on_success()
        except Exception as e:
            vertex_circuit_breaker._on_failure()
            if isinstance(e, httpx.HTTPStatusError):
                logger.error(f"Vertex AI stream HTTP error: {e.response.status_code}")
                raise RuntimeError(
                    f"Vertex AI stream failed: HTTP {e.response.status_code}"
                )
            logger.error(f"Vertex AI stream error: {str(e)}")
            raise RuntimeError(f"Vertex AI stream failed: {e}")

    async def stream_generate_with_retry(
        self,
        system_prompt: str,
        user_message: str,
        max_retries: int = 1,
        retry_delay: float = 0.3,
    ) -> AsyncGenerator[str, None]:
        """
        Stream with retry logic for resilience.

        - On 5xx or timeout: retries up to max_retries times
        - If all retries exhausted, raises to let caller handle fallback
        """
        last_error: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                async for chunk in self.stream_generate(system_prompt, user_message):
                    yield chunk
                return  # Success - exit after full stream
            except RuntimeError as e:
                last_error = e
                if attempt < max_retries:
                    logger.warning(
                        f"Vertex AI stream attempt {attempt + 1} failed: {e}, "
                        f"retrying in {retry_delay}s..."
                    )
                    await asyncio.sleep(retry_delay)
                else:
                    break

        # All retries exhausted
        raise last_error or RuntimeError("Vertex AI stream failed after retries")


# Singleton instance
vertex_client = VertexAIClient()


async def generate_with_vertex(
    system_prompt: str, user_message: str, model: str = None, stream: bool = False
) -> str:
    """Convenience function for Vertex AI generation"""
    return await vertex_client.generate(
        system_prompt=system_prompt, user_message=user_message, stream=stream
    )
