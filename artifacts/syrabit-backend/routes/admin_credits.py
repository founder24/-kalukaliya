"""routes.admin_credits — Provider credit-burn panel (Task #250).

GET /admin/credits/summary
  Returns per-provider rows: startup credit amount, current weight,
  estimated remaining credits (manually maintained), and "credits low"
  warning when the provider's PROVIDER_CREDITS value drops below 20% of
  the original reference amount.

  Operators update PROVIDER_CREDITS in config.py (or via env override)
  when topping up or consuming credits; this endpoint reads those values
  at request time (no DB required).

GET /admin/credits/provider-weights
  Returns the weighted pool for each of the 15 feature keys — useful for
  verifying that the weighted round-robin will actually send traffic to
  the expected providers.

GET /admin/credits/smoke-test
  Iterates all 15 PROVIDER_PRIORITY feature keys. For each key, calls
  select_provider() to confirm weighted selection works, then makes a
  provider-specific minimal real request through the chosen provider's CF
  AI Gateway slug. Each probe exercises BYOK auth and upstream reachability:

    cohere      POST /embed with 1-word input            (BYOK: Authorization: "")
    cartesia    GET  /v1/voices (voice list)              (BYOK: X-API-Key: "")
    assemblyai  GET  /v2/transcript (list)                (BYOK: Authorization: "")
    elevenlabs  GET  /v1/models (model list)              (BYOK: xi-api-key: "")
    sarvam      POST /v1/chat/completions  max_tokens=1   (BYOK: api-subscription-key: "")
    bedrock     POST /model/nova-micro/converse max_tokens=1  (CF SigV4 BYOK)
    azure_openai POST /chat/completions    max_tokens=1   (BYOK: api-key + Authorization: "")

  Providers without a CF AI Gateway slug (vertex, workers_ai, pinecone_ai,
  exa_ai, tavily, mongodb_atlas) are marked "skip" — select_provider() is
  still exercised for them.

  Logs pass/fail/skip with latency per feature. Fires a Slack alert to
  SMOKE_TEST_SLACK_WEBHOOK when any probe returns non-200 or errors.
  Results are returned as JSON and visible at /admin/credits/smoke-test.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx
from fastapi import APIRouter, Depends

from auth_deps import get_admin_user

logger = logging.getLogger(__name__)
router = APIRouter(tags=["admin-credits"])

# Reference (original programme) amounts — used to compute "credits low" %.
# These should only change when a new programme is activated or renewed.
_CREDIT_REFERENCE: dict[str, int] = {
    "vertex":        2000,
    "bedrock":       1000,
    "azure_openai":  2500,   # $2.5k Azure for Startups — weight is fixed at 1 though
    "sarvam":         500,
    "cartesia":       500,
    "elevenlabs":     500,
    "assemblyai":    1000,
    "cohere":        1000,
    "pinecone_ai":    500,
    "exa_ai":        1000,
    "tavily":         500,
    "mongodb_atlas":    0,
    "workers_ai":       0,
}

_PROGRAMME_NAMES: dict[str, str] = {
    "vertex":        "Google Cloud for Startups",
    "bedrock":       "AWS Activate",
    "azure_openai":  "Azure for Startups",
    "sarvam":        "Sarvam Startup Credits",
    "cartesia":      "Cartesia Startup Credits",
    "elevenlabs":    "ElevenLabs Startup Credits",
    "assemblyai":    "AssemblyAI Startup Credits",
    "cohere":        "Cohere Startup Credits",
    "pinecone_ai":   "Pinecone Startup Credits",
    "exa_ai":        "Exa Startup Credits",
    "tavily":        "Tavily Startup Credits",
    "mongodb_atlas": "MongoDB Atlas Free Tier",
    "workers_ai":    "Cloudflare Workers AI Free Tier",
}

_CREDITS_LOW_THRESHOLD = 0.20   # < 20% of original = "credits_low"

# ── Smoke-test probe specifications ──────────────────────────────────────────
# Maps provider name → a minimal real API call that traverses the CF AI
# Gateway slug and exercises BYOK upstream auth.
#
# Design principles:
#   • Prefer cheap/free listing endpoints (GET /models, /voices, etc.) that
#     validate auth without consuming LLM credits.
#   • For LLM-only providers (sarvam, bedrock, azure_openai), use max_tokens=1
#     completion calls — cheapest path that exercises the full BYOK chain.
#   • extra_headers contains the provider-specific upstream auth header set to
#     "" (empty) so CF AI Gateway substitutes its BYOK-stored key.
#   • Common CF headers (cf-aig-byok-key, cf-aig-authorization, cf-aig-cache-ttl)
#     are added at call time from config so they stay in sync with config.py.
#
# Providers without a CF slug (vertex, workers_ai, pinecone_ai, exa_ai, tavily,
# mongodb_atlas) are not listed here — they are marked "skip" in the probe loop.
_PROVIDER_PROBE_SPECS: dict[str, dict] = {
    # Cohere embed/v1 — POST /embed with one word.
    # "embed" exercises the BYOK auth chain on the cohere/v1 slug and verifies
    # that the Cohere key stored in the CF dashboard is valid.
    "cohere": {
        "method": "POST",
        "path": "/embed",
        "body": {
            "model": "embed-multilingual-v3.0",
            "texts": ["smoke"],
            "input_type": "search_query",
            "embedding_types": ["float"],
        },
        "extra_headers": {
            "Content-Type": "application/json",
            # BYOK: empty Authorization → CF substitutes "Bearer <COHERE_API_KEY>"
            "Authorization": "",
        },
        "description": "1-word embed → validates BYOK cohere key",
    },
    # Cartesia TTS — GET /v1/voices.
    # Lists available voices; lightweight, no TTS cost.
    # Cartesia uses X-API-Key (not Authorization Bearer) — empty value triggers BYOK.
    "cartesia": {
        "method": "GET",
        "path": "/v1/voices",
        "body": None,
        "extra_headers": {
            "X-API-Key": "",          # BYOK: empty → CF substitutes CARTESIA_API_KEY
            "Cartesia-Version": "2024-06-10",
        },
        "description": "voice list → validates BYOK cartesia key",
    },
    # AssemblyAI STT — GET /v2/transcript.
    # Lists recent transcript jobs (may be empty list); validates API key, no cost.
    "assemblyai": {
        "method": "GET",
        "path": "/v2/transcript",
        "body": None,
        "extra_headers": {
            # BYOK: empty Authorization → CF substitutes ASSEMBLYAI_API_KEY
            "Authorization": "",
        },
        "description": "transcript list → validates BYOK assemblyai key",
    },
    # ElevenLabs TTS — GET /v1/models.
    # Lists available voice models; lightweight, no TTS cost.
    # ElevenLabs uses xi-api-key header; empty value triggers BYOK substitution.
    "elevenlabs": {
        "method": "GET",
        "path": "/v1/models",
        "body": None,
        "extra_headers": {
            "xi-api-key": "",         # BYOK: empty → CF substitutes ELEVENLABS_API_KEY
        },
        "description": "model list → validates BYOK elevenlabs key",
    },
    # Sarvam LLM — POST /v1/chat/completions (max_tokens=1).
    # Sarvam is an OpenAI-compatible LLM for Indic languages. The CF custom-sarvam
    # slug routes to api.sarvam.ai. Sarvam uses api-subscription-key; empty → BYOK.
    "sarvam": {
        "method": "POST",
        "path": "/v1/chat/completions",
        "body": {
            "model": "sarvam-m",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 1,
        },
        "extra_headers": {
            "Content-Type": "application/json",
            # BYOK: empty → CF substitutes SARVAM_API_KEY into api-subscription-key
            "api-subscription-key": "",
        },
        "description": "1-token completion → validates BYOK sarvam key",
    },
    # AWS Bedrock — POST /model/amazon.nova-micro-v1:0/converse (max_tokens=1).
    # Nova Micro is the smallest/cheapest Bedrock model (~$0.000035/1k tokens).
    # CF AI Gateway handles AWS SigV4 signing via BYOK — no AWS keys in this request.
    "bedrock": {
        "method": "POST",
        "path": "/model/amazon.nova-micro-v1:0/converse",
        "body": {
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "hi"}]}
            ],
            "inferenceConfig": {"maxTokens": 1},
        },
        "extra_headers": {
            "Content-Type": "application/json",
            # No upstream auth header — CF handles AWS SigV4 signing via BYOK.
        },
        "description": "1-token Converse → validates CF SigV4 BYOK for bedrock",
    },
    # Azure OpenAI — POST /chat/completions?api-version=2024-02-01 (max_tokens=1).
    # CF AI gateway azure-openai slug routes to the Azure OpenAI REST endpoint.
    # BYOK: api-key=placeholder + empty Authorization → CF substitutes Azure key.
    "azure_openai": {
        "method": "POST",
        "path": f"/chat/completions?api-version=2024-02-01",
        "body": {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 1,
        },
        "extra_headers": {
            "Content-Type": "application/json",
            # BYOK: empty Authorization + placeholder api-key → CF substitutes Azure key
            "api-key": "x",
            "Authorization": "",
        },
        "description": "1-token chat → validates BYOK azure_openai key",
    },
}

# ── Task #263: Cloudflare paid add-on migration status ────────────────────────
# Static registry of the six paid CF add-ons that are being replaced with
# startup-credit-covered alternatives (~$50/mo total).  Operators update
# `status` here (and redeploy) as each migration completes.
# Statuses: "pending" | "in_progress" | "complete"
_CF_ADDON_ROWS: list[dict] = [
    {
        "service":           "Workers for Platforms",
        "monthly_cost_usd":  25,
        "migration_target":  "GCP Cloud Run (asia-south1)",
        "credit_programme":  "Google Cloud for Startups",
        "status":            "pending",
        "notes":             (
            "Dispatch logic in edge-proxy. Move tenant dispatch to Cloud Run; "
            "cancel at CF dash → Workers & Pages → Plans."
        ),
        "runbook_anchor":    "step-5--workers-paid--workers-for-platforms-30mo",
    },
    {
        "service":           "Workers Paid",
        "monthly_cost_usd":  5,
        "migration_target":  "GCP Cloud Run / AWS Lambda (compute-heavy workers) — free tier sufficient for remainder",
        "credit_programme":  "Google Cloud for Startups / AWS Activate",
        "status":            "pending",
        "notes":             (
            "Verify daily request count < 100k (free tier). Move email-worker "
            "and bedrock-proxy to Cloud Run, then cancel."
        ),
        "runbook_anchor":    "step-5--workers-paid--workers-for-platforms-30mo",
    },
    {
        "service":           "Argo Smart Routing",
        "monthly_cost_usd":  5,
        "migration_target":  "GCP Premium Tier network routing (already active)",
        "credit_programme":  "Google Cloud for Startups",
        "status":            "pending",
        "notes":             (
            "Quickest win. Disable Argo at CF dash → Speed → Optimization → Argo. "
            "Monitor latency for 48h before confirming."
        ),
        "runbook_anchor":    "step-1--argo-smart-routing-quickest-win-5mo",
    },
    {
        "service":           "Basic Load Balancing",
        "monthly_cost_usd":  5,
        "migration_target":  "GCP Global HTTPS LB + Route 53 health-check failover",
        "credit_programme":  "Google Cloud for Startups / AWS Activate",
        "status":            "pending",
        "notes":             (
            "Wire Route 53 failover record for api.syrabit.ai then cancel "
            "CF LB pool at CF dash → Traffic → Load Balancing."
        ),
        "runbook_anchor":    "step-4--basic-load-balancing-5mo",
    },
    {
        "service":           "Cache Reserve",
        "monthly_cost_usd":  5,
        "migration_target":  "GCP Cloud CDN (attached to existing Cloud Run LB)",
        "credit_programme":  "Google Cloud for Startups",
        "status":            "pending",
        "notes":             (
            "Enable Cloud CDN on the Cloud Run backend service; set Cache-Control "
            "headers on API responses; cancel at CF dash → Caching → Cache Reserve."
        ),
        "runbook_anchor":    "step-2--cache-reserve-5mo",
    },
    {
        "service":           "R2 Paid (syrabit-media)",
        "monthly_cost_usd":  5,
        "migration_target":  "GCP Cloud Storage (asia-south1)",
        "credit_programme":  "Google Cloud for Startups",
        "status":            "pending",
        "notes":             (
            "Create GCS bucket syrabit-media asia-south1; sync existing R2 objects; "
            "update backend upload routes; cancel R2 Paid if monthly bill reaches $0."
        ),
        "runbook_anchor":    "step-3--r2-paid-5mo",
    },
]

_CF_ADDON_RUNBOOK_URL = (
    "https://github.com/syrabit/syrabit/blob/main/"
    "artifacts/syrabit/docs/infra/startup-credits-migration.md"
)

_SMOKE_PROBE_TIMEOUT_S = 15.0   # per-provider request timeout
_FEATURE_LANG: dict[str, str] = {
    "assamese_rag_chat": "as",
    "assamese_content":  "as",
}


def _build_provider_rows() -> list[dict[str, Any]]:
    from config import PROVIDER_CREDITS
    rows = []
    for provider, ref_amount in _CREDIT_REFERENCE.items():
        current = PROVIDER_CREDITS.get(provider, 0)
        is_fallback_only = (current == 0)
        credits_low = (
            not is_fallback_only
            and ref_amount > 0
            and current < ref_amount * _CREDITS_LOW_THRESHOLD
        )
        pct_remaining = (
            round(current / ref_amount * 100, 1)
            if ref_amount > 0 and not is_fallback_only
            else None
        )
        rows.append({
            "provider":          provider,
            "programme":         _PROGRAMME_NAMES.get(provider, provider),
            "reference_credits": ref_amount,
            "current_credits":   current,
            "pct_remaining":     pct_remaining,
            "weight":            current,
            "fallback_only":     is_fallback_only,
            "credits_low":       credits_low,
        })
    # Sort: fallback-only last, then by current_credits descending.
    rows.sort(key=lambda r: (r["fallback_only"], -r["current_credits"]))
    return rows


def _build_feature_pools() -> dict[str, Any]:
    import random
    from config import PROVIDER_PRIORITY, PROVIDER_CREDITS

    result = {}
    for feature, providers in PROVIDER_PRIORITY.items():
        weighted = [
            {"provider": p, "weight": PROVIDER_CREDITS.get(p, 0)}
            for p in providers
            if PROVIDER_CREDITS.get(p, 0) > 0
        ]
        fallbacks = [
            p for p in providers
            if PROVIDER_CREDITS.get(p, 0) == 0
        ]
        total_weight = sum(w["weight"] for w in weighted)
        for entry in weighted:
            entry["selection_pct"] = (
                round(entry["weight"] / total_weight * 100, 1)
                if total_weight > 0 else 0.0
            )
        result[feature] = {
            "weighted_pool":   weighted,
            "fallback_order":  fallbacks,
            "total_weight":    total_weight,
        }
    return result


# ── Smoke-test helpers ────────────────────────────────────────────────────────

def _build_probe_headers(provider: str, extra_headers: dict) -> dict:
    """Merge provider-specific extra_headers with CF AI Gateway BYOK headers.

    CF BYOK headers (cf-aig-byok-key, cf-aig-cache-ttl, cf-aig-authorization)
    are loaded at call time from config so they always reflect the live state.
    Provider-specific headers (empty upstream auth key) tell CF to inject the
    stored BYOK key for that provider.
    """
    from config import CF_CACHE_TTL, CF_AI_GATEWAY_TOKEN
    h: dict = dict(extra_headers)
    h["cf-aig-byok-key"] = "true"
    h["cf-aig-cache-ttl"] = str(CF_CACHE_TTL)
    if CF_AI_GATEWAY_TOKEN:
        h["cf-aig-authorization"] = f"Bearer {CF_AI_GATEWAY_TOKEN}"
    return h


async def _probe_provider(
    provider: str,
    gateway_url: str,
    spec: dict,
) -> tuple[int, float]:
    """Execute the provider-specific probe request.

    Returns (http_status, latency_ms).
    Returns (0, latency_ms) on connection error / timeout — never raises.
    """
    url = f"{gateway_url}{spec['path']}"
    headers = _build_probe_headers(provider, spec.get("extra_headers", {}))
    body = spec.get("body")
    method = spec.get("method", "GET").upper()
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=_SMOKE_PROBE_TIMEOUT_S) as client:
            if method == "POST":
                resp = await client.post(url, headers=headers, json=body)
            else:
                resp = await client.get(url, headers=headers)
        return resp.status_code, round((time.monotonic() - t0) * 1000, 1)
    except Exception as exc:
        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        logger.warning("[smoke-test] probe failed for %s (%s): %s", provider, url, exc)
        return 0, latency_ms


async def _run_feature_smoke(feature: str) -> dict[str, Any]:
    """Run one smoke-test probe for *feature*.

    1. Calls select_provider(feature, lang) to exercise weighted dispatch.
    2. If the selected provider has a CF AI Gateway slug AND a probe spec,
       makes a minimal real API call through the gateway.
    3. Providers without a CF slug (vertex, workers_ai, pinecone_ai, etc.)
       or without a probe spec return outcome="skip".

    Returns a result dict with keys: feature, provider, gateway_url,
    probe_description, status, latency_ms, outcome, error.
    """
    from llm import select_provider
    from config import CF_GATEWAY_ENABLED, cf_gateway_url

    lang = _FEATURE_LANG.get(feature, "en")
    t0 = time.monotonic()

    try:
        provider = select_provider(feature, lang=lang)
    except Exception as exc:
        return {
            "feature": feature,
            "provider": None,
            "gateway_url": None,
            "probe_description": None,
            "status": 0,
            "latency_ms": round((time.monotonic() - t0) * 1000, 1),
            "outcome": "fail",
            "error": f"select_provider raised: {exc}",
        }

    gw_url = cf_gateway_url(provider) if CF_GATEWAY_ENABLED else ""
    spec = _PROVIDER_PROBE_SPECS.get(provider)

    if not gw_url or not spec:
        # Provider has no CF AI Gateway slug, or no probe spec registered.
        skip_reason = (
            "no CF gateway slug for provider"
            if CF_GATEWAY_ENABLED and not gw_url
            else ("CF gateway disabled" if not CF_GATEWAY_ENABLED
                  else "no probe spec for provider")
        )
        return {
            "feature":           feature,
            "provider":          provider,
            "gateway_url":       None,
            "probe_description": None,
            "status":            None,
            "latency_ms":        round((time.monotonic() - t0) * 1000, 1),
            "outcome":           "skip",
            "error":             skip_reason,
        }

    status, latency_ms = await _probe_provider(provider, gw_url, spec)
    outcome = "pass" if status == 200 else "fail"
    error_msg = (
        "" if outcome == "pass"
        else (
            f"HTTP {status}" if status != 0
            else "connection error / timeout"
        )
    )
    logger.info(
        "[smoke-test] feature=%-22s provider=%-12s %s status=%s latency=%.0fms outcome=%s",
        feature, provider, spec["description"],
        status or "ERR", latency_ms, outcome,
    )
    return {
        "feature":           feature,
        "provider":          provider,
        "gateway_url":       gw_url,
        "probe_description": spec["description"],
        "status":            status,
        "latency_ms":        latency_ms,
        "outcome":           outcome,
        "error":             error_msg,
    }


async def _post_smoke_slack_alert(failures: list[dict[str, Any]]) -> None:
    """Best-effort Slack POST for smoke-test failures.

    Reads webhook URL from ``SMOKE_TEST_SLACK_WEBHOOK``. No-op when unset.
    Never raises.
    """
    from routes.slack_alerter_config import slack_webhook_url_for, SMOKE_TEST_SLACK_WEBHOOK_ENV
    webhook_url = slack_webhook_url_for(SMOKE_TEST_SLACK_WEBHOOK_ENV)
    if not webhook_url:
        return

    lines = [
        f"• `{r['feature']}` → *{r['provider']}* "
        f"[{r.get('probe_description', 'probe')}] "
        f"status={r['status'] or 'ERR'} | {r['latency_ms']:.0f}ms"
        for r in failures
    ]
    text_body = "\n".join(lines)
    payload = {
        "text": f":red_circle: CF Gateway smoke-test: {len(failures)} slug(s) failed",
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f":red_circle: CF Gateway smoke-test — {len(failures)} failure(s)",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "The following provider slugs returned non-200 or a "
                        "connection error during the smoke-test:\n\n"
                        + text_body[:2800]
                    ),
                },
            },
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(webhook_url, json=payload)
            if resp.status_code >= 400:
                logger.warning(
                    "[smoke-test] Slack webhook returned %s: %s",
                    resp.status_code, resp.text[:200],
                )
    except Exception as exc:
        logger.debug("[smoke-test] Slack webhook post failed: %s", exc)


# ── Route handlers ────────────────────────────────────────────────────────────

@router.get(
    "/admin/credits/cf-addons",
    summary="Cloudflare paid add-on migration status (Task #263)",
    description=(
        "Returns the list of paid Cloudflare add-ons being replaced by startup-credit-covered "
        "alternatives. Each row shows the service, monthly cost saved, migration target, "
        "credit programme, and current status (pending / in_progress / complete). "
        "Total projected savings and per-status counts are included. "
        "Operators update status in _CF_ADDON_ROWS in admin_credits.py and redeploy."
    ),
)
async def admin_credits_cf_addons(
    _admin: dict = Depends(get_admin_user),
):
    rows = _CF_ADDON_ROWS
    total_monthly_savings = sum(
        r["monthly_cost_usd"] for r in rows if r["status"] == "complete"
    )
    total_pending_savings = sum(
        r["monthly_cost_usd"] for r in rows if r["status"] != "complete"
    )
    status_counts = {
        "pending":     sum(1 for r in rows if r["status"] == "pending"),
        "in_progress": sum(1 for r in rows if r["status"] == "in_progress"),
        "complete":    sum(1 for r in rows if r["status"] == "complete"),
    }
    return {
        "addons": rows,
        "total_addons": len(rows),
        "status_counts": status_counts,
        "monthly_savings_realised_usd": total_monthly_savings,
        "monthly_savings_pending_usd": total_pending_savings,
        "runbook_url": _CF_ADDON_RUNBOOK_URL,
        "note": (
            "Update status in _CF_ADDON_ROWS in routes/admin_credits.py and redeploy "
            "as each migration step completes. Full operator runbook: "
            "artifacts/syrabit/docs/infra/startup-credits-migration.md"
        ),
    }


@router.get(
    "/admin/credits/summary",
    summary="Provider credit-burn summary",
    description=(
        "Returns per-provider rows showing startup credit programme, current weight, "
        "and a 'credits_low' flag when below 20% of original amount. "
        "Update PROVIDER_CREDITS in config.py when topping up."
    ),
)
async def admin_credits_summary(
    _admin: dict = Depends(get_admin_user),
):
    rows = _build_provider_rows()
    low_count = sum(1 for r in rows if r["credits_low"])
    return {
        "providers": rows,
        "total_providers": len(rows),
        "credits_low_count": low_count,
        "note": (
            "Update PROVIDER_CREDITS in config.py (and redeploy) to reflect "
            "programme top-ups or manual credit consumption. Weights are static."
        ),
    }


@router.get(
    "/admin/credits/provider-weights",
    summary="Weighted provider pools per feature",
    description=(
        "Shows the weighted round-robin pool for each of the 15 feature keys. "
        "Use this to verify that traffic will be sent to the expected providers."
    ),
)
async def admin_credits_provider_weights(
    _admin: dict = Depends(get_admin_user),
):
    pools = _build_feature_pools()
    return {
        "features": pools,
        "total_features": len(pools),
    }


@router.get(
    "/admin/credits/smoke-test",
    summary="CF Gateway BYOK smoke-test for all 15 feature keys",
    description=(
        "Iterates all 15 PROVIDER_PRIORITY feature keys. For each key, calls "
        "select_provider() to confirm weighted selection works, then makes a "
        "provider-specific minimal real request through the chosen provider's "
        "CF AI Gateway slug to exercise BYOK auth and upstream reachability. "
        "Probes: cohere POST /embed, cartesia GET /v1/voices, assemblyai GET "
        "/v2/transcript, elevenlabs GET /v1/models, sarvam POST /v1/chat/completions, "
        "bedrock POST /model/nova-micro/converse, azure_openai POST /chat/completions. "
        "Providers without a CF slug (vertex, workers_ai, etc.) are marked 'skip'. "
        "Fires a Slack alert (SMOKE_TEST_SLACK_WEBHOOK) on non-200 or connection error. "
        "All probes run concurrently."
    ),
)
async def admin_credits_smoke_test(
    _admin: dict = Depends(get_admin_user),
):
    from config import PROVIDER_PRIORITY, CF_GATEWAY_ENABLED
    from routes.slack_alerter_config import (
        slack_config_for,
        SMOKE_TEST_SLACK_WEBHOOK_ENV,
    )

    features = list(PROVIDER_PRIORITY.keys())
    run_at = time.time()

    results: list[dict[str, Any]] = await asyncio.gather(
        *[_run_feature_smoke(f) for f in features]
    )

    pass_count  = sum(1 for r in results if r["outcome"] == "pass")
    fail_count  = sum(1 for r in results if r["outcome"] == "fail")
    skip_count  = sum(1 for r in results if r["outcome"] == "skip")
    failures    = [r for r in results if r["outcome"] == "fail"]

    overall = "pass" if fail_count == 0 else "fail"

    if failures:
        logger.warning(
            "[smoke-test] %d/%d feature keys FAILED: %s",
            fail_count,
            len(features),
            [r["feature"] for r in failures],
        )
        asyncio.ensure_future(_post_smoke_slack_alert(failures))
    else:
        logger.info(
            "[smoke-test] all %d feature keys passed (%d skipped, no CF slug/spec)",
            pass_count + skip_count,
            skip_count,
        )

    return {
        "overall":              overall,
        "total_features":       len(features),
        "pass_count":           pass_count,
        "fail_count":           fail_count,
        "skip_count":           skip_count,
        "cf_gateway_enabled":   CF_GATEWAY_ENABLED,
        "slack":                slack_config_for(SMOKE_TEST_SLACK_WEBHOOK_ENV),
        "run_at_epoch":         run_at,
        "results":              results,
        "note": (
            "'skip' = provider has no CF AI Gateway slug or no probe spec "
            "(vertex/workers_ai/pinecone_ai/exa_ai/tavily/mongodb_atlas). "
            "select_provider() was still exercised for those features. "
            "Slack alerts fire only on 'fail' (non-200 or connection error)."
        ),
    }
