import httpx
import json
import asyncio
import logging
from typing import AsyncGenerator

from app.config import settings
from app.core.circuit_breaker import (
    sarvam_circuit_breaker,
    CircuitBreakerError,
    CircuitState,
)

logger = logging.getLogger(__name__)


class SarvamAIClient:
    """Sarvam AI Client for Assamese/Indic content"""

    def __init__(self):
        self.api_key = settings.SARVAM_API_KEY
        self.base_url = settings.SARVAM_BASE_URL
        self.model = settings.SARVAM_MODEL
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=10.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    async def close(self):
        """Close the HTTP client (call on app shutdown)"""
        await self._client.aclose()

    async def generate(
        self, system_prompt: str, user_message: str, stream: bool = False
    ) -> str:
        """Generate response using Sarvam OpenHathi"""
        if not self.api_key:
            raise RuntimeError("Sarvam AI not configured (SARVAM_API_KEY is empty)")

        try:

            async def _do_generate():
                response = await self._client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_message},
                        ],
                        "temperature": 0.7,
                        "max_tokens": 1024,
                        "stream": stream,
                    },
                )
                response.raise_for_status()
                data = response.json()

                # Extract response text
                if "choices" in data and len(data["choices"]) > 0:
                    return data["choices"][0]["message"]["content"]
                return "\u09ae\u0987 \u0995\u09cb\u09a8\u09cb \u0989\u09a4\u09cd\u09a4\u09f0 \u09b8\u09c3\u09b7\u09cd\u099f\u09bf \u0995\u09f0\u09bf\u09ac \u09aa\u09f0\u09be \u09a8\u09be\u0987\u09b2\u09cb\u0964 \u0985\u09a8\u09c1\u0997\u09cd\u09f0\u09b9 \u0995\u09f0\u09bf \u09aa\u09c1\u09a8\u09f0 \u099a\u09c7\u09b7\u09cd\u099f\u09be \u0995\u09f0\u0995\u0964"

            result = await sarvam_circuit_breaker.call(_do_generate)
            return result
        except CircuitBreakerError as e:
            raise RuntimeError(f"Sarvam AI unavailable: {e}")
        except httpx.HTTPStatusError as e:
            logger.error(f"Sarvam API HTTP error: {e.response.status_code}")
            raise RuntimeError(f"Sarvam API error: {e.response.status_code}")
        except Exception as e:
            logger.error(f"Sarvam AI error: {str(e)}")
            raise RuntimeError(f"Sarvam AI service failed: {e}")

    async def stream_generate(
        self,
        system_prompt: str,
        user_message: str,
    ) -> AsyncGenerator[str, None]:
        """
        Stream response using Sarvam AI OpenAI-compatible SSE endpoint.

        Parses chunked SSE lines in the format:
            data: {"choices": [{"delta": {"content": "..."}}]}
            data: [DONE]

        Yields text content deltas as they arrive.
        """
        if not self.api_key:
            raise RuntimeError("Sarvam AI not configured (SARVAM_API_KEY is empty)")

        if sarvam_circuit_breaker.state == CircuitState.OPEN:
            raise RuntimeError("Sarvam AI unavailable (circuit open)")

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": 0.7,
            "max_tokens": 2048,
            "stream": True,
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
                    if raw == "[DONE]":
                        break
                    if not raw:
                        continue
                    try:
                        chunk = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    # OpenAI-compatible: choices[0].delta.content
                    choices = chunk.get("choices", [])
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        yield content
            sarvam_circuit_breaker._on_success()
        except Exception as e:
            sarvam_circuit_breaker._on_failure()
            if isinstance(e, httpx.HTTPStatusError):
                logger.error(f"Sarvam stream HTTP error: {e.response.status_code}")
                raise RuntimeError(
                    f"Sarvam stream failed: HTTP {e.response.status_code}"
                )
            logger.error(f"Sarvam stream error: {str(e)}")
            raise RuntimeError(f"Sarvam stream failed: {e}")

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
                        f"Sarvam stream attempt {attempt + 1} failed: {e}, "
                        f"retrying in {retry_delay}s..."
                    )
                    await asyncio.sleep(retry_delay)
                else:
                    break

        # All retries exhausted
        raise last_error or RuntimeError("Sarvam stream failed after retries")


# Singleton instance
sarvam_client = SarvamAIClient()


async def generate_with_sarvam(
    system_prompt: str, user_message: str, stream: bool = False
) -> str:
    """Convenience function for Sarvam AI generation"""
    return await sarvam_client.generate(
        system_prompt=system_prompt, user_message=user_message, stream=stream
    )
