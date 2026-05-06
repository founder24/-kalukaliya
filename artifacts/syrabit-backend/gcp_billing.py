"""GCP Billing integration for the credit burn panel (Task #253).

Three data sources, each optional and independently graceful-degrading:

1. **Cloud Billing Budget API** (billingbudgets.googleapis.com/v1)
   - Lists budgets for the billing account
   - Extracts alert threshold amounts from ``thresholdRules``
   - Reads ``currentSpend`` when the field is present (not guaranteed by all
     billing account types)
   Requires: GOOGLE_BILLING_ACCOUNT_ID + roles/billing.viewer on the account.

2. **Cloud Billing API** (cloudbilling.googleapis.com/v1)
   - Verifies the billing account exists and is open.
   Requires: GOOGLE_BILLING_ACCOUNT_ID + roles/billing.viewer.

3. **BigQuery Billing Export** (bigquery.googleapis.com/bigquery/v2)
   - Standard GCP billing export table grouped by service for current month.
   - This is the *only* GCP API that returns real per-service spend figures.
   - Table name convention: gcp_billing_export_v1_{ACCOUNT_ID_underscored}
   Requires: GOOGLE_BILLING_ACCOUNT_ID + GOOGLE_BILLING_BIGQUERY_PROJECT +
             GOOGLE_BILLING_BIGQUERY_DATASET + GOOGLE_BILLING_BIGQUERY_TABLE
             + roles/bigquery.jobUser on the project
             + roles/bigquery.dataViewer on the dataset.

Auth: all three calls reuse the same GOOGLE_APPLICATION_CREDENTIALS_JSON
service account used by STT / TTS / Translation / Vision / Vertex providers.

The module exposes two top-level summary functions:
  get_billing_summary()   — budget thresholds + account status (Budget/Billing APIs)
  get_service_spend()     — per-service MTD spend from BigQuery Billing Export

Both return dicts with an ``error`` key (str|None) so the endpoint can detect
degradation without catching exceptions.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_BUDGET_API_BASE = "https://billingbudgets.googleapis.com/v1"
_BILLING_API_BASE = "https://cloudbilling.googleapis.com/v1"
_BQ_API_BASE = "https://bigquery.googleapis.com/bigquery/v2"

_BILLING_SCOPES = [
    "https://www.googleapis.com/auth/cloud-billing.readonly",
    "https://www.googleapis.com/auth/cloud-platform",
]

_TOKEN_REFRESH_BUFFER_SEC = 60.0
_token: Optional[str] = None
_token_expiry: float = 0.0
_token_lock = asyncio.Lock()

_GCP_SERVICE_NAME_MAP: dict[str, str] = {
    "Cloud Speech-to-Text": "stt_chirp2",
    "Speech-to-Text": "stt_chirp2",
    "Cloud Text-to-Speech": "tts_neural2",
    "Text-to-Speech": "tts_neural2",
    "Cloud Translation API": "translation_v3",
    "Cloud Translation": "translation_v3",
    "Cloud Vision API": "vision_ocr",
    "Cloud Vision": "vision_ocr",
    # Task #490 (V4 §15): Vertex is scoped to the content-format polish
    # surface only (`vertex_format.format_with_vertex`). All Vertex SKUs
    # roll up under the single `vertex_format` bucket.
    "Vertex AI": "vertex_format",
    "AI Platform": "vertex_format",
    "Generative AI on Vertex AI": "vertex_format",
    "Vertex AI (Gemini)": "vertex_format",
    "Google AI Studio": "vertex_format",
}


def _sa_raw() -> str:
    return os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON", "").strip()


def is_configured() -> bool:
    """Return True when both the SA JSON and billing account ID are available."""
    billing_id = os.environ.get("GOOGLE_BILLING_ACCOUNT_ID", "").strip()
    return bool(billing_id and _sa_raw() and _sa_raw().startswith("{"))


def _load_sa_credentials(extra_scopes: list[str] | None = None):
    raw = _sa_raw()
    if not raw or not raw.startswith("{"):
        return None
    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("[gcp-billing] GOOGLE_APPLICATION_CREDENTIALS_JSON is not valid JSON")
        return None
    scopes = _BILLING_SCOPES + (extra_scopes or [])
    try:
        from google.oauth2 import service_account
        return service_account.Credentials.from_service_account_info(info, scopes=scopes)
    except Exception as exc:
        logger.warning("[gcp-billing] Failed to load SA credentials: %s", exc)
        return None


def _refresh_token_sync() -> tuple[str, float]:
    creds = _load_sa_credentials()
    if creds is None:
        raise RuntimeError("No service account credentials for GCP Billing API")
    from google.auth.transport.requests import Request as _Req
    creds.refresh(_Req())
    from datetime import datetime, timezone
    if creds.expiry is None:
        ttl = 3600.0
    else:
        exp_utc = creds.expiry.replace(tzinfo=timezone.utc).timestamp()
        ttl = max(60.0, exp_utc - datetime.now(tz=timezone.utc).timestamp())
    return creds.token, time.monotonic() + ttl


async def _get_access_token() -> str:
    global _token, _token_expiry
    async with _token_lock:
        now = time.monotonic()
        if _token and now < (_token_expiry - _TOKEN_REFRESH_BUFFER_SEC):
            return _token
        _token, _token_expiry = await asyncio.to_thread(_refresh_token_sync)
        return _token


def _parse_money(money: dict) -> float:
    """Parse a Google Money proto dict (units + nanos) into a float USD amount."""
    if not money:
        return 0.0
    units = float(money.get("units", 0) or 0)
    nanos = float(money.get("nanos", 0) or 0)
    return units + nanos / 1_000_000_000


def _extract_budget_info(budget: dict) -> dict:
    """Extract threshold and spend information from a single budget resource."""
    amount_obj = budget.get("amount", {})

    budget_usd: float = 0.0
    if "specifiedAmount" in amount_obj:
        budget_usd = _parse_money(amount_obj["specifiedAmount"])

    threshold_rules = budget.get("thresholdRules", [])
    warn_pct: float = 0.0
    critical_pct: float = 0.0
    for rule in threshold_rules:
        pct = float(rule.get("thresholdPercent", 0))
        if 0.80 <= pct <= 0.92:
            warn_pct = max(warn_pct, pct)
        elif pct > 0.92:
            critical_pct = max(critical_pct, pct)

    if not warn_pct and threshold_rules:
        sorted_pcts = sorted(float(r.get("thresholdPercent", 0)) for r in threshold_rules)
        if len(sorted_pcts) >= 2:
            warn_pct = sorted_pcts[-2]
            critical_pct = sorted_pcts[-1]
        elif sorted_pcts:
            critical_pct = sorted_pcts[-1]

    warn_threshold_usd = round(budget_usd * warn_pct, 2) if warn_pct else None
    critical_threshold_usd = round(budget_usd * critical_pct, 2) if critical_pct else None

    current_spend_usd: Optional[float] = None
    if "currentSpend" in budget:
        current_spend_usd = _parse_money(budget["currentSpend"])

    return {
        "name": budget.get("name", ""),
        "display_name": budget.get("displayName", ""),
        "budget_usd": budget_usd,
        "warn_threshold_usd": warn_threshold_usd,
        "critical_threshold_usd": critical_threshold_usd,
        "current_spend_usd": current_spend_usd,
        "threshold_rules": [
            {
                "threshold_percent": float(r.get("thresholdPercent", 0)),
                "spend_basis": r.get("spendBasis", "CURRENT_SPEND"),
            }
            for r in threshold_rules
        ],
    }


async def fetch_budgets(billing_account_id: str) -> dict:
    """Fetch all budgets for a billing account from the Cloud Billing Budget API.

    Returns:
      budgets     list[dict]  — parsed budget objects
      raw_count   int         — number of budgets returned
      error       str | None  — error message if the call failed
    """
    try:
        token = await _get_access_token()
    except Exception as exc:
        return {"budgets": [], "raw_count": 0, "error": str(exc)}

    url = f"{_BUDGET_API_BASE}/billingAccounts/{billing_account_id}/budgets"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                params={"pageSize": 100},
            )
        if resp.status_code == 403:
            msg = (
                "Budget API 403 — grant the service account roles/billing.viewer on "
                f"billing account {billing_account_id} in GCP Console → Billing → "
                "Account management → Permissions."
            )
            logger.warning("[gcp-billing] %s", msg)
            return {"budgets": [], "raw_count": 0, "error": msg}
        if resp.status_code == 404:
            msg = f"Billing account not found: {billing_account_id}"
            logger.warning("[gcp-billing] %s", msg)
            return {"budgets": [], "raw_count": 0, "error": msg}
        if resp.status_code != 200:
            msg = f"Budget API error {resp.status_code}: {resp.text[:300]}"
            logger.warning("[gcp-billing] %s", msg)
            return {"budgets": [], "raw_count": 0, "error": msg}

        data = resp.json()
        raw_budgets = data.get("budgets", [])
        parsed = [_extract_budget_info(b) for b in raw_budgets]
        return {"budgets": parsed, "raw_count": len(raw_budgets), "error": None}

    except httpx.TimeoutException:
        msg = "Budget API request timed out (10 s)"
        logger.warning("[gcp-billing] %s", msg)
        return {"budgets": [], "raw_count": 0, "error": msg}
    except Exception as exc:
        msg = f"Budget API unexpected error: {exc}"
        logger.warning("[gcp-billing] %s", msg)
        return {"budgets": [], "raw_count": 0, "error": msg}


async def fetch_billing_account(billing_account_id: str) -> dict:
    """Verify the billing account via Cloud Billing API v1.

    Returns:
      display_name  str | None
      open          bool
      error         str | None
    """
    try:
        token = await _get_access_token()
    except Exception as exc:
        return {"display_name": None, "open": False, "error": str(exc)}

    url = f"{_BILLING_API_BASE}/billingAccounts/{billing_account_id}"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
        if resp.status_code == 200:
            data = resp.json()
            return {
                "display_name": data.get("displayName"),
                "open": data.get("open", False),
                "error": None,
            }
        return {
            "display_name": None,
            "open": False,
            "error": f"Billing API {resp.status_code}: {resp.text[:200]}",
        }
    except Exception as exc:
        return {"display_name": None, "open": False, "error": str(exc)}


async def fetch_service_spend_from_bigquery(
    project: str,
    dataset: str,
    table: str,
    location: str = "US",
) -> dict:
    """Query the BigQuery Billing Export table for current-month per-service spend.

    Uses the BigQuery synchronous query REST API to run a cost-grouped query.
    The BigQuery Standard Billing Export table has per-row cost entries keyed
    by ``service.description``; this function sums by service for the calendar
    month and maps GCP service names to internal service keys.

    Returns:
      services        dict[str, float]  — internal service key → MTD spend USD
      total_spend_usd float             — total MTD spend across ALL services
      services_raw    dict[str, float]  — original GCP service name → spend USD
      error           str | None        — error if the query failed
    """
    if not all([project, dataset, table]):
        return {
            "services": {},
            "total_spend_usd": 0.0,
            "services_raw": {},
            "error": "GOOGLE_BILLING_BIGQUERY_PROJECT / _DATASET / _TABLE not configured",
        }
    try:
        token = await _get_access_token()
    except Exception as exc:
        return {"services": {}, "total_spend_usd": 0.0, "services_raw": {}, "error": str(exc)}

    full_table = f"`{project}.{dataset}.{table}`"
    query = f"""
