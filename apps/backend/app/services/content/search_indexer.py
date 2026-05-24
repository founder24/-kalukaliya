"""
SearchIndexer - Chunks content and upserts to Azure AI Search.
"""

import logging
from typing import Optional

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
            chunks = self.chunk_text(knowledge_obj.body_markdown)
            if not chunks:
                logger.warning(
                    f"No content to index for slug={knowledge_obj.slug}"
                )
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

            # Upsert in batches of 100
            batch_size = 100
            for start in range(0, len(documents), batch_size):
                batch = documents[start : start + batch_size]
                await self.client.upload_documents(documents=batch)

            logger.info(
                f"Indexed {len(documents)} chunks for slug={knowledge_obj.slug}"
            )
            return True

        except AzureError as e:
            logger.error(
                f"Azure Search indexing failed for slug={knowledge_obj.slug}: {e}"
            )
            return False
        except Exception as e:
            logger.error(
                f"Unexpected error indexing slug={knowledge_obj.slug}: {e}"
            )
            return False


# Singleton
search_indexer = SearchIndexer()
