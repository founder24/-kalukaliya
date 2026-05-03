"""Smoke test — Task #291: Assamese-first cross-language RAG path.

Validates two independently-failing pieces of the Assamese pipeline:

1. ``_detect_is_assamese_script`` correctly classifies Assamese vs. English.
2. The RAG vector path (``rag._try_vector_provider``) routes
   ``lang="as"`` queries through Pinecone Inference's
   ``multilingual-e5-large`` embed and queries Pinecone with
   ``namespace="as"``.

Run::

    python -m pytest tests/test_assamese_rag_namespace.py -v
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.disable(logging.CRITICAL)


def test_detect_assamese_script_recognises_assamese_and_rejects_english():
    from routes.ai_chat import _detect_is_assamese_script
    # Pure Assamese
    assert _detect_is_assamese_script("অসমীয়াত উত্তৰ দিয়া") is True
    assert _detect_is_assamese_script("এইটো কি?") is True
    # Pure English / Latin / numeric / empty
    assert _detect_is_assamese_script("What is photosynthesis?") is False
    assert _detect_is_assamese_script("") is False
    assert _detect_is_assamese_script("123 + 456 = 579") is False
    # Mixed script — mostly English with one Assamese word must still be
    # treated as English so we translate before embedding (Task #291).
    assert _detect_is_assamese_script("Photosynthesis কি?") is False
    # Mixed script — mostly Assamese with one English term retained verbatim
    # should still count as Assamese (above 60% threshold).
    assert _detect_is_assamese_script("সালোকসংশ্লেষণ ক'ৰা DNA সম্পৰ্কে কোৱা") is True
    print("  PASS: _detect_is_assamese_script handles mixed-script correctly")


def test_assamese_rag_uses_pinecone_namespace_as_with_e5_large():
    """When lang=='as', _try_vector_provider must:
       1. embed the query via providers.pinecone_ai.embed_one (e5-large path)
       2. issue PineconeVectorRetriever.query(..., namespace="as").
    """
    import rag

    embed_stub = mock.AsyncMock(return_value=[0.01] * 1024)
    query_stub = mock.AsyncMock(return_value=[
        {"id": "ch1", "score": 0.9,
         "metadata": {"chapter_id": "ch1", "chapter_title": "ত", "subject_id": "s1"}},
    ])

    class _StubRetriever:
        def is_configured(self):
            return True

        async def query(self, vector, top_k=10, metadata_filter=None,
                        return_metadata=True, namespace=None):
            return await query_stub(vector, top_k=top_k,
                                    metadata_filter=metadata_filter,
                                    return_metadata=return_metadata,
                                    namespace=namespace)

    # Stub providers.pinecone_ai (embed_one) and the retriever class.
    with mock.patch("providers.pinecone_ai.embed_one", embed_stub), \
         mock.patch("providers.pinecone_ai.ENABLED", True), \
         mock.patch("retrievers.pinecone_vector.PineconeVectorRetriever",
                    return_value=_StubRetriever()):
        result = asyncio.run(
            rag._try_vector_provider("pinecone_ai", "এইটো কি?", limit=5,
                                     subject_id="s1", lang="as")
        )

    assert result and result[0]["chapter_id"] == "ch1", \
        f"Expected chapter ch1, got {result!r}"
    embed_stub.assert_awaited_once()
    # input_type should be "query" for the question-side embed
    assert embed_stub.await_args.kwargs.get("input_type") == "query", \
        f"Expected input_type='query', got {embed_stub.await_args!r}"

    query_stub.assert_awaited_once()
    assert query_stub.await_args.kwargs.get("namespace") == "as", (
        f"Expected namespace='as' on Pinecone query, got "
        f"{query_stub.await_args.kwargs!r}"
    )
    print("  PASS: lang='as' → pinecone_ai.embed_one + Pinecone namespace='as'")


def test_english_rag_uses_pinecone_namespace_en_with_e5_large():
    """Sanity guard for the English leg of Task #291: lang='en' must use
    the same multilingual-e5-large embedder as the corpus writer
    (scripts/embed_english_corpus.py) so the query embedding space matches
    the index embedding space, and must query Pinecone with
    namespace='en'. This eliminates the previous Cohere/e5 dimension-space
    mismatch."""
    import rag

    embed_stub = mock.AsyncMock(return_value=[0.02] * 1024)
    query_stub = mock.AsyncMock(return_value=[
        {"id": "ch9", "score": 0.7,
         "metadata": {"chapter_id": "ch9", "chapter_title": "Photosynthesis",
                      "subject_id": "s1"}},
    ])

    class _StubRetriever:
        def is_configured(self):
            return True

        async def query(self, vector, top_k=10, metadata_filter=None,
                        return_metadata=True, namespace=None):
            return await query_stub(vector, top_k=top_k,
                                    metadata_filter=metadata_filter,
                                    return_metadata=return_metadata,
                                    namespace=namespace)

    with mock.patch("providers.pinecone_ai.embed_one", embed_stub), \
         mock.patch("providers.pinecone_ai.ENABLED", True), \
         mock.patch("retrievers.pinecone_vector.PineconeVectorRetriever",
                    return_value=_StubRetriever()):
        asyncio.run(
            rag._try_vector_provider("pinecone_ai", "What is photosynthesis?",
                                     limit=5, subject_id="s1", lang="en")
        )

    embed_stub.assert_awaited_once()
    assert embed_stub.await_args.kwargs.get("input_type") == "query"
    assert query_stub.await_args.kwargs.get("namespace") == "en", (
        f"English path must query Pinecone with namespace='en'; got "
        f"{query_stub.await_args.kwargs!r}"
    )
    print("  PASS: lang='en' → pinecone_ai.embed_one + Pinecone namespace='en'")


if __name__ == "__main__":
    test_detect_assamese_script_recognises_assamese_and_rejects_english()
    test_assamese_rag_uses_pinecone_namespace_as_with_e5_large()
    test_english_rag_uses_pinecone_namespace_en_with_e5_large()
    print("\nAll Task #291 Assamese RAG namespace assertions verified.")
