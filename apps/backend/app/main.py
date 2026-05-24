from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from posthog import Posthog
import logging
import uuid
import time
import contextlib

logger = logging.getLogger(__name__)

from app.config import settings
from app.db.mongo import init_mongo, close_mongo
from app.db.redis import init_redis, close_redis
from app.api.v1 import chat, auth, subscription, users, health, feedback, admin
from app.api.webhooks import razorpay


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application Lifespan Events - Startup and Shutdown"""
    # Startup
    try:
        await init_mongo()
        logger.info("MongoDB initialized successfully")
    except Exception as e:
        logger.warning(f"MongoDB initialization failed (expected in local dev without DB): {e}")
    
    try:
        await init_redis()
        logger.info("Redis initialized successfully")
    except Exception as e:
        logger.warning(f"Redis initialization failed (expected in local dev without DB): {e}")
    
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
    if settings.POSTHOG_API_KEY:
        posthog = Posthog(
            project_api_key=settings.POSTHOG_API_KEY,
            host=settings.POSTHOG_HOST
        )
        logger.info("PostHog initialized")
    
    yield
    
    # Shutdown
    from app.services.ai.vertex_client import vertex_client
    from app.services.ai.sarvam_client import sarvam_client
    await vertex_client.close()
    await sarvam_client.close()
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

    # CORS Middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-Razorpay-Signature", "Accept", "Origin"],
    )

    # CSRF Origin Validation Middleware
    @app.middleware("http")
    async def csrf_origin_check(request: Request, call_next):
        """Validate Origin header on mutating requests to prevent CSRF."""
        if request.method in ("POST", "PUT", "DELETE"):
            origin = request.headers.get("origin")
            # Skip for health checks
            if request.url.path.startswith("/health") or request.url.path.startswith("/api/health"):
                return await call_next(request)
            if origin and origin not in settings.allowed_origins_list:
                from fastapi.responses import JSONResponse
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Origin not allowed"}
                )
        return await call_next(request)

    # Security Headers Middleware
    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        """Add security response headers to all responses."""
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["X-XSS-Protection"] = "0"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

    # Request ID Middleware for structured logging
    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id

        start_time = time.time()
        response = await call_next(request)
        elapsed_ms = int((time.time() - start_time) * 1000)

        logger.info("request_completed", extra={
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "latency_ms": elapsed_ms,
            "request_id": request_id,
        })

        response.headers["X-Request-ID"] = request_id
        return response

    # Initialize OpenTelemetry (no-op if packages not installed)
    from app.core.telemetry import init_telemetry
    init_telemetry(app)

    # Register Routes
    app.include_router(chat.router, prefix="/api/v1/chat", tags=["Chat"])
    app.include_router(auth.router, prefix="/api/v1/auth", tags=["Authentication"])
    app.include_router(subscription.router, prefix="/api/v1/subscription", tags=["Subscription"])
    app.include_router(users.router, prefix="/api/v1/users", tags=["Users"])
    app.include_router(health.router, prefix="/health", tags=["Health"])
    app.include_router(health.router, prefix="/api/health", tags=["Health"])  # Legacy probe path
    app.include_router(feedback.router, prefix="/api/v1/chat/feedback", tags=["Feedback"])
    app.include_router(razorpay.router, prefix="/api/webhooks", tags=["Webhooks"])
    app.include_router(admin.router, prefix="/api/v1/admin", tags=["Admin"])

    return app


app = create_app()
