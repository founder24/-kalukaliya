import hashlib
import logging

from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential
from app.config import settings
from app.models.knowledge import KnowledgeObject

logger = logging.getLogger(__name__)


class SearchIndexer:
    """Indexes KnowledgeObject content into Azure Search for RAG retrieval."""

    def __init__(self):
        self.client = None
        if settings.AZURE_SEARCH_ENDPOINT and settings.AZURE_SEARCH_ADMIN_KEY:
            self.client = SearchClient(
                endpoint=settings.AZURE_SEARCH_ENDPOINT,
                index_name=settings.AZURE_SEARCH_INDEX_NAME,
                credential=AzureKeyCredential(settings.AZURE_SEARCH_ADMIN_KEY),
            )
        else:
            logger.warning(
                "Azure Search admin credentials not configured - indexing disabled"
            )

    def chunk_content(self, text: str, max_tokens: int = 500) -> list[str]:
        """Split text into ~500 token chunks (approx 4 chars per token).

        Splits by paragraphs first, then combines into chunks of ~2000 chars.
        """
        max_chars = max_tokens * 4  # ~2000 chars per chunk
        paragraphs = text.split("\n\n")
        chunks = []
        current_chunk = ""

        for paragraph in paragraphs:
            paragraph = paragraph.strip()
            if not paragraph:
                continue

            if len(current_chunk) + len(paragraph) + 2 > max_chars:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                # If a single paragraph exceeds max_chars, split it
                if len(paragraph) > max_chars:
                    words = paragraph.split()
                    current_chunk = ""
                    for word in words:
                        if len(current_chunk) + len(word) + 1 > max_chars:
                            chunks.append(current_chunk.strip())
                            current_chunk = word
                        else:
                            current_chunk += " " + word if current_chunk else word
                else:
                    current_chunk = paragraph
            else:
                current_chunk += "\n\n" + paragraph if current_chunk else paragraph

        if current_chunk.strip():
            chunks.append(current_chunk.strip())

        return chunks

    def index(self, knowledge_object: KnowledgeObject) -> bool:
        """Create chunked documents and upsert to Azure Search.

        Each document has: id, title, content, content_vector (populated by
        built-in vectorization), source_url, tier_access, board, class_level,
        subject, chapter, difficulty, language.
        """
        if not self.client:
            logger.warning("Search indexer not initialized - skipping indexing")
            return False

        try:
            ko = knowledge_object
            chunks = self.chunk_content(ko.content.body_markdown)

            if not chunks:
                logger.warning(f"No content chunks generated for slug: {ko.slug}")
                return False

            source_url = (
                f"https://syrabit.ai/{ko.board}/{ko.class_level}"
                f"/{ko.subject}/{ko.chapter}"
            )

            documents = []
            for i, chunk in enumerate(chunks):
                doc_id = hashlib.md5(
                    f"{ko.slug}-chunk-{i}".encode()
                ).hexdigest()

                doc = {
                    "id": doc_id,
                    "title": f"{ko.topic} - Part {i + 1}",
                    "content": chunk,
                    "source_url": source_url,
                    "tier_access": "free",
                    "board": ko.board,
                    "class_level": ko.class_level,
                    "subject": ko.subject,
                    "chapter": ko.chapter,
                    "difficulty": str(ko.metadata.difficulty),
                    "language": ko.metadata.language,
                }
                documents.append(doc)

            result = self.client.upload_documents(documents=documents)
            succeeded = sum(1 for r in result if r.succeeded)
            logger.info(
                f"Indexed {succeeded}/{len(documents)} chunks for slug: {ko.slug}"
            )
            return succeeded == len(documents)

        except Exception as e:
            logger.error(f"Failed to index knowledge object {knowledge_object.slug}: {e}")
            return False
