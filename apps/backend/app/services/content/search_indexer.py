"""
SearchIndexer - Chunks content and upserts to Azure AI Search.
"""

import logging

from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import AzureError

from app.config import settings

logger = logging.getLogger(__name__)

# Approximate tokens per chunk
CHUNK_SIZE_TOKENS = 500
# Rough chars per token estimate
CHARS_PER_TOKEN = 4


class SearchIndexer:
    """Chunks body_markdown and upserts documents to Azure AI Search."""

    def __init__(self):
        self.client = None
        if settings.AZURE_SEARCH_ENDPOINT and settings.AZURE_SEARCH_ADMIN_KEY:
            try:
                from azure.search.documents.aio import SearchClient

                self.client = SearchClient(
                    endpoint=settings.AZURE_SEARCH_ENDPOINT,
                    index_name=settings.AZURE_SEARCH_INDEX_NAME,
                    credential=AzureKeyCredential(settings.AZURE_SEARCH_ADMIN_KEY),
                )
            except Exception as e:
                logger.warning(f"Failed to initialize search indexer: {e}")
        else:
            logger.info("Azure Search admin key not configured - indexing disabled")

    async def _delete_stale_chunks(self, knowledge_obj) -> None:
        """Delete existing chunks for a knowledge object before re-indexing."""
        try:
            results = self.client.search(
                search_text="*",
                filter=f"slug eq '{knowledge_obj.slug}'",
                select=["id"],
                top=1000,
            )
            stale_ids = []
            async for doc in results:
                stale_ids.append(doc["id"])

            if stale_ids:
                await self.client.delete_documents(
                    documents=[{"id": doc_id} for doc_id in stale_ids]
                )
                logger.info(
                    f"Deleted {len(stale_ids)} stale chunks for slug={knowledge_obj.slug}"
                )
        except Exception as e:
            logger.warning(
                f"Failed to delete stale chunks for slug={knowledge_obj.slug}: {e}"
            )

    def chunk_text(self, text: str, chunk_size: int = CHUNK_SIZE_TOKENS) -> list[str]:
        """
        Split text into approximately chunk_size token segments.
        Splits on paragraph boundaries where possible.
        """
        if not text:
            return []

        max_chars = chunk_size * CHARS_PER_TOKEN
        paragraphs = text.split("\n\n")
        chunks: list[str] = []
        current_chunk = ""

        for paragraph in paragraphs:
            paragraph = paragraph.strip()
            if not paragraph:
                continue

            if len(current_chunk) + len(paragraph) + 2 <= max_chars:
                if current_chunk:
                    current_chunk += "\n\n" + paragraph
                else:
                    current_chunk = paragraph
            else:
                if current_chunk:
                    chunks.append(current_chunk)
                # If single paragraph exceeds chunk size, split it
                if len(paragraph) > max_chars:
                    words = paragraph.split()
                    current_chunk = ""
                    for word in words:
                        if len(current_chunk) + len(word) + 1 <= max_chars:
                            current_chunk = (
                                f"{current_chunk} {word}" if current_chunk else word
                            )
                        else:
                            if current_chunk:
                                chunks.append(current_chunk)
                            current_chunk = word
                else:
                    current_chunk = paragraph

        if current_chunk:
            chunks.append(current_chunk)

        return chunks

    async def index_knowledge_object(self, knowledge_obj) -> bool:
        """
        Chunk and upsert a knowledge object to Azure AI Search.

        Args:
            knowledge_obj: KnowledgeObject instance

        Returns:
            True if indexing succeeded, False otherwise
        """
        if not self.client:
            logger.warning("Search indexer not configured - skipping indexing")
            return False

        try:
            # Delete stale chunks before re-indexing
            await self._delete_stale_chunks(knowledge_obj)

            chunks = self.chunk_text(knowledge_obj.body_markdown)
            if not chunks:
                logger.warning(f"No content to index for slug={knowledge_obj.slug}")
                return False

            meta = knowledge_obj.metadata
            documents = []

            for i, chunk in enumerate(chunks):
                doc_id = f"{knowledge_obj.slug}_chunk_{i}"
                documents.append(
                    {
                        "id": doc_id,
                        "title": knowledge_obj.title,
                        "content": chunk,
                        "source_url": f"/render/{meta.board}/{meta.class_level}/{meta.subject}/{meta.chapter}/notes",
                        "board": meta.board,
                        "class_level": meta.class_level,
                        "subject": meta.subject,
                        "chapter": meta.chapter,
                        "difficulty": meta.difficulty,
                        "language": meta.language,
                        "slug": knowledge_obj.slug,
                        "chunk_index": i,
                        "tier_access": "free",
                    }
                )

            # Upsert in batches of 100 with per-batch error handling
            batch_size = 100
            batches_succeeded = 0
            batches_failed = 0
            for start in range(0, len(documents), batch_size):
                batch = documents[start : start + batch_size]
                try:
                    await self.client.upload_documents(documents=batch)
                    batches_succeeded += 1
                except Exception as e:
                    batches_failed += 1
                    logger.error(
                        f"Batch {start // batch_size + 1} failed for slug={knowledge_obj.slug}: {e}"
                    )

            if batches_failed > 0:
                logger.warning(
                    f"{batches_failed} batch(es) failed for slug={knowledge_obj.slug}"
                )

            if batches_succeeded > 0:
                logger.info(
                    f"Indexed {len(documents)} chunks for slug={knowledge_obj.slug}"
                )
            return batches_succeeded > 0

        except AzureError as e:
            logger.error(
                f"Azure Search indexing failed for slug={knowledge_obj.slug}: {e}"
            )
            return False
        except Exception as e:
            logger.error(f"Unexpected error indexing slug={knowledge_obj.slug}: {e}")
            return False


# Singleton
search_indexer = SearchIndexer()
