from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import ConnectionFailure
from beanie import init_beanie
from app.config import settings
from app.models.user import User
from app.models.chat import Chat
from app.models.feedback import ChatFeedback
import logging

logger = logging.getLogger(__name__)

_client: AsyncIOMotorClient | None = None


async def init_mongo() -> None:
    """Initialize MongoDB connection pool with Beanie ODM"""
    global _client
    
    if not settings.MONGODB_URI:
        logger.warning("MONGODB_URI not set — MongoDB disabled")
        return
    
    try:
        _client = AsyncIOMotorClient(
            settings.MONGODB_URI,
            maxPoolSize=settings.MONGODB_MAX_POOL_SIZE,
            minPoolSize=settings.MONGODB_MIN_POOL_SIZE,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=10000,
            socketTimeoutMS=45000,
        )
        
        # Initialize Beanie with document models
        await init_beanie(
            database=_client[settings.MONGODB_DB_NAME],
            document_models=[User, Chat, ChatFeedback],
        )
        
        # Create indexes
        await create_indexes()
        
        logger.info("MongoDB connection initialized successfully")
    except ConnectionFailure as e:
        logger.error(f"Failed to connect to MongoDB: {e}")
        raise


async def create_indexes() -> None:
    """Create necessary database indexes"""
    db = _client[settings.MONGODB_DB_NAME] if _client else None
    
    if not db:
        return
    
    # Users collection indexes
    await db.users.create_index([("email", ASCENDING)], unique=True)
    await db.users.create_index([("subscription.razorpay_subscription_id", ASCENDING)], sparse=True)
    await db.users.create_index([("profile.preferences.language", ASCENDING)])
    await db.users.create_index([("created_at", DESCENDING)])
    
    # Chats collection indexes
    await db.chats.create_index([("user_id", ASCENDING), ("updated_at", DESCENDING)])
    await db.chats.create_index([("session_id", ASCENDING)])
    await db.chats.create_index([("updated_at", DESCENDING)])
    
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
