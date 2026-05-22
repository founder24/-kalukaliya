from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator, model_validator
from typing import Optional
import json


class Settings(BaseSettings):
    """
    Application Configuration
    
    All fields are Optional so the backend can START even with missing env vars.
    Features that require specific vars will fail at call-time with clear errors,
    rather than crashing the entire container on startup.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra='ignore',
    )

    # --- P1: Cloudflare (Edge) — not used by backend at runtime ---
    CF_ACCOUNT_ID: Optional[str] = None
    CF_API_TOKEN: Optional[str] = None
    CF_TURNSTILE_SECRET: Optional[str] = None
    CF_R2_BUCKET: str = "syrabit-assets"
    CF_R2_ACCESS_KEY: Optional[str] = None
    CF_R2_SECRET_KEY: Optional[str] = None
    CF_WORKER_URL: str = "https://edge.syrabit.ai"

    # --- P2: Azure Compute (Backend) — metadata only ---
    AZURE_SUBSCRIPTION_ID: Optional[str] = None
    AZURE_RESOURCE_GROUP: str = "rg-syrabit-prod"
    AZURE_CONTAINER_APP_NAME: str = "ca-syrabit-api"
    AZURE_LOG_ANALYTICS_WORKSPACE: str = "law-syrabit"
    KEYVAULT_URL: Optional[str] = None

    # --- P3: Azure Search (Intelligence) ---
    AZURE_SEARCH_ENDPOINT: Optional[str] = None
    AZURE_SEARCH_ADMIN_KEY: Optional[str] = None
    AZURE_SEARCH_QUERY_KEY: Optional[str] = None
    AZURE_SEARCH_INDEX_NAME: str = "syrabit-edu-index"
    AZURE_SEARCH_SEMANTIC_CONFIG: str = "default"
    AZURE_EMBEDDING_MODEL: str = "text-embedding-3-large"
    AZURE_EMBEDDING_DIMENSIONS: int = 1536

    # --- P4: MongoDB (Data) ---
    MONGODB_URI: Optional[str] = None
    MONGODB_DB_NAME: str = "syrabit_prod"
    MONGODB_MAX_POOL_SIZE: int = 50
    MONGODB_MIN_POOL_SIZE: int = 10

    # --- P5: Upstash (Gatekeeper) ---
    UPSTASH_REDIS_REST_URL: Optional[str] = None
    UPSTASH_REDIS_REST_TOKEN: Optional[str] = None
    RATE_LIMIT_FREE_TIER: int = 30
    RATE_LIMIT_PRO_TIER: int = 999999

    # --- P6: Vertex AI (Google) ---
    GOOGLE_APPLICATION_CREDENTIALS_JSON: Optional[str] = None
    VERTEX_PROJECT_ID: Optional[str] = None
    VERTEX_LOCATION: str = "us-central1"
    VERTEX_GEMINI_MODEL: str = "gemini-1.5-pro"
    VERTEX_VISION_MODEL: str = "gemini-1.5-pro-vision"

    # --- P7: Sarvam AI (Indic) ---
    SARVAM_API_KEY: Optional[str] = None
    SARVAM_BASE_URL: str = "https://api.sarvam.ai/v1"
    SARVAM_MODEL: str = "openhathi-7b"

    # --- P8: Razorpay (Payments) ---
    RAZORPAY_KEY_ID: Optional[str] = None
    RAZORPAY_KEY_SECRET: Optional[str] = None
    RAZORPAY_WEBHOOK_SECRET: Optional[str] = None
    RAZORPAY_PLAN_ID: str = "plan_pro_monthly"
    RAZORPAY_CURRENCY: str = "INR"

    # --- P9: Resend (Email) ---
    RESEND_API_KEY: Optional[str] = None
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
    JWT_SECRET: str = "CHANGE_ME_IN_PRODUCTION_AT_LEAST_32_CHARS_LONG"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRY_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRY_DAYS: int = 7
    ALLOWED_ORIGINS: str = "https://syrabit.ai,https://app.syrabit.ai,http://localhost:5173"
    MAX_CONTEXT_DOCS: int = 5
    STREAM_CHUNK_SIZE: int = 128

    @model_validator(mode='before')
    @classmethod
    def empty_strings_to_none(cls, values):
        """Convert empty strings to None so Optional fields work correctly."""
        if isinstance(values, dict):
            for key, val in values.items():
                if val == '':
                    values[key] = None
        return values

    @property
    def allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",")]

    @property
    def google_credentials(self) -> dict:
        if not self.GOOGLE_APPLICATION_CREDENTIALS_JSON:
            return {}
        return json.loads(self.GOOGLE_APPLICATION_CREDENTIALS_JSON)


settings = Settings()
