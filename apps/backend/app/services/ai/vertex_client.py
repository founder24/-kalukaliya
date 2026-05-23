import asyncio
import httpx
import json
import logging
from typing import AsyncGenerator

from app.config import settings

logger = logging.getLogger(__name__)


class VertexAIClient:
    """Vertex AI Gemini Client for English content"""
    
    def __init__(self):
        self.project_id = settings.VERTEX_PROJECT_ID
        self.location = settings.VERTEX_LOCATION
        self.model = settings.VERTEX_GEMINI_MODEL
        self.base_url = f"https://{self.location}-aiplatform.googleapis.com/v1/projects/{self.project_id}/locations/{self.location}/publishers/google/models"
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=10.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    async def close(self):
        """Close the HTTP client (call on app shutdown)"""
        await self._client.aclose()

    async def generate(
        self,
        system_prompt: str,
        user_message: str,
        stream: bool = False
    ) -> str:
        """Generate response using Gemini"""
        try:
            # Build prompt
            full_prompt = f"{system_prompt}\n\nUser: {user_message}\nAssistant:"
            
            response = await self._client.post(
                f"{self.base_url}/{self.model}:generateContent",
                headers={
                    "Authorization": f"Bearer {await self._get_access_token()}",
                    "Content-Type": "application/json"
                },
                json={
                    "contents": [{
                        "parts": [{"text": full_prompt}]
                    }],
                    "generationConfig": {
                        "temperature": 0.7,
                        "maxOutputTokens": 1024,
                    }
                }
            )
            response.raise_for_status()
            data = response.json()
            
            # Extract response text
            if 'candidates' in data and len(data['candidates']) > 0:
                return data['candidates'][0]['content']['parts'][0]['text']
            return "I couldn't generate a response. Please try again."
                
        except Exception as e:
            logger.error(f"Vertex AI error: {str(e)}")
            raise RuntimeError(f"Vertex AI service failed: {e}")

    async def _get_access_token(self) -> str:
        """Get OAuth2 access token for Vertex AI"""
        from google.oauth2 import service_account

        creds = service_account.Credentials.from_service_account_info(
            settings.google_credentials,
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        
        # Refresh token if needed (blocking call wrapped in executor)
        import google.auth.transport.requests
        request = google.auth.transport.requests.Request()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, creds.refresh, request)
        
        return creds.token

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
        full_prompt = f"{system_prompt}\n\nUser: {user_message}\nAssistant:"

        url = f"{self.base_url}/{self.model}:streamGenerateContent?alt=sse"
        headers = {
            "Authorization": f"Bearer {await self._get_access_token()}",
            "Content-Type": "application/json",
        }
        payload = {
            "contents": [{"parts": [{"text": full_prompt}]}],
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 2048,
            },
        }

        try:
            async with self._client.stream("POST", url, headers=headers, json=payload) as resp:
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
        except httpx.HTTPStatusError as e:
            logger.error(f"Vertex AI stream HTTP error: {e.response.status_code}")
            raise RuntimeError(f"Vertex AI stream failed: HTTP {e.response.status_code}")
        except Exception as e:
            logger.error(f"Vertex AI stream error: {str(e)}")
            raise RuntimeError(f"Vertex AI stream failed: {e}")


# Singleton instance
vertex_client = VertexAIClient()


async def generate_with_vertex(
    system_prompt: str,
    user_message: str,
    model: str = None,
    stream: bool = False
) -> str:
    """Convenience function for Vertex AI generation"""
    return await vertex_client.generate(
        system_prompt=system_prompt,
        user_message=user_message,
        stream=stream
    )
