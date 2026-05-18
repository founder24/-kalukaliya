from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import sentry_sdk
from posthog import Posthog

from app.config import settings
from app.db.mongo import init_mongo, close_mongo
from app.db.redis import init_redis, close_redis
from app.api.v1 import chat, auth, subscription, users
from app.api.webhooks import razorpay


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application Lifespan Events - Startup and Shutdown"""
    # Startup
    await init_mongo()
    await init_redis()
    
    # Initialize Sentry
    if settings.SENTRY_DSN:
        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            environment=settings.SENTRY_ENVIRONMENT,
            traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
            integrations=[],
        )
    
    yield
    
    # Shutdown
    await close_mongo()
    await close_redis()


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

    # Register Routes
    app.include_router(chat.router, prefix="/api/v1/chat", tags=["Chat"])
    app.include_router(auth.router, prefix="/api/v1/auth", tags=["Authentication"])
    app.include_router(subscription.router, prefix="/api/v1/subscription", tags=["Subscription"])
    app.include_router(users.router, prefix="/api/v1/users", tags=["Users"])
    app.include_router(razorpay.router, prefix="/api/webhooks", tags=["Webhooks"])

    @app.get("/health")
    async def health_check():
        return {"status": "healthy", "version": "3.0.0"}

    return app


app = create_app()
