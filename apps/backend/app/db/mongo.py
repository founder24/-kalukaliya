from typing import Optional
from pymongo import AsyncMongoClient
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import ConnectionFailure
from beanie import init_beanie
from app.config import settings
from app.models.user import User
from app.models.chat import Chat
from app.models.feedback import ChatFeedback
from app.models.knowledge import KnowledgeObject
from app.models.cms import CmsDocument
from app.models.quota import QuotaUsage
from app.models.content import (
    Board,
    Class,
    Stream,
    Subject,
    Chapter,
    TopicEmbedding,
    QuestionPaper,
    ContentAuditLog,
)
from app.models.rag import (
    RagDocument,
    Chunk,
    ContentNode,
    PageAsset,
    GenerationJob,
    PublishJob,
)
from app.models.ai_usage_log import AiUsageLog
from app.models.document import LibraryDocument
from app.db.migrations.runner import check_and_apply_migrations
import logging

logger = logging.getLogger(__name__)

_client: Optional[AsyncMongoClient] = None


async def init_mongo() -> None:
    """Initialize MongoDB connection pool with Beanie ODM"""
    global _client

    if not settings.MONGODB_URI:
        if settings.APP_ENV in ("production", "staging"):
            msg = "CRITICAL: MONGODB_URI is not configured in this environment. Check GCP Secret Manager secret 'MONGODB_URI'."
            logger.critical(msg)
            settings.startup_errors.append(msg)
        else:
            logger.warning("MONGODB_URI not set — MongoDB disabled")
        return

    import asyncio

    max_retries = 3
    for attempt in range(max_retries):
        try:
            _client = AsyncMongoClient(
                settings.MONGODB_URI,
                maxPoolSize=settings.MONGODB_MAX_POOL_SIZE,
                minPoolSize=settings.MONGODB_MIN_POOL_SIZE,
                serverSelectionTimeoutMS=30000,
                connectTimeoutMS=30000,
                socketTimeoutMS=45000,
                heartbeatFrequencyMS=10000,
                maxIdleTimeMS=25000,
                retryWrites=True,
                retryReads=True,
                waitQueueTimeoutMS=5000,
                appName="syrabit-backend",
            )

            # Initialize Beanie with document models
            await init_beanie(
                database=_client[settings.MONGODB_DB_NAME],
                document_models=[
                    User,
                    Chat,
                    ChatFeedback,
                    KnowledgeObject,
                    CmsDocument,
                    QuotaUsage,
                    Board,
                    Class,
                    Stream,
                    Subject,
                    Chapter,
                    TopicEmbedding,
                    QuestionPaper,
                    RagDocument,
                    Chunk,
                    ContentNode,
                    PageAsset,
                    GenerationJob,
                    PublishJob,
                    AiUsageLog,
                    ContentAuditLog,
                    LibraryDocument,
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
            _client = None
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
        except Exception as e:
            _client = None
            logger.error(f"Unexpected error during MongoDB initialization: {e}")
            raise


async def _ensure_ttl_index(collection, key_spec: list, expire_after_seconds: int) -> None:
    """Create a TTL index, auto-healing any conflicting non-TTL index.

    MongoDB error code 85 (IndexOptionsConflict) is raised when an index with
    the same key spec already exists WITHOUT expireAfterSeconds. This commonly
    happens when an Atlas cluster was bootstrapped before TTL was added to the
    code. We drop the conflicting index and recreate it so the TTL is actually
    applied rather than silently skipped.
    """
    try:
        await collection.create_index(key_spec, expireAfterSeconds=expire_after_seconds)
    except Exception as e:
        if getattr(e, "code", None) == 85 or "IndexOptionsConflict" in str(e):
            index_name = "_".join(f"{field}_{direction}" for field, direction in key_spec)
            coll_name = collection.name
            logger.warning(
                f"Dropping conflicting non-TTL index '{index_name}' on "
                f"'{coll_name}' to apply {expire_after_seconds}s TTL retention"
            )
            await collection.drop_index(index_name)
            await collection.create_index(
                key_spec, expireAfterSeconds=expire_after_seconds
            )
            logger.info(
                f"TTL index '{index_name}' recreated on '{coll_name}' "
                f"({expire_after_seconds // 86400}d retention)"
            )
        else:
            raise


async def create_indexes() -> None:
    """Create necessary database indexes.

    Each index group is wrapped in its own try/except so a conflict on one
    collection never prevents indexes on subsequent collections from being
    created. TTL indexes use _ensure_ttl_index() which auto-heals conflicts.
    """
    db = _client[settings.MONGODB_DB_NAME] if _client else None

    if db is None:
        return

    # ── Users ────────────────────────────────────────────────────────────────
    # sparse=True: users without an email (anonymous) are excluded from the
    # unique index, allowing multiple email=None docs to coexist.
    try:
        await db.users.create_index(
            [("email", ASCENDING)], unique=True, sparse=True
        )
    except Exception as e:
        err_str = str(e)
        if "IndexKeySpecsConflict" in err_str or "code: 86" in err_str or getattr(e, "code", None) == 86:
            logger.info("Email unique+sparse index already exists on Atlas with compatible spec — skipping")
        elif settings.APP_ENV in ("production", "staging"):
            logger.error(f"FATAL: Failed to create email unique index: {e}")
            raise
        else:
            logger.warning(f"Email unique index creation failed (non-prod): {e}")

    try:
        await db.users.create_index([("razorpay_subscription_id", ASCENDING)])
        await db.users.create_index([("preferred_language", ASCENDING)])
        await db.users.create_index([("created_at", DESCENDING)])
    except Exception as e:
        logger.warning(f"Users secondary index creation failed (non-fatal): {e}")

    # ── Chats ─────────────────────────────────────────────────────────────────
    try:
        await db.chats.create_index([("user_id", ASCENDING), ("updated_at", DESCENDING)])
        await db.chats.create_index([("session_id", ASCENDING)])
        await db.chats.create_index([("updated_at", DESCENDING)])
    except Exception as e:
        logger.warning(f"Chats query index creation failed (non-fatal): {e}")

    await _ensure_ttl_index(
        db.chats, [("created_at", ASCENDING)], 90 * 24 * 60 * 60
    )

    # ── Dead letters ──────────────────────────────────────────────────────────
    await _ensure_ttl_index(
        db.dead_letters, [("timestamp", DESCENDING)], 30 * 24 * 60 * 60
    )
    try:
        await db.dead_letters.create_index(
            [("user_id", ASCENDING), ("timestamp", DESCENDING)]
        )
        await db.dead_letters.create_index(
            [("status", ASCENDING), ("timestamp", DESCENDING)]
        )
    except Exception as e:
        logger.warning(f"Dead-letters query index creation failed (non-fatal): {e}")

    # ── Chat feedback ─────────────────────────────────────────────────────────
    await _ensure_ttl_index(
        db.chat_feedback, [("timestamp", ASCENDING)], 30 * 24 * 60 * 60
    )

    # ── Audit logs ────────────────────────────────────────────────────────────
    await _ensure_ttl_index(
        db.audit_logs, [("timestamp", ASCENDING)], 180 * 24 * 60 * 60
    )

    # ── Content hierarchy ─────────────────────────────────────────────────────
    try:
        await db.boards.create_index([("slug", ASCENDING)], unique=True)
        await db.classes.create_index([("board_id", ASCENDING)])
        await db.streams.create_index([("class_id", ASCENDING)])
        await db.subjects.create_index([("stream_id", ASCENDING)])
        await db.chapters.create_index([("subject_id", ASCENDING), ("status", ASCENDING)])
    except Exception as e:
        logger.warning(f"Content hierarchy index creation failed (non-fatal): {e}")

    # ── Question papers ───────────────────────────────────────────────────────
    try:
        await db.question_papers.create_index(
            [
                ("board", ASCENDING),
                ("class_level", ASCENDING),
                ("subject", ASCENDING),
                ("year", ASCENDING),
            ]
        )
        await db.question_papers.create_index([("status", ASCENDING)])
    except Exception as e:
        logger.warning(f"Question-papers index creation failed (non-fatal): {e}")

    # ── Topic embeddings ──────────────────────────────────────────────────────
    try:
        await db.topic_embeddings.create_index([("topic_id", ASCENDING)])
    except Exception as e:
        logger.warning(f"Topic-embeddings index creation failed (non-fatal): {e}")

    # ── RAG chunks (v1 — Atlas Vector Search, kept for backward compat) ──────
    try:
        await db.rag_chunks.create_index([("chapter_id", ASCENDING)])
        await db.rag_chunks.create_index([("subject_id", ASCENDING), ("language", ASCENDING)])
        await db.rag_chunks.create_index([("source_type", ASCENDING), ("language", ASCENDING)])
        await db.rag_chunks.create_index([("board", ASCENDING), ("class_level", ASCENDING)])
        await db.rag_chunks.create_index([("updated_at", DESCENDING)])
    except Exception as e:
        logger.warning(f"RAG chunks (v1) index creation failed (non-fatal): {e}")

    # ── RAG documents (v2 ingestion) ──────────────────────────────────────────
    try:
        await db.rag_documents.create_index([("subject_id", ASCENDING), ("medium", ASCENDING)])
        await db.rag_documents.create_index([("status", ASCENDING)])
        await db.rag_documents.create_index([("source_type", ASCENDING)])
        await db.rag_documents.create_index([("updated_at", DESCENDING)])
    except Exception as e:
        logger.warning(f"rag_documents index creation failed (non-fatal): {e}")

    # ── Chunks (v2 — Vectorize-linked, text + metadata only, no embedding) ───
    try:
        await db.chunks.create_index([("document_id", ASCENDING)])
        await db.chunks.create_index([("subject_id", ASCENDING), ("medium", ASCENDING)])
        await db.chunks.create_index([("chapter_id", ASCENDING), ("medium", ASCENDING)])
        await db.chunks.create_index([("topic_id", ASCENDING)])
        await db.chunks.create_index([("source_type", ASCENDING), ("medium", ASCENDING)])
        await db.chunks.create_index([("vector_id", ASCENDING)], sparse=True)
        await db.chunks.create_index([("updated_at", DESCENDING)])
    except Exception as e:
        logger.warning(f"chunks index creation failed (non-fatal): {e}")

    # ── Content nodes ─────────────────────────────────────────────────────────
    try:
        await db.content_nodes.create_index(
            [("subject_id", ASCENDING), ("chapter_id", ASCENDING), ("medium", ASCENDING)]
        )
        await db.content_nodes.create_index([("topic_id", ASCENDING), ("medium", ASCENDING)])
        await db.content_nodes.create_index([("status", ASCENDING)])
        await db.content_nodes.create_index([("node_type", ASCENDING), ("status", ASCENDING)])
        await db.content_nodes.create_index([("updated_at", DESCENDING)])
    except Exception as e:
        logger.warning(f"content_nodes index creation failed (non-fatal): {e}")

    # ── Page assets ───────────────────────────────────────────────────────────
    try:
        await db.page_assets.create_index([("cloudflare_path", ASCENDING)], unique=True)
        await db.page_assets.create_index(
            [("subject_id", ASCENDING), ("chapter_id", ASCENDING), ("medium", ASCENDING)]
        )
        await db.page_assets.create_index([("topic_id", ASCENDING), ("medium", ASCENDING)])
        await db.page_assets.create_index([("invalidated", ASCENDING)])
    except Exception as e:
        logger.warning(f"page_assets index creation failed (non-fatal): {e}")

    # ── Generation jobs ───────────────────────────────────────────────────────
    try:
        await db.generation_jobs.create_index([("status", ASCENDING), ("created_at", DESCENDING)])
        await db.generation_jobs.create_index([("document_id", ASCENDING)], sparse=True)
        await db.generation_jobs.create_index([("subject_id", ASCENDING), ("medium", ASCENDING)])
        await db.generation_jobs.create_index([("job_type", ASCENDING), ("status", ASCENDING)])
        await db.generation_jobs.create_index([("created_at", DESCENDING)])
    except Exception as e:
        logger.warning(f"generation_jobs index creation failed (non-fatal): {e}")

    # ── AI usage logs ──────────────────────────────────────────────────────────
    try:
        await db.ai_usage_logs.create_index([("created_at", DESCENDING)])
        await db.ai_usage_logs.create_index([("provider", ASCENDING), ("created_at", DESCENDING)])
        await db.ai_usage_logs.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
        # TTL: auto-delete records older than 90 days
        await db.ai_usage_logs.create_index(
            [("created_at", ASCENDING)],
            expireAfterSeconds=90 * 24 * 3600,
            name="ai_usage_logs_ttl",
        )
    except Exception as e:
        logger.warning(f"ai_usage_logs index creation failed (non-fatal): {e}")

    # ── Auth rate limit (IP-based, 90s TTL buckets) ───────────────────────────
    # _id is the rate key (endpoint:ip:minute_bucket), expires_at drives TTL.
    # Short TTL (90s) covers the current minute + partial next minute so no
    # bucket survives longer than needed.
    await _ensure_ttl_index(
        db.auth_rate_limit, [("expires_at", ASCENDING)], 0
    )

    logger.info("MongoDB indexes created/verified")


def get_mongo_client() -> AsyncMongoClient:
    """Get MongoDB client instance"""
    if _client is None:
        raise RuntimeError("MongoDB not initialized. Call init_mongo() first.")
    return _client


async def close_mongo() -> None:
    """Close MongoDB connection"""
    global _client
    if _client:
        # AsyncMongoClient.close() is a coroutine in pymongo 4.x — must await.
        await _client.close()
        _client = None
        logger.info("MongoDB connection closed")
