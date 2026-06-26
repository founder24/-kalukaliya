from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, model_validator
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
        env_ignore_empty=True,
    )

    # --- P1: Cloudflare (Edge) — not used by backend at runtime ---
    CF_ACCOUNT_ID: Optional[str] = None
    CF_API_TOKEN: Optional[str] = None
    CF_R2_BUCKET: str = "syrabit-assets"
    # Note (HF-110): CF_R2_BUCKET default matches wrangler.toml binding name.
    CF_R2_ACCESS_KEY: Optional[str] = None
    CF_R2_SECRET_KEY: Optional[str] = None
    CF_WORKER_URL: str = "https://edge.syrabit.ai"
    # CF_AI_MODEL: primary model for English chat + OCR + TTS via CF Workers AI REST API.
    # AWQ quantized variant is faster and available across all CF Workers AI regions.
    CF_AI_MODEL: str = "@cf/meta/llama-3.1-8b-instruct-awq"
    CF_AI_VISION_MODEL: str = "@cf/unum/uform-gen2-qwen-500m"
    CF_AI_TTS_MODEL: str = "@cf/myshell/melotts"
    # Cloudflare Pages deploy hook — triggers a rebuild to regenerate static content
    CF_PAGES_DEPLOY_HOOK: Optional[str] = None
    # GCS bucket name for educational content (source of truth for CF Pages)
    GCS_CONTENT_BUCKET: Optional[str] = None

    # --- Cloudflare Analytics (traffic + WAF stats) ---
    # Zone ID from the Cloudflare dashboard (Overview → Zone ID on the right sidebar).
    CF_ZONE_ID: Optional[str] = None
    # API token with Analytics:Read permission (separate from CF_API_TOKEN).
    CF_ANALYTICS_TOKEN: Optional[str] = None

    # --- Cloudflare Vectorize (RAG vector store) ---
    # Index name created via: wrangler vectorize create syrabit-rag --dimensions=1024 --metric=cosine
    # Uses the same CF_ACCOUNT_ID and CF_API_TOKEN / CF_WORKER_AI_TOKEN as Workers AI.
    CF_VECTORIZE_INDEX_NAME: str = "syrabit-rag"
    # Optional separate token scoped to Vectorize only; falls back to CF_API_TOKEN / CF_WORKER_AI_TOKEN.
    CF_VECTORIZE_API_TOKEN: Optional[str] = None
    # Workers AI token (current Cloud Run env var name; also used for embeddings)
    CF_WORKER_AI_TOKEN: Optional[str] = None

    # --- MongoDB Search Cache ---
    SEARCH_CACHE_ENABLED: bool = True

    # --- MongoDB (Data) ---
    MONGODB_URI: Optional[str] = None
    MONGODB_URL: Optional[str] = None  # Alias used by Replit secret store
    MONGODB_DB_NAME: str = "syrabit_prod"
    MONGODB_MAX_POOL_SIZE: int = 50
    # Note (HF-111): MONGODB_MAX_POOL_SIZE=50 is sufficient. Monitor via OTel metrics.
    MONGODB_MIN_POOL_SIZE: int = 10

    # --- P5: Rate Limiting (quota via MongoDB, burst via Cloudflare KV) ---
    RATE_LIMIT_FREE_TIER: int = 30
    RATE_LIMIT_PRO_TIER: int = 999999

    # --- GCP Credentials (SA key for Cloud Run OIDC, topic embeddings) ---
    # Option 1 (recommended): Path to service account key file
    GOOGLE_APPLICATION_CREDENTIALS: Optional[str] = None
    # Option 2: Inline JSON string of service account key
    # Used by CF Worker for Cloud Run OIDC auth; also used by the embedding API.
    GOOGLE_APPLICATION_CREDENTIALS_JSON: Optional[str] = None

    # --- Sarvam AI (Indic + English) ---
    SARVAM_API_KEY: Optional[str] = None
    SARVAM_BASE_URL: str = "https://api.sarvam.ai/v1"
    # Valid Sarvam chat-completion models (as of 2025-06): sarvam-30b, sarvam-105b
    # sarvam-m1 was renamed; use sarvam-30b (fast) or sarvam-105b (quality).
    # Override via SARVAM_MODEL env var if this needs to change without a deploy.
    SARVAM_MODEL: str = "sarvam-30b"

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
    # SM secrets: CF_KV_API_TOKEN → CLOUDFLARE_KV_API_TOKEN
    #             CF_ACCOUNT_ID   → CLOUDFLARE_ACCOUNT_ID (also CF_ACCOUNT_ID for Workers AI)
    #             CF_KV_NAMESPACE_ID → CLOUDFLARE_KV_NAMESPACE_ID
    CLOUDFLARE_KV_API_TOKEN: Optional[str] = None
    CLOUDFLARE_ACCOUNT_ID: Optional[str] = None
    CLOUDFLARE_KV_NAMESPACE_ID: Optional[str] = None
    INDEXNOW_KEY: Optional[str] = None

    # --- Cron/CI Translation ---
    TRANSLATE_CRON_SECRET: Optional[str] = None

    # --- Observability ---
    SENTRY_DSN: Optional[str] = None
    SENTRY_ENVIRONMENT: str = "production"
    SENTRY_TRACES_SAMPLE_RATE: float = 0.1
    # Note (HF-109): SENTRY_TRACES_SAMPLE_RATE=0.1 is acceptable for current traffic.
    # Lower to 0.01 if Sentry costs become a concern at scale.
    POSTHOG_API_KEY: Optional[str] = None
    POSTHOG_HOST: str = "https://app.posthog.com"

    # --- Application Logic ---
    APP_ENV: str = "production"
    DEBUG: bool = False
    JWT_SECRET: str = "dev-only-secret-not-for-production-use-32chars"
    ADMIN_JWT_SECRET: Optional[str] = None

    # --- Admin Bootstrap ---
    # Set ADMIN_EMAIL + ADMIN_PASSWORD in Cloud Run env vars to auto-create the
    # admin account on first startup. Subsequent restarts are safe (idempotent).
    # Set ADMIN_FORCE_RESET=true to overwrite the password on every restart.
    ADMIN_EMAIL: Optional[str] = None
    ADMIN_PASSWORD: Optional[str] = None
    ADMIN_FORCE_RESET: bool = False
    RESET_TOKEN_SECRET: Optional[str] = None
    JWT_ALGORITHM: str = "HS256"
    JWT_PRIVATE_KEY: Optional[str] = None  # PEM-encoded RSA private key for RS256
    JWT_PUBLIC_KEY: Optional[str] = None  # PEM-encoded RSA public key for RS256
    JWT_EXPIRY_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRY_DAYS: int = 7
    EDGE_SHARED_SECRET: Optional[str] = None
    TRUST_EDGE_AUTH: bool = True
    ALLOWED_ORIGINS: str = (
        "https://syrabit.ai,https://www.syrabit.ai,https://app.syrabit.ai,"
        "https://api.syrabit.ai,https://syrabitfrontend.pages.dev"
    )
    # Note (HF-108): ALLOWED_ORIGINS uses exact match; Cloudflare Pages preview URLs
    # (subdomain deployments) are handled by is_origin_allowed() regex. Use that method
    # for all origin checks. The bare syrabitfrontend.pages.dev is in ALLOWED_ORIGINS above.
    MAX_CONTEXT_DOCS: int = 5
    STREAM_CHUNK_SIZE: int = 128

    # --- Startup validation errors (populated by validate_production_secrets) ---
    startup_errors: list = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def empty_strings_to_none(cls, values):
        """Convert empty strings to None so Optional fields work correctly.

        Note (HF-112): empty_strings_to_none intentionally converts "" to None for all fields.
        Also maps MONGODB_URL → MONGODB_URI for Replit compatibility.
        """
        if isinstance(values, dict):
            for key, val in values.items():
                if val == "":
                    values[key] = None
            # Replit secret MONGODB_URL → internal MONGODB_URI
            if not values.get("MONGODB_URI") and values.get("MONGODB_URL"):
                values["MONGODB_URI"] = values["MONGODB_URL"]
            # Replit secret CLOUDFLARE_API_TOKEN → CF_API_TOKEN + CF_WORKER_AI_TOKEN
            # The embedder reads CF_WORKER_AI_TOKEN first, falls back to CF_API_TOKEN.
            cf_token = values.get("CLOUDFLARE_API_TOKEN")
            if cf_token:
                if not values.get("CF_API_TOKEN"):
                    values["CF_API_TOKEN"] = cf_token
                if not values.get("CF_WORKER_AI_TOKEN"):
                    values["CF_WORKER_AI_TOKEN"] = cf_token
        return values

    @model_validator(mode="after")
    def validate_production_secrets(self):
        """Validate critical secrets are properly configured in production.

        Instead of raising ValueError (which would crash the app at import time),
        errors are collected in self.startup_errors and logged. This allows the
        app to start and health endpoints to report useful diagnostics.
        """
        # Enforce edge secret when trust is enabled
        if self.TRUST_EDGE_AUTH and not self.EDGE_SHARED_SECRET:
            if self.APP_ENV in ("production", "staging"):
                msg = "EDGE_SHARED_SECRET must be set when TRUST_EDGE_AUTH is True"
                self.startup_errors.append(msg)
                logger.error(f"CONFIG ERROR: {msg}")
            else:
                logger.warning(
                    "TRUST_EDGE_AUTH is True but EDGE_SHARED_SECRET is not set. "
                    "Edge auth trust is effectively disabled in this environment."
                )

        KNOWN_PLACEHOLDER_SECRETS = {
            "super_secret_jwt_key_32_chars_min",
            "CHANGE_ME_IN_PRODUCTION_AT_LEAST_32_CHARS_LONG",
            "test-secret-at-least-32-characters-long",
            "dev-only-secret-not-for-production-use-32chars",
        }

        if self.APP_ENV in ("production", "staging"):
            if self.JWT_SECRET in KNOWN_PLACEHOLDER_SECRETS:
                msg = "JWT_SECRET is a known placeholder value and must be changed in production"
                self.startup_errors.append(msg)
                logger.error(f"CONFIG ERROR: {msg}")
            if len(self.JWT_SECRET) < 32:
                msg = "JWT_SECRET must be at least 32 characters long in production"
                self.startup_errors.append(msg)
                logger.error(f"CONFIG ERROR: {msg}")
            if not self.ADMIN_JWT_SECRET:
                msg = (
                    "ADMIN_JWT_SECRET is required in production for admin key isolation"
                )
                self.startup_errors.append(msg)
                logger.error(f"CONFIG ERROR: {msg}")
            if (
                self.ADMIN_JWT_SECRET
                and self.JWT_PRIVATE_KEY
                and self.ADMIN_JWT_SECRET == self.JWT_PRIVATE_KEY
            ):
                msg = (
                    "ADMIN_JWT_SECRET must not be the same as JWT_PRIVATE_KEY. "
                    "Create a separate secret: openssl rand -base64 48"
                )
                self.startup_errors.append(msg)
                logger.error(f"CONFIG ERROR: {msg}")
            if self.ADMIN_JWT_SECRET and self.ADMIN_JWT_SECRET == self.JWT_SECRET:
                msg = (
                    "ADMIN_JWT_SECRET must not be the same as JWT_SECRET. "
                    "Create a separate secret: openssl rand -base64 48"
                )
                self.startup_errors.append(msg)
                logger.error(f"CONFIG ERROR: {msg}")
            if not self.RESET_TOKEN_SECRET:
                logger.warning(
                    "RESET_TOKEN_SECRET is not set — reset tokens use the shared JWT_SECRET. "
                    "Set a separate RESET_TOKEN_SECRET for improved key isolation."
                )
            if self.JWT_ALGORITHM == "RS256":
                if not self.JWT_PRIVATE_KEY:
                    msg = "JWT_PRIVATE_KEY is required when JWT_ALGORITHM is RS256"
                    self.startup_errors.append(msg)
                    logger.error(f"CONFIG ERROR: {msg}")
                if not self.JWT_PUBLIC_KEY:
                    msg = "JWT_PUBLIC_KEY is required when JWT_ALGORITHM is RS256"
                    self.startup_errors.append(msg)
                    logger.error(f"CONFIG ERROR: {msg}")
            if not self.MONGODB_URI:
                logger.warning("MONGODB_URI is not set in production")
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
        """Check if an origin is allowed, including Cloudflare Pages and Replit dev domains."""
        import re

        if origin in self.allowed_origins_list:
            return True
        # Allow Cloudflare Pages preview URLs
        if re.match(r"^https://[a-z0-9-]+\.syrabitfrontend\.pages\.dev$", origin):
            return True
        # Allow Replit dev preview URLs (always allowed for replit.dev/repl.co domains)
        if re.match(
            r"^https://[a-z0-9-]+\.(sisko\.replit\.dev|repl\.co|replit\.dev|replit\.app)$", origin
        ):
            return True
        return False

    @property
    def google_credentials(self) -> dict:
        """Load Google credentials from file path or inline JSON.

        Priority:
        1. GOOGLE_APPLICATION_CREDENTIALS (file path) - recommended, safer
        2. GOOGLE_SA_KEY (Replit secret — service account JSON)
        3. GOOGLE_APPLICATION_CREDENTIALS_JSON (inline JSON) - legacy fallback
        4. On Cloud Run with Workload Identity, neither is needed (uses ADC)
        """
        import os

        # Option 1: Load from file path
        if self.GOOGLE_APPLICATION_CREDENTIALS:
            creds_path = os.path.expanduser(self.GOOGLE_APPLICATION_CREDENTIALS)
            try:
                with open(creds_path) as f:
                    return json.load(f)
            except (FileNotFoundError, json.JSONDecodeError) as e:
                logger.warning(f"Failed to load credentials from {creds_path}: {e}")

        # Option 2: GOOGLE_SA_KEY Replit secret
        google_sa_key = os.environ.get("GOOGLE_SA_KEY")
        if google_sa_key:
            try:
                return json.loads(google_sa_key)
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse GOOGLE_SA_KEY: {e}")

        # Option 3: Inline JSON string
        if self.GOOGLE_APPLICATION_CREDENTIALS_JSON:
            try:
                return json.loads(self.GOOGLE_APPLICATION_CREDENTIALS_JSON)
            except json.JSONDecodeError as e:
                logger.warning(
                    f"Failed to parse GOOGLE_APPLICATION_CREDENTIALS_JSON: {e}"
                )

        return {}


settings = Settings()
