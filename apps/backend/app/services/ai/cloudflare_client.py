import base64
import asyncio
import httpx
import logging
from typing import AsyncGenerator

from app.config import settings

logger = logging.getLogger(__name__)


class CloudflareAIClient:
    """Cloudflare Workers AI Client for English chat, OCR, and TTS"""

    def __init__(self):
        self.account_id = settings.CF_ACCOUNT_ID
        self.api_token = settings.CF_API_TOKEN
        self.base_url = f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/ai/run"
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=5.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    async def generate(self, system_prompt: str, user_message: str, stream: bool = False) -> str:
        """Generate text response using Workers AI LLM."""
        if not self.account_id or not self.api_token:
            raise RuntimeError("Cloudflare Workers AI not configured (CF_ACCOUNT_ID or CF_API_TOKEN missing)")

        response = await self._client.post(
            f"{self.base_url}/{settings.CF_AI_MODEL}",
            headers={
                "Authorization": f"Bearer {self.api_token}",
                "Content-Type": "application/json",
            },
            json={
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                "max_tokens": 512,
                "temperature": 0.7,
            },
        )
        response.raise_for_status()
        data = response.json()
        result = data.get("result", {})
        return result.get("response", "")

    async def stream_generate(self, system_prompt: str, user_message: str) -> AsyncGenerator[str, None]:
        """Stream text response from Workers AI."""
        if not self.account_id or not self.api_token:
            raise RuntimeError("Cloudflare Workers AI not configured")

        async with self._client.stream(
            "POST",
            f"{self.base_url}/{settings.CF_AI_MODEL}",
            headers={
                "Authorization": f"Bearer {self.api_token}",
                "Content-Type": "application/json",
            },
            json={
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                "max_tokens": 512,
                "temperature": 0.7,
                "stream": True,
            },
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    chunk_data = line[6:]
                    if chunk_data == "[DONE]":
                        break
                    try:
                        import json
                        parsed = json.loads(chunk_data)
                        token = parsed.get("response", "")
                        if token:
                            yield token
                    except (json.JSONDecodeError, KeyError):
                        continue

    async def vision_analyze(self, image_bytes: bytes, prompt: str = "Extract all text from this image") -> str:
        """Analyze an image using Workers AI vision model."""
        if not self.account_id or not self.api_token:
            raise RuntimeError("Cloudflare Workers AI not configured")

        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        response = await self._client.post(
            f"{self.base_url}/{settings.CF_AI_VISION_MODEL}",
            headers={
                "Authorization": f"Bearer {self.api_token}",
                "Content-Type": "application/json",
            },
            json={
                "image": [image_b64],
                "prompt": prompt,
                "max_tokens": 512,
            },
        )
        response.raise_for_status()
        data = response.json()
        result = data.get("result", {})
        if isinstance(result, dict):
            return result.get("description", result.get("response", ""))
        return str(result)

    async def text_to_speech(self, text: str, lang: str = "en") -> bytes:
        """Convert text to speech using Workers AI TTS model."""
        if not self.account_id or not self.api_token:
            raise RuntimeError("Cloudflare Workers AI not configured")

        response = await self._client.post(
            f"{self.base_url}/{settings.CF_AI_TTS_MODEL}",
            headers={
                "Authorization": f"Bearer {self.api_token}",
                "Content-Type": "application/json",
            },
            json={"text": text, "lang": lang},
            timeout=60.0,
        )
        response.raise_for_status()
        return response.content

    async def close(self):
        await self._client.aclose()


cloudflare_client = CloudflareAIClient()


async def generate_with_cloudflare(system_prompt: str, user_message: str, model: str = None, stream: bool = False) -> str:
    """Convenience function for Cloudflare AI generation."""
    return await cloudflare_client.generate(system_prompt=system_prompt, user_message=user_message, stream=stream)
