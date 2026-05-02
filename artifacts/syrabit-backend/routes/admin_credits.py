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


# ── Task #263: Startup credit burn panels ─────────────────────────────────────
# Four read-only endpoints consumed by the AdminHealth billing panels.
#
# Each endpoint first checks whether the relevant env vars are configured.
# When not configured it returns {"configured": false} so the frontend shows
# setup instructions rather than an error.  When configured, live API calls are
# attempted with a generous fallback to manually-maintained env-var overrides.
#
# Env-var reference:
#   AWS_ACTIVATE_GRANT_USD         — original programme grant (e.g. "1000")
#   AWS_ACTIVATE_REMAINING_USD     — current remaining credits (update manually)
#   AWS_ACTIVATE_SPEND_MTD         — spend this/last month (for runway calc)
#   AWS_ACTIVATE_EXPIRY            — credit expiry date YYYY-MM-DD
#   AWS_ACCOUNT_ALIAS              — friendly account name (optional)
#
#   AZURE_ACTIVATE_GRANT_USD       — original programme grant (e.g. "5000")
#   AZURE_ACTIVATE_REMAINING_USD   — current remaining credits
#   AZURE_ACTIVATE_SPEND_MTD       — spend this/last month
#   AZURE_ACTIVATE_EXPIRY          — credit expiry date YYYY-MM-DD
#   AZURE_SUBSCRIPTION_NAME        — friendly subscription name (optional)
#
#   AXIOM_API_TOKEN + AXIOM_ORG_ID — live ingest stats fetched from Axiom API
#   AXIOM_INGEST_GB_MTD            — fallback: ingest GB this month
#   AXIOM_INGEST_LIMIT_GB          — fallback: plan limit (default 500)
#   AXIOM_RETENTION_DAYS           — fallback: retention (default 30)
#
#   SENTRY_AUTH_TOKEN + SENTRY_ORG — live error counts fetched from Sentry API
#   SENTRY_ERRORS_USED_MTD         — fallback: error events this month
#   SENTRY_ERRORS_LIMIT            — fallback: plan quota (default 50000)
#   SENTRY_PLAN                    — fallback: plan name (default "Team")

import datetime as _dt
import os as _os


def _days_until_expiry(date_str: str | None) -> int | None:
    if not date_str:
        return None
    try:
        exp = _dt.date.fromisoformat(date_str)
        return (exp - _dt.date.today()).days
    except ValueError:
        return None


@router.get(
    "/admin/billing/aws-activate",
    summary="AWS Activate credit burn panel (Task #263)",
    description=(
        "Returns current AWS Activate credit balance and runway. "
        "Reads AWS_ACTIVATE_GRANT_USD, AWS_ACTIVATE_REMAINING_USD, "
        "AWS_ACTIVATE_SPEND_MTD, AWS_ACTIVATE_EXPIRY from environment. "
        "Returns {configured: false} when the grant env var is absent."
    ),
)
async def admin_billing_aws_activate(
    _admin: dict = Depends(get_admin_user),
) -> dict:
    grant_str = _os.environ.get("AWS_ACTIVATE_GRANT_USD")
    if not grant_str:
        return {"configured": False}

    grant_usd = float(grant_str)
    remaining_str = _os.environ.get("AWS_ACTIVATE_REMAINING_USD")
    remaining = float(remaining_str) if remaining_str else grant_usd
    spend_mtd_str = _os.environ.get("AWS_ACTIVATE_SPEND_MTD")
    spend_mtd: float | None = float(spend_mtd_str) if spend_mtd_str else None
    expiry_date = _os.environ.get("AWS_ACTIVATE_EXPIRY")
    days_until = _days_until_expiry(expiry_date)
    credits_low = remaining < grant_usd * 0.20

    months_runway: float | None = None
    if spend_mtd and spend_mtd > 0:
        months_runway = round(remaining / spend_mtd, 1)
    elif remaining >= grant_usd * 0.99:
        months_runway = 999.0

    return {
        "configured":             True,
        "credits_low":            credits_low,
        "account_alias":          _os.environ.get("AWS_ACCOUNT_ALIAS", "AWS Activate Portfolio"),
        "grant_usd":              grant_usd,
        "spend_mtd_usd":          spend_mtd,
        "estimated_remaining_usd": remaining,
        "months_runway":          months_runway,
        "expiry_date":            expiry_date,
        "days_until_expiry":      days_until,
        "services":               ["Lambda", "SES", "Route 53", "CloudFront", "Bedrock"],
        "note": (
            "Update AWS_ACTIVATE_REMAINING_USD and AWS_ACTIVATE_SPEND_MTD "
            "to reflect the current credit state. Set AWS_ACTIVATE_GRANT_USD "
            "and AWS_ACTIVATE_EXPIRY once when the programme activates."
        ),
    }


