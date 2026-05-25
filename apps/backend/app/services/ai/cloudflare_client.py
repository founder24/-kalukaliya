import httpx
import json
import logging
from typing import AsyncGenerator

from app.config import settings
from app.core.circuit_breaker import CircuitBreaker, CircuitBreakerError, CircuitState

logger = logging.getLogger(__name__)

# Circuit breaker for Cloudflare Workers AI
cloudflare_circuit_breaker = CircuitBreaker(
    name="Cloudflare Workers AI", failure_threshold=5, reset_timeout=60
)


class CloudflareAIClient:
    """Cloudflare Workers AI Client for English content (replaces Vertex AI)"""

    def __init__(self):
        self.account_id = settings.CF_ACCOUNT_ID
        self.api_token = settings.CF_API_TOKEN
        self.model = settings.CF_AI_MODEL
        self.base_url = f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/ai/run"
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    async def close(self):
        await self._client.aclose()

    async def generate(
        self, system_prompt: str, user_message: str, stream: bool = False
    ) -> str:
        if not self.account_id or not self.api_token:
            raise RuntimeError(
                "Cloudflare Workers AI not configured (CF_ACCOUNT_ID or CF_API_TOKEN is empty)"
            )

        try:

            async def _do_generate():
                response = await self._client.post(
                    f"{self.base_url}/{self.model}",
                    headers={
                        "Authorization": f"Bearer {self.api_token}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_message},
                        ],
                        "max_tokens": 1024,
                        "temperature": 0.7,
                        "stream": False,
                    },
                )
                response.raise_for_status()
                data = response.json()

                result = data.get("result", {})
                if isinstance(result, dict):
                    return result.get(
                        "response",
                        "I couldn't generate a response. Please try again.",
                    )
                return "I couldn't generate a response. Please try again."

            result = await cloudflare_circuit_breaker.call(_do_generate)
            return result
        except CircuitBreakerError as e:
            raise RuntimeError(f"Cloudflare AI unavailable: {e}")
        except httpx.HTTPStatusError as e:
            logger.error(f"Cloudflare AI HTTP error: {e.response.status_code}")
            raise RuntimeError(f"Cloudflare AI error: HTTP {e.response.status_code}")
        except Exception as e:
            logger.error(f"Cloudflare AI error: {str(e)}")
            raise RuntimeError(f"Cloudflare AI service failed: {e}")

    async def stream_generate(
        self, system_prompt: str, user_message: str
    ) -> AsyncGenerator[str, None]:
        if not self.account_id or not self.api_token:
            raise RuntimeError(
                "Cloudflare Workers AI not configured (CF_ACCOUNT_ID or CF_API_TOKEN is empty)"
            )

        if cloudflare_circuit_breaker.state == CircuitState.OPEN:
            raise RuntimeError("Cloudflare AI unavailable (circuit open)")

        try:
            async with self._client.stream(
                "POST",
                f"{self.base_url}/{self.model}",
                headers={
                    "Authorization": f"Bearer {self.api_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    "max_tokens": 2048,
                    "temperature": 0.7,
                    "stream": True,
                },
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
                    text = chunk.get("response", "")
                    if text:
                        yield text
            cloudflare_circuit_breaker._on_success()
        except Exception as e:
            cloudflare_circuit_breaker._on_failure()
            if isinstance(e, httpx.HTTPStatusError):
                logger.error(
                    f"Cloudflare AI stream HTTP error: {e.response.status_code}"
                )
                raise RuntimeError(
                    f"Cloudflare AI stream failed: HTTP {e.response.status_code}"
                )
            logger.error(f"Cloudflare AI stream error: {str(e)}")
            raise RuntimeError(f"Cloudflare AI stream failed: {e}")


# Singleton instance
cloudflare_client = CloudflareAIClient()


async def generate_with_cloudflare(
    system_prompt: str, user_message: str, model: str = None, stream: bool = False
) -> str:
    return await cloudflare_client.generate(
        system_prompt=system_prompt, user_message=user_message, stream=stream
    )
