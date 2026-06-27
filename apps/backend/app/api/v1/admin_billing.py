"""
Admin Billing / Cloud Credits Endpoints
Surfaces GCP, AWS Activate, Axiom, and Sentry usage/credit data.
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
    GCP credit balance via Cloud Billing API.
    Requires GCP_BILLING_ACCOUNT_ID + a SA with billing.accounts.get permission.
    Returns configured:false with an explanation when creds are absent.
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
            "message": "Set GCP_BILLING_ACCOUNT_ID secret to enable credit tracking. "
                       "The service account also needs roles/billing.viewer on the billing account.",
        }

    try:
        import httpx, json

        creds_json = getattr(settings, "GOOGLE_APPLICATION_CREDENTIALS_JSON", None)
        if not creds_json:
            raise ValueError("GOOGLE_APPLICATION_CREDENTIALS_JSON not set")

        creds_data = json.loads(creds_json)
        project_id = creds_data.get("project_id", settings.VERTEX_PROJECT_ID)

        async with httpx.AsyncClient(timeout=15.0) as client:
            token_resp = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": _make_jwt(creds_data, ["https://www.googleapis.com/auth/cloud-billing.readonly"]),
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


def _make_jwt(creds: dict, scopes: list) -> str:
    """Minimal JWT for Google service account auth."""
    import time, base64, json, hashlib
    try:
        from cryptography.hazmat.primitives import serialization, hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.backends import default_backend

        now = int(time.time())
        header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256", "typ": "JWT"}).encode()).rstrip(b"=")
        payload = base64.urlsafe_b64encode(json.dumps({
            "iss": creds["client_email"],
            "scope": " ".join(scopes),
            "aud": "https://oauth2.googleapis.com/token",
            "exp": now + 3600,
            "iat": now,
        }).encode()).rstrip(b"=")

        to_sign = header + b"." + payload
        private_key = serialization.load_pem_private_key(
            creds["private_key"].encode(), password=None, backend=default_backend()
        )
        signature = base64.urlsafe_b64encode(
            private_key.sign(to_sign, padding.PKCS1v15(), hashes.SHA256())
        ).rstrip(b"=")
        return (to_sign + b"." + signature).decode()
    except Exception as e:
        raise ValueError(f"JWT creation failed: {e}")


@router.get("/billing/aws-activate")
async def billing_aws_activate():
    """
    AWS Activate credit status.
    Requires AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY with Cost Explorer read permission.
    """
    aws_key = getattr(settings, "AWS_ACCESS_KEY_ID", None)
    if not aws_key:
        return {
            "configured": False,
            "credits_usd": None,
            "spend_this_month_usd": None,
            "expiry_date": None,
            "currency": "USD",
            "as_of": None,
            "message": "Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY secrets to enable AWS credit tracking.",
        }

    try:
        import httpx, hmac, hashlib, datetime as dt

        secret = getattr(settings, "AWS_SECRET_ACCESS_KEY", "")
        region = getattr(settings, "AWS_REGION", "us-east-1")

        today = dt.date.today()
        start = today.replace(day=1).isoformat()
        end = today.isoformat()

        payload = {
            "TimePeriod": {"Start": start, "End": end},
            "Granularity": "MONTHLY",
            "Metrics": ["BlendedCost"],
        }

        import json
        body = json.dumps(payload)
        now_dt = dt.datetime.utcnow()
        amz_date = now_dt.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now_dt.strftime("%Y%m%d")
        host = "ce.us-east-1.amazonaws.com"

        def sign(key, msg):
            return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

        def get_signature_key(key, date_stamp, region, service):
            k_date = sign(("AWS4" + key).encode("utf-8"), date_stamp)
            k_region = sign(k_date, region)
            k_service = sign(k_region, service)
            k_signing = sign(k_service, "aws4_request")
            return k_signing

        content_type = "application/x-amz-json-1.1"
        amz_target = "AWSInsightsIndexService.GetCostAndUsage"
        canonical_headers = (
            f"content-type:{content_type}\nhost:{host}\n"
            f"x-amz-date:{amz_date}\nx-amz-target:{amz_target}\n"
        )
        signed_headers = "content-type;host;x-amz-date;x-amz-target"
        payload_hash = hashlib.sha256(body.encode()).hexdigest()
        canonical_request = (
            f"POST\n/\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
        )
        credential_scope = f"{date_stamp}/{region}/ce/aws4_request"
        string_to_sign = (
            f"AWS4-HMAC-SHA256\n{amz_date}\n{credential_scope}\n"
            + hashlib.sha256(canonical_request.encode()).hexdigest()
        )
        signing_key = get_signature_key(secret, date_stamp, region, "ce")
        signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        auth = (
            f"AWS4-HMAC-SHA256 Credential={aws_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"https://{host}/",
                content=body,
                headers={
                    "Content-Type": content_type,
                    "X-Amz-Date": amz_date,
                    "X-Amz-Target": amz_target,
                    "Authorization": auth,
                },
            )
            if resp.is_success:
                data = resp.json()
                results = data.get("ResultsByTime", [])
                spend = 0.0
                if results:
                    spend = float(results[0].get("Total", {}).get("BlendedCost", {}).get("Amount", 0))
                return {
                    "configured": True,
                    "credits_usd": None,
                    "spend_this_month_usd": round(spend, 2),
                    "expiry_date": None,
                    "currency": "USD",
                    "as_of": datetime.now(timezone.utc).isoformat(),
                    "source": "aws_cost_explorer",
                }

    except Exception as e:
        logger.warning(f"AWS billing fetch failed: {e}")

    return {
        "configured": bool(aws_key),
        "credits_usd": None,
        "spend_this_month_usd": None,
        "expiry_date": None,
        "currency": "USD",
        "as_of": None,
        "source": "unavailable",
        "message": "Could not fetch AWS cost data. Check key permissions.",
    }


@router.get("/billing/axiom")
async def billing_axiom():
    """Axiom usage/limits — returns configured:false until AXIOM_TOKEN is set."""
    axiom_token = getattr(settings, "AXIOM_TOKEN", None) or getattr(settings, "AXIOM_API_TOKEN", None)
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
            "message": "Set SENTRY_AUTH_TOKEN and SENTRY_ORG_SLUG secrets to enable Sentry quota tracking.",
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
