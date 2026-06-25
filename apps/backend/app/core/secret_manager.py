"""
GCP Secret Manager client for the Syrabit backend.

Fetches runtime secrets from Google Cloud Secret Manager using the
service-account JSON stored in GOOGLE_APPLICATION_CREDENTIALS_JSON.
This keeps sensitive values out of Replit environment variables and
provides a single, auditable secrets source for both Replit dev and
Cloud Run production.

Usage (called once in main.py lifespan):
    from app.core.secret_manager import load_secrets_into_settings
    await load_secrets_into_settings()
"""

import asyncio
import logging
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)

# Map: settings attribute name → GCP Secret Manager secret ID
_SECRET_MAP: dict[str, str] = {
    "SARVAM_API_KEY": "SARVAM_API_KEY",
    "JWT_SECRET": "JWT_SECRET",
    "ADMIN_JWT_SECRET": "ADMIN_JWT_SECRET",
    "RESET_TOKEN_SECRET": "RESET_TOKEN_SECRET",
    "RAZORPAY_KEY_ID": "RAZORPAY_KEY_ID",
    "RAZORPAY_KEY_SECRET": "RAZORPAY_KEY_SECRET",
    "RAZORPAY_WEBHOOK_SECRET": "RAZORPAY_WEBHOOK_SECRET",
    "RESEND_API_KEY": "RESEND_API_KEY",
    "POSTHOG_API_KEY": "POSTHOG_API_KEY",
    "INDEXNOW_API_KEY": "INDEXNOW_API_KEY",
    "EDGE_SHARED_SECRET": "EDGE_SHARED_SECRET",
}


def _build_sm_client():
    """Build a synchronous SecretManagerServiceClient using the service account."""
    from google.cloud import secretmanager
    from google.oauth2 import service_account

    creds_info = settings.google_credentials
    if not creds_info:
        raise RuntimeError(
            "GOOGLE_APPLICATION_CREDENTIALS_JSON is not set — "
            "cannot authenticate to GCP Secret Manager"
        )

    credentials = service_account.Credentials.from_service_account_info(
        creds_info,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    return secretmanager.SecretManagerServiceClient(credentials=credentials)


def _fetch_secret_sync(client, project_id: str, secret_id: str) -> Optional[str]:
    """Fetch the latest version of a secret (synchronous — run in executor)."""
    name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
    try:
        response = client.access_secret_version(request={"name": name})
        value = response.payload.data.decode("utf-8").strip()
        return value if value else None
    except Exception as e:
        logger.warning(f"Secret Manager: could not fetch '{secret_id}': {e}")
        return None


async def fetch_secret(secret_id: str) -> Optional[str]:
    """
    Async wrapper: fetch a single secret from GCP Secret Manager.

    Returns the secret value string, or None if the secret doesn't exist
    or the service account lacks access.
    """
    creds_info = settings.google_credentials
    if not creds_info:
        logger.warning("Secret Manager: no GCP credentials configured — skipping")
        return None

    project_id = creds_info.get("project_id")
    if not project_id:
        logger.warning("Secret Manager: project_id not found in credentials")
        return None

    try:
        client = await asyncio.to_thread(_build_sm_client)
        value = await asyncio.to_thread(
            _fetch_secret_sync, client, project_id, secret_id
        )
        return value
    except Exception as e:
        logger.error(f"Secret Manager: unexpected error fetching '{secret_id}': {e}")
        return None


async def load_secrets_into_settings() -> dict[str, str]:
    """
    Fetch all secrets in _SECRET_MAP from GCP Secret Manager and inject
    them into the live `settings` object if the setting is currently empty.

    This is the primary entry point — call once from the FastAPI lifespan.

    Returns a dict of {secret_id: "loaded" | "skipped" | "failed"}.
    """
    creds_info = settings.google_credentials
    if not creds_info:
        logger.warning(
            "Secret Manager: no GCP credentials found (checked GOOGLE_SA_KEY, "
            "GOOGLE_APPLICATION_CREDENTIALS_JSON, GOOGLE_APPLICATION_CREDENTIALS) — "
            "all secrets will use env-var fallback"
        )
        return {sid: "skipped_no_creds" for sid in _SECRET_MAP.values()}

    project_id = creds_info.get("project_id", "?")
    logger.info(
        f"Secret Manager: loading secrets from project={project_id} "
        f"using SA={creds_info.get('client_email', '?')}"
    )

    try:
        client = await asyncio.to_thread(_build_sm_client)
    except Exception as e:
        logger.error(f"Secret Manager: failed to build client: {e}")
        return {sid: "failed_client" for sid in _SECRET_MAP.values()}

    results: dict[str, str] = {}
    for attr_name, secret_id in _SECRET_MAP.items():
        current_val = getattr(settings, attr_name, None)

        value = await asyncio.to_thread(
            _fetch_secret_sync, client, project_id, secret_id
        )

        if value is None:
            status = "not_found"
            logger.warning(
                f"Secret Manager: '{secret_id}' not found or inaccessible — "
                f"keeping existing value ({'set' if current_val else 'empty'})"
            )
        else:
            # Always prefer SM value over env var (SM is authoritative)
            object.__setattr__(settings, attr_name, value)
            status = "loaded"
            logger.info(
                f"Secret Manager: '{secret_id}' loaded successfully "
                f"(len={len(value)}, prefix={value[:8]}...)"
            )

        results[secret_id] = status

    return results

