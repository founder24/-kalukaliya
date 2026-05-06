"""Unified GCP services-status endpoint (Phase 3).

  GET /api/admin/gcp/services-status

Returns a single dict listing every wired GCP service, whether it has the
credentials it needs (API key vs SA), and which env vars to set if it
doesn't. Designed to be the one endpoint the admin dashboard hits to
render a "GCP integrations" tile without firing 8 separate health probes.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict

from fastapi import APIRouter, Depends

from auth_deps import get_admin_user
import gcp_auth
import kg_search_client
import pagespeed_service
import fact_check_client
import nlp_client
import web_risk_client
import books_client
import web_security_scanner_client
import discovery_engine_client
import slack_notifier

logger = logging.getLogger(__name__)
router = APIRouter()


def _api_key_state(env_vars: list[str]) -> Dict[str, Any]:
    """Report which of `env_vars` are set (without leaking values)."""
    present = [v for v in env_vars if (os.environ.get(v) or "").strip()]
    return {
        "configured": bool(present),
        "from_env": present[0] if present else None,
        "candidates": env_vars,
    }


@router.get("/admin/gcp/services-status")
async def admin_gcp_services_status(admin: dict = Depends(get_admin_user)):
    """Snapshot of all wired GCP services and their credential state."""
    sa_configured = gcp_auth.is_configured()
    sa_project = gcp_auth.project_id()

    services: Dict[str, Any] = {
        # API-key-only (Phase 1 + 2)
        "knowledge_graph_search": {
            "auth_mode": "api_key",
            "endpoint": "/api/admin/seo/kg-search",
            "configured": kg_search_client.is_configured(),
            "key": _api_key_state(["GOOGLE_KG_API_KEY"]),
        },
        "pagespeed_insights": {
            "auth_mode": "api_key_optional",
            "endpoint": "/api/admin/seo/pagespeed",
            "configured": True,  # works without key at lower rate
            "key": _api_key_state(["GOOGLE_PAGESPEED_API_KEY", "GOOGLE_KG_API_KEY"]),
        },
        "fact_check_tools": {
            "auth_mode": "api_key",
            "endpoint": "/api/admin/content/fact-check",
            "configured": fact_check_client.is_configured(),
            "key": _api_key_state(["GOOGLE_FACT_CHECK_API_KEY", "GOOGLE_KG_API_KEY"]),
        },
        "natural_language": {
            "auth_mode": "api_key",
            "endpoint": "/api/admin/content/nlp/analyze",
            "configured": nlp_client.is_configured(),
            "key": _api_key_state(["GOOGLE_NLP_API_KEY", "GOOGLE_KG_API_KEY"]),
        },
        "web_risk": {
            "auth_mode": "api_key",
            "endpoint": "/api/admin/security/web-risk",
            "configured": web_risk_client.is_configured(),
            "key": _api_key_state(["GOOGLE_WEB_RISK_API_KEY", "GOOGLE_KG_API_KEY"]),
        },
        "books": {
            "auth_mode": "api_key_optional",
            "endpoint": "/api/admin/discovery/books/search",
            "configured": True,
            "key": _api_key_state(["GOOGLE_BOOKS_API_KEY", "GOOGLE_KG_API_KEY"]),
        },
        # SA-required (Phase 3)
        # Cloud Scheduler + Cloud Tasks intentionally OMITTED — Task #489
        # deleted both client modules. Periodic ticks live on AWS
        # EventBridge (`infra/aws/eventbridge.tf`); async enqueueing
        # goes through `sqs_fanout.enqueue` to AWS SQS.
        "web_security_scanner": {
            "auth_mode": "service_account",
            "endpoint": "/api/admin/gcp/wss/configs",
            "configured": sa_configured,
            "project": sa_project,
        },
        "discovery_engine": {
            "auth_mode": "service_account",
            "endpoint": "/api/admin/discovery/engine/search",
            "configured": sa_configured and bool(
                (os.environ.get("GCP_DISCOVERY_DATA_STORE") or "").strip()
            ),
            "project": sa_project,
            "extra_env_required": [
                "GCP_DISCOVERY_DATA_STORE",
                "GCP_DISCOVERY_LOCATION (default global)",
                "GCP_DISCOVERY_COLLECTION (default default_collection)",
                "GCP_DISCOVERY_SERVING_CONFIG (default default_search)",
            ],
        },
    }

    # Side channel: Slack notifier (not GCP, but driven by these services).
    services["slack_notifier"] = {
        "auth_mode": "webhook",
        "endpoint": "/api/admin/gcp/wss/notify-slack",
        "configured": slack_notifier.is_configured(),
        "key": _api_key_state(["SLACK_WEBHOOK_URL"]),
    }

    # Aggregate counts so the dashboard can render a single status pill.
    configured = [k for k, v in services.items() if v.get("configured")]
    disabled = [k for k, v in services.items() if not v.get("configured")]
    return {
        "status": "ok",
        "service_account_configured": sa_configured,
        "service_account_project": sa_project,
        "service_account_secret_required":
            "GOOGLE_APPLICATION_CREDENTIALS_JSON" if not sa_configured else None,
        "configured_count": len(configured),
        "disabled_count": len(disabled),
        "services": services,
    }
