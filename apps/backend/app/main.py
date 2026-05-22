from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import sentry_sdk
from posthog import Posthog
import logging
import uuid
import contextlib

logger = logging.getLogger(__name__)

from app.config import settings
from app.db.mongo import init_mongo, close_mongo
from app.db.redis import init_redis, close_redis
from app.api.v1 import chat, auth, subscription, users, health, feedback
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
    
    # Initialize Sentry with FastAPI integration
    if settings.SENTRY_DSN:
        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            environment=settings.SENTRY_ENVIRONMENT,
            traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
            profiles_sample_rate=0.1,
            integrations=[
                sentry_sdk.integrations.fastapi.FastApiIntegration(transaction_style="endpoint"),
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
    await close_mongo()
    await close_redis()
    logger.info("Application shutdown complete")


def create_app() -> FastAPI:
    """Factory function to create FastAPI application"""
    
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
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Request ID Middleware for structured logging
    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        
        # Add request_id to logging context
        with contextlib.nullcontext():
            response = await call_next(request)
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
    app.include_router(feedback.router, prefix="/api/v1/chat/feedback", tags=["Feedback"])
    app.include_router(razorpay.router, prefix="/api/webhooks", tags=["Webhooks"])

    return app


app = create_app()
