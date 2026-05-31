from typing import Optional
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import ConnectionFailure
from beanie import init_beanie
from app.config import settings
from app.models.user import User
from app.models.chat import Chat
from app.models.feedback import ChatFeedback
from app.models.knowledge import KnowledgeObject
from app.models.content import Board, Class, Stream, Subject, Chapter, QuestionPaper
from app.db.migrations.runner import check_and_apply_migrations
import logging

logger = logging.getLogger(__name__)

_client: Optional[AsyncIOMotorClient] = None


async def init_mongo() -> None:
    """Initialize MongoDB connection pool with Beanie ODM"""
    global _client

    if not settings.MONGODB_URI:
        logger.warning("MONGODB_URI not set — MongoDB disabled")
        return

    import asyncio

    max_retries = 3
    for attempt in range(max_retries):
        try:
            _client = AsyncIOMotorClient(
                settings.MONGODB_URI,
                maxPoolSize=settings.MONGODB_MAX_POOL_SIZE,
                minPoolSize=settings.MONGODB_MIN_POOL_SIZE,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=10000,
                socketTimeoutMS=45000,
                heartbeatFrequencyMS=10000,
            )

            # Initialize Beanie with document models
            await init_beanie(
                database=_client[settings.MONGODB_DB_NAME],
                document_models=[
                    User,
                    Chat,
                    ChatFeedback,
                    KnowledgeObject,
                    Board,
                    Class,
                    Stream,
                    Subject,
                    Chapter,
                    QuestionPaper,
                ],
            )

            # Create indexes — failures here are non-fatal (indexes may already
            # exist with slightly different specs on an existing cluster).
            try:
                await create_indexes()
            except Exception as idx_err:
                logger.warning(
                    f"Index creation partially failed (non-fatal): {idx_err}"
                )

            # Run pending database migrations
            db = _client[settings.MONGODB_DB_NAME]
            try:
                await check_and_apply_migrations(db)
            except Exception as mig_err:
                logger.warning(f"Migration check failed (non-fatal): {mig_err}")

            logger.info("MongoDB connection initialized successfully")
            return
        except ConnectionFailure as e:
            if attempt < max_retries - 1:
                wait_time = 2**attempt
                logger.warning(
                    f"MongoDB connection attempt {attempt + 1} failed, retrying in {wait_time}s: {e}"
                )
                await asyncio.sleep(wait_time)
            else:
                logger.error(
                    f"Failed to connect to MongoDB after {max_retries} attempts: {e}"
                )
                raise


async def create_indexes() -> None:
    """Create necessary database indexes"""
    db = _client[settings.MONGODB_DB_NAME] if _client else None

    if db is None:
        return

    # Users collection indexes
    try:
        await db.users.create_index([("email", ASCENDING)], unique=True)
    except Exception as e:
        if settings.APP_ENV in ("production", "staging"):
            logger.error(f"FATAL: Failed to create email unique index: {e}")
            raise
        logger.warning(f"Email unique index creation failed (non-prod): {e}")
    await db.users.create_index([("razorpay_subscription_id", ASCENDING)], sparse=True)
    await db.users.create_index([("profile.preferences.language", ASCENDING)])
    await db.users.create_index([("created_at", DESCENDING)])

    # Chats collection indexes
    await db.chats.create_index([("user_id", ASCENDING), ("updated_at", DESCENDING)])
    await db.chats.create_index([("session_id", ASCENDING)])
    await db.chats.create_index([("updated_at", DESCENDING)])

    # Dead letters collection indexes
    await db.dead_letters.create_index(
        [("timestamp", DESCENDING)], expireAfterSeconds=30 * 24 * 60 * 60
    )  # 30 day TTL
    await db.dead_letters.create_index(
        [("user_id", ASCENDING), ("timestamp", DESCENDING)]
    )
    await db.dead_letters.create_index(
        [("status", ASCENDING), ("timestamp", DESCENDING)]
    )

    # Chat feedback TTL index (HF-038)
    await db.chat_feedback.create_index(
        [("timestamp", 1)], expireAfterSeconds=30 * 24 * 60 * 60
    )

    # Content hierarchy indexes
    await db.boards.create_index([("slug", ASCENDING)], unique=True)
    await db.classes.create_index([("board_id", ASCENDING)])
    await db.streams.create_index([("class_id", ASCENDING)])
    await db.subjects.create_index([("stream_id", ASCENDING)])
    await db.chapters.create_index([("subject_id", ASCENDING), ("status", ASCENDING)])

    # Question papers indexes
    await db.question_papers.create_index(
        [("board", ASCENDING), ("class_level", ASCENDING), ("subject", ASCENDING), ("year", ASCENDING)]
    )
    await db.question_papers.create_index([("status", ASCENDING)])

    logger.info("MongoDB indexes created/verified")


def get_mongo_client() -> AsyncIOMotorClient:
    """Get MongoDB client instance"""
    if _client is None:
        raise RuntimeError("MongoDB not initialized. Call init_mongo() first.")
    return _client


async def close_mongo() -> None:
    """Close MongoDB connection"""
    global _client
    if _client:
        _client.close()
        _client = None
        logger.info("MongoDB connection closed")
