from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from contextlib import asynccontextmanager
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from posthog import Posthog
import logging
import uuid
import time

from app.config import settings
from app.db.mongo import init_mongo, close_mongo
from app.db.redis import init_redis, close_redis
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
    seo,
    indexnow,
    content,
    public_content,
    changelog,
    payments,
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
    except Exception as e:
        logger.warning(
            f"MongoDB initialization failed (expected in local dev without DB): {e}"
        )

    try:
        await init_redis()
        logger.info("Redis initialized successfully")
    except Exception as e:
        logger.warning(
            f"Redis initialization failed (expected in local dev without DB): {e}"
        )

    # Warm up Azure Search connection (DNS/TLS handshake)
    try:
        from app.services.search.azure_search import search_service

        await search_service.warm_up()
        logger.info("Azure Search warmed up successfully")
    except Exception as e:
        logger.warning(f"Azure Search warm-up failed: {e}")

    # Warm up Vertex AI OAuth token
    try:
        from app.services.ai.vertex_client import vertex_client

        await vertex_client._get_access_token()
        logger.info("Vertex AI OAuth token pre-fetched")
    except Exception as e:
        logger.warning(f"Vertex AI token warm-up failed: {e}")

    if settings.JWT_SECRET == "CHANGE_ME_IN_PRODUCTION_AT_LEAST_32_CHARS_LONG":
        logger.warning(
            "WARNING: Using default JWT_SECRET. "
            "This is acceptable for local dev but MUST be changed in production."
        )

    # Initialize Sentry with FastAPI integration
    if settings.SENTRY_DSN:
        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            environment=settings.SENTRY_ENVIRONMENT,
            traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
            profiles_sample_rate=0.1,
            integrations=[
                FastApiIntegration(transaction_style="endpoint"),
            ],
        )
        logger.info("Sentry initialized")

    # Initialize PostHog
    app.state.posthog = None
    if settings.POSTHOG_API_KEY:
        app.state.posthog = Posthog(
            project_api_key=settings.POSTHOG_API_KEY, host=settings.POSTHOG_HOST
        )
        logger.info("PostHog initialized")

    yield

    # Shutdown
    from app.services.ai.vertex_client import vertex_client
    from app.services.ai.sarvam_client import sarvam_client
    from app.services.payment.razorpay_client import razorpay_client
    from app.services.comms.resend_client import close_resend_client

    await vertex_client.close()
    await sarvam_client.close()
    await razorpay_client.close()
    await close_resend_client()
    await close_mongo()
    await close_redis()
    logger.info("Application shutdown complete")


def create_app() -> FastAPI:
    """Factory function to create FastAPI application"""
    from app.core.logging_config import setup_logging

    setup_logging()

    app = FastAPI(
        title="Syrabit API",
        description="Educational AI Assistant for Assamese Students",
        version="3.0.0",
        lifespan=lifespan,
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
                # Skip CSRF origin check in test environment and when no Origin
                # header is present (API clients and test runners)
                if (
                    origin
                    and settings.APP_ENV != "test"
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

        return response

    # Initialize OpenTelemetry (no-op if packages not installed)
    from app.core.telemetry import init_telemetry

    init_telemetry(app)

    # Register Routes
    app.include_router(chat.router, prefix="/api/v1/chat", tags=["Chat"])
    # Legacy alias: frontend historically used /api/v1/ai/chat/stream
    app.include_router(chat.router, prefix="/api/v1/ai/chat", tags=["Chat"])
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
    app.include_router(indexnow.router, prefix="/api/v1/indexnow", tags=["IndexNow"])
    app.include_router(content.router, prefix="/api/v1/content", tags=["Content"])
    app.include_router(
        public_content.router, prefix="/api/v1/content", tags=["Public Content"]
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
    app.include_router(
        payments.router, prefix="/api/v1/payments", tags=["Payments"]
    )
    app.include_router(
        admin_security.router, prefix="/api/v1/admin", tags=["Admin Security"]
    )
    app.include_router(users.router, prefix="/api/v1/user", tags=["Users"])

    # Legacy health probe redirects for backward compatibility.
    # The canonical health endpoint is /health (registered via health.router).
    # These /api/health redirects exist for older monitoring tools and load balancers.
    @app.get("/api/health")
    async def legacy_health_redirect():
        return RedirectResponse(url="/health", status_code=301)

    @app.get("/api/health/deep")
    async def legacy_health_deep_redirect():
        return RedirectResponse(url="/health/deep", status_code=301)

    return app


app = create_app()
