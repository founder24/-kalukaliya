"""
SearchIndexer - Chunks content and upserts to Vertex AI Search (Discovery Engine).
"""

import logging
import re

from app.config import settings

# Slug must be lowercase alphanumeric with hyphens only
_SAFE_SLUG_RE = re.compile(r"^[a-z0-9-]+$")

logger = logging.getLogger(__name__)

# Approximate tokens per chunk
CHUNK_SIZE_TOKENS = 500
# Rough chars per token estimate
CHARS_PER_TOKEN = 4


class SearchIndexer:
    """Chunks body_markdown and upserts documents to Vertex AI Search (Discovery Engine)."""

    def __init__(self):
        self._client = None
        self._parent: str | None = None

        if (
            settings.VERTEX_PROJECT_ID
            and settings.GOOGLE_APPLICATION_CREDENTIALS_JSON
            and settings.VERTEX_SEARCH_DATASTORE_ID
        ):
            try:
                self._init_client()
            except Exception as e:
                logger.warning(f"Failed to initialize search indexer: {e}")
        else:
            logger.info(
                "Vertex AI Search credentials not configured - indexing disabled"
            )

    def _init_client(self):
        """Initialize the Discovery Engine DocumentServiceClient."""
        from google.cloud import discoveryengine_v1
        from google.oauth2 import service_account

        credentials = service_account.Credentials.from_service_account_info(
            settings.google_credentials,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )

        self._client = discoveryengine_v1.DocumentServiceClient(
            credentials=credentials,
        )

        # Build the parent branch resource name for document operations
        self._parent = self._client.branch_path(
            project=settings.VERTEX_PROJECT_ID,
            location=settings.VERTEX_SEARCH_LOCATION,
            data_store=settings.VERTEX_SEARCH_DATASTORE_ID,
            branch="default_branch",
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
        Chunk and upsert a knowledge object to Vertex AI Search.

        Args:
            knowledge_obj: KnowledgeObject instance

        Returns:
            True if indexing succeeded, False otherwise
        """
        if not self._client:
            logger.warning("Search indexer not configured - skipping indexing")
            return False

        try:
            import asyncio

            from google.cloud import discoveryengine_v1
            from google.protobuf import struct_pb2

            chunks = self.chunk_text(knowledge_obj.body_markdown)
            if not chunks:
                logger.warning(f"No content to index for slug={knowledge_obj.slug}")
                return False

            meta = knowledge_obj.metadata
            documents = []

            for i, chunk in enumerate(chunks):
                doc_id = f"{knowledge_obj.slug}_chunk_{i}"

                # Build struct data for the document
                struct_data = struct_pb2.Struct()
                struct_data.update(
                    {
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

                doc = discoveryengine_v1.Document(
                    id=doc_id,
                    struct_data=struct_data,
                )
                documents.append(doc)

            # Upsert documents concurrently with bounded semaphore
            semaphore = asyncio.Semaphore(10)
            succeeded = 0
            failed = 0

            async def _upsert_document(doc):
                """Upsert a single document (create, fallback to update)."""
                nonlocal succeeded, failed
                async with semaphore:
                    try:
                        request = discoveryengine_v1.CreateDocumentRequest(
                            parent=self._parent,
                            document=doc,
                            document_id=doc.id,
                        )
                        await asyncio.to_thread(
                            self._client.create_document, request=request
                        )
                        succeeded += 1
                    except Exception:
                        try:
                            doc.name = f"{self._parent}/documents/{doc.id}"
                            request = discoveryengine_v1.UpdateDocumentRequest(
                                document=doc,
                                allow_missing=True,
                            )
                            await asyncio.to_thread(
                                self._client.update_document, request=request
                            )
                            succeeded += 1
                        except Exception as update_err:
                            failed += 1
                            logger.error(
                                f"Failed to upsert doc {doc.id} for "
                                f"slug={knowledge_obj.slug}: {update_err}"
                            )

            await asyncio.gather(*[_upsert_document(doc) for doc in documents])

            if failed > 0:
                logger.warning(
                    f"{failed} document(s) failed for slug={knowledge_obj.slug}"
                )

            if succeeded > 0:
                logger.info(
                    f"Indexed {succeeded}/{len(documents)} chunks for slug={knowledge_obj.slug}"
                )
            return succeeded > 0

        except Exception as e:
            logger.error(
                f"Vertex Search indexing failed for slug={knowledge_obj.slug}: {e}"
            )
            return False


# Singleton
search_indexer = SearchIndexer()
