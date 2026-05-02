"""routes.admin_billing — Live credit-burn panels for AWS Activate, Azure for
Startups, Axiom, and Sentry (Task #264).

GET /admin/billing/aws-activate
  Calls AWS Cost Explorer (ce:GetCostAndUsage) for month-to-date spend against
  the AWS Activate programme grant.  Returns:
    { configured, grant_usd, spend_mtd_usd, estimated_remaining_usd,
      months_runway, credits_low, expiry_date, days_until_expiry,
      account_alias, services }

GET /admin/billing/azure-startups
  Authenticates via client-credentials OAuth2 and calls Azure Cost Management
  REST API for MTD spend.  Returns the same spend shape plus subscription_name.

GET /admin/billing/axiom
  Calls Axiom Cloud API for organisation plan limits and dataset ingest usage.
  Returns: { configured, ingest_gb, ingest_limit_gb, retention_days, over_limit }

GET /admin/billing/sentry
  Calls Sentry Stats v2 API and subscription endpoint for error / performance
  quota consumption.  Returns:
    { configured, plan, errors_used, errors_limit, perf_transactions_used,
      perf_transactions_limit, over_limit, expiry_date, days_until_expiry }

All endpoints:
  • Admin-gated via get_admin_user dependency
  • Always return HTTP 200 — errors are represented inside the payload
  • Redis-cached for 5 minutes via asyncio.to_thread over the sync Upstash client
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import date, datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Depends

from auth_deps import get_admin_user

logger = logging.getLogger(__name__)
router = APIRouter(tags=["admin-billing"])

_CACHE_TTL_S = 300              # 5 minutes
_HTTP_TIMEOUT_S = 12.0
_CREDITS_LOW_THRESHOLD = 0.20   # < 20 % of grant total


# ── Helpers ───────────────────────────────────────────────────────────────────

def _days_until(date_str: str | None) -> int | None:
    """Calendar days from today until *date_str* (YYYY-MM-DD), or None."""
    if not date_str:
        return None
    try:
        return (date.fromisoformat(date_str.strip()) - date.today()).days
    except ValueError:
        return None


def _months_runway(remaining: float | None, monthly_spend: float | None) -> float | None:
    """Months of runway based on remaining credits and average monthly spend."""
    if remaining is None:
        return None
    if not monthly_spend or monthly_spend <= 0:
        return 999.0    # effectively infinite
    return round(remaining / monthly_spend, 1)


# Redis helpers: the Upstash client in deps.py is the synchronous REST client
# (upstash_redis.Redis).  We wrap every call in asyncio.to_thread so the event
# loop is not blocked and the cache actually functions.

def _redis_get_sync(key: str) -> Any | None:
    """Synchronous Redis GET.  Returns deserialised value or None."""
    try:
        from deps import redis_client
        if redis_client:
            raw = redis_client.get(key)
            if raw:
                return json.loads(raw)
    except Exception:
        pass
    return None


def _redis_set_sync(key: str, value: Any) -> None:
    """Synchronous Redis SET with 5-minute TTL.  Silently ignores errors."""
    try:
        from deps import redis_client
        if redis_client:
            redis_client.set(key, json.dumps(value), ex=_CACHE_TTL_S)
    except Exception:
        pass


async def _cache_get(key: str) -> Any | None:
    return await asyncio.to_thread(_redis_get_sync, key)


async def _cache_set(key: str, value: Any) -> None:
    await asyncio.to_thread(_redis_set_sync, key, value)


# ── AWS Activate ──────────────────────────────────────────────────────────────

def _aws_env() -> dict[str, str]:
    return {
        "key_id":    os.environ.get("AWS_ACCESS_KEY_ID", "").strip(),
        "secret":    os.environ.get("AWS_SECRET_ACCESS_KEY", "").strip(),
        "grant_usd": os.environ.get("AWS_ACTIVATE_GRANT_USD", "").strip(),
        "expiry":    os.environ.get("AWS_ACTIVATE_EXPIRY", "").strip(),
    }


def _fetch_aws_billing() -> dict[str, Any]:
    """Blocking AWS Cost Explorer call — intended to run in a thread."""
    cfg = _aws_env()

    try:
        import boto3

        ce = boto3.client(
            "ce",
            aws_access_key_id=cfg["key_id"],
            aws_secret_access_key=cfg["secret"],
            region_name="us-east-1",
        )

        today = date.today()
        month_start = today.replace(day=1).isoformat()
        # CE end date is exclusive; advance by one day if start == end (1st of month).
        if month_start == today.isoformat():
            from datetime import timedelta
            end_date = (today + timedelta(days=1)).isoformat()
        else:
            end_date = today.isoformat()

        resp = ce.get_cost_and_usage(
            TimePeriod={"Start": month_start, "End": end_date},
            Granularity="MONTHLY",
            Metrics=["BlendedCost"],
        )
        results = resp.get("ResultsByTime", [])
        spend_mtd = 0.0
        if results:
            amount_str = (
                results[0].get("Total", {})
                          .get("BlendedCost", {})
                          .get("Amount", "0")
            )
            spend_mtd = round(float(amount_str), 2)

        # Per-service breakdown (best-effort, for the badge list in the UI).
        services: list[str] = []
        try:
            svc_resp = ce.get_cost_and_usage(
                TimePeriod={"Start": month_start, "End": end_date},
                Granularity="MONTHLY",
                Metrics=["BlendedCost"],
                GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
            )
            for period in svc_resp.get("ResultsByTime", []):
                for grp in period.get("Groups", []):
                    cost = float(grp["Metrics"]["BlendedCost"]["Amount"])
                    if cost > 0.0001:
                        services.append(grp["Keys"][0])
        except Exception:
            pass

        # Account alias (best-effort).
        account_alias: str | None = None
        try:
            iam = boto3.client(
                "iam",
                aws_access_key_id=cfg["key_id"],
                aws_secret_access_key=cfg["secret"],
            )
            aliases = iam.list_account_aliases().get("AccountAliases", [])
            account_alias = aliases[0] if aliases else None
        except Exception:
            pass

        grant_usd = float(cfg["grant_usd"]) if cfg["grant_usd"] else None
        expiry_date = cfg["expiry"] or None
        days_until_expiry = _days_until(expiry_date)

        estimated_remaining: float | None = None
        credits_low = False
        if grant_usd is not None:
            estimated_remaining = round(grant_usd - spend_mtd, 2)
            credits_low = estimated_remaining < grant_usd * _CREDITS_LOW_THRESHOLD

        runway = _months_runway(estimated_remaining, spend_mtd)

        return {
            "configured":              True,
            "grant_usd":               grant_usd,
            "spend_mtd_usd":           spend_mtd,
            "estimated_remaining_usd": estimated_remaining,
            "months_runway":           runway,
            "credits_low":             credits_low,
            "expiry_date":             expiry_date,
            "days_until_expiry":       days_until_expiry,
            "account_alias":           account_alias,
            "services":                services,
        }

    except Exception as exc:
        logger.warning("[admin-billing/aws] Cost Explorer error: %s", exc)
        cfg = _aws_env()
        return {
            "configured":              True,
            "error":                   str(exc),
            "grant_usd":               float(cfg["grant_usd"]) if cfg["grant_usd"] else None,
            "spend_mtd_usd":           None,
            "estimated_remaining_usd": None,
            "months_runway":           None,
            "credits_low":             False,
            "expiry_date":             cfg["expiry"] or None,
            "days_until_expiry":       _days_until(cfg["expiry"] or None),
            "account_alias":           None,
            "services":                [],
        }


@router.get(
    "/admin/billing/aws-activate",
    summary="AWS Activate credit-burn panel (Task #264)",
)
async def admin_billing_aws(
    _admin: dict = Depends(get_admin_user),
) -> dict[str, Any]:
    """Return AWS Activate programme spend and runway.  Always HTTP 200."""
    cache_key = "admin_billing:aws_activate"
    cached = await _cache_get(cache_key)
    if cached is not None:
        return cached

    cfg = _aws_env()
    if not cfg["key_id"] or not cfg["secret"]:
        result: dict[str, Any] = {"configured": False}
    else:
        result = await asyncio.to_thread(_fetch_aws_billing)

    await _cache_set(cache_key, result)
    return result


# ── Azure for Startups ────────────────────────────────────────────────────────

def _azure_env() -> dict[str, str]:
    return {
        "client_id":     os.environ.get("AZURE_CLIENT_ID", "").strip(),
        "client_secret": os.environ.get("AZURE_CLIENT_SECRET", "").strip(),
        "tenant_id":     os.environ.get("AZURE_TENANT_ID", "").strip(),
        "sub_id":        os.environ.get("AZURE_SUBSCRIPTION_ID", "").strip(),
        "grant_usd":     os.environ.get("AZURE_ACTIVATE_GRANT_USD", "").strip(),
        "expiry":        os.environ.get("AZURE_ACTIVATE_EXPIRY", "").strip(),
    }


async def _fetch_azure_billing() -> dict[str, Any]:
    """Call Azure Cost Management REST API and return the credit-burn payload."""
    cfg = _azure_env()
    grant_usd = float(cfg["grant_usd"]) if cfg["grant_usd"] else None
    expiry_date = cfg["expiry"] or None
    days_until_expiry = _days_until(expiry_date)

    try:
        # 1. Acquire OAuth2 token via client credentials.
        token_url = (
            f"https://login.microsoftonline.com/{cfg['tenant_id']}"
            "/oauth2/v2.0/token"
        )
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
            token_resp = await client.post(
                token_url,
                data={
                    "grant_type":    "client_credentials",
                    "client_id":     cfg["client_id"],
                    "client_secret": cfg["client_secret"],
                    "scope":         "https://management.azure.com/.default",
                },
            )
            token_resp.raise_for_status()
            access_token: str = token_resp.json()["access_token"]

        arm_headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type":  "application/json",
        }
        base = "https://management.azure.com"
        sub_url = (
            f"{base}/subscriptions/{cfg['sub_id']}"
            "?api-version=2020-01-01"
        )
        cost_url = (
            f"{base}/subscriptions/{cfg['sub_id']}"
            "/providers/Microsoft.CostManagement/query"
            "?api-version=2023-11-01"
        )

        today = date.today()
        month_start = today.replace(day=1).strftime("%Y-%m-%dT00:00:00+00:00")
        month_end   = today.strftime("%Y-%m-%dT23:59:59+00:00")

        cost_body = {
            "type": "ActualCost",
            "timeframe": "Custom",
            "timePeriod": {"from": month_start, "to": month_end},
            "dataset": {
                "granularity": "None",
                "aggregation": {
                    "totalCost": {"name": "Cost", "function": "Sum"},
                },
            },
        }

        # 2. Fetch subscription name (optional) and MTD cost (required) in parallel.
        # subscription name is a nice-to-have — we suppress its errors below.
        # cost query is required — any failure (transport or non-2xx) surfaces as an error.
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
            sub_resp, cost_resp = await asyncio.gather(
                client.get(sub_url, headers=arm_headers),
                client.post(cost_url, headers=arm_headers, json=cost_body),
                return_exceptions=True,
            )

        # Cost query: re-raise transport exceptions and HTTP errors so the outer
        # except block converts them to an error payload instead of silently
        # returning zero spend.
        if isinstance(cost_resp, BaseException):
            raise cost_resp
        cost_resp.raise_for_status()

        subscription_name: str | None = None
        if isinstance(sub_resp, httpx.Response):
            try:
                subscription_name = sub_resp.json().get("displayName")
            except Exception:
                pass

        spend_mtd = 0.0
        rows = (
            cost_resp.json()
                     .get("properties", {})
                     .get("rows", [])
        )
        if rows:
            spend_mtd = round(float(rows[0][0]), 2)

        estimated_remaining: float | None = None
        credits_low = False
        if grant_usd is not None:
            estimated_remaining = round(grant_usd - spend_mtd, 2)
            credits_low = estimated_remaining < grant_usd * _CREDITS_LOW_THRESHOLD

        runway = _months_runway(estimated_remaining, spend_mtd)

        return {
            "configured":              True,
            "grant_usd":               grant_usd,
            "spend_mtd_usd":           spend_mtd,
            "estimated_remaining_usd": estimated_remaining,
            "months_runway":           runway,
            "credits_low":             credits_low,
            "expiry_date":             expiry_date,
            "days_until_expiry":       days_until_expiry,
            "subscription_name":       subscription_name,
        }

    except Exception as exc:
        logger.warning("[admin-billing/azure] Cost Management error: %s", exc)
        return {
            "configured":              True,
            "error":                   str(exc),
            "grant_usd":               grant_usd,
            "spend_mtd_usd":           None,
            "estimated_remaining_usd": None,
            "months_runway":           None,
            "credits_low":             False,
            "expiry_date":             expiry_date,
            "days_until_expiry":       days_until_expiry,
            "subscription_name":       None,
        }


@router.get(
    "/admin/billing/azure-startups",
    summary="Azure for Startups credit-burn panel (Task #264)",
)
async def admin_billing_azure(
    _admin: dict = Depends(get_admin_user),
) -> dict[str, Any]:
    """Return Azure for Startups programme spend and runway.  Always HTTP 200."""
    cache_key = "admin_billing:azure_startups"
    cached = await _cache_get(cache_key)
    if cached is not None:
        return cached

    cfg = _azure_env()
    if not (cfg["client_id"] and cfg["client_secret"] and cfg["tenant_id"] and cfg["sub_id"]):
        result: dict[str, Any] = {"configured": False}
    else:
        result = await _fetch_azure_billing()

    await _cache_set(cache_key, result)
    return result


# ── Axiom ─────────────────────────────────────────────────────────────────────

def _axiom_env() -> dict[str, str]:
    return {
        "token":  os.environ.get("AXIOM_API_TOKEN", "").strip(),
        "org_id": os.environ.get("AXIOM_ORG_ID", "").strip(),
    }


async def _fetch_axiom_usage() -> dict[str, Any]:
    """Call Axiom Cloud API for organisation plan limits and dataset ingest usage."""
    cfg = _axiom_env()
    headers = {
        "Authorization": f"Bearer {cfg['token']}",
        "Content-Type":  "application/json",
    }

    # Default Startup tier values; overridden below if the API returns them.
    ingest_limit_gb = 500   # Axiom Startup tier: 500 GB/mo
    retention_days  = 30    # Axiom Startup tier: 30-day retention

    try:
        async with httpx.AsyncClient(
            base_url="https://api.axiom.co",
            headers=headers,
            timeout=_HTTP_TIMEOUT_S,
        ) as client:
            # Fetch datasets list and (if org_id is known) org info in parallel.
            tasks: list[Any] = [client.get("/v2/datasets")]
            if cfg["org_id"]:
                tasks.append(client.get(f"/v2/orgs/{cfg['org_id']}"))
            else:
                # Fall back to listing all orgs the token can see.
                tasks.append(client.get("/v2/orgs"))

            responses = await asyncio.gather(*tasks, return_exceptions=True)

        datasets_resp = responses[0]
        org_resp      = responses[1] if len(responses) > 1 else None

        # Parse org info for plan limits.
        if isinstance(org_resp, httpx.Response) and org_resp.status_code == 200:
            try:
                org_data = org_resp.json()
                # Single org object when org_id was provided.
                if isinstance(org_data, dict):
                    plan = org_data.get("plan") or {}
                    # Some fields vary by Axiom plan API version.
                    limit_bytes = (
                        plan.get("ingestStorageLimit")
                        or plan.get("ingestLimitGb")
                        or org_data.get("ingestLimitGb")
                    )
                    if limit_bytes and limit_bytes > 1024:
                        # If stored in bytes, convert to GB.
                        ingest_limit_gb = round(limit_bytes / (1024 ** 3))
                    elif limit_bytes:
                        ingest_limit_gb = int(limit_bytes)
                    ret = (
                        plan.get("retention")
                        or plan.get("retentionDays")
                        or org_data.get("retentionDays")
                    )
                    if ret:
                        retention_days = int(ret)
                # List of orgs — pick the one matching org_id (or first entry).
                elif isinstance(org_data, list) and org_data:
                    target = next(
                        (o for o in org_data if o.get("id") == cfg["org_id"]),
                        org_data[0],
                    )
                    limit_val = (
                        target.get("plan", {}).get("ingestStorageLimit")
                        or target.get("ingestLimitGb")
                    )
                    if limit_val and limit_val > 1024:
                        ingest_limit_gb = round(limit_val / (1024 ** 3))
                    elif limit_val:
                        ingest_limit_gb = int(limit_val)
                    ret = target.get("plan", {}).get("retention") or target.get("retentionDays")
                    if ret:
                        retention_days = int(ret)
            except Exception as parse_exc:
                logger.debug("[admin-billing/axiom] org parse error: %s", parse_exc)

        # Parse dataset list — sum inputBytes for total historical ingest.
        ingest_gb: float | None = None
        if isinstance(datasets_resp, httpx.Response):
            datasets_resp.raise_for_status()
            datasets: list[dict] = datasets_resp.json()

            total_input_bytes: int = 0
            ds_min_retention: int | None = None
            for ds in datasets:
                total_input_bytes += ds.get("inputBytes", 0) or 0
                ds_ret = ds.get("retentionDays") or ds.get("retention_days")
                if ds_ret:
                    ds_min_retention = min(ds_min_retention or int(ds_ret), int(ds_ret))

            ingest_gb = round(total_input_bytes / (1024 ** 3), 3)
            if ds_min_retention and retention_days == 30:
                # Prefer dataset-level retention if org-level wasn't resolved.
                retention_days = ds_min_retention

        over_limit = (ingest_gb or 0) > ingest_limit_gb

        return {
            "configured":      True,
            "ingest_gb":       ingest_gb,
            "ingest_limit_gb": ingest_limit_gb,
            "retention_days":  retention_days,
            "over_limit":      over_limit,
        }

    except Exception as exc:
        logger.warning("[admin-billing/axiom] API error: %s", exc)
        return {
            "configured":      True,
            "error":           str(exc),
            "ingest_gb":       None,
            "ingest_limit_gb": ingest_limit_gb,
            "retention_days":  retention_days,
            "over_limit":      False,
        }


@router.get(
    "/admin/billing/axiom",
    summary="Axiom startup-tier usage panel (Task #264)",
)
async def admin_billing_axiom(
    _admin: dict = Depends(get_admin_user),
) -> dict[str, Any]:
    """Return Axiom log-ingest usage.  Always HTTP 200."""
    cache_key = "admin_billing:axiom"
    cached = await _cache_get(cache_key)
    if cached is not None:
        return cached

    cfg = _axiom_env()
    if not cfg["token"]:
        result: dict[str, Any] = {"configured": False}
    else:
        result = await _fetch_axiom_usage()

    await _cache_set(cache_key, result)
    return result


# ── Sentry ────────────────────────────────────────────────────────────────────

def _sentry_env() -> dict[str, str]:
    return {
        "token": os.environ.get("SENTRY_AUTH_TOKEN", "").strip(),
        "org":   os.environ.get("SENTRY_ORG", "").strip(),
    }


async def _fetch_sentry_usage() -> dict[str, Any]:
    """Call Sentry Stats v2 and subscription APIs to return usage payload."""
    cfg = _sentry_env()
    org = cfg["org"]
    headers = {
        "Authorization": f"Bearer {cfg['token']}",
        "Content-Type":  "application/json",
    }
    base = "https://sentry.io"

    today = date.today()
    month_start = today.replace(day=1).strftime("%Y-%m-%dT00:00:00Z")
    month_end   = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    stats_url = (
        f"{base}/api/0/organizations/{org}/stats_v2/"
        f"?field=sum(quantities)"
        f"&groupBy=category"
        f"&interval=1d"
        f"&start={month_start}"
        f"&end={month_end}"
    )
    org_url = f"{base}/api/0/organizations/{org}/"
    sub_url = f"{base}/api/0/subscriptions/{org}/"

    plan: str | None = None
    errors_limit: int | None = None
    perf_transactions_limit: int | None = None
    expiry_date: str | None = None
    days_until_expiry: int | None = None

    try:
        async with httpx.AsyncClient(headers=headers, timeout=_HTTP_TIMEOUT_S) as client:
            stats_resp, org_resp, sub_resp = await asyncio.gather(
                client.get(stats_url),
                client.get(org_url),
                client.get(sub_url),
                return_exceptions=True,
            )

        # 1. Parse organisation info for quota limits.
        if isinstance(org_resp, httpx.Response) and org_resp.status_code == 200:
            try:
                org_data = org_resp.json()
                quota = org_data.get("quota") or {}
                # accountLimit is the org-level error quota.
                errors_limit = quota.get("accountLimit") or quota.get("projectLimit") or None
            except Exception:
                pass

        # 2. Parse subscription for plan name, performance limits, and contract expiry.
        if isinstance(sub_resp, httpx.Response) and sub_resp.status_code == 200:
            try:
                sub_data = sub_resp.json()
                plan_details = sub_data.get("planDetails") or {}
                plan = (
                    plan_details.get("name")
                    or sub_data.get("plan")
                    or plan
                )
                # planDetails may expose per-category quotas.
                categories = plan_details.get("categories") or {}
                err_cat  = categories.get("errors") or {}
                perf_cat = categories.get("transactions") or categories.get("performance") or {}

                err_quota  = err_cat.get("quota")  or err_cat.get("reserved")
                perf_quota = perf_cat.get("quota") or perf_cat.get("reserved")
                if err_quota and not errors_limit:
                    errors_limit = int(err_quota)
                if perf_quota:
                    perf_transactions_limit = int(perf_quota)

                # contractPeriodEnd is the billing cycle / credit expiry date.
                contract_end = sub_data.get("contractPeriodEnd")
                if not contract_end:
                    contract_end = sub_data.get("onDemandPeriodEnd")
                if contract_end:
                    expiry_date = contract_end[:10]
                    days_until_expiry = _days_until(expiry_date)
            except Exception as sub_exc:
                logger.debug("[admin-billing/sentry] subscription parse error: %s", sub_exc)

        # 3. Parse stats v2 for MTD event counts (required — propagate failures).
        # stats_resp is the primary data source; a transport error or non-2xx
        # response means we cannot report live usage, so we re-raise so the
        # outer except converts it to an explicit error payload.
        if isinstance(stats_resp, BaseException):
            raise stats_resp
        stats_resp.raise_for_status()

        errors_used = 0
        perf_transactions_used = 0

        try:
            stats_data = stats_resp.json()
            for grp in stats_data.get("groups", []):
                category = grp.get("by", {}).get("category", "")
                total = sum(
                    v for v in grp.get("series", {}).get("sum(quantities)", [])
                    if v is not None
                )
                if category == "error":
                    errors_used = int(total)
                elif category in ("transaction", "performance"):
                    perf_transactions_used = int(total)
        except Exception as stats_exc:
            logger.debug("[admin-billing/sentry] stats parse error: %s", stats_exc)

        over_limit = bool(
            (errors_limit and errors_used > errors_limit)
            or (perf_transactions_limit and perf_transactions_used > perf_transactions_limit)
        )

        return {
            "configured":               True,
            "plan":                     plan,
            "errors_used":              errors_used,
            "errors_limit":             errors_limit,
            "perf_transactions_used":   perf_transactions_used,
            "perf_transactions_limit":  perf_transactions_limit,
            "over_limit":               over_limit,
            "expiry_date":              expiry_date,
            "days_until_expiry":        days_until_expiry,
        }

    except Exception as exc:
        logger.warning("[admin-billing/sentry] API error: %s", exc)
        return {
            "configured":               True,
            "error":                    str(exc),
            "plan":                     None,
            "errors_used":              None,
            "errors_limit":             None,
            "perf_transactions_used":   None,
            "perf_transactions_limit":  None,
            "over_limit":               False,
            "expiry_date":              None,
            "days_until_expiry":        None,
        }


@router.get(
    "/admin/billing/sentry",
    summary="Sentry startup-tier usage panel (Task #264)",
)
async def admin_billing_sentry(
    _admin: dict = Depends(get_admin_user),
) -> dict[str, Any]:
    """Return Sentry error / performance quota consumption.  Always HTTP 200."""
    cache_key = "admin_billing:sentry"
    cached = await _cache_get(cache_key)
    if cached is not None:
        return cached

    cfg = _sentry_env()
    if not (cfg["token"] and cfg["org"]):
        result: dict[str, Any] = {"configured": False}
    else:
        result = await _fetch_sentry_usage()

    await _cache_set(cache_key, result)
    return result
