import httpx
import json
import asyncio
import logging
import re
from typing import AsyncGenerator

from app.config import settings
from app.core.circuit_breaker import (
    sarvam_circuit_breaker,
    CircuitBreakerError,
    CircuitState,
)

logger = logging.getLogger(__name__)


def _strip_think_block(text: str | None) -> str:
    """Remove <think>...</think> reasoning blocks from Sarvam model output.

    sarvam-30b / sarvam-105b are reasoning models: they may return
    reasoning_content separately and leave content=null.  This function
    is null-safe — returns "" if text is None or empty.
    """
    if not text:
        return ""
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return cleaned.strip()


class SarvamAIClient:
    """Sarvam AI Client for Assamese/Indic content"""

    def __init__(self):
        self.api_key = settings.SARVAM_API_KEY
        self.base_url = settings.SARVAM_BASE_URL
        self.model = settings.SARVAM_MODEL
        self._client = httpx.AsyncClient(
            # sarvam-30b / sarvam-105b are reasoning models — they can take
            # 30-90 s to produce a long translation; raise the read timeout.
            timeout=httpx.Timeout(120.0, connect=5.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    async def close(self):
        """Close the HTTP client (call on app shutdown)"""
        await self._client.aclose()

    async def generate(
        self, system_prompt: str, user_message: str, stream: bool = False
    ) -> str:
        """Generate response using Sarvam AI (sarvam-m model).

        Retry policy (mirrors vertex_client):
          - 429 Too Many Requests → wait 3 s then retry once
          - 500 / 502 / 503 from Sarvam → wait 2 s then retry once
        httpx.HTTPStatusError is re-raised after exhaustion so chat.py can
        return an appropriate response to the client.
        """
        if not self.api_key:
            raise RuntimeError("Sarvam AI not configured (SARVAM_API_KEY is empty)")

        async def _do_generate():
            response = await self._client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "api-subscription-key": self.api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    "temperature": 0.7,
                    # sarvam-30b / sarvam-105b are reasoning models.
                    # enable_thinking=False skips the internal English reasoning
                    # phase entirely — brings TTFB from 5-30s down to 1-3s for
                    # Assamese chat.  budget_tokens=0 is the fallback for APIs
                    # that use the alternative parameter name.
                    "enable_thinking": False,
                    "max_tokens": 4096,
                    "stream": stream,
                },
            )
            response.raise_for_status()
            data = response.json()

            if "choices" in data and len(data["choices"]) > 0:
                msg = data["choices"][0]["message"]
                # sarvam-30b / sarvam-105b: content is the final answer.
                # reasoning_content is the internal thinking (English) — do NOT
                # fall back to it, it's not a translation.
                content = msg.get("content") or ""
                return content
            return "\u09ae\u0987 \u0995\u09cb\u09a8\u09cb \u0989\u09a4\u09cd\u09a4\u09f0 \u09b8\u09c3\u09b7\u09cd\u099f\u09bf \u0995\u09f0\u09bf\u09ac \u09aa\u09f0\u09be \u09a8\u09be\u0987\u09b2\u09cb\u0964 \u0985\u09a8\u09c1\u0997\u09cd\u09f0\u09b9 \u0995\u09f0\u09bf \u09aa\u09c1\u09a8\u09f0 \u099a\u09c7\u09b7\u09cd\u099f\u09be \u0995\u09f0\u0995\u0964"

        _last_http_exc: httpx.HTTPStatusError | None = None

        for attempt in range(2):
            try:
                result = await sarvam_circuit_breaker.call(_do_generate)
                return _strip_think_block(result)
            except CircuitBreakerError as e:
                raise RuntimeError(f"Sarvam AI unavailable: {e}")
            except httpx.HTTPStatusError as e:
                _last_http_exc = e
                status = e.response.status_code
                if status in (429, 500, 502, 503) and attempt == 0:
                    wait = 3 if status == 429 else 2
                    body = ""
                    try:
                        body = e.response.text[:200]
                    except Exception:
                        pass
                    logger.warning(
                        f"Sarvam API HTTP {status} on attempt 1; retrying after {wait}s | body={body}"
                    )
                    await asyncio.sleep(wait)
                    continue
                body = ""
                try:
                    body = e.response.text[:500]
                except Exception:
                    pass
                logger.error(
                    f"Sarvam API HTTP {status} "
                    f"({'retries exhausted' if attempt > 0 else 'non-retryable'}) "
                    f"| model={self.model} | body={body}"
                )
                raise
            except Exception as e:
                logger.error(f"Sarvam AI error: {str(e)}")
                raise RuntimeError(f"Sarvam AI service failed: {e}")

        raise _last_http_exc or RuntimeError("Sarvam AI generate: exhausted retries")

    async def stream_generate(
        self,
        system_prompt: str,
        user_message: str,
    ) -> AsyncGenerator[str, None]:
        """
        Stream response using Sarvam AI OpenAI-compatible SSE endpoint.

        sarvam-30b is a hard-reasoning model: it ALWAYS streams its thinking
        via delta.reasoning_content before emitting the final answer via
        delta.content.  The enable_thinking flag is silently ignored.

        Strategy for <3 s TTFB:
          - Yield delta.reasoning_content tokens immediately (first token
            arrives at ~150 ms).
          - When the system prompt is written in Assamese and instructs
            Assamese-only reasoning, the thinking tokens are in Assamese
            script, so users see native content from the first token.
          - delta.content (final answer) is yielded after reasoning completes.

        Input language: accepted in any language — the system prompt is
        responsible for enforcing Assamese output.
        """
        if not self.api_key:
            raise RuntimeError("Sarvam AI not configured (SARVAM_API_KEY is empty)")

        if sarvam_circuit_breaker.state == CircuitState.OPEN:
            raise RuntimeError("Sarvam AI unavailable (circuit open)")

        url = f"{self.base_url}/chat/completions"
        headers = {
            "api-subscription-key": self.api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": 0.7,
            # enable_thinking=False is sent for forward compatibility; the
            # current sarvam-30b API ignores it and always reasons first.
            "enable_thinking": False,
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

                    choices = chunk.get("choices", [])
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {})

                    # Yield reasoning_content first — arrives at ~150 ms,
                    # contains Assamese thinking when the system prompt
                    # instructs Assamese-only output.
                    reasoning = delta.get("reasoning_content") or ""
                    if reasoning:
                        yield reasoning
                        continue

                    # Final answer tokens follow after reasoning completes.
                    content = delta.get("content") or ""
                    if content:
                        yield content

            sarvam_circuit_breaker._on_success()
        except Exception as e:
            sarvam_circuit_breaker._on_failure()
            if isinstance(e, httpx.HTTPStatusError):
                body = ""
                try:
                    body = e.response.text[:500]
                except Exception:
                    pass
                logger.error(
                    f"Sarvam stream HTTP error: {e.response.status_code} | model={self.model} | body={body}"
                )
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
        retry_delay: float = 2.0,
    ) -> AsyncGenerator[str, None]:
        """
        Stream with retry logic for resilience.

        - On 5xx or timeout: retries up to max_retries times
        - If all retries exhausted, raises to let caller handle fallback
        - HF-078: If chunks were already sent to client, cannot retry
        """
        last_error: Exception | None = None
        chunks_yielded = False

        for attempt in range(max_retries + 1):
            try:
                async for chunk in self.stream_generate(system_prompt, user_message):
                    chunks_yielded = True
                    yield chunk
                return  # Success - exit after full stream
            except RuntimeError as e:
                last_error = e
                # HF-078: If chunks were already sent to client, cannot retry
                if chunks_yielded:
                    raise
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
