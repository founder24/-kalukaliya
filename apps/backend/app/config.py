from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import model_validator
from typing import Optional
import json
import logging

logger = logging.getLogger(__name__)


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
        extra="ignore",
    )

    # --- P1: Cloudflare (Edge) — not used by backend at runtime ---
    CF_ACCOUNT_ID: Optional[str] = None
    CF_API_TOKEN: Optional[str] = None
    CF_TURNSTILE_SECRET: Optional[str] = None
    CF_R2_BUCKET: str = "syrabit-assets"
    CF_R2_ACCESS_KEY: Optional[str] = None
    CF_R2_SECRET_KEY: Optional[str] = None
    CF_WORKER_URL: str = "https://edge.syrabit.ai"
    # NOTE: CF_AI_MODEL is used ONLY for OCR (vision_analyze) and TTS (text_to_speech).
    # English chat routing was moved to Vertex AI (VERTEX_GEMINI_MODEL) for performance.
    CF_AI_MODEL: str = "@cf/meta/llama-3.1-8b-instruct"
    CF_AI_VISION_MODEL: str = "@cf/unum/uform-gen2-qwen-500m"
    CF_AI_TTS_MODEL: str = "@cf/myshell/melotts"

    # --- P2: Azure Compute (Backend) --- metadata only, not used at runtime ---
    # (Removed: AZURE_SUBSCRIPTION_ID, AZURE_RESOURCE_GROUP, etc. - migrated to GCP)

    # --- P3: Vertex AI Search (Discovery Engine) ---
    VERTEX_SEARCH_DATASTORE_ID: Optional[str] = None
    VERTEX_SEARCH_SERVING_CONFIG: str = "default_search"
    VERTEX_SEARCH_LOCATION: str = "global"
    SEARCH_CACHE_ENABLED: bool = True

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
    # Option 1 (recommended): Path to service account key file
    GOOGLE_APPLICATION_CREDENTIALS: Optional[str] = None
    # Option 2 (legacy): Inline JSON string of service account key
    GOOGLE_APPLICATION_CREDENTIALS_JSON: Optional[str] = None
    # Option 3: Gemini API key (Generative Language API - bypasses Vertex AI SDK)
    GEMINI_API_KEY: Optional[str] = None
    VERTEX_PROJECT_ID: Optional[str] = None
    VERTEX_LOCATION: str = "us-central1"
    VERTEX_GEMINI_MODEL: str = "gemini-2.5-flash"
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

    # --- SEO / IndexNow ---
    INDEXNOW_API_KEY: Optional[str] = None
    INDEXNOW_INTERNAL_SECRET: Optional[str] = None

    # --- Cloudflare KV (Content Edge Cache) ---
    CLOUDFLARE_KV_API_TOKEN: str = ""
    CLOUDFLARE_ACCOUNT_ID: str = ""
    CLOUDFLARE_KV_NAMESPACE_ID: str = ""
    INDEXNOW_KEY: str = ""

    # --- Cron/CI Translation ---
    TRANSLATE_CRON_SECRET: str = ""

    # --- Observability ---
    SENTRY_DSN: Optional[str] = None
    SENTRY_ENVIRONMENT: str = "production"
    SENTRY_TRACES_SAMPLE_RATE: float = 0.1
    POSTHOG_API_KEY: Optional[str] = None
    POSTHOG_HOST: str = "https://app.posthog.com"

    # --- Application Logic ---
    APP_ENV: str = "production"
    DEBUG: bool = False
    JWT_SECRET: str = "dev-only-secret-not-for-production-use-32chars"
    ADMIN_JWT_SECRET: Optional[str] = None
    RESET_TOKEN_SECRET: Optional[str] = None
    JWT_ALGORITHM: str = "HS256"
    JWT_PRIVATE_KEY: Optional[str] = None  # PEM-encoded RSA private key for RS256
    JWT_PUBLIC_KEY: Optional[str] = None  # PEM-encoded RSA public key for RS256
    JWT_EXPIRY_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRY_DAYS: int = 7
    EDGE_SHARED_SECRET: Optional[str] = None
    TRUST_EDGE_AUTH: bool = True
    ALLOWED_ORIGINS: str = (
        "https://syrabit.ai,https://www.syrabit.ai,https://app.syrabit.ai"
    )
    MAX_CONTEXT_DOCS: int = 5
    STREAM_CHUNK_SIZE: int = 128

    @model_validator(mode="before")
    @classmethod
    def empty_strings_to_none(cls, values):
        """Convert empty strings to None so Optional fields work correctly."""
        if isinstance(values, dict):
            for key, val in values.items():
                if val == "":
                    values[key] = None
        return values

    @model_validator(mode="after")
    def validate_production_secrets(self):
        """Validate critical secrets are properly configured in production."""
        KNOWN_PLACEHOLDER_SECRETS = {
            "super_secret_jwt_key_32_chars_min",
            "CHANGE_ME_IN_PRODUCTION_AT_LEAST_32_CHARS_LONG",
            "test-secret-at-least-32-characters-long",
            "dev-only-secret-not-for-production-use-32chars",
        }

        if self.APP_ENV == "production":
            if self.JWT_SECRET in KNOWN_PLACEHOLDER_SECRETS:
                raise ValueError(
                    "JWT_SECRET is a known placeholder value and must be changed in production"
                )
            if len(self.JWT_SECRET) < 32:
                raise ValueError(
                    "JWT_SECRET must be at least 32 characters long in production"
                )
            if not self.ADMIN_JWT_SECRET:
                logger.warning(
                    "ADMIN_JWT_SECRET is not set — admin tokens use the shared JWT_SECRET. "
                    "Set a separate ADMIN_JWT_SECRET for improved key isolation."
                )
            if not self.RESET_TOKEN_SECRET:
                logger.warning(
                    "RESET_TOKEN_SECRET is not set — reset tokens use the shared JWT_SECRET. "
                    "Set a separate RESET_TOKEN_SECRET for improved key isolation."
                )
            if self.JWT_ALGORITHM == "RS256":
                if not self.JWT_PRIVATE_KEY:
                    raise ValueError(
                        "JWT_PRIVATE_KEY is required when JWT_ALGORITHM is RS256"
                    )
                if not self.JWT_PUBLIC_KEY:
                    raise ValueError(
                        "JWT_PUBLIC_KEY is required when JWT_ALGORITHM is RS256"
                    )
            if not self.MONGODB_URI:
                logger.warning("MONGODB_URI is not set in production")
            if not self.UPSTASH_REDIS_REST_URL:
                logger.warning("UPSTASH_REDIS_REST_URL is not set in production")
            if not self.VERTEX_SEARCH_DATASTORE_ID:
                logger.warning("VERTEX_SEARCH_DATASTORE_ID is not set in production")
            # Warn about missing service credentials
            if (
                not self.VERTEX_PROJECT_ID
                or not self.GOOGLE_APPLICATION_CREDENTIALS_JSON
            ):
                logger.warning("Vertex AI credentials not configured in production")
            if not self.SARVAM_API_KEY:
                logger.warning("Sarvam AI API key not configured in production")
            if not self.RAZORPAY_KEY_ID or not self.RAZORPAY_KEY_SECRET:
                logger.warning(
                    "Razorpay payment credentials not configured in production"
                )
            if not self.RESEND_API_KEY:
                logger.warning("Resend email API key not configured in production")
            if not self.SENTRY_DSN:
                logger.warning(
                    "Sentry DSN not configured in production - error tracking disabled"
                )
        return self

    @property
    def allowed_origins_list(self) -> list[str]:
        origins = [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",")]
        if self.APP_ENV == "production":
            origins = [
                o for o in origins if "localhost" not in o and "127.0.0.1" not in o
            ]
        return origins

    def is_origin_allowed(self, origin: str) -> bool:
        """Check if an origin is allowed, including Cloudflare Pages preview domains."""
        import re

        if origin in self.allowed_origins_list:
            return True
        # Allow Cloudflare Pages preview URLs
        if re.match(r"^https://[a-z0-9-]+\.syrabitfrontend\.pages\.dev$", origin):
            return True
        return False

    @property
    def google_credentials(self) -> dict:
        """Load Google credentials from file path or inline JSON.

        Priority:
        1. GOOGLE_APPLICATION_CREDENTIALS (file path) - recommended, safer
        2. GOOGLE_APPLICATION_CREDENTIALS_JSON (inline JSON) - legacy fallback
        3. On Cloud Run with Workload Identity, neither is needed (uses ADC)
        """
        # Option 1: Load from file path
        if self.GOOGLE_APPLICATION_CREDENTIALS:
            import os
            creds_path = os.path.expanduser(self.GOOGLE_APPLICATION_CREDENTIALS)
            try:
                with open(creds_path) as f:
                    return json.load(f)
            except (FileNotFoundError, json.JSONDecodeError) as e:
                logger.warning(f"Failed to load credentials from {creds_path}: {e}")
                return {}

        # Option 2: Inline JSON string
        if self.GOOGLE_APPLICATION_CREDENTIALS_JSON:
            try:
                return json.loads(self.GOOGLE_APPLICATION_CREDENTIALS_JSON)
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse GOOGLE_APPLICATION_CREDENTIALS_JSON: {e}")
                return {}

        return {}


settings = Settings()