@router.get(
    "/admin/billing/azure-startups",
    summary="Azure for Startups credit burn panel (Task #263)",
    description=(
        "Returns current Azure for Startups credit balance and runway. "
        "Reads AZURE_ACTIVATE_GRANT_USD, AZURE_ACTIVATE_REMAINING_USD, "
        "AZURE_ACTIVATE_SPEND_MTD, AZURE_ACTIVATE_EXPIRY from environment. "
        "Returns {configured: false} when the grant env var is absent."
    ),
)
async def admin_billing_azure_startups(
    _admin: dict = Depends(get_admin_user),
) -> dict:
    grant_str = _os.environ.get("AZURE_ACTIVATE_GRANT_USD")
    if not grant_str:
        return {"configured": False}

    grant_usd = float(grant_str)
    remaining_str = _os.environ.get("AZURE_ACTIVATE_REMAINING_USD")
    remaining = float(remaining_str) if remaining_str else grant_usd
    spend_mtd_str = _os.environ.get("AZURE_ACTIVATE_SPEND_MTD")
    spend_mtd: float | None = float(spend_mtd_str) if spend_mtd_str else None
    expiry_date = _os.environ.get("AZURE_ACTIVATE_EXPIRY")
    days_until = _days_until_expiry(expiry_date)
    credits_low = remaining < grant_usd * 0.20

    months_runway: float | None = None
    if spend_mtd and spend_mtd > 0:
        months_runway = round(remaining / spend_mtd, 1)
    elif remaining >= grant_usd * 0.99:
        months_runway = 999.0

    return {
        "configured":             True,
        "credits_low":            credits_low,
        "subscription_name":      _os.environ.get("AZURE_SUBSCRIPTION_NAME", "Azure for Startups"),
        "grant_usd":              grant_usd,
        "spend_mtd_usd":          spend_mtd,
        "estimated_remaining_usd": remaining,
        "months_runway":          months_runway,
        "expiry_date":            expiry_date,
        "days_until_expiry":      days_until,
        "services":               ["Front Door", "Cosmos DB", "DDoS Protection", "Monitor"],
        "note": (
            "Update AZURE_ACTIVATE_REMAINING_USD and AZURE_ACTIVATE_SPEND_MTD "
            "to reflect the current credit state. Set AZURE_ACTIVATE_GRANT_USD "
            "and AZURE_ACTIVATE_EXPIRY once when the programme activates."
        ),
    }


