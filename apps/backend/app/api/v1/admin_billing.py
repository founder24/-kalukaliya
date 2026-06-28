"""
Admin Billing / Cloud Credits Endpoints
Surfaces GCP, Axiom, and Sentry usage/credit data.
All integrations are optional — returns configured:false when creds absent.
"""

from fastapi import APIRouter, Depends
import logging
from datetime import datetime, timezone

from app.api.v1.admin import require_admin_session, csrf_guard
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Admin Billing"],
    dependencies=[Depends(require_admin_session), Depends(csrf_guard)],
)


@router.get("/billing/gcp-credits")
async def billing_gcp_credits():
    """
    GCP budget info via Cloud Billing API.
    Requires GCP_BILLING_ACCOUNT_ID + a SA with roles/billing.viewer on the billing account.
    Returns configured:false with a setup message when the secret is absent.
    """
    billing_account = getattr(settings, "GCP_BILLING_ACCOUNT_ID", None)
    if not billing_account:
        return {
            "configured": False,
            "credits_usd": None,
            "spend_this_month_usd": None,
            "spend_last_month_usd": None,
            "budget_usd": None,
            "budget_alert_threshold": None,
            "currency": "USD",
            "as_of": None,
            "message": (
                "Set GCP_BILLING_ACCOUNT_ID secret to enable credit tracking. "
                "The service account also needs roles/billing.viewer on the billing account."
            ),
        }

    try:
        import httpx, json

        creds_json = getattr(settings, "GOOGLE_APPLICATION_CREDENTIALS_JSON", None)
        if not creds_json:
            raise ValueError("GOOGLE_APPLICATION_CREDENTIALS_JSON not set")

        creds_data = json.loads(creds_json)

        async with httpx.AsyncClient(timeout=15.0) as client:
            token_resp = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": _make_sa_jwt(
                        creds_data,
                        ["https://www.googleapis.com/auth/cloud-billing.readonly"],
                    ),
                },
            )
            if not token_resp.is_success:
                raise ValueError(f"Token error: {token_resp.text}")
            access_token = token_resp.json()["access_token"]

            resp = await client.get(
                f"https://cloudbilling.googleapis.com/v1/billingAccounts/{billing_account}/budgets",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if resp.is_success:
                budgets = resp.json().get("budgets", [])
                budget_usd = None
                if budgets:
                    amount = budgets[0].get("amount", {}).get("specifiedAmount", {})
                    budget_usd = float(amount.get("units", 0)) + float(amount.get("nanos", 0)) / 1e9

                return {
                    "configured": True,
                    "credits_usd": None,
                    "spend_this_month_usd": None,
                    "spend_last_month_usd": None,
                    "budget_usd": budget_usd,
                    "budget_alert_threshold": None,
                    "currency": "USD",
                    "as_of": datetime.now(timezone.utc).isoformat(),
                    "source": "cloud_billing_api",
                    "message": "Credit spend breakdown requires BigQuery billing export — see GCP docs.",
                }
    except Exception as e:
        logger.warning(f"GCP billing fetch failed: {e}")

    return {
        "configured": bool(billing_account),
        "credits_usd": None,
        "spend_this_month_usd": None,
        "spend_last_month_usd": None,
        "budget_usd": None,
        "budget_alert_threshold": None,
        "currency": "USD",
        "as_of": None,
        "source": "unavailable",
        "message": "Could not fetch GCP billing data. Check SA permissions and GCP_BILLING_ACCOUNT_ID.",
    }


def _make_sa_jwt(creds: dict, scopes: list) -> str:
    """Build a signed JWT for Google service account OAuth2."""
    import time, base64, json
    try:
        from cryptography.hazmat.primitives import serialization, hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.backends import default_backend

        now = int(time.time())
        header = base64.urlsafe_b64encode(
            json.dumps({"alg": "RS256", "typ": "JWT"}).encode()
        ).rstrip(b"=")
        payload = base64.urlsafe_b64encode(
            json.dumps({
                "iss": creds["client_email"],
                "scope": " ".join(scopes),
                "aud": "https://oauth2.googleapis.com/token",
                "exp": now + 3600,
                "iat": now,
            }).encode()
        ).rstrip(b"=")

        to_sign = header + b"." + payload
        private_key = serialization.load_pem_private_key(
            creds["private_key"].encode(), password=None, backend=default_backend()
        )
        signature = base64.urlsafe_b64encode(
            private_key.sign(to_sign, padding.PKCS1v15(), hashes.SHA256())
        ).rstrip(b"=")
        return (to_sign + b"." + signature).decode()
    except Exception as e:
        raise ValueError(f"SA JWT creation failed: {e}")


@router.get("/billing/axiom")
async def billing_axiom():
    """Axiom usage/limits — returns configured:false until AXIOM_TOKEN is set."""
    axiom_token = (
        getattr(settings, "AXIOM_TOKEN", None)
        or getattr(settings, "AXIOM_API_TOKEN", None)
    )
    if not axiom_token:
        return {
            "configured": False,
            "events_ingested": None,
            "events_limit": None,
            "retention_days": None,
            "as_of": None,
            "message": "Set AXIOM_TOKEN secret to enable Axiom usage tracking.",
        }

    try:
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.axiom.co/v1/user",
                headers={"Authorization": f"Bearer {axiom_token}"},
            )
            if resp.is_success:
                user = resp.json()
                return {
                    "configured": True,
                    "org": user.get("organization", {}).get("name"),
                    "plan": user.get("organization", {}).get("plan"),
                    "events_ingested": None,
                    "events_limit": None,
                    "retention_days": None,
                    "as_of": datetime.now(timezone.utc).isoformat(),
                    "source": "axiom_api",
                }
    except Exception as e:
        logger.warning(f"Axiom fetch failed: {e}")

    return {
        "configured": bool(axiom_token),
        "events_ingested": None,
        "events_limit": None,
        "retention_days": None,
        "as_of": None,
        "source": "unavailable",
        "message": "Could not reach Axiom API.",
    }


