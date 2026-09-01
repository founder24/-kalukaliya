from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from posthog import Posthog
import logging
import uuid
import time

import asyncio
import re

from app.config import settings
from app.db.mongo import init_mongo, close_mongo
from app.api.v1 import (
    chat,
    auth,
    subscription,
    users,
    health,
    feedback,
    admin,
    edu,
    conversations,
    analytics,
    config,
)
from app.api.v1 import (
    admin_dashboard,
    admin_users,
    admin_conversations,
    admin_content,
    admin_analytics,
    admin_settings,
    admin_notifications,
    admin_seo,
    admin_ai,
    admin_revenue,
    admin_alerts,
    admin_knowledge,
    admin_translate,
    admin_dead_letters,
    admin_security,
    admin_corpus,
    admin_db_health,
   admin_pipeline_health,
    admin_rag,
    admin_cron,
    admin_billing,
    admin_ga4,
    admin_ads,
    admin_syra,
    admin_logs,
    admin_vertex,
    admin_indexnow_admin,
    admin_seo_advanced,
    admin_pyq,
    admin_documents,
    public_documents,
    seo,
    indexnow,
    content,
    public_content,
    changelog,
    payments,
    staff_content,
)
from app.api.webhooks import razorpay

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application Lifespan Events - Startup and Shutdown"""
    # Startup
    try:
        await init_mongo()
        logger.info("MongoDB initialized successfully")
        from app.services.memory_service import ensure_memory_indexes
        await ensure_memory_indexes()
    except Exception as e:
        # Log as critical but do NOT raise — allow the app to start in degraded mode.
        # Cloud Run health checks hit /health (not MongoDB), so the service will report
        # as running but degraded. This prevents a missing MONGODB_URI env var from
        # causing Cloud Run to report 0 healthy instances (and serve Google's 404 page).
        # Operators can detect the degraded state via /health/deep or Sentry alerts.
        err_msg = (
            f"MongoDB initialization failed ({settings.APP_ENV}): {e} — "
            "service is DEGRADED. Check Atlas connectivity/IP allowlist and MONGODB_URI secret."
        )
        logger.critical(err_msg)
        settings.startup_errors.append(err_msg)

    # ── Load shared internal credentials from GCP Secret Manager ──────────────
    try:
        from app.core.secret_manager import load_secrets_into_settings

        sm_results = await load_secrets_into_settings()
        logger.info(f"Secret Manager fetch results: {sm_results}")
        if settings.EDGE_SHARED_SECRET:
            logger.info("Workers AI internal generation authentication is configured")
        else:
            logger.warning("EDGE_SHARED_SECRET is empty — Worker generation jobs are unavailable")
    except Exception as e:
        logger.error(f"Secret Manager startup failed (non-fatal): {e}")

    # Warm up topic embeddings + greeting RAG in the background so they don't
    # delay server startup or block the first request.  Both are non-fatal —
    # the first real request that needs them will trigger a lazy load instead.
    async def _warm_topic_matcher() -> None:
        try:
            from app.services.ai.topic_matcher import topic_matcher
            await topic_matcher._load_embeddings()
            logger.info(
                f"Topic embeddings warmed up: {len(topic_matcher._embeddings or [])} topics"
            )
        except Exception as e:
            logger.warning(f"Topic embedding warm-up failed (non-fatal): {e}")

    async def _warm_greeting_rag() -> None:
        try:
            from app.services.ai.greeting_rag import greeting_rag
            await greeting_rag.initialize()
        except Exception as e:
            logger.warning(f"Greeting RAG warm-up failed (non-fatal, fast-path still works): {e}")

    asyncio.create_task(_warm_topic_matcher(), name="warm_topic_matcher")
    asyncio.create_task(_warm_greeting_rag(), name="warm_greeting_rag")

    # ── Admin Bootstrap ──────────────────────────────────────────────────────
    # Creates or updates the admin user when ADMIN_EMAIL + ADMIN_PASSWORD are set.
    # Safe to run on every restart — idempotent unless ADMIN_FORCE_RESET=true.
    if settings.ADMIN_EMAIL and settings.ADMIN_PASSWORD:
        try:
            from app.models.user import User

            existing = await User.find_one({"email": settings.ADMIN_EMAIL})
            if existing:
                update: dict = {
                    "role": "admin",
                    "updated_at": __import__("datetime").datetime.now(
                        __import__("datetime").timezone.utc
                    ),
                }
                if settings.ADMIN_FORCE_RESET:
                    update["hashed_password"] = User.hash_password(
                        settings.ADMIN_PASSWORD
                    )
                    logger.info(f"Admin password reset for: {settings.ADMIN_EMAIL}")
                if existing.role != "admin":
                    logger.info(
                        f"Promoted existing user to admin: {settings.ADMIN_EMAIL}"
                    )
                await existing.update({"$set": update})
            else:
                admin_user = User(
                    email=settings.ADMIN_EMAIL,
                    hashed_password=User.hash_password(settings.ADMIN_PASSWORD),
                    role="admin",
                    auth_provider="local",
                    name="Admin",
                )
                await admin_user.insert()
                logger.info(f"Admin user created: {settings.ADMIN_EMAIL}")
        except Exception as e:
            logger.warning(f"Admin bootstrap skipped (DB may not be ready): {e}")
    # ─────────────────────────────────────────────────────────────────────────

    if not settings.JWT_SECRET:
        logger.warning(
            "JWT_SECRET is not configured; JWT authentication will remain unavailable "
            "until the runtime supplies it."
        )

    # Initialize Sentry with FastAPI integration
    if settings.SENTRY_DSN:
        import os as _os
        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            environment=settings.SENTRY_ENVIRONMENT,
            traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
            profiles_sample_rate=0.1,
            send_default_pii=False,
            integrations=[
                FastApiIntegration(transaction_style="endpoint"),
            ],
        )
        sentry_sdk.set_tag("revision", _os.environ.get("K_REVISION", "local"))
        sentry_sdk.set_tag("service", "syrabit-backend")
        logger.info("Sentry initialized")

    # Initialize PostHog
    app.state.posthog = None
    if settings.POSTHOG_API_KEY:
        app.state.posthog = Posthog(
            project_api_key=settings.POSTHOG_API_KEY, host=settings.POSTHOG_HOST
        )
        logger.info("PostHog initialized")

    yield

    # ── Shutdown ─────────────────────────────────────────────────────────────
    # Cancel all pending background tasks (asyncio.create_task calls in
    # admin_rag.py, admin_cron.py, etc.) so that in-progress AI/DB writes are
    # not left in a half-written state when uvicorn reloads on a file save.
    _current = asyncio.current_task()
    _pending = [
        t for t in asyncio.all_tasks()
        if t is not _current and not t.done()
    ]
    if _pending:
        logger.info(f"Shutdown: cancelling {len(_pending)} background task(s)...")
        for _t in _pending:
            _t.cancel()
        await asyncio.gather(*_pending, return_exceptions=True)
        logger.info("Background tasks cancelled cleanly.")

    from app.services.ai.workers_ai_client import workers_ai_client
    from app.services.payment.razorpay_client import razorpay_client
    from app.services.comms.resend_client import close_resend_client

    await workers_ai_client.close()
    await razorpay_client.close()
    await close_resend_client()
    await close_mongo()
    logger.info("Application shutdown complete")


# Content API paths that fire when users load content pages.
# These are the real signals — frontend routes never reach FastAPI.
#
# Signals tracked:
#   chapter-by-slug  → user opened a chapter page
#   resolve-subject  → user opened a subject/stream page
#   library-bundle   → user loaded the library/browse page
#   subjects         → user loaded the library listing
#   chapters/{id}/published-topics → secondary chapter-load signal (filtered out below
#                                     because the id gives no readable frontend path)
_CONTENT_API_RE = re.compile(
    r"^/api/v1/content/(?:chapter-by-slug|resolve-subject|library-bundle|subjects\b)",
    re.IGNORECASE,
)


def _api_path_to_frontend(path: str) -> str:
    """
    Convert a content API path to the equivalent frontend URL that caused the call.

    Examples
    --------
    /api/v1/content/chapter-by-slug/ahsec/hs-1st-year/physics/units-and-measurements
      → /ahsec/hs-1st-year/physics/units-and-measurements

    /api/v1/content/resolve-subject/ahsec/hs-1st-year/physics
      → /ahsec/hs-1st-year/physics

    /api/v1/content/library-bundle  → /library
    /api/v1/content/subjects        → /library
    """
    if "/chapter-by-slug/" in path:
        # Strip prefix up to and including "chapter-by-slug/"
        slug_part = path.split("/chapter-by-slug/", 1)[-1]
        return "/" + slug_part.strip("/")
    if "/resolve-subject/" in path:
        slug_part = path.split("/resolve-subject/", 1)[-1]
        return "/" + slug_part.strip("/")
    if "/library-bundle" in path or "/subjects" in path:
        return "/library"
    return path  # fallback: store as-is


def _maybe_log_request(request: Request, status_code: int, latency_ms: int) -> None:
    """
    Fire-and-forget: write a lightweight document to request_logs when a content
    API call succeeds (GET 2xx). Converts the API path to the equivalent frontend
    URL so the top-routes widget shows human-readable page paths.

    Scheduled with asyncio.create_task — never blocks the HTTP response.
    """
    if request.method != "GET":
        return
    if status_code < 200 or status_code >= 300:
        return
    path = request.url.path
    if not _CONTENT_API_RE.match(path):
        return

    frontend_path = _api_path_to_frontend(path)

    async def _write() -> None:
        try:
            from app.db.mongo import get_mongo_client
            from datetime import datetime, timezone

            db = get_mongo_client()[settings.MONGODB_DB_NAME]
            ip = (
                request.headers.get("cf-connecting-ip")
                or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
                or (request.client.host if request.client else "unknown")
            )
            await db.request_logs.insert_one({
                "path": frontend_path,   # readable frontend URL shown in top-routes
                "api_path": path,        # raw API path for debugging
                "method": "GET",
                "status": status_code,
                "ip": ip,
                "latency_ms": latency_ms,
                "user_agent": request.headers.get("user-agent", "")[:200],
                "referer": request.headers.get("referer", "")[:200],
                "created_at": datetime.now(timezone.utc),
            })
        except Exception:
            pass  # Never raise from fire-and-forget logging

    try:
        asyncio.create_task(_write())
    except RuntimeError:
        pass  # No running loop in tests — silently skip


def create_app() -> FastAPI:
    """Factory function to create FastAPI application"""
    from app.core.logging_config import setup_logging

    setup_logging()

    is_prod = settings.APP_ENV in ("production", "staging")
    app = FastAPI(
        title="Syrabit API",
        description="Educational AI Assistant for Assamese Students",
        version="3.0.0",
        lifespan=lifespan,
        docs_url=None if is_prod else "/docs",
        redoc_url=None if is_prod else "/redoc",
        openapi_url=None if is_prod else "/openapi.json",
    )

    # Unified Middleware - combines CSRF, security headers, and request ID
    # into a single middleware to reduce per-request overhead from 3 call_next chains to 1
    @app.middleware("http")
    async def unified_middleware(request: Request, call_next):
        """Combined middleware: CSRF origin check, security headers, and request ID tracking."""
        # CSRF Origin Validation on mutating requests
        if request.method in ("POST", "PUT", "DELETE"):
            origin = request.headers.get("origin")
            if not (
                request.url.path.startswith("/health")
                or request.url.path.startswith("/api/health")
            ):
                # Skip CSRF origin check in test/development and when no Origin
                # header is present (API clients and test runners).
                # Development mode allows any origin so the Replit preview
                # domain (*.sisko.replit.dev) can reach auth/chat/analytics.
                if (
                    origin
                    and settings.APP_ENV not in ("test", "development")
                    and not settings.is_origin_allowed(origin)
                ):
                    from fastapi.responses import JSONResponse

                    return JSONResponse(
                        status_code=403, content={"detail": "Origin not allowed"}
                    )

        # Request ID
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id

        start_time = time.time()
        response = await call_next(request)
        elapsed_ms = int((time.time() - start_time) * 1000)

        # Request ID header + logging
        response.headers["X-Request-ID"] = request_id
        response.headers["X-API-Version"] = app.version
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
        logger.info(
            "request_completed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "latency_ms": elapsed_ms,
                "request_id": request_id,
            },
        )

        # Fire-and-forget request log to MongoDB (powers /analytics/top-routes).
        # Only logs GET 2xx requests to content paths; skips health/admin/static.
        _maybe_log_request(request, response.status_code, elapsed_ms)

        return response

    # Initialize OpenTelemetry (no-op if packages not installed)
    from app.core.telemetry import init_telemetry

    init_telemetry(app)

    # Register Routes
    app.include_router(chat.router, prefix="/api/v1/chat", tags=["Chat"])
    app.include_router(
        conversations.router,
        prefix="/api/v1/conversations",
        tags=["Conversations"],
    )
    app.include_router(edu.router, prefix="/api/v1", tags=["Education"])
    app.include_router(auth.router, prefix="/api/v1/auth", tags=["Authentication"])
    app.include_router(
        subscription.router, prefix="/api/v1/subscription", tags=["Subscription"]
    )
    app.include_router(users.router, prefix="/api/v1/users", tags=["Users"])
    app.include_router(health.router, prefix="/health", tags=["Health"])
    app.include_router(health.router, prefix="/api/v1/health", tags=["Health"])
    app.include_router(
        feedback.router, prefix="/api/v1/chat/feedback", tags=["Feedback"]
    )
    app.include_router(razorpay.router, prefix="/api/webhooks", tags=["Webhooks"])
    app.include_router(admin.router, prefix="/api/v1/admin", tags=["Admin"])
    app.include_router(
        admin_dashboard.router, prefix="/api/v1/admin", tags=["Admin Dashboard"]
    )
    app.include_router(admin_users.router, prefix="/api/v1/admin", tags=["Admin Users"])
    app.include_router(
        admin_conversations.router,
        prefix="/api/v1/admin",
        tags=["Admin Conversations"],
    )
    app.include_router(
        admin_content.router, prefix="/api/v1/admin", tags=["Admin Content"]
    )
    app.include_router(
        admin_analytics.router, prefix="/api/v1/admin", tags=["Admin Analytics"]
    )
    app.include_router(
        admin_settings.router, prefix="/api/v1/admin", tags=["Admin Settings"]
    )
    app.include_router(
        admin_notifications.router,
        prefix="/api/v1/admin",
        tags=["Admin Notifications"],
    )
    app.include_router(admin_seo.router, prefix="/api/v1/admin", tags=["Admin SEO"])
    app.include_router(admin_ai.router, prefix="/api/v1/admin", tags=["Admin AI"])
    app.include_router(
        admin_revenue.router, prefix="/api/v1/admin", tags=["Admin Revenue"]
    )
    app.include_router(
        admin_alerts.router, prefix="/api/v1/admin", tags=["Admin Alerts"]
    )
    app.include_router(seo.router, prefix="/api/v1/seo", tags=["SEO"])
    app.include_router(seo.router, prefix="", tags=["SEO Root"])
    app.include_router(indexnow.router, prefix="/api/v1/indexnow", tags=["IndexNow"])
    app.include_router(
        public_content.router, prefix="/api/v1/content", tags=["Public Content"]
    )
    app.include_router(content.router, prefix="/api/v1/content", tags=["Content"])
    app.include_router(
        public_content.router, prefix="/api/content", tags=["Public Content Legacy"]
    )
    app.include_router(
        admin_knowledge.router, prefix="/api/v1/admin", tags=["Admin Knowledge"]
    )
    app.include_router(
        admin_translate.router,
        prefix="/api/v1/admin",
        tags=["Admin Translation"],
    )
    app.include_router(
        admin_dead_letters.router,
        prefix="/api/v1/admin",
        tags=["Admin Dead Letters"],
    )
    app.include_router(changelog.router, prefix="/api/v1", tags=["Changelog"])
    app.include_router(payments.router, prefix="/api/v1/payments", tags=["Payments"])
    app.include_router(
        admin_security.router, prefix="/api/v1/admin", tags=["Admin Security"]
    )
    app.include_router(users.router, prefix="/api/v1/user", tags=["Users"])
    app.include_router(
        analytics.router, prefix="/api/v1/analytics", tags=["Analytics"]
    )
    app.include_router(
        analytics.router, prefix="/api/analytics", tags=["Analytics Legacy"]
    )
    app.include_router(config.router, prefix="/api/v1/config", tags=["Config"])
    app.include_router(config.router, prefix="/api/config", tags=["Config Legacy"])
    app.include_router(admin_corpus.router, prefix="/api/v1", tags=["Admin Corpus"])
    app.include_router(
        admin_db_health.router, prefix="/api/v1/admin", tags=["Admin DB Health"]
    )
    app.include_router(
        admin_pipeline_health.router, prefix="/api/v1/admin", tags=["Admin Pipeline Health"]
    )
    app.include_router(admin_rag.router, prefix="/api/v1/admin", tags=["Admin RAG"])
    app.include_router(admin_pyq.router, prefix="/api/v1/admin", tags=["Admin PYQ"])
    app.include_router(admin_cron.router, prefix="/api/v1/admin", tags=["Admin Cron"])
    app.include_router(admin_billing.router, prefix="/api/v1/admin", tags=["Admin Billing"])
    app.include_router(admin_ga4.router, prefix="/api/v1/admin", tags=["Admin GA4"])
    app.include_router(admin_ads.router, prefix="/api/v1/admin", tags=["Admin Ads"])
    app.include_router(admin_syra.router, prefix="/api/v1/admin", tags=["Admin Syra"])
    app.include_router(admin_logs.router, prefix="/api/v1/admin", tags=["Admin Logs"])
    app.include_router(admin_vertex.router, prefix="/api/v1/admin", tags=["Admin Vertex"])
    app.include_router(admin_indexnow_admin.router, prefix="/api/v1/admin", tags=["Admin IndexNow"])
    app.include_router(admin_seo_advanced.router, prefix="/api/v1/admin", tags=["Admin SEO Advanced"])
    app.include_router(admin_documents.router, prefix="/api/v1/admin", tags=["Admin Documents"])
    app.include_router(public_documents.router, prefix="/api/v1", tags=["Documents"])
    app.include_router(staff_content.router, prefix="/api/v1", tags=["Staff"])

    # CORS middleware — added last so it becomes the outermost layer and
    # handles OPTIONS preflight before any other middleware runs.
    # The edge worker already adds CORS headers for production browser traffic;
    # this middleware covers direct backend access (health monitors, admin tools,
    # internal tooling).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins_list,
        allow_origin_regex=r"^https://([a-z0-9-]+\.syrabitfrontend\.pages\.dev|[a-z0-9-]+\.replit\.dev|[a-z0-9-]+\.replit\.app|[a-z0-9-]+\.sisko\.replit\.dev)$",
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
        allow_headers=[
            "Content-Type",
            "Authorization",
            "x-anon-id",
            "traceparent",
            "X-Request-ID",
        ],
        expose_headers=[
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            "X-RateLimit-Reset",
            "X-Request-ID",
            "X-API-Version",
        ],
        max_age=86400,
    )

    # Legacy health probe redirects for backward compatibility.
    # The canonical health endpoint is /health (registered via health.router).
    # These /api/health redirects exist for older monitoring tools and load balancers.
    @app.get("/api/health")
    async def legacy_health_redirect():
        return RedirectResponse(url="/health", status_code=301)

    @app.get("/api/health/deep")
    async def legacy_health_deep_redirect():
        return RedirectResponse(url="/health/deep", status_code=301)

    @app.get("/api/v1/cms/posts")
    async def legacy_cms_posts_redirect(request: Request):
        qs = request.url.query
        url = "/api/v1/content/cms/posts" + (f"?{qs}" if qs else "")
        return RedirectResponse(url=url, status_code=307)

    @app.get("/api/v1/cms/posts/{path:path}")
    async def legacy_cms_posts_path_redirect(path: str, request: Request):
        qs = request.url.query
        url = f"/api/v1/content/cms/{path}" + (f"?{qs}" if qs else "")
        return RedirectResponse(url=url, status_code=307)

    return app


app = create_app()

