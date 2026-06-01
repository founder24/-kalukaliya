"""
Backfill script: Generate topic embeddings for all published chapters.

Usage:
    cd apps/backend
    python -m scripts.backfill_topic_embeddings

Connects to MongoDB, loads all published chapters with topics,
generates embeddings via Vertex AI text-embedding-005, and upserts
TopicEmbedding documents.
"""

import asyncio
import logging
import sys
from datetime import datetime, timezone

from pymongo import AsyncMongoClient
from beanie import init_beanie

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def main():
    from app.config import settings
    from app.models.content import (
        Board,
        Chapter,
        Class,
        Stream,
        Subject,
        TopicEmbedding,
    )
    from app.models.user import User
    from app.models.chat import Chat
    from app.models.feedback import ChatFeedback
    from app.models.knowledge import KnowledgeObject
    from app.services.ai.embedder import generate_embedding_vector

    if not settings.MONGODB_URI:
        logger.error("MONGODB_URI not set")
        sys.exit(1)

    # Connect to MongoDB
    client = AsyncMongoClient(settings.MONGODB_URI)
    await init_beanie(
        database=client[settings.MONGODB_DB_NAME],
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
            TopicEmbedding,
        ],
    )

    # Load all published chapters with topics
    chapters = await Chapter.find({"status": "published"}).to_list()
    logger.info(f"Found {len(chapters)} published chapters")

    total_topics = 0
    generated = 0
    errors = 0

    for chapter in chapters:
        if not chapter.published_topics:
            continue

        # Resolve hierarchy for metadata
        subject = await Subject.get(chapter.subject_id) if chapter.subject_id else None
        stream = (
            await Stream.get(subject.stream_id)
            if subject and subject.stream_id
            else None
        )
        cls = await Class.get(stream.class_id) if stream and stream.class_id else None
        board = await Board.get(cls.board_id) if cls and cls.board_id else None

        subject_slug = subject.name.lower().replace(" ", "-") if subject else ""
        board_slug = board.slug if board else ""
        class_level = cls.name if cls else ""

        for topic in chapter.published_topics:
            total_topics += 1
            try:
                embedding = await generate_embedding_vector(topic.title)

                # Upsert by topic_id
                existing = await TopicEmbedding.find_one(
                    TopicEmbedding.topic_id == topic.id
                )
                if existing:
                    existing.topic_title = topic.title
                    existing.chapter_id = chapter.id
                    existing.chapter_title = chapter.title
                    existing.subject_slug = subject_slug
                    existing.board_slug = board_slug
                    existing.class_level = class_level
                    existing.embedding = embedding
                    existing.updated_at = datetime.now(timezone.utc)
                    await existing.save()
                else:
                    doc = TopicEmbedding(
                        topic_id=topic.id,
                        topic_title=topic.title,
                        chapter_id=chapter.id,
                        chapter_title=chapter.title,
                        subject_slug=subject_slug,
                        board_slug=board_slug,
                        class_level=class_level,
                        embedding=embedding,
                    )
                    await doc.insert()
                generated += 1
                logger.info(f"  [{generated}/{total_topics}] {topic.title}")
            except Exception as e:
                errors += 1
                logger.error(f"  FAILED: {topic.title} - {e}")

            # Small delay to avoid rate limiting
            await asyncio.sleep(0.1)

    logger.info(
        f"Backfill complete: {generated} generated, {errors} errors, "
        f"{total_topics} total topics across {len(chapters)} chapters"
    )

    client.close()


if __name__ == "__main__":
    asyncio.run(main())
