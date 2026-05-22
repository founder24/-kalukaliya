from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator
from typing import Optional
import json


class Settings(BaseSettings):
    """
    Application Configuration - All 42 Environment Variables
    Strict typing enforced via Pydantic
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra='ignore')

    # --- P1: Cloudflare (Edge) — not used at runtime by backend, only by edge worker ---
    CF_ACCOUNT_ID: Optional[str] = None
    CF_API_TOKEN: Optional[str] = None
    CF_TURNSTILE_SECRET: Optional[str] = None
    CF_R2_BUCKET: str = "syrabit-assets"
    CF_R2_ACCESS_KEY: Optional[str] = None
    CF_R2_SECRET_KEY: Optional[str] = None
    CF_WORKER_URL: str = "https://edge.syrabit.ai"

    # --- P2: Azure Compute (Backend) — metadata, not used in request handling ---
    AZURE_SUBSCRIPTION_ID: Optional[str] = None
    AZURE_RESOURCE_GROUP: str = "rg-syrabit-prod"
    AZURE_CONTAINER_APP_NAME: str = "ca-syrabit-api"
    AZURE_LOG_ANALYTICS_WORKSPACE: str = "law-syrabit"
    KEYVAULT_URL: Optional[str] = None

    # --- P3: Azure Search (Intelligence) ---
    AZURE_SEARCH_ENDPOINT: str
    AZURE_SEARCH_ADMIN_KEY: str
    AZURE_SEARCH_QUERY_KEY: str
    AZURE_SEARCH_INDEX_NAME: str = "syrabit-edu-index"
    AZURE_SEARCH_SEMANTIC_CONFIG: str = "default"
    AZURE_EMBEDDING_MODEL: str = "text-embedding-3-large"
    AZURE_EMBEDDING_DIMENSIONS: int = 1536

    # --- P4: MongoDB (Data) ---
    MONGODB_URI: str
    MONGODB_DB_NAME: str = "syrabit_prod"
    MONGODB_MAX_POOL_SIZE: int = 50
    MONGODB_MIN_POOL_SIZE: int = 10

    # --- P5: Upstash (Gatekeeper) ---
    UPSTASH_REDIS_REST_URL: str
    UPSTASH_REDIS_REST_TOKEN: str
    RATE_LIMIT_FREE_TIER: int = 30
    RATE_LIMIT_PRO_TIER: int = 999999

    # --- P6: Vertex AI (Google) ---
    GOOGLE_APPLICATION_CREDENTIALS_JSON: str
    VERTEX_PROJECT_ID: str
    VERTEX_LOCATION: str = "us-central1"
    VERTEX_GEMINI_MODEL: str = "gemini-1.5-pro"
    VERTEX_VISION_MODEL: str = "gemini-1.5-pro-vision"

    # --- P7: Sarvam AI (Indic) ---
    SARVAM_API_KEY: str
    SARVAM_BASE_URL: str = "https://api.sarvam.ai/v1"
    SARVAM_MODEL: str = "openhathi-7b"

    # --- P8: Razorpay (Payments) ---
    RAZORPAY_KEY_ID: str
    RAZORPAY_KEY_SECRET: str
    RAZORPAY_WEBHOOK_SECRET: Optional[str] = None
    RAZORPAY_PLAN_ID: str = "plan_pro_monthly"
    RAZORPAY_CURRENCY: str = "INR"

    # --- P9: Resend (Email) ---
    RESEND_API_KEY: str
    RESEND_FROM_ADDRESS: str = "noreply@syrabit.ai"
    RESEND_FROM_NAME: str = "Syrabit Education"

    # --- Observability ---
    SENTRY_DSN: Optional[str] = None
    SENTRY_ENVIRONMENT: str = "production"
    SENTRY_TRACES_SAMPLE_RATE: float = 0.1
    POSTHOG_API_KEY: Optional[str] = None
    POSTHOG_HOST: str = "https://app.posthog.com"

    # --- Application Logic ---
    APP_ENV: str = "production"
    DEBUG: bool = False
    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRY_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRY_DAYS: int = 7
    ALLOWED_ORIGINS: str = "https://syrabit.ai,https://app.syrabit.ai"
    MAX_CONTEXT_DOCS: int = 5
    STREAM_CHUNK_SIZE: int = 128

    @field_validator('JWT_SECRET')
    @classmethod
    def validate_jwt_strength(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters long for security")
        return v

    @property
    def allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",")]

    @property
    def google_credentials(self) -> dict:
        return json.loads(self.GOOGLE_APPLICATION_CREDENTIALS_JSON)


settings = Settings()
