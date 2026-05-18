import httpx
import logging

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
