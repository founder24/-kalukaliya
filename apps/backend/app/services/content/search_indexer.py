"""
SearchIndexer - Text chunking utility used by the MongoDB RAG pipeline.

Vertex AI Search (Discovery Engine) has been removed; this module now only
provides the `chunk_text()` helper used by chat_service and content_publisher
to split chapter content into ~500-token segments for RAG retrieval.
"""

import logging
import re

# Slug must be lowercase alphanumeric with hyphens only
_SAFE_SLUG_RE = re.compile(r"^[a-z0-9-]+$")

logger = logging.getLogger(__name__)

# Approximate tokens per chunk
CHUNK_SIZE_TOKENS = 500
# Rough chars per token estimate
CHARS_PER_TOKEN = 4


class SearchIndexer:
    """Text chunking helper — Vertex AI Search client removed."""

    def __init__(self):
        pass

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
        Vertex AI Search removed — knowledge object indexing is a no-op.
        RAG retrieval now uses MongoDB topic embeddings (cosine similarity).
        """
        logger.debug(
            f"index_knowledge_object: skipped for slug={knowledge_obj.slug} "
            "(Vertex Search removed)"
        )
        return True


# Singleton
search_indexer = SearchIndexer()
