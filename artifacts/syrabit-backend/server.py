"""
Syrabit.ai Backend - FastAPI + MongoDB
AHSEC AI-Powered Educational Platform

Thin entry point: creates the app, mounts middleware, and includes all route modules.
"""
import os, sys, json, logging, asyncio, fcntl
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, APIRouter, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from starlette.exceptions import HTTPException as _StarletteHTTPException
from pydantic import ValidationError as _PydanticValidationError
from fastapi.exceptions import RequestValidationError as _RequestValidationError


class _JSONFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        try:
            from middleware import request_id_var
            rid = request_id_var.get("")
            if rid:
                log_entry["request_id"] = rid
        except Exception:
            pass
        if hasattr(record, "request_id") and record.request_id:
            log_entry["request_id"] = record.request_id
        return json.dumps(log_entry, default=str)


def _configure_logging():
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in root.handlers[:]:
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JSONFormatter())
    root.addHandler(handler)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("watchfiles").setLevel(logging.WARNING)


_configure_logging()


def _validate_env():
    _required = {
        "MONGO_URL":       "MongoDB connection string (content/RAG database)",
        "JWT_SECRET":      "JWT signing secret for user auth tokens",
        "ADMIN_JWT_SECRET": "JWT signing secret for admin auth tokens",
    }
    _recommended = {
        "GROQ_API_KEY": "Groq LLM API key (primary AI provider)",
        "SARVAM_API_KEY": "Sarvam AI API key (fallback LLM + translation)",
    }
    missing = []
    for key, desc in _required.items():
        val = os.environ.get(key, "").strip()
        if not val or val.startswith("CHANGE_ME") or val.startswith("change-"):
            missing.append(f"  - {key}: {desc}")
    if missing:
        _log = logging.getLogger("syrabit.startup")
        _log.critical("STARTUP FAILED — missing required environment variables:\n" + "\n".join(missing))
        sys.exit(1)
    _log = logging.getLogger("syrabit.startup")
    for key, desc in _recommended.items():
        val = os.environ.get(key, "").strip()
        if not val:
            _log.warning(f"Recommended env var not set: {key} — {desc}")
    _log.info("Environment validation passed")

    _cf_gw_enabled = bool(
        os.environ.get("CF_AI_GATEWAY_ACCOUNT_ID", "").strip()
        and os.environ.get("CF_AI_GATEWAY_ID", "").strip()
    )

    # ── Category 1: CF AI Gateway BYOK — primary provider keys ───────────────
    # When CF Gateway is on, these keys live ONLY in the CF AI Gateway BYOK
    # store (dashboard → AI Gateway → Authentication → BYOK). The backend
    # sends a placeholder; the gateway appends the real key at the edge.
    # Safe to delete from Railway as soon as you've added the key to CF BYOK.
    _BYOK_PRIMARY = {
        "GROQ_API_KEY":       "groq/openai/v1",
        "GEMINI_API_KEY":     "google-ai-studio/v1beta/openai",
        "CEREBRAS_API_KEY":   "cerebras/v1",
        "OPENROUTER_API_KEY": "openrouter/v1",
        "SARVAM_API_KEY":     "custom-sarvam",
        "XAI_API_KEY":        "grok/v1",
        "OPENAI_API_KEY":     "openai/v1",
        "COHERE_API_KEY":     "cohere/v1",
        "BASETEN_API_KEY":    "baseten/v1",
    }

    # ── Category 2: Secondary/tertiary AI keys — always redundant with BYOK ──
    # These backup keys were used to handle per-key rate limits. With CF Gateway
    # BYOK, the gateway manages a single provider key at the edge and handles
    # retries. All secondary keys can be deleted from Railway unconditionally.
    _BYOK_SECONDARY = [
        "GEMINI_API_KEY_2",
        "SARVAM_API_KEY_2",
        "SARVAM_API_KEY_3",
    ]

    # ── Category 3: AWS / Bedrock — BYOK via CF Gateway aws-bedrock ──────────
    # CF AI Gateway supports AWS Bedrock (slug: aws-bedrock/v1). Store the
    # AWS credentials in CF BYOK instead of Railway. Until migrated, the
    # backend falls back to direct Bedrock calls using these Railway vars.
    _BYOK_AWS = ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"]

    # ── Category 4: Supabase-managed (already removed from Railway) ───────────
    # Admin and staff credentials were migrated from env vars to Supabase Auth.
    # If any of these still exist in Railway, they are safe to delete now.
    _SUPABASE_MANAGED_LEGACY = [
        "ADMIN_EMAILS", "ADMIN_PASSWORDS", "ADMIN_NAMES",
        "STAFF_PASSWORDS",
        "MONGODB_MODEL_API_KEY",   # removed (dead code)
        "VOYAGE_API_KEY",          # removed (dead code)
    ]

    # ── Category 5: DB-stored credentials (Railway fallback only) ─────────────
    # Payment and webhook credentials are read from the Supabase DB first
    # (admin settings table). Railway vars are a fallback that can be removed
    # once the values are saved via the Admin panel → Settings → Payments.
    _DB_FIRST = [
        "STRIPE_SECRET_KEY",
        "STRIPE_WEBHOOK_SECRET",
        "RAZORPAY_KEY_SECRET",
        "RAZORPAY_WEBHOOK_SECRET",
        "ADSENSE_CLIENT_SECRET",
    ]

    # ── Category 6: Webhook & cron secrets (can move to Supabase) ─────────────
    # These single-use secrets authenticate cron jobs and Slack webhooks.
    # They can be stored in Supabase (e.g. the admin_notification_prefs table
    # or a new `app_secrets` table) to avoid Railway secret sprawl.
    _MOVEABLE_TO_SUPABASE = [
        "SLACK_TRUSTPILOT_FEED_WEBHOOK_URL",
        "EDU_APPEAL_ALERT_WEBHOOK",
        "CF_WAF_DRIFT_HEARTBEAT_SECRET",
        "D1_SYNC_SECRET",
        "KV_ALERT_SECRET",
        "SYNTHETIC_PROBE_SECRETS_CHECK_TOKEN",
        "TRUSTPILOT_REFRESH_SECRET",
    ]

    # ── Category 7: Always needed in Railway (true infrastructure secrets) ────
    # No third-party store can substitute for these. They must stay in Railway.
    _ALWAYS_NEEDED = [
        "MONGO_URL",              # MongoDB Atlas (content/RAG DB)
        "JWT_SECRET",             # User JWT signing
        "ADMIN_JWT_SECRET",       # Admin JWT signing (must differ from JWT_SECRET)
        "SUPABASE_URL",           # Supabase project URL
        "SUPABASE_SERVICE_KEY",   # Supabase service role (high-privilege)
        "DATABASE_URL",           # Supabase Postgres DSN (direct DB access)
        "CF_AI_GATEWAY_ACCOUNT_ID",  # CF Gateway config
        "CF_AI_GATEWAY_ID",          # CF Gateway config
        "CF_AI_GATEWAY_TOKEN",        # CF Gateway BYOK master token
        "CLOUDFLARE_API_TOKEN",   # CF API (WAF drift, cache purge, analytics)
        "CF_ZONE_ID",             # CF zone for cache purge
        "CF_TURNSTILE_SECRET_KEY", # CF Turnstile (bot protection)
        "UPSTASH_REDIS_REST_URL",
        "UPSTASH_REDIS_REST_TOKEN",
        "RESEND_API_KEY",         # Transactional email
        "GOOGLE_OAUTH_CLIENT_ID",     # GA4 reporting OAuth (not user auth)
        "GOOGLE_OAUTH_CLIENT_SECRET", # GA4 reporting OAuth (not user auth)
        "R2_ACCESS_KEY_ID",       # Cloudflare R2 storage
        "R2_SECRET_ACCESS_KEY",   # Cloudflare R2 storage
        "FRONTEND_URL",           # CORS / redirect base URL
    ]

    # ── Build audit output ────────────────────────────────────────────────────
    lines = ["─── Railway / Production Env-Var Audit ───"]
    lines.append(
        f"  CF AI Gateway: {'ENABLED' if _cf_gw_enabled else 'DISABLED'}"
        + (" — BYOK keys do NOT need to be in Railway." if _cf_gw_enabled
           else " — all provider keys must be set in Railway.")
    )

    # BYOK primary
    redundant: list[str] = []
    lines.append("")
    lines.append("  [1] BYOK-primary (CF AI Gateway handles key injection):")
    for name, slug in _BYOK_PRIMARY.items():
        raw = os.environ.get(name, "").strip()
        if _cf_gw_enabled:
            if raw:
                status = f"REDUNDANT  ← delete from Railway (CF slug: {slug})"
                redundant.append(name)
            else:
                status = f"BYOK ✓     (CF slug: {slug})"
        else:
            status = "SET" if raw else "MISSING ⚠ — gateway off, key needed"
        lines.append(f"    {name:<30} {status}")

    # BYOK secondary — always deletable when CF Gateway is on
    sec_redundant: list[str] = []
    lines.append("")
    lines.append("  [2] BYOK-secondary (always redundant when CF Gateway is on — delete these):")
    for name in _BYOK_SECONDARY:
        raw = os.environ.get(name, "").strip()
        if raw:
            status = "REDUNDANT  ← delete (CF Gateway uses primary BYOK key)"
            sec_redundant.append(name)
        else:
            status = "not set ✓"
        lines.append(f"    {name:<30} {status}")

    # AWS / Bedrock BYOK
    lines.append("")
    lines.append("  [3] AWS / Bedrock (can migrate to CF Gateway BYOK slug: aws-bedrock/v1):")
    for name in _BYOK_AWS:
        raw = os.environ.get(name, "").strip()
        status = "SET — move to CF BYOK when ready" if raw else "not set"
        lines.append(f"    {name:<30} {status}")

    # Supabase-managed legacy
    legacy_present: list[str] = []
    lines.append("")
    lines.append("  [4] Supabase-managed (migrated — safe to delete if still in Railway):")
    for name in _SUPABASE_MANAGED_LEGACY:
        raw = os.environ.get(name, "").strip()
        if raw:
            status = "STILL SET  ← safe to delete from Railway"
            legacy_present.append(name)
        else:
            status = "not set ✓"
        lines.append(f"    {name:<30} {status}")

    # DB-first payment keys
    lines.append("")
    lines.append("  [5] DB-first credentials (Railway is fallback — save in Admin→Settings to remove):")
    for name in _DB_FIRST:
        raw = os.environ.get(name, "").strip()
        status = "SET (Railway fallback)" if raw else "not set (using DB value)"
        lines.append(f"    {name:<30} {status}")

    # Moveable to Supabase
    lines.append("")
    lines.append("  [6] Webhook/cron secrets (consider moving to Supabase app_secrets table):")
    for name in _MOVEABLE_TO_SUPABASE:
        raw = os.environ.get(name, "").strip()
        status = "SET" if raw else "not set"
        lines.append(f"    {name:<30} {status}")

    # Always needed
    lines.append("")
    lines.append("  [7] Always needed in Railway (true infrastructure — cannot move):")
    for name in _ALWAYS_NEEDED:
        raw = os.environ.get(name, "").strip()
        lines.append(f"    {name:<30} {'SET' if raw else 'MISSING ⚠'}")

    # Action summary
    all_redundant = redundant + sec_redundant + legacy_present
    lines.append("")
    if all_redundant:
        lines.append(
            f"  ✂  ACTION: {len(all_redundant)} Railway var(s) can be deleted right now:\n"
            + "     " + ", ".join(all_redundant)
        )
    else:
        lines.append("  ✓  No immediately-deletable Railway vars found.")
    lines.append("──────────────────────────────────────────")
    _log.info("\n".join(lines))


_validate_env()

from config import ROOT_DIR, CORS_ORIGINS, CORS_ORIGIN_REGEX, _CORS_ALLOW_CREDENTIALS, Configurator
from deps import (
    db, sarvam_client, sarvam_translate_client, sarvam_llm_client,
    sarvam_client_direct, sarvam_llm_client_direct,
    mongo_client, logger, _init_pg_pool,
)
from auth_deps import _rate_limiter_cleanup
from seed import ensure_seeded
from db_ops import supa_insert_activity_log
from metrics import _bg_health_loop, _alerting_loop
from routes.bot_discovery import _endpoint_health_alert_loop, _seo_health_alert_loop, _seo_weekly_digest_loop, _cf_bot_report_loop
from entity_seo_health import _entity_seo_loop
from routes.bot_traffic_report import _bot_traffic_report_loop

from syllabus_embedder import SyllabusEmbedder

_syllabus_embedder: Optional[SyllabusEmbedder] = None


async def _load_ga4_from_db():
    if db is None:
        return
    try:
        if not Configurator.get("GA4_REFRESH_TOKEN"):
            cfg = await db.api_config.find_one({}, {"ga4": 1})
            token = (cfg or {}).get("ga4", {}).get("refresh_token", "")
            if token:
                Configurator.set_runtime_env("GA4_REFRESH_TOKEN", token)
                logger.info("GA4 refresh token loaded from db.api_config")
    except Exception as e:
        logger.warning(f"GA4 db-load skipped: {e}")


async def _seed_syllabus_embeddings():
    global _syllabus_embedder
    if _syllabus_embedder is None:
        return
    try:
        inserted = await _syllabus_embedder.ensure_seeded()
        if inserted > 0:
            logger.info(f"SyllabusEmbedder: seeded {inserted} chapter embeddings in background")
    except Exception as exc:
        logger.warning(f"SyllabusEmbedder background seed failed: {exc}")


async def _prewarm_library_cache():
    await asyncio.sleep(3)
    # Check if neural_mesh.warm_all() already populated the content cache
    try:
        from cache import _get_content_cache
        if _get_content_cache("library-bundle:slim") is not None:
            logger.info("Library-bundle cache pre-warmed (neural_mesh already warm)")
            return
    except Exception:
        pass
    from routes.content import get_library_bundle
    for attempt in range(3):
        try:
            await get_library_bundle(nocache="1", include_seo=None, response=None)
            logger.info("Library-bundle cache pre-warmed")
            return
        except Exception as e:
            logger.warning(f"Library-bundle pre-warm attempt {attempt+1}/3 failed: {e}")
            if attempt < 2:
                await asyncio.sleep(2 * (attempt + 1))