SELECT
  service.description AS service_name,
  ROUND(
    SUM(cost) + SUM(IFNULL(
      (SELECT SUM(c.amount) FROM UNNEST(credits) AS c), 0
    )), 6
  ) AS net_cost_usd
FROM {full_table}
WHERE
  DATE(_PARTITIONTIME) >= DATE_TRUNC(CURRENT_DATE(), MONTH)
  OR DATE(usage_start_time) >= DATE_TRUNC(CURRENT_DATE(), MONTH)
GROUP BY service.description
ORDER BY net_cost_usd DESC
LIMIT 200
""".strip()

    url = f"{_BQ_API_BASE}/projects/{project}/queries"
    payload = {
        "query": query,
        "useLegacySql": False,
        "timeoutMs": 15000,
        "location": location or "US",
        "maxResults": 200,
    }
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        if resp.status_code == 403:
            msg = (
                "BigQuery API 403 — grant the service account roles/bigquery.jobUser on "
                f"project {project!r} and roles/bigquery.dataViewer on dataset "
                f"{dataset!r}. Enable BigQuery Billing Export in GCP Console → Billing → "
                "Billing export → BigQuery export."
            )
            logger.warning("[gcp-billing] %s", msg)
            return {"services": {}, "total_spend_usd": 0.0, "services_raw": {}, "error": msg}
        if resp.status_code == 404:
            msg = (
                f"BigQuery table not found: {project}.{dataset}.{table} — "
                "enable GCP Billing Export in GCP Console → Billing → Billing export → BigQuery export."
            )
            logger.warning("[gcp-billing] %s", msg)
            return {"services": {}, "total_spend_usd": 0.0, "services_raw": {}, "error": msg}
        if resp.status_code != 200:
            msg = f"BigQuery API error {resp.status_code}: {resp.text[:300]}"
            logger.warning("[gcp-billing] %s", msg)
            return {"services": {}, "total_spend_usd": 0.0, "services_raw": {}, "error": msg}

        data = resp.json()
        if not data.get("jobComplete"):
            return {
                "services": {},
                "total_spend_usd": 0.0,
                "services_raw": {},
                "error": "BigQuery query did not complete within 15 s timeout; retry later",
            }

        rows = data.get("rows", [])
        services_raw: dict[str, float] = {}
        for row in rows:
            cells = row.get("f", [])
            if len(cells) >= 2:
                svc_name = (cells[0].get("v") or "").strip()
                try:
                    cost = float(cells[1].get("v") or 0)
                except (TypeError, ValueError):
                    cost = 0.0
                if svc_name:
                    services_raw[svc_name] = round(cost, 6)

        services: dict[str, float] = {}
        unmapped: list[str] = []
        for gcp_name, cost in services_raw.items():
            internal_key = _GCP_SERVICE_NAME_MAP.get(gcp_name)
            if internal_key:
                services[internal_key] = services.get(internal_key, 0.0) + cost
            elif cost > 0:
                unmapped.append(f"{gcp_name!r} (${cost:.4f})")

        if unmapped:
            logger.info(
                "[gcp-billing] BigQuery returned %d unmapped service name(s) — "
                "add to _GCP_SERVICE_NAME_MAP if relevant: %s",
                len(unmapped),
                ", ".join(unmapped[:10]),
            )

        services = {k: round(v, 6) for k, v in services.items()}
        total_spend_usd = round(sum(services_raw.values()), 6)

        return {
            "services": services,
            "total_spend_usd": total_spend_usd,
            "services_raw": services_raw,
            "unmapped_service_names": unmapped,
            "error": None,
        }

    except httpx.TimeoutException:
        msg = "BigQuery API request timed out (20 s)"
        logger.warning("[gcp-billing] %s", msg)
        return {"services": {}, "total_spend_usd": 0.0, "services_raw": {}, "error": msg}
    except Exception as exc:
        msg = f"BigQuery billing query unexpected error: {exc}"
        logger.warning("[gcp-billing] %s", msg)
        return {"services": {}, "total_spend_usd": 0.0, "services_raw": {}, "error": msg}


async def get_billing_summary(billing_account_id: str) -> dict:
    """Budget thresholds + account status from the Budget/Billing APIs.

    Returns:
      billing_account_configured   bool
      billing_account_open         bool | None
      billing_account_name         str | None
      budgets                      list[dict]
      primary_budget               dict | None
      budget_usd                   float | None   — from specifiedAmount
      warn_threshold_usd           float | None   — from thresholdRules
      critical_threshold_usd       float | None
      spend_mtd_usd_from_budget    float | None   — currentSpend if present in API
      live_budget_data             bool           — True when Budget API succeeded
      error                        str | None
    """
    if not billing_account_id:
        return {
            "billing_account_configured": False,
            "billing_account_open": None,
            "billing_account_name": None,
            "budgets": [],
            "primary_budget": None,
            "budget_usd": None,
            "warn_threshold_usd": None,
            "critical_threshold_usd": None,
            "spend_mtd_usd_from_budget": None,
            "live_budget_data": False,
            "error": "GOOGLE_BILLING_ACCOUNT_ID not set",
        }

    budgets_result, account_result = await asyncio.gather(
        fetch_budgets(billing_account_id),
        fetch_billing_account(billing_account_id),
        return_exceptions=True,
    )

    if isinstance(budgets_result, Exception):
        budgets_result = {"budgets": [], "raw_count": 0, "error": str(budgets_result)}
    if isinstance(account_result, Exception):
        account_result = {"display_name": None, "open": False, "error": str(account_result)}

    budgets = budgets_result.get("budgets", [])
    budgets_error = budgets_result.get("error")
    account_error = account_result.get("error")

    primary_budget: Optional[dict] = None
    if budgets:
        primary_budget = max(budgets, key=lambda b: b.get("budget_usd", 0))

    budget_usd = primary_budget["budget_usd"] if primary_budget else None
    warn_threshold_usd = primary_budget["warn_threshold_usd"] if primary_budget else None
    critical_threshold_usd = primary_budget["critical_threshold_usd"] if primary_budget else None
    spend_mtd_from_budget = primary_budget["current_spend_usd"] if primary_budget else None

    combined_error = " | ".join(filter(None, [budgets_error, account_error])) or None

    return {
        "billing_account_configured": True,
        "billing_account_open": account_result.get("open"),
        "billing_account_name": account_result.get("display_name"),
        "budgets": budgets,
        "primary_budget": primary_budget,
        "budget_usd": budget_usd,
        "warn_threshold_usd": warn_threshold_usd,
        "critical_threshold_usd": critical_threshold_usd,
        "spend_mtd_usd_from_budget": spend_mtd_from_budget,
        "live_budget_data": budgets_error is None,
        "error": combined_error,
    }


async def get_service_spend(
    project: str,
    dataset: str,
    table: str,
    location: str = "US",
) -> dict:
    """Per-service MTD spend from BigQuery Billing Export.

    Wraps fetch_service_spend_from_bigquery with a clear is_configured() guard.

    Args:
      project   GCP project containing the billing export dataset.
      dataset   BigQuery dataset name.
      table     BigQuery table name (standard: gcp_billing_export_v1_{ACCOUNT_ID_underscored}).
      location  BigQuery dataset location — must match where the dataset lives.
                Common values: "US" (default, GCP multi-region), "EU", "us-central1".
                Set GOOGLE_BILLING_BIGQUERY_LOCATION to override if your export
                dataset is in a non-US region.

    Returns:
      services          dict[str, float]  — internal key → MTD spend USD (real)
      total_spend_usd   float
      services_raw      dict[str, float]  — GCP service name → spend USD
      live_spend_data   bool              — True only when BigQuery query succeeded
      bq_configured     bool              — True when all BQ env vars are set
      error             str | None
    """
    bq_configured = bool(project and dataset and table)
    if not bq_configured:
        return {
            "services": {},
            "total_spend_usd": 0.0,
            "services_raw": {},
            "live_spend_data": False,
            "bq_configured": False,
            "error": (
                "BigQuery Billing Export not configured — set GOOGLE_BILLING_BIGQUERY_PROJECT, "
                "_DATASET, _TABLE (or enable billing export and set GOOGLE_BILLING_ACCOUNT_ID "
                "to auto-derive the table name)."
            ),
        }

    result = await fetch_service_spend_from_bigquery(project, dataset, table, location=location)
    live = result.get("error") is None
    return {
        "services": result.get("services", {}),
        "total_spend_usd": result.get("total_spend_usd", 0.0),
        "services_raw": result.get("services_raw", {}),
        "live_spend_data": live,
        "bq_configured": True,
        "error": result.get("error"),
    }
