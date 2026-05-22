import httpx
import json
import asyncio
import logging
from typing import AsyncGenerator

from app.config import settings

logger = logging.getLogger(__name__)


class SarvamAIClient:
    """Sarvam AI Client for Assamese/Indic content"""
    
    def __init__(self):
        self.api_key = settings.SARVAM_API_KEY
        self.base_url = settings.SARVAM_BASE_URL
        self.model = settings.SARVAM_MODEL

    async def generate(
        self,
        system_prompt: str,
        user_message: str,
        stream: bool = False
    ) -> str:
        """Generate response using Sarvam OpenHathi"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_message}
                        ],
                        "temperature": 0.7,
                        "max_tokens": 1024,
                        "stream": stream
                    }
                )
                response.raise_for_status()
                data = response.json()
                
                # Extract response text
                if 'choices' in data and len(data['choices']) > 0:
                    return data['choices'][0]['message']['content']
                return "মই কোনো উত্তৰ সৃষ্টি কৰিব পৰা নাইলো। অনুগ্ৰহ কৰি পুনৰ চেষ্টা কৰক।"
                
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
            async with httpx.AsyncClient(timeout=60.0) as client:
                async with client.stream("POST", url, headers=headers, json=payload) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        raw = line[6:].strip()
                        if raw == "[DONE]":
                            return
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
        except httpx.HTTPStatusError as e:
            logger.error(f"Sarvam stream HTTP error: {e.response.status_code}")
            raise RuntimeError(f"Sarvam stream failed: HTTP {e.response.status_code}")
        except Exception as e:
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
                return  # Success — exit after full stream
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
    system_prompt: str,
    user_message: str,
    stream: bool = False
) -> str:
    """Convenience function for Sarvam AI generation"""
    return await sarvam_client.generate(
        system_prompt=system_prompt,
        user_message=user_message,
        stream=stream
    )