@router.get("/billing/sentry")
async def billing_sentry():
    """Sentry error quota and plan info."""
    sentry_token = getattr(settings, "SENTRY_AUTH_TOKEN", None)
    sentry_org = getattr(settings, "SENTRY_ORG_SLUG", None)
    if not sentry_token or not sentry_org:
        return {
            "configured": False,
            "errors_used": None,
            "errors_limit": None,
            "plan": None,
            "as_of": None,
            "message": (
                "Set SENTRY_AUTH_TOKEN and SENTRY_ORG_SLUG secrets "
                "to enable Sentry quota tracking."
            ),
        }

    try:
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://sentry.io/api/0/organizations/{sentry_org}/",
                headers={"Authorization": f"Bearer {sentry_token}"},
            )
            if resp.is_success:
                data = resp.json()
                quota = data.get("quota", {})
                return {
                    "configured": True,
                    "plan": data.get("plan", {}).get("name"),
                    "errors_used": quota.get("used"),
                    "errors_limit": quota.get("max"),
                    "as_of": datetime.now(timezone.utc).isoformat(),
                    "source": "sentry_api",
                }
    except Exception as e:
        logger.warning(f"Sentry billing fetch failed: {e}")

    return {
        "configured": bool(sentry_token and sentry_org),
        "errors_used": None,
        "errors_limit": None,
        "plan": None,
        "as_of": None,
        "source": "unavailable",
        "message": "Could not fetch Sentry quota. Check SENTRY_AUTH_TOKEN and SENTRY_ORG_SLUG.",
    }


@router.get("/billing/tokens")
async def billing_tokens():
    """
    Token spend summary per AI provider from the ai_usage_logs collection.
    Covers the last 24 h.
    """
    from datetime import timedelta
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        agg = await (await db.ai_usage_logs.aggregate([
            {"$match": {"created_at": {"$gte": since}}},
            {"$group": {
                "_id": "$provider",
                "calls": {"$sum": 1},
                "input_tokens": {"$sum": "$input_tokens"},
                "output_tokens": {"$sum": "$output_tokens"},
            }},
        ])).to_list(length=20)
        providers = [
            {
                "provider": r["_id"],
                "calls": r["calls"],
                "input_tokens": r["input_tokens"],
                "output_tokens": r["output_tokens"],
                "total_tokens": r["input_tokens"] + r["output_tokens"],
            }
            for r in agg
        ]
        return {
            "window_hours": 24,
            "providers": providers,
            "total_calls": sum(p["calls"] for p in providers),
            "total_tokens": sum(p["total_tokens"] for p in providers),
            "source": "ai_usage_logs" if providers else "empty",
            "as_of": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"billing/tokens error: {e}")
        return {"window_hours": 24, "providers": [], "total_calls": 0,
                "total_tokens": 0, "source": "unavailable"}