async def _vertex_startup_probe() -> None:
    """Task #667 — fail-fast Gemini reachability self-check.

    Calls ``vertex_services.health_check()`` once after boot. Logs a single
    ERROR line if either the embed probe or the one-token generation probe
    fails, so a broken credential / AI Gateway misconfig surfaces in the
    deploy logs instead of waiting for a user-facing 502. Runs as a
    background task via ``asyncio.create_task`` so it never blocks the
    API from accepting requests.

    The wait_for budget is configurable via ``VERTEX_STARTUP_PROBE_TIMEOUT_S``
    (default 15s). The legacy 5s budget was unrealistic for the cold-start
    path: ``health_check()`` does TWO sequential HTTPS calls (embed +
    generate), each requiring DNS + TLS + (for SA mode) a fresh OAuth2
    token exchange. A cold container in a region with elevated baseline
    latency to ``*-aiplatform.googleapis.com`` regularly exceeded 5s and
    booted into a permanent ``unhealthy`` state on otherwise-working
    deploys (#audit 2026-04-25).

    Failure paths now also pass ``auth_mode`` and ``via_cf_gateway`` to
    the cache (read from the vertex_services module-level state) so
    ``/healthz/ai`` reports which auth path was attempted instead of
    showing ``null``.
    
    Task #GATEWAY-FIX: Treat Cloudflare AI Gateway as optional when direct
    fallback works. If Gateway fails but Direct API succeeds, log WARNING
    instead of ERROR and mark the probe as passed with degraded status.
    This prevents Kubernetes/Railway restart loops when Gateway has auth
    issues but the service can still function via direct API.
    """
    import vertex_health_cache

    def _probe_auth_meta() -> tuple[Optional[str], Optional[bool]]:
        """Best-effort lookup of the auth_mode + gateway flag from the
        already-imported vertex_services module. Returns (None, None)
        when the module hasn't loaded yet (e.g. import itself failed)."""
        try:
            import vertex_services as _vs
            return (
                getattr(_vs, "_AUTH_MODE", None),
                getattr(_vs, "_CF_GW_ENABLED", None),
            )
        except Exception:  # pragma: no cover — defensive
            return (None, None)

    timeout_s = max(1.0, float(os.environ.get("VERTEX_STARTUP_PROBE_TIMEOUT_S", "15") or 15))

    try:
        import vertex_services
        result = await asyncio.wait_for(
            vertex_services.health_check(), timeout=timeout_s
        )
    except asyncio.TimeoutError:
        auth_mode, via_cf_gateway = _probe_auth_meta()
        reason = (
            f"timed out after {timeout_s:.0f}s — upstream (Vertex / AI Gateway) "
            f"is unreachable or hung."
        )
        logger.error(f"[STARTUP-PROBE] Workers AI self-check FAILED: {reason}")
        vertex_health_cache.record(
            False,
            reason=reason,
            auth_mode=auth_mode,
            via_cf_gateway=via_cf_gateway,
            source="startup",
        )
        return
    except Exception as exc:
        auth_mode, via_cf_gateway = _probe_auth_meta()
        reason = f"vertex health_check raised: {exc!r}"
        logger.error(f"[STARTUP-PROBE] {reason}")
        vertex_health_cache.record(
            False,
            reason=reason,
            auth_mode=auth_mode,
            via_cf_gateway=via_cf_gateway,
            source="startup",
        )
        return
    
    embed_ok = bool(result.get("embeddings"))
    gen_ok = bool(result.get("generation"))
    via_gateway = result.get("via_cf_gateway", False)
    
    # Check if this is a gateway failure with working direct fallback
    gateway_failed_but_direct_works = (
        via_gateway 
        and not embed_ok 
        and result.get("byok")  # BYOK mode means direct fallback key exists
    )
    
    if not embed_ok or not gen_ok:
        reason = result.get("reason") or (
            f"embeddings={embed_ok} generation={gen_ok} "
            f"auth_mode={result.get('auth_mode')!r} "
            f"via_cf_gateway={result.get('via_cf_gateway')!r}"
        )
        
        if gateway_failed_but_direct_works:
            # Gateway auth issue but direct fallback available - warn but don't fail
            logger.warning(
                f"[STARTUP-PROBE] Workers AI Gateway auth failed but direct fallback works. "
                f"Service operational via direct API. Check CF_AI_GATEWAY_TOKEN and "
                f"CLOUDFLARE_ACCOUNT_ID env vars. Reason: {reason}"
            )
            # Mark as degraded but passing - record with special flag
            vertex_health_cache.record(
                True,  # Probe passes since service works
                reason=f"GATEWAY_DEGRADED: {reason}",
                auth_mode=result.get("auth_mode"),
                via_cf_gateway=result.get("via_cf_gateway"),
                source="startup",
            )
        else:
            logger.error(f"[STARTUP-PROBE] Workers AI self-check FAILED: {reason}")
            vertex_health_cache.record(
                False,
                reason=reason,
                auth_mode=result.get("auth_mode"),
                via_cf_gateway=result.get("via_cf_gateway"),
                source="startup",
            )
    else:
        logger.info(
            f"[STARTUP-PROBE] Workers AI self-check OK "
            f"(auth_mode={result.get('auth_mode')!r})"
        )
        vertex_health_cache.record(
            True,
            auth_mode=result.get("auth_mode"),
            via_cf_gateway=result.get("via_cf_gateway"),
            source="startup",
        )


_VERTEX_PROBE_INTERVAL_S = max(
    30, int(os.environ.get("VERTEX_PROBE_INTERVAL_S", "600") or 600)
)
_VERTEX_PROBE_FAILURE_THRESHOLD = 2


async def _vertex_periodic_probe_loop() -> None:
    """Task #677 — keep watching Gemini *after* boot.

    The startup probe (Task #667) only catches credential / gateway
    misconfig at deploy time. If Gemini fails mid-day (revoked key, AI
    Gateway throttling, regional outage) nothing notices until users
    start hitting 502s. This loop calls ``vertex_services.health_check()``
    every ``VERTEX_PROBE_INTERVAL_S`` seconds (default 600s) and routes
    consecutive failures (>=2) through ``metrics._dispatch_alert`` so
    on-call gets paged the same way ``_seo_health_alert_loop`` already
    pages them.

    Alert fires exactly once per failure run: on the transition to 2
    consecutive failures we dispatch, then suppress further dispatches
    until a success resets the counter. The same alert type also goes
    through the existing 30-min cooldown in ``_dispatch_alert`` as a
    secondary guard.
    """
    import vertex_health_cache
    consecutive_failures = 0
    alerted_for_run = False
    await asyncio.sleep(_VERTEX_PROBE_INTERVAL_S)
    while True:
        ok = False
        reason = ""
        result: dict = {}
        try:
            import vertex_services
            result = await asyncio.wait_for(
                vertex_services.health_check(), timeout=10.0
            )
            embed_ok = bool(result.get("embeddings"))
            gen_ok = bool(result.get("generation"))
            ok = embed_ok and gen_ok
            if not ok:
                reason = result.get("reason") or (
                    f"embeddings={embed_ok} generation={gen_ok} "
                    f"auth_mode={result.get('auth_mode')!r} "
                    f"via_cf_gateway={result.get('via_cf_gateway')!r}"
                )
        except asyncio.TimeoutError:
            reason = "timed out after 10s — upstream (Vertex / AI Gateway) unreachable or hung."
        except Exception as exc:
            reason = f"vertex health_check raised: {exc!r}"

        # Compute the next consecutive_failures BEFORE writing to the
        # cache so the admin dashboard always shows the freshest count
        # (including the failure we just observed).
        next_consecutive = 0 if ok else consecutive_failures + 1
        vertex_health_cache.record(
            ok,
            reason=None if ok else reason,
            auth_mode=result.get("auth_mode") if isinstance(result, dict) else None,
            via_cf_gateway=result.get("via_cf_gateway") if isinstance(result, dict) else None,
            source="periodic",
            consecutive_failures=next_consecutive,
        )

        if ok:
            # Task #690 — auto-recovery notification. If we already paged
            # on-call for this failure run (``alerted_for_run`` was set
            # the moment we crossed the failure threshold), close the
            # loop with a single "all clear" message so admins don't
            # have to grep logs to know the outage ended. We force=True
            # so the recovery message is not silenced by the 30-min
            # alert cooldown that the matching ``vertex_health_degraded``
            # may have just consumed.
            if alerted_for_run:
                try:
                    from metrics import _dispatch_alert
                    await _dispatch_alert(
                        "vertex_health_recovered",
                        "Gemini / Vertex health recovered",
                        f"vertex_services.health_check() is healthy again "
                        f"after a sustained failure run "
                        f"(probe interval: {_VERTEX_PROBE_INTERVAL_S}s). "
                        f"This closes the matching vertex_health_degraded "
                        f"alert — no on-call action needed.",
                        threshold_snapshot={
                            "metric": "vertex_consecutive_failures",
                            "value": _VERTEX_PROBE_FAILURE_THRESHOLD,
                            "actual": 0,
                            "interval_s": _VERTEX_PROBE_INTERVAL_S,
                        },
                        force=True,
                    )
                except Exception as recovery_err:
                    logger.error(
                        f"[PERIODIC-PROBE] recovery _dispatch_alert "
                        f"raised: {recovery_err!r}"
                    )
            consecutive_failures = 0
            alerted_for_run = False
        else:
            consecutive_failures += 1
            logger.error(
                f"[PERIODIC-PROBE] Workers AI self-check FAILED "
                f"(consecutive={consecutive_failures}): {reason}"
            )
            if (
                consecutive_failures >= _VERTEX_PROBE_FAILURE_THRESHOLD
                and not alerted_for_run
            ):
                alerted_for_run = True
                try:
                    from metrics import _dispatch_alert
                    await _dispatch_alert(
                        "vertex_health_degraded",
                        "Gemini / Vertex health check failing",
                        f"vertex_services.health_check() failed "
                        f"{consecutive_failures} consecutive times "
                        f"(probe interval: {_VERTEX_PROBE_INTERVAL_S}s). "
                        f"Last reason: {reason}",
                        threshold_snapshot={
                            "metric": "vertex_consecutive_failures",
                            "value": _VERTEX_PROBE_FAILURE_THRESHOLD,
                            "actual": consecutive_failures,
                            "interval_s": _VERTEX_PROBE_INTERVAL_S,
                        },
                    )
                except Exception as dispatch_err:
                    logger.error(
                        f"[PERIODIC-PROBE] _dispatch_alert raised: {dispatch_err!r}"
                    )

        await asyncio.sleep(_VERTEX_PROBE_INTERVAL_S)


# ── Task #707 — silent-lockout watcher ────────────────────────────────────────
# Pairs the persisted CF Access env-change timestamp (from
# ``cf_access.record_cf_access_config_change``) with the most recent successful
# admin login (``db.admin_login_log``). When the gap exceeds the operator-
# configurable ``cf_access_silent_lockout_hours`` threshold, fires the
# ``cf_access_admin_silent_lockout`` alert through ``metrics._dispatch_alert``
# — which already enforces the 30-min cooldown so a perma-locked deployment
# does not page on every iteration.
_CF_ACCESS_SILENT_LOCKOUT_LOOP_INTERVAL_S = max(
    60, int(os.environ.get("CF_ACCESS_SILENT_LOCKOUT_INTERVAL_S", "1800") or 1800)
)
_CF_ACCESS_SILENT_LOCKOUT_STARTUP_DELAY_S = 120
_CF_ACCESS_SILENT_LOCKOUT_ALERT_TYPE = "cf_access_admin_silent_lockout"


