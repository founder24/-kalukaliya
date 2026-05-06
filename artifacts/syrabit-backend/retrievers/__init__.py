"""
retrievers — pluggable vector-retrieval backend for Syrabit RAG.

Three implementations live behind a tiny ABC (`base.Retriever`):

  * VectorizeRetriever          — Cloudflare Vectorize (production default)
  * MongoVectorRetriever        — MongoDB Atlas Vector Search (Flex tier, 2026-04)
  * PineconeVectorRetriever     — Pinecone serverless (AHSEC/SEBA chunks, 2026-05)

Task #490 removed the Vertex Vector Search backend along with the
multilingual-embed and chat hot-path Vertex surfaces.

Selection happens via `factory.get_retriever()` which inspects (in order):

  1. The runtime override set through the admin UI / API
     (db.settings document `id="retriever_config"`, field `active`).
  2. The `RAG_RETRIEVER` environment variable
     (`vectorize` | `mongodb_vector` | `pinecone_vector`).
  3. Default: `vectorize`.
"""

from .base import Retriever
from .factory import (
    get_retriever,
    get_retriever_by_name,
    list_available_retrievers,
    invalidate_retriever_cache,
    get_active_retriever_name,
    set_active_retriever,
    DEFAULT_RETRIEVER,
)

__all__ = [
    "Retriever",
    "get_retriever",
    "get_retriever_by_name",
    "list_available_retrievers",
    "invalidate_retriever_cache",
    "get_active_retriever_name",
    "set_active_retriever",
    "DEFAULT_RETRIEVER",
]