@router.get(
    "/admin/billing/axiom",
    summary="Axiom Log Explorer startup-tier usage panel (Task #263)",
    description=(
        "Returns Axiom ingest usage for the current month. "
        "When AXIOM_API_TOKEN + AXIOM_ORG_ID are set, live dataset stats are "
        "fetched from api.axiom.co; otherwise falls back to env-var overrides "
        "(AXIOM_INGEST_GB_MTD, AXIOM_INGEST_LIMIT_GB, AXIOM_RETENTION_DAYS). "
        "Returns {configured: false} when neither token nor fallback vars are set."
    ),
)
async def admin_billing_axiom(
    _admin: dict = Depends(get_admin_user),
) -> dict:
    api_token = _os.environ.get("AXIOM_API_TOKEN")
    org_id = _os.environ.get("AXIOM_ORG_ID")

    if api_token and org_id:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    "https://api.axiom.co/v1/datasets",
                    headers={
                        "Authorization": f"Bearer {api_token}",
                        "X-Axiom-Org-Id": org_id,
                    },
                )
            if resp.status_code == 200:
                datasets = resp.json()
                total_bytes = sum(d.get("compressedBytes", 0) for d in datasets)
                ingest_gb = round(total_bytes / 1_073_741_824, 2)
                ingest_limit_gb = int(_os.environ.get("AXIOM_INGEST_LIMIT_GB", "500"))
                retention_days = int(_os.environ.get("AXIOM_RETENTION_DAYS", "30"))
                return {
                    "configured":     True,
                    "over_limit":     ingest_gb > ingest_limit_gb,
                    "ingest_gb":      ingest_gb,
                    "ingest_limit_gb": ingest_limit_gb,
                    "retention_days": retention_days,
                    "dataset_count":  len(datasets),
                }
            logger.warning("[admin-billing] axiom API returned %s", resp.status_code)
        except Exception as exc:
            logger.warning("[admin-billing] axiom API error: %s", exc)

    ingest_str = _os.environ.get("AXIOM_INGEST_GB_MTD")
    if not api_token and not ingest_str:
        return {"configured": False}

    ingest_gb = float(ingest_str) if ingest_str else 0.0
    ingest_limit_gb = int(_os.environ.get("AXIOM_INGEST_LIMIT_GB", "500"))
    retention_days = int(_os.environ.get("AXIOM_RETENTION_DAYS", "30"))
    return {
        "configured":     True,
        "over_limit":     ingest_gb > ingest_limit_gb,
        "ingest_gb":      ingest_gb,
        "ingest_limit_gb": ingest_limit_gb,
        "retention_days": retention_days,
        "dataset_count":  None,
        "note":           "Live Axiom API unreachable — showing env-var fallback values.",
    }


@router.get(
    "/admin/billing/sentry",
    summary="Sentry error-tracking startup-tier usage panel (Task #263)",
    description=(
        "Returns Sentry error event counts for the last 30 days. "
        "When SENTRY_AUTH_TOKEN + SENTRY_ORG are set, live stats are fetched "
        "from sentry.io/api/0/organizations/{org}/stats_v2/; otherwise falls "
        "back to env-var overrides (SENTRY_ERRORS_USED_MTD, SENTRY_ERRORS_LIMIT, "
        "SENTRY_PLAN). Returns {configured: false} when no token or fallback vars."
    ),
)
async def admin_billing_sentry(
    _admin: dict = Depends(get_admin_user),
) -> dict:
    auth_token = _os.environ.get("SENTRY_AUTH_TOKEN")
    org_slug = _os.environ.get("SENTRY_ORG")

    if auth_token and org_slug:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"https://sentry.io/api/0/organizations/{org_slug}/stats_v2/",
                    params={
                        "statsPeriod": "30d",
                        "category":    "error",
                        "outcome":     "accepted",
                        "groupBy":     "category",
                        "field":       "sum(quantity)",
                    },
                    headers={"Authorization": f"Bearer {auth_token}"},
                )
            if resp.status_code == 200:
                data = resp.json()
                totals = data.get("totals", {})
                errors_used = int(totals.get("sum(quantity)", 0))
                errors_limit = int(_os.environ.get("SENTRY_ERRORS_LIMIT", "50000"))
                plan = _os.environ.get("SENTRY_PLAN", "Team")
                return {
                    "configured":   True,
                    "over_limit":   errors_used > errors_limit,
                    "plan":         plan,
                    "errors_used":  errors_used,
                    "errors_limit": errors_limit,
                }
            logger.warning("[admin-billing] sentry API returned %s", resp.status_code)
        except Exception as exc:
            logger.warning("[admin-billing] sentry API error: %s", exc)

    errors_str = _os.environ.get("SENTRY_ERRORS_USED_MTD")
    if not auth_token and not errors_str:
        return {"configured": False}

    errors_used = int(errors_str) if errors_str else 0
    errors_limit = int(_os.environ.get("SENTRY_ERRORS_LIMIT", "50000"))
    return {
        "configured":   True,
        "over_limit":   errors_used > errors_limit,
        "plan":         _os.environ.get("SENTRY_PLAN", "Team"),
        "errors_used":  errors_used,
        "errors_limit": errors_limit,
        "note":         "Live Sentry API unreachable — showing env-var fallback values.",
    }