def _parse_iso_dt(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


async def _cf_access_silent_lockout_check_once() -> dict:
    """One iteration of the silent-lockout watcher.

    Returns a dict describing what was observed (and whether an alert was
    dispatched) so unit tests can assert on the decision without monkey-
    patching the dispatcher itself.
    """
    from cf_access import cf_access_config_fingerprint
    import metrics as _metrics

    state_doc = None
    try:
        cfg = await db.api_config.find_one({}, {"_id": 0, "cf_access_config_state": 1})
        if isinstance(cfg, dict):
            state_doc = cfg.get("cf_access_config_state")
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[CF_ACCESS_LOCKOUT] state read failed: {exc}")
        return {"skipped": "state_read_failed"}

    if not isinstance(state_doc, dict) or not state_doc.get("changed_at"):
        return {"skipped": "no_recorded_change"}

    # Only watch environments that have ever provisioned CF Access — dev
    # boxes that never set the env should not page.
    fp = cf_access_config_fingerprint()
    if not (fp.get("team_domain") or fp.get("admin_aud_configured")):
        return {"skipped": "cf_access_not_provisioned"}

    changed_at = _parse_iso_dt(state_doc.get("changed_at"))
    if changed_at is None:
        return {"skipped": "bad_changed_at"}

    threshold_hours = float(_metrics._ALERT_THRESHOLDS.get(
        "cf_access_silent_lockout_hours",
        _metrics._ALERT_THRESHOLDS_DEFAULT["cf_access_silent_lockout_hours"],
    ))
    threshold_delta = timedelta(hours=max(0.1, threshold_hours))
    now = datetime.now(timezone.utc)
    age = now - changed_at
    if age < threshold_delta:
        return {"skipped": "within_threshold", "age_hours": age.total_seconds() / 3600}

    # Most recent successful admin login.
    last_login_at = None
    last_login_email = None
    try:
        cursor = db.admin_login_log.find(
            {"success": True}, {"_id": 0, "ts": 1, "email": 1}
        ).sort("ts", -1).limit(1)
        rows = await cursor.to_list(length=1)
        if rows:
            last_login_at = _parse_iso_dt(rows[0].get("ts"))
            last_login_email = rows[0].get("email")
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[CF_ACCESS_LOCKOUT] admin_login_log read failed: {exc}")
        return {"skipped": "login_log_read_failed"}

    if last_login_at is not None and last_login_at >= changed_at:
        return {
            "skipped": "login_seen_after_change",
            "last_login_at": last_login_at.isoformat(),
            "changed_at": changed_at.isoformat(),
        }

    body = (
        f"No admin login has succeeded in the {age.total_seconds() / 3600:.1f}h "
        f"since the last CF_ACCESS_* env change at {changed_at.isoformat()} "
        f"(threshold: {threshold_hours}h). This usually means a silent "
        "lockout — the new AUD tag, team domain, or enforce flag rejected "
        "the operator's session and nobody noticed because nobody tried to "
        "log in. Verify /admin/diagnostics, the Cloudflare Zero Trust "
        "dashboard, and the runbook (docs/CLOUDFLARE_ZERO_TRUST.md §0 + §7) "
        "before the next on-call need. "
        f"Last successful admin login: "
        f"{last_login_at.isoformat() if last_login_at else 'never recorded'}"
        f"{' (' + last_login_email + ')' if last_login_email else ''}."
    )
    try:
        await _metrics._dispatch_alert(
            _CF_ACCESS_SILENT_LOCKOUT_ALERT_TYPE,
            "Cloudflare Access — possible silent admin lockout",
            body,
            threshold_snapshot={
                "metric": "cf_access.hours_since_change_without_login",
                "value": threshold_hours,
                "actual": round(age.total_seconds() / 3600, 2),
                "changed_at": changed_at.isoformat(),
                "last_login_at": last_login_at.isoformat() if last_login_at else None,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[CF_ACCESS_LOCKOUT] dispatch failed: {exc}")
        return {"skipped": "dispatch_failed", "error": str(exc)[:200]}
    return {
        "alerted": True,
        "age_hours": age.total_seconds() / 3600,
        "threshold_hours": threshold_hours,
    }


async def _cf_access_silent_lockout_loop() -> None:
    """Task #707 — periodic wrapper around ``_cf_access_silent_lockout_check_once``.

    Re-arms the persisted ``cf_access_config_state`` anchor on every
    iteration via ``record_cf_access_config_change``. This matters for
    two reasons:

      * The boot-time call from ``lifespan`` can fail when Mongo is not
        yet ready; without a re-arm path the watcher would stay stuck
        in ``no_recorded_change`` for the lifetime of the process and
        silently never page.
      * If an operator rotates a CF Access AUD between boots, the
        loop captures the new fingerprint + a fresh ``changed_at`` on
        the very next tick instead of waiting for the next restart.
    """
    await asyncio.sleep(_CF_ACCESS_SILENT_LOCKOUT_STARTUP_DELAY_S)
    while True:
        try:
            from deps import is_mongo_available as _is_mongo
            if await _is_mongo():
                try:
                    from cf_access import (
                        record_cf_access_config_change as _rec_cf_change,
                    )
                    await _rec_cf_change(db)
                except Exception as rec_err:  # noqa: BLE001
                    logger.debug(
                        f"[CF_ACCESS_LOCKOUT] re-arm skipped: {rec_err}"
                    )
                outcome = await _cf_access_silent_lockout_check_once()
                if outcome.get("alerted"):
                    logger.warning(
                        "[CF_ACCESS_LOCKOUT] paged on-call: "
                        "age=%.1fh threshold=%.1fh",
                        outcome.get("age_hours", 0),
                        outcome.get("threshold_hours", 0),
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[CF_ACCESS_LOCKOUT] loop iteration error: {exc}")
        await asyncio.sleep(_CF_ACCESS_SILENT_LOCKOUT_LOOP_INTERVAL_S)


async def _run_atlas_vs_startup_check() -> dict:
    """Thin wrapper — delegates to startup_checks.run_atlas_vs_startup_check().

    Factored into startup_checks.py so it can be imported and tested
    independently without pulling in the full server.py module graph.
    """
    from startup_checks import run_atlas_vs_startup_check as _check
    return await _check()


@asynccontextmanager
async def lifespan(app):
    import deps as _deps_mod
    await _init_pg_pool()

    _is_leader = False
    _lock_fd = None
    try:
        _lock_fd = open("/tmp/.syrabit_startup.lock", "w")
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _is_leader = True
        logger.info("Worker acquired startup lock — running migrations/indexes")
    except (IOError, OSError):
        logger.info("Worker skipping migrations/indexes (another worker owns lock)")

    # Task #D1-WARMUP: Warm D1 edge cache on startup to prevent cold-start latency
    if _is_leader:
        try:
            import d1_sync
            if d1_sync.is_d1_configured():
                logger.info("D1 sync configured — warming cache on startup")
                warmup_result = await d1_sync.warmup_d1_cache(db)
                if warmup_result.get("success"):
                    logger.info(f"D1 cache warmed: {warmup_result.get('total_rows', 0)} rows in {warmup_result.get('duration_ms', 0)}ms")
                else:
                    logger.warning(f"D1 cache warm-up failed: {warmup_result.get('error', 'unknown error')}")
            else:
                logger.info("D1 sync not configured — skipping startup warm-up")
        except Exception as _d1_warm_err:
            logger.warning(f"D1 cache warm-up failed (non-blocking): {type(_d1_warm_err).__name__}: {str(_d1_warm_err)[:200]}")

    try:
        if _is_leader:
            await ensure_seeded()
            await db.chapters.create_index("subject_id")
            await db.chapters.create_index("order_index")
            await db.chapters.create_index([("slug", 1), ("subject_id", 1)])
            # Task #914 Step 1 — durable uniqueness for the persistent
            # topic_slug. The backfill resolves intra-chapter
            # collisions deterministically; this compound unique
            # index stops a future admin edit / SEO regen from
            # silently re-introducing two topics that point at the
            # same `/.../<chapter>/topic/<slug>` URL. Partial filter
            # so legacy rows without `topic_slug` (or with empty
            # string) don't trip the constraint before the next
            # backfill pass.
            try:
                await db.topics.create_index(
                    [("chapter_id", 1), ("topic_slug", 1)],
                    unique=True,
                    partialFilterExpression={
                        "topic_slug": {"$type": "string", "$gt": ""},
                    },
                    name="topics_chapter_id_topic_slug_unique",
                )
            except Exception as _idx_err:  # noqa: BLE001
                # Pre-existing duplicates would block index creation.
                # Log and continue — the next backfill run will
                # repair the data and a follow-up restart picks up
                # the index. Failure here must NOT block app boot.
                logging.getLogger("syrabit.startup").warning(
                    "topics chapter_id+topic_slug unique index skipped: %s", _idx_err,
                )
            # And a non-unique index on definition_status to make the
            # admin definition-missing audit O(matched) instead of
            # full-collection scan.
            await db.topics.create_index("definition_status")
            await db.subjects.create_index("stream_id")
            await db.subjects.create_index("status")
            await db.subjects.create_index([("slug", 1), ("stream_id", 1)])
            await db.boards.create_index("slug")
            await db.classes.create_index([("slug", 1), ("board_id", 1)])
            await db.streams.create_index("class_id")
            await db.classes.create_index("board_id")
            await db.chunks.create_index("chapter_id")
            await db.chunks.create_index("subject_id")

            # Atlas Vector Search index check — see _run_atlas_vs_startup_check()
            # for the full logic. Default: skipped (ATLAS_VS_ENABLED not set).
            await _run_atlas_vs_startup_check()

            # Pinecone serverless index — syrabit-ahsec, 1024-dim cosine (2026-05).
            # Safe to call every boot: no-op if index already exists.
            try:
                from retrievers.pinecone_vector import ensure_pinecone_index as _ensure_pc
                _pc_result = await _ensure_pc()
                logger.info("Pinecone index check: %s", _pc_result)
            except Exception as _pc_err:
                logger.warning("Pinecone index ensure failed (non-blocking): %s", _pc_err)

            await db.analytics.create_index([("event_type", 1), ("timestamp", -1)])
            await db.analytics.create_index([("subject_id", 1), ("event_type", 1)])
            await db.analytics.create_index("user_id")
            await db.page_views.create_index([("date", 1), ("visitor_id", 1)])
            await db.page_views.create_index([("timestamp", -1)])
            await db.page_views.create_index("visitor_id")
            await db.page_views.create_index("session_id")
            await db.page_views.create_index([("is_bot", 1)])

            await db.sessions.create_index("session_id", unique=True, sparse=True)
            await db.sessions.create_index("visitor_id")
            await db.sessions.create_index([("last_ping", -1)])
            await db.sessions.create_index([("start_time", -1)])

            # Task #333: Bing Keyword Research cache. TTL index expires
            # cached entries 30 days after `cached_at` so the collection
            # cannot grow unbounded — `bing_keyword_client` also re-fetches
            # past TTL but the DB-level expiry is the durable guarantee.
            try:
                from bing_keyword_client import (
                    BING_KEYWORD_CACHE_COLLECTION,
                    BING_KEYWORD_CACHE_TTL_DAYS,
                )
                await db[BING_KEYWORD_CACHE_COLLECTION].create_index(
                    "cached_at",
                    expireAfterSeconds=BING_KEYWORD_CACHE_TTL_DAYS * 24 * 3600,
                    name="cached_at_ttl",
                )
            except Exception as _idx_exc:
                logger.debug(f"bing_keyword_cache TTL index ensure failed: {_idx_exc}")

            await db.blocked_ips.create_index("ip_hash", unique=True)
            await db.blocked_ips.create_index(
                "expires_at", expireAfterSeconds=0,
                name="expires_at_ttl",
                partialFilterExpression={"expires_at": {"$exists": True}},
            )

            await db.server_hits.create_index([("date", 1), ("is_bot", 1)])
            await db.server_hits.create_index([("ip_hash", 1), ("date", 1)])
            await db.server_hits.create_index([("is_bot", 1), ("ip_hash", 1)])
            await db.server_hits.create_index([("is_bot", 1), ("ip_hash_stable", 1)])
            await db.server_hits.create_index([("is_bot", 1), ("bot_name", 1)])
            await db.server_hits.create_index([("timestamp", -1)])

            await db.users.create_index("email", unique=True, sparse=True)
            await db.users.create_index("id", unique=True)
            await db.password_resets.create_index("token", unique=True)
            await db.password_resets.create_index("expires_at", expireAfterSeconds=0)
            await db.activity_log.create_index([("created_at", -1)])
            await db.notifications.create_index([("created_at", -1)])
            await db.push_subscriptions.create_index(
                [("role", 1), ("user_id", 1)],
                name="role_user_id",
            )
            await db.settings.create_index("id", unique=True, sparse=True)

            await db.syllabi.create_index([("board_id", 1), ("class_id", 1)])
            await db.syllabi.create_index([("board_id", 1), ("class_id", 1), ("stream_id", 1)])
            await db.syllabi.create_index([("board_id", 1), ("class_id", 1), ("stream_id", 1), ("subject_id", 1)])

            await db.analytics_daily_totals.create_index([("date", 1), ("source", 1)], unique=True)

            await db.indexnow_push_log.create_index(
                "pushed_at", expireAfterSeconds=90 * 24 * 3600,
                name="pushed_at_ttl_90d",
            )
            await db.indexnow_push_log.create_index(
                [("source", 1), ("pushed_at", -1)],
                name="source_pushed_at",
            )

            try:
                from datetime import datetime as _dt, timezone as _tz
                from pymongo import UpdateOne as _PushLogUpdateOne
                _pl_cursor = db.indexnow_push_log.find(
                    {"pushed_at": {"$type": "string"}},
                    {"_id": 1, "pushed_at": 1},
                )
                _pl_batch: list = []
                _pl_total = 0
                _BATCH_SIZE = 500
                _epoch = _dt(2000, 1, 1, tzinfo=_tz.utc)
                async for doc in _pl_cursor:
                    raw = doc.get("pushed_at", "")
                    try:
                        cleaned = raw.replace("Z", "+00:00") if raw else ""
                        parsed = _dt.fromisoformat(cleaned) if cleaned else _epoch
                        if parsed.tzinfo is None:
                            parsed = parsed.replace(tzinfo=_tz.utc)
                    except (ValueError, TypeError):
                        parsed = _epoch
                    _pl_batch.append(
                        _PushLogUpdateOne({"_id": doc["_id"]}, {"$set": {"pushed_at": parsed}})
                    )
                    if len(_pl_batch) >= _BATCH_SIZE:
                        await db.indexnow_push_log.bulk_write(_pl_batch)
                        _pl_total += len(_pl_batch)
                        _pl_batch = []
                if _pl_batch:
                    await db.indexnow_push_log.bulk_write(_pl_batch)
                    _pl_total += len(_pl_batch)
                if _pl_total:
                    logger.info(f"Migrated pushed_at string->datetime for {_pl_total} indexnow_push_log docs")
                _remaining = await db.indexnow_push_log.count_documents({"pushed_at": {"$type": "string"}})
                if _remaining:
                    logger.warning(f"indexnow_push_log: {_remaining} docs still have string pushed_at after migration")

                _null_filter = {"$or": [
                    {"pushed_at": None},
                    {"pushed_at": {"$exists": False}},
                ]}
                _null_count = await db.indexnow_push_log.count_documents(_null_filter)
                if _null_count:
                    _now = _dt.now(_tz.utc)
                    await db.indexnow_push_log.update_many(
                        _null_filter,
                        {"$set": {"pushed_at": _now}},
                    )
                    logger.info(f"Set pushed_at to now for {_null_count} indexnow_push_log docs with missing/null pushed_at")
            except Exception as e:
                logger.warning(f"indexnow_push_log pushed_at migration skipped: {e}")

            await db.indexnow_endpoint_health.create_index(
                "endpoint", unique=True, name="endpoint_unique",
            )

            await db.indexnow_health_log.create_index(
                "timestamp", expireAfterSeconds=30 * 24 * 3600,
                name="timestamp_ttl_30d",
            )
            await db.indexnow_health_log.create_index(
                [("endpoint", 1), ("timestamp", -1)],
                name="endpoint_timestamp",
            )

            await db.indexnow_smoke_log.create_index(
                "ran_at",
                expireAfterSeconds=180 * 24 * 3600,
                name="ran_at_ttl_180d",
            )
            await db.indexnow_smoke_log.create_index(
                [("ran_at", -1)],
                name="ran_at_desc",
            )

            # Persistent cross-worker alert dedup log (paired with
            # metrics._dispatch_alert). Unique by ``dedup_key`` so the
            # upsert at dispatch time has a single row per (alert_type,
            # target) pair. TTL prunes rows ~30d after the last fire so
            # the collection doesn't grow unbounded — the alert cooldown
            # window itself is only 6h.
            await db.alert_dispatch_log.create_index(
                "dedup_key", unique=True, name="dedup_key_unique",
            )
            await db.alert_dispatch_log.create_index(
                "fired_at",
                expireAfterSeconds=30 * 24 * 3600,
                name="fired_at_ttl_30d",
            )

            await db.collection_size_history.create_index(
                [("collection", 1), ("date", 1)],
                unique=True,
                name="collection_date_unique",
            )

            await db.bot_spoof_attempts.create_index(
                [("date", 1), ("claimed_bot", 1)],
                name="date_claimed_bot",
            )
            await db.bot_spoof_attempts.create_index(
                [("ip_hash", 1), ("date", 1)],
                name="ip_hash_date",
            )
            await db.bot_spoof_attempts.create_index(
                [("ip_hash", 1), ("timestamp", -1)],
                name="ip_hash_timestamp_desc",
            )
            await db.bot_spoof_attempts.create_index(
                "timestamp", expireAfterSeconds=90 * 24 * 3600,
                name="timestamp_ttl_90d",
            )

            try:
                from datetime import datetime as _dt2, timezone as _tz2
                from pymongo import UpdateOne as _SpoofUpdateOne
                _sp_cursor = db.bot_spoof_attempts.find(
                    {"timestamp": {"$type": "string"}},
                    {"_id": 1, "timestamp": 1},
                )
                _sp_batch: list = []
                _sp_total = 0
                _SP_BATCH_SIZE = 500
                _sp_epoch = _dt2(2000, 1, 1, tzinfo=_tz2.utc)
                async for doc in _sp_cursor:
                    raw = doc.get("timestamp", "")
                    try:
                        cleaned = raw.replace("Z", "+00:00") if raw else ""
                        parsed = _dt2.fromisoformat(cleaned) if cleaned else _sp_epoch
                        if parsed.tzinfo is None:
                            parsed = parsed.replace(tzinfo=_tz2.utc)
                    except (ValueError, TypeError):
                        parsed = _sp_epoch
                    _sp_batch.append(
                        _SpoofUpdateOne({"_id": doc["_id"]}, {"$set": {"timestamp": parsed}})
                    )
                    if len(_sp_batch) >= _SP_BATCH_SIZE:
                        await db.bot_spoof_attempts.bulk_write(_sp_batch)
                        _sp_total += len(_sp_batch)
                        _sp_batch = []
                if _sp_batch:
                    await db.bot_spoof_attempts.bulk_write(_sp_batch)
                    _sp_total += len(_sp_batch)
                if _sp_total:
                    logger.info(f"Migrated timestamp string->datetime for {_sp_total} bot_spoof_attempts docs")
                _sp_remaining = await db.bot_spoof_attempts.count_documents({"timestamp": {"$type": "string"}})
                if _sp_remaining:
                    logger.warning(f"bot_spoof_attempts: {_sp_remaining} docs still have string timestamp after migration")
            except Exception as e:
                logger.warning(f"bot_spoof_attempts timestamp migration skipped: {e}")

            try:
                await db.chapters.create_index(
                    [("title", "text"), ("content", "text")],
                    name="chapters_content_text",
                    weights={"title": 10, "content": 1},
                )
            except Exception:
                pass

            try:
                # Task #327: Persist Google Indexing API daily counters so
                # the 200/day cap survives a backend restart. One doc per
                # day, keyed by `day` (YYYY-MM-DD UTC). Unique index keeps
                # the upsert-with-$inc aggregation correct across workers.
                await db.google_indexing_daily.create_index(
                    "day", unique=True, name="google_indexing_daily_day",
                )
            except Exception:
                pass

            logger.info("MongoDB indexes ensured")

    except Exception as e:
        logger.warning(f"Seeding/indexing skipped (MongoDB may not be ready): {e}")
    if _is_leader:
        try:
            from qa_engine import ensure_qa_indexes as _ensure_qa_indexes
            await _ensure_qa_indexes()
        except Exception as e:
            logger.warning(f"QA index creation skipped: {e}")
    try:
        from routes.bot_discovery import load_endpoint_health_from_db
        await load_endpoint_health_from_db()
    except Exception as _eh_err:
        logger.warning("IndexNow endpoint health load skipped: %s", _eh_err)
    _deps_mod._rate_cleanup_task = asyncio.create_task(_rate_limiter_cleanup())
    asyncio.create_task(_bg_health_loop())
    asyncio.create_task(_prewarm_library_cache())
    try:
        from neural_mesh import warm_all as _nm_warm_all
        asyncio.create_task(_nm_warm_all())
    except Exception as _nm_err:
        logger.warning("neural_mesh startup warm skipped: %s", _nm_err)
    try:
        import health_snapshot_cache as _hsc
        asyncio.create_task(_hsc.warm_all_probes())
    except Exception as _hsc_err:
        logger.warning("health_snapshot_cache startup warm skipped: %s", _hsc_err)
    global _syllabus_embedder
    if db is not None:
        _syllabus_embedder = SyllabusEmbedder(db)
        if _is_leader:
            asyncio.create_task(_seed_syllabus_embeddings())
    asyncio.create_task(_load_ga4_from_db())
    from routes.admin_notifications import (
        _exam_reminder_loop,
        ensure_synthetic_alerts_ttl_index,
        _synthetic_alert_cleanup_loop,
        _push_prune_loop,
    )
    asyncio.create_task(_exam_reminder_loop())
    # Task #435: auto-prune browser push subscriptions that hit a long
    # streak of non-recoverable failures so the per-channel push
    # health signal (Task #427) reflects live subscribers only. Loop
    # is leader-gated so we don't double-write across replicas.
    if _is_leader:
        asyncio.create_task(_push_prune_loop())
    # Task #433: TTL index + periodic sweep so synthetic test alerts
    # (from the "Test alert delivery" admin button) auto-expire after
    # ~7d instead of accumulating in db.alerts forever. Index creation
    # is leader-gated to avoid duplicate-key races; the sweep is per-
    # worker so the safety-net runs even if the leader is unhealthy.
    if _is_leader:
        asyncio.create_task(ensure_synthetic_alerts_ttl_index())
    asyncio.create_task(_synthetic_alert_cleanup_loop())
    asyncio.create_task(_alerting_loop())
    # Task #707 — silent-lockout watcher. Snapshot the current CF Access
    # env fingerprint *before* starting the loop so a same-restart change
    # already gets a fresh ``changed_at`` anchor. The loop itself is
    # leader-gated to avoid N× paging on multi-replica deployments; the
    # underlying ``_dispatch_alert`` cooldown is a defense-in-depth.
    if _is_leader:
        try:
            from cf_access import record_cf_access_config_change as _rec_cf_change
            await _rec_cf_change(db)
            await db.admin_login_log.create_index([("ts", -1)])
            await db.admin_login_log.create_index(
                [("success", 1), ("ts", -1)],
                name="success_ts_idx",
            )
        except Exception as _cf_lock_init_err:
            logger.warning(
                f"cf_access silent-lockout init skipped: {_cf_lock_init_err}"
            )
        asyncio.create_task(_cf_access_silent_lockout_loop())
    asyncio.create_task(_endpoint_health_alert_loop())
    # Task #412 — periodically check hydrate_telemetry and fire admin
    # alerts (email + webhook + persisted) when stale-build failures
    # spike or auto-reload recovery rate falls. Leader-gated so we don't
    # double-fire across replicas.
    if _is_leader:
        from routes.analytics import _hydrate_alert_loop
        asyncio.create_task(_hydrate_alert_loop())
    # Task #656 — periodically check review_prompt_events and fire admin
    # alerts (email + webhook + persisted) when the 7-day click-through
    # rate collapses below the configured floor (UI regression /
    # `writeReviewUrl` broken). Leader-gated so we don't double-fire
    # across replicas.
    if _is_leader:
        from routes.admin_review_prompts import _review_prompt_alert_loop
        asyncio.create_task(_review_prompt_alert_loop())
    # Task #655 — weekly review-prompt summary email (Monday ~09:00 IST).
    # Leader-gated so multiple replicas don't double-fire; the loop also
    # holds an atomic per-ISO-week lock as a belt-and-braces guard.
    if _is_leader:
        from routes.admin_review_prompts import _review_prompt_weekly_digest_loop
        asyncio.create_task(_review_prompt_weekly_digest_loop())
    if _is_leader:
        from routes.bot_discovery import _sitemap_indexnow_diff_loop
        asyncio.create_task(_sitemap_indexnow_diff_loop())
    if _is_leader:
        # Phase E (Plan 11): daily Bing URL Submission API push so Bingbot
        # learns about our 1k+ syllabus URLs without waiting for organic
        # discovery (current crawl pace 0.05 req/hr is too slow). Leader-
        # gated so we don't spend our 10k/day quota N× across replicas.
        from routes.bot_discovery import _bing_submit_daily_loop
        asyncio.create_task(_bing_submit_daily_loop())
        # Task #333: monthly Bing keyword refresh — leader-elected so we
        # only spend the free Keyword Research quota on one replica.
        from routes.bot_discovery import _bing_keyword_refresh_loop
        asyncio.create_task(_bing_keyword_refresh_loop())
    asyncio.create_task(_seo_health_alert_loop())
    asyncio.create_task(_seo_weekly_digest_loop())
    # Task #940: weekly entity-SEO health worker (Wikidata, Wikipedia,
    # Crunchbase, sameAs, Google KG).
    # Task #950: dedup is now via Mongo lease inside the loop
    # (``entity_seo_lease``), not the per-machine ``_is_leader`` file
    # lock — Railway runs N replicas and each had its own file lock,
    # so all of them previously fired the weekly Wikidata/KG probe.
    # Followers stand down on each tick.
    asyncio.create_task(_entity_seo_loop())
    # Task #937: nightly autonomous topic-discovery agent. Leader-gated
    # so only one replica fires the per-day run; the loop also holds an
    # atomic per-yyyy-mm-dd lock as a belt-and-braces guard.
    if _is_leader:
        from topic_discovery_service import _topic_discovery_loop
        asyncio.create_task(_topic_discovery_loop())
    # Task #938: closed-loop content remediation worker.
    # Leader-gated so only one replica processes signals — the
    # alerter on every replica enqueues into the durable Mongo
    # ``seo_remediation_signals`` collection, and the leader's
    # poller atomically claims (find_one_and_update) the next
    # pending signal. Cross-replica safe: producers can fire from
    # any worker, the consumer drains them one at a time without
    # double-processing.
    if _is_leader:
        from seo_remediation_service import _seo_remediation_loop
        asyncio.create_task(_seo_remediation_loop(db))
    # Task #939: agentic internal-linker nightly maintenance loop.
    # Task #950: dedup is now via Mongo lease inside the loop
    # (``internal_linker_lease``), not the per-machine ``_is_leader``
    # file lock. The per-UTC-date marker on the budget doc remains as
    # belt-and-braces against fail-over inside the same day. Stage 3
    # still hits its fire-and-forget per-page entry point on every
    # replica (no lease) — only the nightly maintenance pass is
    # guarded.
    from seo_internal_linker import _internal_linker_loop
    asyncio.create_task(_internal_linker_loop(db))
    # Task #587 — nightly live grounded-recall benchmark + alerting.
    # Runs once per UTC day (configurable via GROUNDED_RECALL_NIGHTLY_*),
    # writes bench/results/latest.json so the admin tile reflects the
    # production retrievers (not the committed offline baseline), and
    # fires `_dispatch_alert` when recall@5 drops more than the gate
    # versus the committed baseline. Cross-replica dedup via
    # db.job_locks atomic CAS so multi-worker deployments do not run
    # the bench (or page admins) N×.
    try:
        from bench.grounded_recall import _grounded_recall_nightly_loop
        asyncio.create_task(_grounded_recall_nightly_loop())
    except Exception as _gr_err:
        logger.warning(f"grounded-recall nightly loop not started: {_gr_err}")
    # Tasks #599 / #618 — per-language live-retriever nightly subsets.
    # Each Indian-language subset has only ~5–8 tagged cases vs >100
    # globally, so a total coverage drop on e.g. as.wikipedia or
    # hi.wikipedia barely moves the global recall@5 and never trips
    # the global gate. Each subset owns its lock + baseline_<code>.json
    # + alert_type so it cannot interfere with (or be masked by) the
    # global nightly or another language. Boot staggers inside each
    # loop prevent all three from double-hitting the live retrievers
    # in the same minute.
    try:
        from bench.grounded_recall import (
            PER_LANGUAGE_NIGHTLY_SUBSETS,
            per_language_nightly_loops,
        )
        # Iterate the registry so adding a language (tagged fixtures +
        # baseline_<code>.json) is a one-line change in grounded_recall.py
        # — no risk of the server.py wiring drifting out of sync.
        for _lang, _loop in per_language_nightly_loops().items():
            asyncio.create_task(_loop())
        logger.info(
            "grounded-recall per-language nightly loops started: %s",
            ",".join(PER_LANGUAGE_NIGHTLY_SUBSETS),
        )
    except Exception as _gr_lang_err:
        logger.warning(f"grounded-recall per-language nightly loops not started: {_gr_lang_err}")
    # Task #458 — daily/weekly auto-publish of SEO pages so the 991 syllabus
    # topics steadily fill in without admin clicks. Cross-replica dedup is
    # handled inside the loop via atomic CAS on db.job_locks, so it does not
    # need a leader gate. No-op when SEO_AUTO_PUBLISH_ENABLED=false.
    try:
        from seo_engine import _seo_auto_publish_loop
        asyncio.create_task(_seo_auto_publish_loop())
    except Exception as _sap_err:
        logger.warning(f"seo auto-publish loop not started: {_sap_err}")
    # Task #471 — proactive staleness monitor for the auto-publish job.
    # Hourly check; emails admins + drops an in-app notification when the
    # cron has not completed a run within 36h (daily) / 8d (weekly).
    # Debounced to at most one alert per 24h while stale, plus exactly
    # one recovery notification when the job runs again.
    try:
        from seo_engine import _seo_auto_publish_staleness_loop
        asyncio.create_task(_seo_auto_publish_staleness_loop())
    except Exception as _sap_stale_err:
        logger.warning(
            f"seo auto-publish staleness loop not started: {_sap_stale_err}")
    # Task #491 — liveness heartbeat for the staleness monitor itself.
    # Every 6h, verify the monitor's lock-doc ``updated_at`` is younger
    # than ~3h (2x its 1h cadence) and page admins exactly once if not.
    # Task #950: dedup is now via Mongo lease inside the loop
    # (``seo_staleness_heartbeat_lease``), not the per-machine
    # ``_is_leader`` file lock — Railway runs N replicas and each had
    # its own file lock, so all of them previously paged when the
    # monitor went quiet. The per-doc CAS inside the alerter remains
    # as defense-in-depth against fail-over mid-iteration.
    try:
        from seo_engine import _seo_staleness_heartbeat_loop
        asyncio.create_task(_seo_staleness_heartbeat_loop())
    except Exception as _sap_hb_err:
        logger.warning(
            f"seo staleness heartbeat loop not started: {_sap_hb_err}")
    # Task #484 — poll GitHub Actions every 10 min and email admins +
    # drop an in-app notification when the latest main-branch run for
    # backend-tests/frontend-tests flips to failure (or stays red past
    # the 6h re-page window). Recovery alert fires once on red→green.
    # Task #950: dedup is now via Mongo lease inside the loop
    # (``ci_alert_lease``), not the per-machine ``_is_leader`` file
    # lock — Railway runs N replicas and each had its own file lock,
    # so the GitHub API quota was being burned N×. The per-workflow
    # CAS inside the loop remains as defense-in-depth in case
    # leadership fails over mid-poll. No-ops cleanly when GITHUB_REPO
    # is unset (e.g. local dev).
    try:
        from routes.admin_ci_alerts import _ci_alert_loop
        asyncio.create_task(_ci_alert_loop())
    except Exception as _ci_alert_err:
        logger.warning(
            f"ci alert loop not started: {_ci_alert_err}")
    # Task #728 — hourly poll of the in-process Trustpilot aggregate
    # cache; emails admins + drops an in-app notification when the
    # /api/config/trustpilot/aggregate feed has had no successful
    # upstream fetch in >24h (rotated key, expired plan, WAF block,
    # etc). Debounced to one alert per 24h while broken, plus exactly
    # one recovery notification on broken→healthy.
    #
    # Run on EVERY replica (no leader gate) — the feed health state
    # lives in *per-process* memory (_tp_aggregate_cache), so a
    # leader-only loop could miss an outage entirely if production
    # traffic hashes around the leader replica. Cross-replica spam is
    # prevented by the atomic CAS on db.job_locks inside the loop, the
    # same dedup pattern Task #484's CI alerter relies on.
    try:
        from routes.admin_trustpilot_alerts import (
            _trustpilot_feed_alert_loop,
        )
        asyncio.create_task(_trustpilot_feed_alert_loop())
    except Exception as _tp_alert_err:
        logger.warning(
            f"trustpilot feed alert loop not started: {_tp_alert_err}")
    # Task #751 — separate alerter that pages when the daily refresh
    # GitHub Actions cron itself stops checking in (>36h since the last
    # heartbeat), distinct from the Task #728 data-staleness alert so
    # on-call can tell "Trustpilot is down" apart from "our cron has
    # been disabled".
    # Task #950: dedup is now via Mongo lease inside the loop
    # (``trustpilot_refresh_cron_alert_lease``), not the per-machine
    # ``_is_leader`` file lock — Railway runs N replicas and each
    # had its own file lock, so all of them previously hammered the
    # CAS each tick.
    try:
        from routes.admin_trustpilot_cron_alerts import (
            _trustpilot_refresh_cron_alert_loop,
        )
        asyncio.create_task(_trustpilot_refresh_cron_alert_loop())
    except Exception as _tp_cron_alert_err:
        logger.warning(
            "trustpilot refresh-cron alert loop not started: "
            f"{_tp_cron_alert_err}"
        )
    # Task #831 — symmetric silence alerter for the daily Cloudflare
    # firewall drift cron (.github/workflows/cf-waf-drift-daily.yml,
    # Task #828). The workflow already posts per-run Slack alerts on
    # drift; this loop adds the missing "the workflow itself stopped
    # running" signal, mirroring the Task #751 pattern. Leader-gated
    # Task #950: dedup is now via Mongo lease inside the loop
    # (``cf_waf_drift_cron_alert_lease``), not the per-machine
    # ``_is_leader`` file lock — Railway runs N replicas and each had
    # its own file lock, so all of them previously hammered the CAS
    # each tick.
    try:
        from routes.admin_cf_waf_drift_cron_alerts import (
            _cf_waf_drift_cron_alert_loop,
        )
        asyncio.create_task(_cf_waf_drift_cron_alert_loop())
    except Exception as _cfw_cron_alert_err:
        logger.warning(
            "cf-waf-drift cron alert loop not started: "
            f"{_cfw_cron_alert_err}"
        )
    # Task #951 — symmetric silence alerter for the unified-logs
    # Cloudflare GraphQL pull. Task #947 made the pull single-leader
    # via a Mongo lease; the flip side is that if every replica is
    # unhealthy (or the lease doc gets stuck owned by a zombie process
    # whose ``lease_expires_at`` is being refreshed by a frozen task),
    # the unified log explorer silently stops ingesting Cloudflare
    # data. This loop watches ``unified_logs_cf_pull_lock.updated_at``
    # — only stamped after a successful pull's cursor advance, so it
    # rules out the zombie-lease case the lease TTL alone cannot
    # detect — and pages on-call when the cursor goes stale. Mirrors
    # the cf-waf-drift cron alert loop above for cross-replica dedup
    # via :mod:`background_lease`.
    try:
        from routes.admin_logs_cf_pull_silence_alerts import (
            _cf_pull_silence_alert_loop,
        )
        asyncio.create_task(_cf_pull_silence_alert_loop())
    except Exception as _ulogs_silence_err:
        logger.warning(
            "unified-logs cf-pull silence alert loop not started: "
            f"{_ulogs_silence_err}"
        )
    # Task #893 — silence-alerter for the edge-proxy-deploy CI workflow.
    # Task #882 added a red/amber/green pill to AdminHealth that polls
    # the GitHub Actions REST API for the latest edge-proxy-deploy run;
    # this loop pages on-call (email + in-app + best-effort Slack) when
    # that pill flips to silent (failure) or degraded (>7d stale) so a
    # red smoke-preview regression at 03:00 UTC doesn't wait for an
    # admin to open the dashboard.
    # Task #950: dedup is now via Mongo lease inside the loop
    # (``edge_proxy_deploy_cron_alert_lease``), not the per-machine
    # ``_is_leader`` file lock — Railway runs N replicas and each had
    # its own file lock, so the GitHub REST quota was being burned N×.
    try:
        from routes.admin_edge_proxy_deploy_cron_alerts import (
            _edge_proxy_deploy_cron_alert_loop,
        )
        asyncio.create_task(_edge_proxy_deploy_cron_alert_loop())
    except Exception as _epd_cron_alert_err:
        logger.warning(
            "edge-proxy-deploy cron alert loop not started: "
            f"{_epd_cron_alert_err}"
        )
    # Task #970 — page on-call when one of the three sibling cron Slack
    # webhook env vars (UNIFIED_LOGS_CF_PULL_SLACK_WEBHOOK,
    # CF_WAF_DRIFT_SLACK_WEBHOOK, EDGE_PROXY_DEPLOY_SLACK_WEBHOOK) stays
    # unset for >24h after deploy. Task #963 documented the env vars and
    # Task #964 added the AdminHealth "Slack ✓ / ✗" badge for at-a-glance
    # visibility, but a deploy that ships without a webhook can sit
    # "Slack ✗" indefinitely until an admin happens to look at the
    # dashboard. This loop closes that gap by paging via in-app + email
    # (no Slack — the whole point is "your Slack webhook is missing")
    # using the same leader-gated lease + per-state CAS dedup the
    # silence alerters above use.
    try:
        from routes.admin_slack_webhook_missing_alerts import (
            _slack_webhook_missing_alert_loop,
        )
        asyncio.create_task(_slack_webhook_missing_alert_loop())
    except Exception as _swm_alert_err:
        logger.warning(
            "slack-webhook-missing alert loop not started: "
            f"{_swm_alert_err}"
        )
    # Task #950: dedup is now via Mongo lease inside the loop
    # (``cf_bot_report_lease``), not the per-machine ``_is_leader``
    # file lock — Railway runs N replicas and each had its own file
    # lock, so all of them previously polled the CF GraphQL API every
    # 5 min, multiplying the analytics quota cost.
    asyncio.create_task(_cf_bot_report_loop())
    # Task #387 — nightly Cloudflare Pages deploy hook so the
    # prerendered subject/chapter HTML stays current even when no
    # admin edits trigger a debounced refresh. No-ops if
    # CF_PAGES_DEPLOY_HOOK_URL is unset.
    # Task #950: dedup is now via Mongo lease inside the loop
    # (``pages_deploy_nightly_lease``), not the per-machine
    # ``_is_leader`` file lock — Railway runs N replicas and each
    # had its own file lock, so the deploy hook fired N CF Pages
    # builds per night.
    try:
        import pages_deploy as _pages_deploy
        asyncio.create_task(_pages_deploy.nightly_loop())
    except Exception as _pd_err:
        logger.warning(f"pages_deploy nightly loop not started: {_pd_err}")

    # Task #314 uses atomic Mongo CAS via db.job_locks for dedup across
    # replicas, so it does not need a leader gate.
    asyncio.create_task(_bot_traffic_report_loop())
    from middleware import _init_blocked_ip_cache
    asyncio.create_task(_init_blocked_ip_cache())
    from routes.admin_advanced import _collection_size_snapshot_loop, _cache_warm_loop
    asyncio.create_task(_collection_size_snapshot_loop())
    # Auto pre-warm AI response cache for the most common queries (Task #282 T004)
    # Task #950: dedup is now via Mongo lease inside the loop
    # (``cache_warm_lease``), not the per-machine ``_is_leader`` file
    # lock — Railway runs N replicas and each had its own file lock,
    # so the warm cycle was burning N× the LLM budget every 6h.
    # Followers stand down each tick.
    asyncio.create_task(_cache_warm_loop())

    # Task #310 — rehydrate chat speed-up metrics from Redis and start the
    # periodic flush so the per-day counters and warm-run history survive
    # API restarts/redeploys. Runs on EVERY worker (not leader-gated) so any
    # request that lands on any worker sees the historical aggregate; the
    # underlying Redis ops are atomic HINCRBY/HINCRBYFLOAT against per-day
    # hashes, so concurrent flushes from multiple workers add correctly.
    import chat_speedup_metrics as _speedup
    try:
        await asyncio.to_thread(_speedup.load_from_store)
    except Exception as _sp_load_err:
        logger.warning(f"chat_speedup_metrics startup load failed: {_sp_load_err}")
    _speedup_flush_task = asyncio.create_task(_speedup.periodic_flush_loop())

    # Task #422: re-apply persisted Assamese-purity admin override (if
    # any) so behaviour/threshold survive api restarts without needing
    # a redeploy. Runs on every worker so each one sees the override
    # in-memory.
    try:
        from routes.cms_sarvam_health import (
            apply_persisted_assamese_purity_override,
            _assamese_purity_refresh_loop,
            ensure_assamese_runs_index,
            ensure_assamese_audit_index,
        )
        await apply_persisted_assamese_purity_override()
        # Per-worker refresher so a PATCH/DELETE done on one gunicorn
        # worker propagates to all sibling workers within ~15s without
        # needing pub/sub infra.
        asyncio.create_task(_assamese_purity_refresh_loop())
        # Task #423: TTL index on the per-run stats collection so old
        # docs auto-expire after 14 days and the dashboard stays cheap.
        asyncio.create_task(ensure_assamese_runs_index())
        # Task #424: ts-desc index on the override-edit audit collection
        # so the history panel's `find().sort(ts,-1).limit(20)` is cheap.
        asyncio.create_task(ensure_assamese_audit_index())
    except Exception as _asm_load_err:
        logger.warning(f"[INDIC-SANITIZE] startup override load failed: {_asm_load_err}")

    # Task #754 — TTL index on the Trustpilot JSON-LD per-run history
    # collection so the AdminHealth tile can render a 30-day pass-rate
    # sparkline without unbounded growth.
    try:
        from routes.admin_trustpilot_jsonld_status import (
            ensure_trustpilot_jsonld_runs_index,
        )
        asyncio.create_task(ensure_trustpilot_jsonld_runs_index())
    except Exception as _tp_runs_err:
        logger.warning(
            f"[trustpilot-jsonld] runs index startup failed: {_tp_runs_err}"
        )

    # Task #609 — initialise the managed AI response cache. Safe no-op when
    # MEMORYSTORE_REDIS_URL is unset; the cache transparently falls back to
    # in-memory L1. LLM upstream caching is handled by Cloudflare AI Gateway.
    try:
        import ai_cache as _ai_cache
        await _ai_cache.init_async_client()
    except Exception as _ai_cache_err:
        logger.warning(f"ai_cache init failed (continuing with fallback): {_ai_cache_err}")

    # Task #667 — fail-fast startup self-check against Gemini/Vertex. Runs
    # in the background so a slow upstream never blocks the API from
    # accepting requests, but a broken credential or gateway misconfig
    # surfaces in the deploy logs as a single ERROR line within seconds —
    # before any user-facing 502.
    asyncio.create_task(_vertex_startup_probe())

    # Task #677 — periodic Gemini health probe. The startup probe only
    # catches misconfig at boot; this loop reruns health_check() every
    # VERTEX_PROBE_INTERVAL_S (default 600s) and dispatches an alert via
    # the same email/Slack pipeline as _seo_health_alert_loop on >=2
    # consecutive failures, so mid-day Vertex outages page on-call
    # instead of waiting for users to hit 502s.
    asyncio.create_task(_vertex_periodic_probe_loop())

    # Task #944 — Unified Log Explorer.
    #   * Ensure TTL + secondary indexes on the unified_logs collection
    #     so the admin UI's filtered queries hit an index from minute
    #     one (otherwise the first sort-by-timestamp pull on a fresh
    #     deploy is a full collection scan).
    #   * Hydrate the persisted runtime pause flag so a previous
    #     "Pause ingest" click survives the restart.
    #   * Boot the in-process backend log shipper which the global
    #     middleware drips per-request samples into.
    #   * Start the Cloudflare GraphQL pull loop on EVERY replica.
    #     Cross-replica dedup is enforced inside the loop via a
    #     Mongo-backed lease (Task #947, ``_try_acquire_cf_pull_lease``).
    #     The previous ``_is_leader`` gate was a per-machine file
    #     lock, so two Railway replicas would each consider themselves
    #     "leader" and double-poll the CF GraphQL API, multiplying
    #     analytics quota cost. Followers stand down on each tick
    #     instead of firing the GraphQL query, so quota cost stays
    #     at 1× regardless of replica count, and a leader fail-over
    #     is picked up within one follower interval.
    _unified_logs_cf_task = None
    try:
        import unified_logs_dao as _ulogs_dao
        from routes.admin_logs import (
            _hydrate_pause_state_from_db,
            _unified_logs_cf_pull_loop,
        )
        await _ulogs_dao.ensure_indexes(db)
        # Task #952 — TTL index on the rolling-24h saturation log so the
        # admin dashboard's "saturated minutes in last 24h" counter has
        # a bounded collection to scan.
        try:
            from routes.admin_logs_cf_pull_saturation_alerts import (
                ensure_saturation_indexes,
            )
            await ensure_saturation_indexes(db)
        except Exception as _sat_idx_err:
            logger.debug(
                f"[unified_logs] saturation index bootstrap failed: "
                f"{_sat_idx_err}"
            )
        await _hydrate_pause_state_from_db()
        await _ulogs_dao.get_backend_shipper().start(db)
        _unified_logs_cf_task = asyncio.create_task(_unified_logs_cf_pull_loop())
    except Exception as _ulogs_boot_err:
        logger.warning(f"[unified_logs] startup wiring failed: {_ulogs_boot_err}")

    logger.info("Syrabit.ai API started")
    if sarvam_client:
        logger.info("Sarvam AI client ready")
    yield
    # Task #310 — final flush of speed-up metrics before shutting down so the
    # most recent counters survive the restart.
    try:
        _speedup_flush_task.cancel()
        try:
            await _speedup_flush_task
        except (asyncio.CancelledError, Exception):
            pass
        await asyncio.to_thread(_speedup.flush_to_store)
    except Exception as _sp_shutdown_err:
        logger.warning(f"chat_speedup_metrics shutdown flush failed: {_sp_shutdown_err}")
    try:
        import ai_cache as _ai_cache_close
        await _ai_cache_close.close_async_client()
    except Exception:
        pass
    if _deps_mod._rate_cleanup_task:
        _deps_mod._rate_cleanup_task.cancel()
    # Task #944 — drain the in-process unified-log shipper so the
    # tail of records buffered between the last flush tick and the
    # shutdown signal is not lost on restart.
    try:
        import unified_logs_dao as _ulogs_dao_shutdown
        await _ulogs_dao_shutdown.get_backend_shipper().stop()
    except Exception as _ulogs_stop_err:
        logger.debug(f"[unified_logs] shutdown stop raised: {_ulogs_stop_err}")
    # Task #947 — cancel the CF pull loop so its CancelledError
    # handler releases the Mongo lease, letting a peer replica pick
    # up the loop on its next follower tick instead of waiting out
    # the full ``CF_PULL_LEASE_TTL_S`` window.
    if _unified_logs_cf_task is not None and not _unified_logs_cf_task.done():
        _unified_logs_cf_task.cancel()
        try:
            await _unified_logs_cf_task
        except (asyncio.CancelledError, Exception):
            pass
    if sarvam_client:
        await sarvam_client.aclose()
    if sarvam_translate_client:
        await sarvam_translate_client.aclose()
    if sarvam_llm_client:
        await sarvam_llm_client.aclose()
    if sarvam_client_direct:
        await sarvam_client_direct.aclose()
    if sarvam_llm_client_direct:
        await sarvam_llm_client_direct.aclose()
    try:
        from ga4_client import _ga4_http
        if _ga4_http:
            await _ga4_http.aclose()
    except Exception:
        pass
    try:
        import vectorize_client
        await vectorize_client.close()
    except Exception:
        pass
    mongo_client.close()
    if _lock_fd:
        try:
            fcntl.flock(_lock_fd, fcntl.LOCK_UN)
            _lock_fd.close()
        except Exception:
            pass


app = FastAPI(title="Syrabit.ai API", version="2.0.0", lifespan=lifespan)

# Task #610 — OpenTelemetry distributed tracing. Wired immediately after
# FastAPI() so the auto-instrumentor can register its ASGI middleware
# before any other middleware is added (excluded URLs cover health/metrics).
# No-op when TRACING_ENABLED is unset, so dev / Railway origins are
# unaffected. See tracing.py for env contract.
try:
    from tracing import init_tracing as _init_tracing
    _init_tracing(app)
except Exception as _trc_err:
    logger.warning(f"[tracing] init_tracing failed (non-fatal): {_trc_err}")

app.add_middleware(GZipMiddleware, minimum_size=500)


@app.exception_handler(_StarletteHTTPException)
async def _starlette_http_exception_handler(request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": True, "status": exc.status_code, "detail": exc.detail, "path": str(request.url.path)},
    )

@app.exception_handler(HTTPException)
async def _http_exception_handler(request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": True, "status": exc.status_code, "detail": exc.detail, "path": str(request.url.path)},
    )

@app.exception_handler(Exception)
async def _unhandled_exception_handler(request, exc):
    logger.error(f"Unhandled exception on {request.method} {request.url.path}: {type(exc).__name__}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": True, "status": 500, "detail": "Internal server error", "path": str(request.url.path)},
    )

@app.exception_handler(_PydanticValidationError)
async def _validation_exception_handler(request, exc):
    return JSONResponse(
        status_code=422,
        content={
            "error": True, "status": 422, "detail": "Validation error",
            "errors": [{"field": ".".join(str(l) for l in e["loc"]), "message": e["msg"]} for e in exc.errors()],
            "path": str(request.url.path),
        },
    )

@app.exception_handler(_RequestValidationError)
async def _request_validation_handler(request, exc):
    return JSONResponse(
        status_code=422,
        content={
            "error": True, "status": 422, "detail": "Request validation error",
            "errors": [{"field": ".".join(str(l) for l in e["loc"]), "message": e["msg"]} for e in exc.errors()],
            "path": str(request.url.path),
        },
    )


api = APIRouter(prefix="/api")

from routes.auth import router as auth_router
from routes.content import router as content_router
from routes.topic_faq_jsonld import router as topic_faq_jsonld_router
# Task #914 Steps 1-3 — topic citability pipeline:
#   * topic_answer_cards: public endpoints powering the per-topic AI
#     answer card on chapter pages and the dedicated topic deep-link
#     route (`/.../<chapter>/topic/<slug>`).
#   * admin_topic_audit: admin-only audit + on-demand backfill for
#     the persistent `topic_slug` and `definition_status` fields.
from routes.topic_answer_cards import router as topic_answer_cards_router
from routes.admin_topic_audit import router as admin_topic_audit_router
# topic_graph: sibling + cross-chapter related topics, plus the
# subject-wide topic index that powers the pillar SubjectLandingPage.
# Same auth surface as topic_answer_cards (public, edge-cached).
from routes.topic_graph import router as topic_graph_router
from routes.syllabus import router as syllabus_router
from routes.ai_chat import router as ai_chat_router
from routes.conversations import router as conversations_router
from routes.user import router as user_router
from routes.admin_auth_users import router as admin_auth_users_router
from routes.analytics import router as analytics_router
from routes.admin_content import router as admin_content_router
from routes.admin_pipeline import router as admin_pipeline_router
from routes.admin_settings import router as admin_settings_router
# Task #944 — Unified Log Explorer: admin filter/export/trace endpoints
# + the public token-authed ingest endpoint that the edge worker posts
# batched per-request log records to.
from routes.admin_logs import router as admin_logs_router
from routes.admin_notifications import router as admin_notifications_router
from routes.admin_monetization import router as admin_monetization_router
from routes.cms_sarvam_health import router as cms_sarvam_health_router
# Carved out of cms_sarvam_health.py (admin-panel audit Task #5) so the
# routes live in files whose name reflects what they do. Paths
# (/admin/ga4/*, /admin/vertex/*) and behaviour are unchanged.
from routes.admin_ga4 import router as admin_ga4_router
from routes.admin_vertex import router as admin_vertex_router
from routes.admin_advanced import router as admin_advanced_router
from routes.admin_retriever import router as admin_retriever_router
from routes.admin_benchmark import router as admin_benchmark_router
from routes.admin_kv_health import router as admin_kv_health_router
from routes.admin_edge_analytics import router as admin_edge_analytics_router
from routes.admin_ci_status import router as admin_ci_status_router
from routes.admin_trustpilot_alerts import router as admin_trustpilot_alerts_router
from routes.admin_trustpilot_jsonld_status import router as admin_trustpilot_jsonld_status_router
from routes.admin_trustpilot_cron_alerts import router as admin_trustpilot_cron_alerts_router
from routes.cf_waf_drift_cron_heartbeat import router as cf_waf_drift_cron_heartbeat_router
from routes.admin_cf_waf_drift_cron_alerts import router as admin_cf_waf_drift_cron_alerts_router
from routes.admin_cf_enterprise import router as admin_cf_enterprise_router
# Task #951 — silence alerter for the unified-logs Cloudflare GraphQL
# pull. Pages on-call when every backend replica has stopped advancing
# the ``unified_logs_cf_pull_lock.updated_at`` cursor (e.g. all
# replicas unhealthy, or a zombie holds the lease but never completes
# a tick) — the failure mode introduced by Task #947's single-leader
# guarantee.
from routes.admin_logs_cf_pull_silence_alerts import (
    router as admin_logs_cf_pull_silence_alerts_router,
)
# Task #952 — pages on-call when busy hours saturate the 200-buckets
# CF GraphQL cap and the unified-logs explorer starts losing rows
# (the failure mode Task #948's pagination surfaces but doesn't
# itself remediate).
from routes.admin_logs_cf_pull_saturation_alerts import (
    router as admin_logs_cf_pull_saturation_alerts_router,
)
# Task #974 — admin-readable surfaces for the Task #970 missing-Slack-
# webhook nag. Per-env alert-state + alert-history endpoints so the
# AdminHealth dashboard can decorate each cron pill's "Slack ✗" badge
# with "last paged Nh ago" inline.
from routes.admin_slack_webhook_missing_alerts import (
    router as admin_slack_webhook_missing_alerts_router,
)
from routes.synthetic_probe_secret_alert import router as synthetic_probe_secret_alert_router
# Task #882 — surfaces the latest edge-proxy-deploy GitHub Actions run
# as a cron pill in AdminHealth so a red `smoke-preview` regression
# pages on-call via the dashboard they already watch, instead of
# relying on someone noticing a red badge in the GitHub Actions UI.
from routes.admin_health import router as admin_health_router
from routes.admin_ads import router as admin_ads_router
from routes.admin_review_prompts import router as admin_review_prompts_router
from routes.edu_browser import router as edu_browser_router
from routes.edu_study import router as edu_study_router
from routes.admin_seo_keywords import router as admin_seo_keywords_router
from routes.admin_topic_discovery import router as admin_topic_discovery_router
from routes.admin_seo_remediation import router as admin_seo_remediation_router
from routes.admin_seo_internal_linker import router as admin_seo_internal_linker_router
from routes.admin_entity_seo import router as admin_entity_seo_router

api.include_router(auth_router)
api.include_router(content_router)
api.include_router(topic_faq_jsonld_router)
api.include_router(topic_answer_cards_router)
api.include_router(admin_topic_audit_router)
api.include_router(topic_graph_router)
api.include_router(syllabus_router)
api.include_router(ai_chat_router)
api.include_router(conversations_router)
api.include_router(user_router)
api.include_router(admin_auth_users_router)
api.include_router(analytics_router)

api.include_router(admin_content_router)
api.include_router(admin_pipeline_router)
api.include_router(admin_settings_router)
# Task #944 — Unified Log Explorer routes. Mounted on the bare ``app``
# instead of ``api`` because the routes already include ``/api/...``
# prefixes (so the ingest endpoint stays at ``/api/logs/ingest`` and is
# easy for the worker to target without reasoning about router nesting).
app.include_router(admin_logs_router)
api.include_router(admin_notifications_router)
api.include_router(admin_monetization_router)
api.include_router(cms_sarvam_health_router)
api.include_router(admin_ga4_router)
api.include_router(admin_vertex_router)
api.include_router(admin_advanced_router)
api.include_router(admin_retriever_router)
api.include_router(admin_benchmark_router)
api.include_router(admin_kv_health_router)
api.include_router(admin_edge_analytics_router)
api.include_router(admin_ci_status_router)
api.include_router(admin_trustpilot_alerts_router)
api.include_router(admin_trustpilot_jsonld_status_router)
api.include_router(admin_trustpilot_cron_alerts_router)
api.include_router(cf_waf_drift_cron_heartbeat_router)
api.include_router(admin_cf_waf_drift_cron_alerts_router)
api.include_router(admin_cf_enterprise_router)
api.include_router(admin_logs_cf_pull_silence_alerts_router)
api.include_router(admin_logs_cf_pull_saturation_alerts_router)
api.include_router(admin_slack_webhook_missing_alerts_router)
api.include_router(synthetic_probe_secret_alert_router)
api.include_router(admin_health_router)
api.include_router(admin_ads_router)
api.include_router(admin_review_prompts_router)
api.include_router(edu_browser_router)
api.include_router(edu_study_router)
api.include_router(admin_seo_keywords_router)
api.include_router(admin_topic_discovery_router)
api.include_router(admin_seo_remediation_router)
api.include_router(admin_seo_internal_linker_router)
api.include_router(admin_entity_seo_router)

from routes.voice import router as voice_router
api.include_router(voice_router)

from routes.admin_credits import router as admin_credits_router
api.include_router(admin_credits_router)

# Task #264 — Live credit-burn panels: AWS Activate, Azure for Startups,
# Axiom, and Sentry.  Routes: /admin/billing/{aws-activate,azure-startups,axiom,sentry}
from routes.admin_billing import router as admin_billing_router
api.include_router(admin_billing_router)

from llm import call_llm_api_content
from auth_deps import get_admin_user

from seo_engine import router as seo_router, init_seo_engine
init_seo_engine(db, call_llm_api_content, get_admin_user, log_activity_fn=supa_insert_activity_log)
api.include_router(seo_router)

from qa_engine import public_router as qa_public_router, admin_router as qa_admin_router, init_qa_engine
init_qa_engine(db, get_admin_user)
api.include_router(qa_public_router)
api.include_router(qa_admin_router)

from routes.bot_discovery import router as bot_discovery_router
api.include_router(bot_discovery_router)
from routes.bot_traffic_report import router as bot_traffic_report_router
api.include_router(bot_traffic_report_router)

app.include_router(api)

from routes.pyq import router as pyq_router
app.include_router(pyq_router)

from routes.config import router as config_router
app.include_router(config_router)

from routes.staff_content import router as staff_content_router
app.include_router(staff_content_router)

@app.get("/healthz/ai")
async def healthz_ai():
    """Task #678 — Railway-friendly liveness probe for Gemini.

    Reads the cache populated by ``_vertex_startup_probe`` and the
    periodic re-probe in this module. Returns 200 only when the most
    recent probe was healthy and was recorded within the TTL window
    (``2 * VERTEX_PROBE_INTERVAL_S`` by default). When this endpoint
    flips to 503 Railway will refuse to mark the rollout as healthy
    and auto-rollback instead of serving 502s to users.

    Also surfaces Workers AI 429 burst counts (informational — they do
    not affect the HTTP status code, which is driven solely by the
    Vertex/Gemini probe result).
    """
    import vertex_health_cache
    code, body = vertex_health_cache.healthz_ai_response()
    try:
        from llm import get_workers_ai_429_burst, get_workers_ai_429_burst_inprocess
        # burst_60s: in-process timestamp list (always exact 60s window, this worker only)
        # burst_180s: Redis counter (cross-worker, 180s TTL) with in-process fallback
        body["workers_ai_429_burst_60s"] = get_workers_ai_429_burst_inprocess(60)
        body["workers_ai_429_burst_180s"] = get_workers_ai_429_burst(180)
    except Exception:
        pass
    return JSONResponse(status_code=code, content=body)


@app.get("/healthz/r2")
async def healthz_r2():
    """Liveness probe for Cloudflare R2 Object Storage."""
    from r2_storage import r2_health
    result = await r2_health()
    code = 200 if result.get("ok") else 503
    return JSONResponse(status_code=code, content=result)


@app.get("/robots.txt", response_class=Response)
async def serve_robots_txt():
    txt = """# Syrabit.ai — robots.txt

# ── Search & Answer Bots (welcome) ──────────────────────────────────────
User-agent: Googlebot
Allow: /
Allow: /api/seo/
Allow: /api/seo/keyword-index
Allow: /api/seo/keyword-index.txt
Disallow: /admin/
Disallow: /history
Disallow: /profile
Disallow: /cms/

User-agent: Googlebot-Image
Allow: /

User-agent: Google-Extended
Allow: /

User-agent: GoogleOther
Allow: /

User-agent: Bingbot
Allow: /
Allow: /api/seo/
Allow: /api/seo/keyword-index
Allow: /api/seo/keyword-index.txt
Disallow: /admin/
Disallow: /history
Disallow: /profile
Disallow: /cms/

User-agent: Yandexbot
Allow: /
Allow: /api/seo/
Disallow: /admin/
Disallow: /history
Disallow: /profile

User-agent: DuckDuckBot
Allow: /
Allow: /api/seo/
Disallow: /admin/

User-agent: Applebot
Allow: /
Allow: /api/seo/
Disallow: /admin/

User-agent: Applebot-Extended
Allow: /
Allow: /api/seo/
Disallow: /admin/

# ── AI Search/Answer Bots (send traffic, welcome) ──────────────────────
User-agent: ChatGPT-User
Allow: /
Allow: /api/seo/
Allow: /api/seo/keyword-index
Allow: /api/seo/keyword-index.txt
Allow: /api/content/library-bundle
Allow: /api/content/chapters/
Allow: /llms.txt
Allow: /llms-full.txt
Allow: /feed.xml
Disallow: /admin/
Disallow: /api/auth/
Disallow: /api/ai/
Disallow: /api/admin/

User-agent: OAI-SearchBot
Allow: /
Allow: /api/seo/
Allow: /api/seo/keyword-index
Allow: /api/seo/keyword-index.txt
Allow: /llms.txt
Allow: /llms-full.txt
Disallow: /admin/
Disallow: /api/auth/
Disallow: /api/ai/

User-agent: PerplexityBot
Allow: /
Allow: /api/seo/
Allow: /api/seo/keyword-index
Allow: /api/seo/keyword-index.txt
Allow: /api/content/library-bundle
Allow: /llms.txt
Allow: /llms-full.txt
Allow: /feed.xml
Disallow: /admin/
Disallow: /api/auth/
Disallow: /api/ai/

User-agent: ClaudeBot
Allow: /
Allow: /api/seo/
Allow: /api/seo/keyword-index
Allow: /api/seo/keyword-index.txt
Allow: /llms.txt
Allow: /llms-full.txt
Disallow: /admin/
Disallow: /api/auth/
Disallow: /api/ai/

User-agent: Meta-ExternalAgent
Allow: /
Allow: /api/seo/
Allow: /api/seo/keyword-index
Allow: /api/seo/keyword-index.txt
Disallow: /admin/
Disallow: /api/auth/
Disallow: /api/ai/

# ── Training / Scraping Bots (ALLOWED — maximum LLM reach) ──────────────
# Switched from blanket Disallow to permissive by product decision: we
# want Syrabit content in every AI training corpus (ChatGPT, Claude,
# Gemini, Llama, Mistral, Doubao, etc.) so models "know" the domain
# even when they don't cite sources. Admin/auth/AI proxy paths remain
# disallowed uniformly — those leak no useful training signal.
# GPTBot = OpenAI's training-only crawler (different from OAI-SearchBot /
# ChatGPT-User, which cite sources and drive traffic — both remain Allowed
# above). Blocked by explicit product decision: we don't want our content
# silently ingested into OpenAI training sets without attribution.
User-agent: GPTBot
Disallow: /

User-agent: CCBot
Allow: /
Allow: /llms.txt
Allow: /llms-full.txt
Disallow: /admin/
Disallow: /api/auth/
Disallow: /api/ai/
Disallow: /api/admin/

User-agent: anthropic-ai
Allow: /
Allow: /llms.txt
Allow: /llms-full.txt
Disallow: /admin/
Disallow: /api/auth/
Disallow: /api/ai/
Disallow: /api/admin/

User-agent: Cohere-ai
Allow: /
Disallow: /admin/
Disallow: /api/auth/
Disallow: /api/ai/
Disallow: /api/admin/

User-agent: Bytespider
Allow: /
Disallow: /admin/
Disallow: /api/auth/
Disallow: /api/ai/
Disallow: /api/admin/

User-agent: PetalBot
Allow: /
Disallow: /admin/
Disallow: /api/auth/
Disallow: /api/ai/
Disallow: /api/admin/

User-agent: Scrapy
Allow: /
Disallow: /admin/
Disallow: /api/auth/
Disallow: /api/ai/
Disallow: /api/admin/

User-agent: AhrefsBot
Allow: /
Disallow: /admin/
Disallow: /api/auth/
Disallow: /api/ai/
Disallow: /api/admin/

User-agent: SemrushBot
Allow: /
Disallow: /admin/
Disallow: /api/auth/
Disallow: /api/ai/
Disallow: /api/admin/

User-agent: MJ12bot
Allow: /
Disallow: /admin/
Disallow: /api/auth/
Disallow: /api/ai/
Disallow: /api/admin/

User-agent: DotBot
Allow: /
Disallow: /admin/
Disallow: /api/auth/
Disallow: /api/ai/
Disallow: /api/admin/

User-agent: Amazonbot
Allow: /
Disallow: /admin/
Disallow: /api/auth/
Disallow: /api/ai/
Disallow: /api/admin/

User-agent: YouBot
Allow: /
Disallow: /admin/
Disallow: /api/auth/
Disallow: /api/ai/
Disallow: /api/admin/

User-agent: Diffbot
Allow: /
Disallow: /admin/
Disallow: /api/auth/
Disallow: /api/ai/
Disallow: /api/admin/

User-agent: img2dataset
Allow: /
Disallow: /admin/
Disallow: /api/auth/
Disallow: /api/ai/
Disallow: /api/admin/

User-agent: omgili
Allow: /
Disallow: /admin/
Disallow: /api/auth/
Disallow: /api/ai/
Disallow: /api/admin/

User-agent: FacebookBot
Allow: /
Disallow: /admin/
Disallow: /api/auth/
Disallow: /api/ai/
Disallow: /api/admin/

# ── Default (all other bots) ────────────────────────────────────────────
User-agent: *
Allow: /
Allow: /api/seo/
Allow: /api/seo/keyword-index
Allow: /api/seo/keyword-index.txt
Allow: /llms.txt
Allow: /llms-full.txt
Allow: /feed.xml
Disallow: /admin/
Disallow: /history
Disallow: /profile
Disallow: /cms/
Disallow: /api/auth/
Disallow: /api/ai/
Disallow: /api/admin/

# ── Sitemaps & Feeds ────────────────────────────────────────────────────
Sitemap: https://syrabit.ai/sitemap.xml
Sitemap: https://syrabit.ai/sitemap-index.xml

# RSS feeds
# https://syrabit.ai/feed.xml
# https://syrabit.ai/feed/notes.xml
# https://syrabit.ai/feed/mcqs.xml
# https://syrabit.ai/feed/blog.xml
# Atom feeds
# https://syrabit.ai/feed/atom.xml
# https://syrabit.ai/feed/notes-atom.xml
# https://syrabit.ai/feed/mcqs-atom.xml
# https://syrabit.ai/feed/blog-atom.xml
"""
    return Response(content=txt.strip(), media_type="text/plain")

@app.get("/", include_in_schema=False)
async def root_redirect(request: Request):
    # Use the canonical bot regex from utils.py (the source of truth
    # also imported by the tracking middleware) instead of redefining
    # a local copy that drifts out of sync. Covers Googlebot, Bingbot,
    # Applebot, GPTBot, PerplexityBot, ClaudeBot, OAI-SearchBot,
    # ChatGPT-User, Google-Extended, Applebot-Extended, social
    # previews, etc. — anything we want to see prerendered HTML.
    from utils import _SEARCH_BOT_UA_RE as _ROOT_BOT_RE
    ua = request.headers.get("user-agent", "")
    if _ROOT_BOT_RE.search(ua):
        try:
            _seo_port = int(os.environ.get("PORT", "8000"))
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"http://localhost:{_seo_port}/api/seo/html/homepage")
            if resp.status_code == 200 and "text/html" in resp.headers.get("content-type", ""):
                return Response(content=resp.text, media_type="text/html; charset=utf-8")
        except Exception as _root_err:
            logger.warning(f"root_redirect bot render failed: {_root_err}")
    from starlette.responses import RedirectResponse
    return RedirectResponse(url="/chat", status_code=302)

@app.get("/llms.txt", response_class=Response)
async def serve_llms_txt_root():
    from routes.admin_advanced import _build_llms_txt
    txt = await _build_llms_txt()
    return Response(content=txt, media_type="text/plain; charset=utf-8")

@app.get("/llms-full.txt", response_class=Response)
async def serve_llms_full_txt():
    from routes.bot_discovery import build_llms_full_txt
    txt = await build_llms_full_txt()
    return Response(content=txt, media_type="text/plain; charset=utf-8", headers={"Cache-Control": "public, max-age=3600, s-maxage=86400"})

@app.get("/feed.xml", response_class=Response)
async def serve_main_feed():
    from routes.bot_discovery import build_rss_feed
    xml = await build_rss_feed("all")
    return Response(content=xml, media_type="application/rss+xml; charset=utf-8", headers={"Cache-Control": "public, max-age=1800, s-maxage=3600"})

@app.get("/feed/notes.xml", response_class=Response)
async def serve_notes_feed():
    from routes.bot_discovery import build_rss_feed
    xml = await build_rss_feed("notes")
    return Response(content=xml, media_type="application/rss+xml; charset=utf-8", headers={"Cache-Control": "public, max-age=1800, s-maxage=3600"})

@app.get("/feed/mcqs.xml", response_class=Response)
async def serve_mcqs_feed():
    from routes.bot_discovery import build_rss_feed
    xml = await build_rss_feed("mcqs")
    return Response(content=xml, media_type="application/rss+xml; charset=utf-8", headers={"Cache-Control": "public, max-age=1800, s-maxage=3600"})

@app.get("/feed/blog.xml", response_class=Response)
async def serve_blog_feed():
    from routes.bot_discovery import build_rss_feed
    xml = await build_rss_feed("blog")
    return Response(content=xml, media_type="application/rss+xml; charset=utf-8", headers={"Cache-Control": "public, max-age=1800, s-maxage=3600"})

@app.get("/feed/atom.xml", response_class=Response)
async def serve_atom_feed():
    from routes.bot_discovery import build_atom_feed
    xml = await build_atom_feed("all")
    return Response(content=xml, media_type="application/atom+xml; charset=utf-8", headers={"Cache-Control": "public, max-age=1800, s-maxage=3600"})

@app.get("/feed/notes-atom.xml", response_class=Response)
async def serve_notes_atom_feed():
    from routes.bot_discovery import build_atom_feed
    xml = await build_atom_feed("notes")
    return Response(content=xml, media_type="application/atom+xml; charset=utf-8", headers={"Cache-Control": "public, max-age=1800, s-maxage=3600"})

@app.get("/feed/mcqs-atom.xml", response_class=Response)
async def serve_mcqs_atom_feed():
    from routes.bot_discovery import build_atom_feed
    xml = await build_atom_feed("mcqs")
    return Response(content=xml, media_type="application/atom+xml; charset=utf-8", headers={"Cache-Control": "public, max-age=1800, s-maxage=3600"})

@app.get("/feed/blog-atom.xml", response_class=Response)
async def serve_blog_atom_feed():
    from routes.bot_discovery import build_atom_feed
    xml = await build_atom_feed("blog")
    return Response(content=xml, media_type="application/atom+xml; charset=utf-8", headers={"Cache-Control": "public, max-age=1800, s-maxage=3600"})

@app.get("/.well-known/ai-plugin.json", response_class=Response)
async def serve_ai_plugin_json():
    from routes.bot_discovery import build_ai_plugin_json
    data = build_ai_plugin_json()
    return Response(content=data, media_type="application/json; charset=utf-8", headers={"Cache-Control": "public, max-age=86400"})

from routes.bot_discovery import INDEXNOW_KEY as _INDEXNOW_KEY

@app.get(f"/{_INDEXNOW_KEY}.txt", response_class=Response)
async def serve_indexnow_key_root():
    return Response(content=_INDEXNOW_KEY, media_type="text/plain")

@app.get("/sitemap.xml")
async def serve_root_sitemap():
    from starlette.responses import RedirectResponse
    return RedirectResponse(url="/api/seo/sitemap.xml", status_code=301)

@app.get("/sitemap-index.xml")
async def serve_root_sitemap_index():
    from starlette.responses import RedirectResponse
    return RedirectResponse(url="/api/seo/sitemap-index.xml", status_code=301)


# Task #365: Expose every dynamic sitemap that the SEO Manager / Google
# Search Console probes at the *root* of the domain (e.g.
# ``https://syrabit.ai/sitemap-pages.xml``). The actual generators live
# on the seo_engine router under ``/api/seo/...``; we delegate to them
# rather than duplicate the XML build logic so the two paths cannot
# drift. Without these aliases the SPA catch-all returned the React
# shell as text/html and external sitemap validators / Googlebot
# rejected every entry as "not XML". Each route is registered for
# both GET and HEAD so HEAD probes (used by the internal spot-checker
# and many crawlers) report 200 with ``application/xml`` instead of
# 404 ``application/json`` from the catch-all.
_DYNAMIC_SITEMAP_ALIASES = (
    ("sitemap-pages.xml",       "get_sitemap_pages"),
    ("sitemap-subjects.xml",    "get_sitemap_subjects"),
    ("sitemap-chapters.xml",    "get_sitemap_chapters"),
    ("sitemap-learn.xml",       "get_sitemap_learn"),
    ("sitemap-notes.xml",       "get_sitemap_notes"),
    ("sitemap-mcqs.xml",        "get_sitemap_mcqs"),
    ("sitemap-pyqs.xml",        "get_sitemap_pyqs"),
    ("sitemap-examples.xml",    "get_sitemap_examples"),
    ("sitemap-definitions.xml", "get_sitemap_definitions"),
)


def _register_root_sitemap_aliases():
    import seo_engine as _seo
    for filename, handler_name in _DYNAMIC_SITEMAP_ALIASES:
        handler = getattr(_seo, handler_name, None)
        if handler is None:
            continue
        # Capture handler in a default arg so each closure binds its own
        async def _proxy(handler=handler):
            return await handler()
        _proxy.__name__ = f"serve_root_{handler_name}"
        app.add_api_route(
            f"/{filename}",
            _proxy,
            methods=["GET", "HEAD"],
            include_in_schema=False,
        )


_register_root_sitemap_aliases()


# Task #365: HEAD-vs-GET parity. FastAPI's ``app.get`` registers the
# route for the GET method only — HEAD requests fall through and our
# default exception handler emits ``404 application/json`` with
# ``x-source: backend``. Search engines (and our own SEO health probe)
# use HEAD as the cheap pre-check, so every SPA route was being
# counted as broken even though GET returned 200. This middleware
# rewrites the ASGI scope so HEAD is processed by the matching GET
# handler, then drops the response body before flushing — preserving
# correct HEAD semantics (headers only, content-length=0).
class HeadAsGetMiddleware:
    """Pure-ASGI middleware: HEAD → GET, body stripped on the way out.

    Installed as the *outermost* middleware so the rewritten method is
    visible to every downstream layer (auth, rate limit, bot render,
    routing). Non-HEAD requests are forwarded unchanged with zero
    overhead.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("method") != "HEAD":
            await self.app(scope, receive, send)
            return
        new_scope = {**scope, "method": "GET", "_original_method": "HEAD"}

        async def _send(message):
            mtype = message.get("type")
            if mtype == "http.response.start":
                # Drop content-length; HEAD carries no body. Leave
                # every other header (cache-control, content-type,
                # x-source, etc.) intact so HEAD reports the same
                # shape as GET.
                headers = [
                    (k, v) for (k, v) in message.get("headers", [])
                    if k.lower() != b"content-length"
                ]
                await send({**message, "headers": headers})
            elif mtype == "http.response.body":
                # Coalesce streaming bodies into a single empty body
                # message. We only emit the terminator (more_body
                # False) — intermediate chunks are swallowed.
                if not message.get("more_body", False):
                    await send({
                        "type": "http.response.body",
                        "body": b"",
                        "more_body": False,
                    })
            else:
                await send(message)

        await self.app(new_scope, receive, _send)


from middleware import (
    SecurityHeadersMiddleware,
    CfPerformanceMiddleware,
    GlobalRateLimitMiddleware,
    ServerSideTrackingMiddleware,
    OriginSharedSecretMiddleware,
    MtlsClientCertMiddleware,
)
from routes.cms_sarvam_health import CmsNoIndexMiddleware, BotRenderMiddleware
app.add_middleware(CmsNoIndexMiddleware)
app.add_middleware(BotRenderMiddleware)
app.add_middleware(ServerSideTrackingMiddleware)
app.add_middleware(GlobalRateLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(CfPerformanceMiddleware)
# Task #793: re-applies a freshly-minted ``syrabit_device`` cookie to
# the outgoing response when the route handler returned its own
# ``Response`` (e.g. ``StreamingResponse`` on /ai/chat/stream), which
# otherwise causes FastAPI to discard the dependency-injected
# Response and silently drop the Set-Cookie header.
from middleware import DeviceCookieMiddleware
app.add_middleware(DeviceCookieMiddleware)
# Task #606: When deployed on Cloud Run behind Cloudflare, require the
# shared-secret header injected by the edge worker so direct hits to the
# Cloud Run URL (e.g. `https://syrabit-backend-xyz.a.run.app/api/...`) are
# rejected. No-op when ORIGIN_SHARED_SECRET env var is unset, so the
# Railway origin keeps working until cutover.
app.add_middleware(OriginSharedSecretMiddleware)
# Task #120: Application-layer mTLS enforcement — validate the HMAC proof
# header injected by the CF Worker on every backend request when the mTLS
# cert (MTLS_CERT binding) is active.  The HMAC is non-spoofable without
# ORIGIN_SHARED_SECRET.  Active when ENFORCE_MTLS=true is set in the
# Railway service environment AND ORIGIN_SHARED_SECRET is configured.
app.add_middleware(MtlsClientCertMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_credentials=_CORS_ALLOW_CREDENTIALS,
    allow_origins=CORS_ORIGINS,
    allow_origin_regex=CORS_ORIGIN_REGEX,
    allow_methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "Origin", "X-Requested-With", "x-anon-id", "x-turnstile-token", "traceparent", "tracestate", "baggage"],
    expose_headers=["X-RateLimit-Limit", "X-RateLimit-Remaining", "Retry-After", "X-Request-Id", "traceparent"],
    max_age=600,
)
# Task #365: Outermost layer — convert HEAD → GET before any other
# middleware (CORS, security headers, rate limit, bot render) sees it.
app.add_middleware(HeadAsGetMiddleware)

FRONTEND_BUILD = ROOT_DIR / "frontend" / "build"
if FRONTEND_BUILD.is_dir():
    class CachedStaticFiles(StaticFiles):
        async def get_response(self, path, scope):
            response = await super().get_response(path, scope)
            if response.status_code == 200:
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            return response

    static_dir = FRONTEND_BUILD / "static"
    if static_dir.is_dir():
        app.mount("/static", CachedStaticFiles(directory=str(static_dir)), name="static-assets")

    _SPA_SKIP_PREFIXES = ("api/", "docs", "openapi.json", "health")

    import re as _spa_re
    _OG_BOT_RE = _spa_re.compile(
        r"facebookexternalhit|facebookbot|whatsapp|twitterbot|linkedinbot|"
        r"telegrambot|slackbot|discordbot|pinterest|snapchat|skype",
        _spa_re.IGNORECASE,
    )
    _SUBJECT_PATH_RE = _spa_re.compile(
        r"^(?P<board>[^/]+)/(?P<class>[^/]+)(?:/(?P<stream>[^/]+))?/(?P<subject>[^/]+)/?$"
    )
    _CHAPTER_PATH_RE = _spa_re.compile(
        r"^(?P<board>[^/]+)/(?P<class>[^/]+)/(?P<subject>[^/]+)/(?P<chapter>[^/]+)/?$"
    )
    # Canonical bot regex from utils.py — same source of truth as
    # the tracking middleware and root_redirect. Aliased locally
    # under the historical name `_SEO_BOT_RE` so callers below
    # keep working unchanged.
    from utils import _SEARCH_BOT_UA_RE as _SEO_BOT_RE
    _VALID_SEO_PAGE_TYPES = {"mcqs", "important-questions", "examples", "definition"}
    _KNOWN_FIRST_SEGMENTS = {
        "api", "docs", "openapi.json", "health", "static",
        "home", "about", "pricing", "signup", "login", "reset-password",
        "library", "curriculum", "chat", "history", "profile", "admin",
        "onboarding", "terms", "privacy", "status", "exam-routine",
        "learn", "pyq", "subject", "subscribe", "payment", "cms",
    }

    async def _check_seo_content_exists(full_path: str) -> bool | None:
        parts = [p for p in full_path.split("/") if p]
        n = len(parts)
        if n < 3 or n > 5:
            return None
        if parts[0] in _KNOWN_FIRST_SEGMENTS:
            return None
        if n == 5 and parts[4] not in _VALID_SEO_PAGE_TYPES:
            return False
        try:
            from deps import db
            if not db:
                return None
            _pub = {"$or": [{"status": {"$exists": False}}, {"status": "published"}]}
            board = await db.boards.find_one({"$and": [{"slug": parts[0]}, _pub]}, {"_id": 0, "id": 1})
            if not board:
                return False
            cls = await db.classes.find_one({"$and": [{"slug": parts[1], "board_id": board["id"]}, _pub]}, {"_id": 0, "id": 1})
            if not cls:
                return False
            streams = await db.streams.find({"$and": [{"class_id": cls["id"]}, _pub]}, {"_id": 0, "id": 1}).to_list(100)
            stream_ids = [s["id"] for s in streams]
            if not stream_ids:
                return None
            subj = await db.subjects.find_one(
                {"slug": parts[2], "stream_id": {"$in": stream_ids}, "status": "published"},
                {"_id": 0, "id": 1},
            )
            if not subj:
                subj_any = await db.subjects.find_one(
                    {"slug": parts[2], "stream_id": {"$in": stream_ids}},
                    {"_id": 0, "id": 1},
                )
                if subj_any:
                    return None
                return False
            if n == 3:
                return True
            chapter = await db.chapters.find_one(
                {"slug": parts[3], "subject_id": subj["id"]},
                {"_id": 0, "id": 1},
            )
            if chapter:
                return True
            import re as _re_chk
            all_chapters = await db.chapters.find({"subject_id": subj["id"]}, {"_id": 0, "title": 1}).to_list(200)
            for c in all_chapters:
                auto_slug = _re_chk.sub(r'[^a-z0-9]+', '-', c.get("title", "").lower()).strip('-')
                if auto_slug == parts[3]:
                    return True
            return False
        except Exception:
            return None

    def _build_og_html(title: str, desc: str, page_url: str, og_image: str) -> str:
        from html import escape
        return (
            '<!DOCTYPE html><html lang="en"><head>'
            '<meta charset="utf-8">'
            f'<title>{escape(title)} | Syrabit.ai</title>'
            f'<meta name="description" content="{escape(desc)}">'
            f'<meta property="og:site_name" content="Syrabit.ai">'
            f'<meta property="og:title" content="{escape(title)}">'
            f'<meta property="og:description" content="{escape(desc)}">'
            f'<meta property="og:type" content="article">'
            f'<meta property="og:url" content="{escape(page_url)}">'
            f'<meta property="og:image" content="{escape(og_image)}">'
            '<meta property="og:image:width" content="1200">'
            '<meta property="og:image:height" content="630">'
            '<meta name="twitter:card" content="summary_large_image">'
            f'<meta name="twitter:title" content="{escape(title)}">'
            f'<meta name="twitter:description" content="{escape(desc)}">'
            f'<meta name="twitter:image" content="{escape(og_image)}">'
            f'<link rel="canonical" href="{escape(page_url)}">'
            f'<meta http-equiv="refresh" content="0;url={escape(page_url)}">'
            '</head><body></body></html>'
        )

    async def _og_html_for_chapter(path: str) -> Optional[str]:
        m = _CHAPTER_PATH_RE.match(path)
        if not m:
            return None
        try:
            from deps import db
            if not db:
                return None
            board_slug = m.group("board")
            class_slug = m.group("class")
            subject_slug = m.group("subject")
            chapter_slug = m.group("chapter")

            _pub = {"$or": [{"status": {"$exists": False}}, {"status": "published"}]}
            board = await db.boards.find_one({"$and": [{"slug": board_slug}, _pub]}, {"_id": 0, "id": 1, "name": 1})
            if not board:
                return None
            cls = await db.classes.find_one({"$and": [{"slug": class_slug, "board_id": board["id"]}, _pub]}, {"_id": 0, "id": 1, "name": 1})
            if not cls:
                return None
            streams = await db.streams.find({"$and": [{"class_id": cls["id"]}, _pub]}, {"_id": 0, "id": 1}).to_list(100)
            stream_ids = [s["id"] for s in streams]
            subj = await db.subjects.find_one(
                {"slug": subject_slug, "stream_id": {"$in": stream_ids}, "status": "published"},
                {"_id": 0, "id": 1, "name": 1},
            )
            if not subj:
                return None
            chapter = await db.chapters.find_one(
                {"slug": chapter_slug, "subject_id": subj["id"]},
                {"_id": 0, "title": 1, "description": 1},
            )
            if not chapter:
                import re as _re_inner
                all_chapters = await db.chapters.find({"subject_id": subj["id"]}, {"_id": 0, "title": 1, "description": 1}).to_list(200)
                for c in all_chapters:
                    auto_slug = _re_inner.sub(r'[^a-z0-9]+', '-', c.get("title", "").lower()).strip('-')
                    if auto_slug == chapter_slug:
                        chapter = c
                        break
            if not chapter:
                return None

            ch_title = chapter.get("title", chapter_slug)
            subj_name = subj.get("name", "")
            board_name = board.get("name", "")
            class_name = cls.get("name", "")

            title = f"{ch_title} — {subj_name} | {board_name} {class_name} Notes"
            desc = chapter.get("description") or f"{ch_title} notes for {subj_name}. Complete study material for {board_name} {class_name} students."
            page_url = f"https://syrabit.ai/{path}"
            og_image = "https://syrabit.ai/opengraph.jpg"

            return _build_og_html(title, desc, page_url, og_image)
        except Exception as _og_err:
            logger.warning(f"OG chapter tag injection error: {_og_err}")
            return None

    async def _og_html_for_subject(path: str) -> Optional[str]:
        m = _SUBJECT_PATH_RE.match(path)
        if not m:
            return None
        try:
            from deps import db
            if not db:
                return None
            board_slug = m.group("board")
            subject_slug = m.group("subject")
            stream_slug = m.group("stream") or m.group("class")

            subj = await db.subjects.find_one(
                {"slug": subject_slug, "status": "published"},
                {"_id": 0, "id": 1, "name": 1, "description": 1, "slug": 1,
                 "thumbnailUrl": 1, "thumbnail_url": 1, "board_name": 1,
                 "class_name": 1, "stream_name": 1, "chapter_count": 1},
            )
            if not subj:
                return None

            name = subj.get("name", "")
            desc = subj.get("description") or f"Complete {name} notes, chapters, and AI explanations for Assam board students."
            thumb = subj.get("thumbnailUrl") or subj.get("thumbnail_url") or ""
            subj_id = subj.get("id", "")
            board = subj.get("board_name", "")
            cls = subj.get("class_name", "")
            stream = subj.get("stream_name", "")
            label = f"{cls} {board} {stream}".strip() or "Assam Board"

            title = f"{name} Notes — {label}"
            page_url = f"https://syrabit.ai/{path}"

            if thumb and subj_id:
                og_image = f"https://syrabit.ai/api/content/subjects/{subj_id}/og-image.png"
            else:
                og_image = "https://syrabit.ai/opengraph.jpg"

            return _build_og_html(title, desc, page_url, og_image)
        except Exception as _og_err:
            logger.warning(f"OG tag injection error: {_og_err}")
            return None

    @app.get("/{full_path:path}")
    async def serve_spa(request: Request, full_path: str):
        if any(full_path.startswith(p) for p in _SPA_SKIP_PREFIXES):
            return JSONResponse(status_code=404, content={"detail": "Not found"})

        ua = (request.headers.get("user-agent") or "").lower()
        if _OG_BOT_RE.search(ua) and full_path and "/" in full_path:
            og_html = await _og_html_for_chapter(full_path) or await _og_html_for_subject(full_path)
            if og_html:
                return Response(content=og_html, media_type="text/html")

        if _SEO_BOT_RE.search(ua) and full_path:
            exists = await _check_seo_content_exists(full_path)
            if exists is False:
                return JSONResponse(status_code=404, content={"detail": "Not found"})

        index_file = FRONTEND_BUILD / "index.html"
        if index_file.exists():
            from fastapi.responses import FileResponse
            return FileResponse(str(index_file), media_type="text/html")
        return JSONResponse(status_code=404, content={"detail": "Frontend not built"})


if __name__ == "__main__":
    import uvicorn
    PORT = int(Configurator.get("PORT", 5000))
    logger.info(f"Starting server on port {PORT}")
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=PORT,
        log_level="info"
    )
