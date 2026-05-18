import httpx
import logging

from app.config import settings

logger = logging.getLogger(__name__)


class VertexAIClient:
    """Vertex AI Gemini Client for English content"""
    
    def __init__(self):
        self.project_id = settings.VERTEX_PROJECT_ID
        self.location = settings.VERTEX_LOCATION
        self.model = settings.VERTEX_GEMINI_MODEL
        self.base_url = f"https://{self.location}-aiplatform.googleapis.com/v1/projects/{self.project_id}/locations/{self.location}/publishers/google/models"

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
            
            # For now, use simple HTTP call (can be enhanced with Google Auth library)
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
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
        # In production, use google.auth library with service account
        # This is a placeholder - implement proper auth
        from google.oauth2 import service_account
        import json
        
        creds = service_account.Credentials.from_service_account_info(
            settings.google_credentials,
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        
        # Refresh token if needed
        import google.auth.transport.requests
        request = google.auth.transport.requests.Request()
        creds.refresh(request)
        
        return creds.token


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
