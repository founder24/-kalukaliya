"""Azure AI Search wrapper — alternative library retriever for RAG.

Sits parallel to Pinecone in the RAG path. The retriever switch is
controlled by the ``rag.retriever`` feature flag — values:

* ``pinecone`` (default)
* ``azure-search``
* ``shadow`` — query both, return Pinecone, log Azure-side recall as
  a side-by-side report (consumed by the admin RAG panel and the
  ``rag_recall_report`` cron job).

Index name is fixed (``library-v1``); schema and analyzer
configuration are owned by the indexing cron job (``services/backend/
sqs_consumers/azure_search_index.py``) so this wrapper only handles
query-time concerns.

Auth is the runtime managed identity; ``local_authentication_enabled
= false`` on the search service blocks API-key fallback (see
``infra/azure/ai-services.tf``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from . import _resolver

API_VERSION = "2024-07-01"
INDEX_NAME = "library-v1"


@dataclass
class SearchHit:
    doc_id: str
    score: float
    chapter_id: Optional[str] = None
    snippet: str = ""


@dataclass
class HybridResult:
    hits: list[SearchHit] = field(default_factory=list)
    total_count: int = 0


def _token() -> str:
    return _resolver.get_credential().get_token(
        "https://search.azure.com/.default"
    ).token


def hybrid_search(
    query: str,
    *,
    embedding: Optional[list[float]] = None,
    top: int = 10,
    filter_expr: Optional[str] = None,
) -> HybridResult:
    """Hybrid keyword + vector search.

    ``embedding`` should match the model the indexer used (the
    cron job pins ``text-embedding-3-small``; mismatch raises 400).
    Pass ``embedding=None`` for keyword-only baseline used by the
    side-by-side recall report.
    """
    import requests

    endpoint = _resolver.endpoint_for("search").rstrip("/")
    body: dict = {
        "search": query,
        "queryType": "semantic",
        "semanticConfiguration": "library-semantic",
        "top": top,
        "select": "doc_id,chapter_id,snippet",
        "count": True,
    }
    if embedding is not None:
        body["vectorQueries"] = [
            {
                "kind": "vector",
                "vector": embedding,
                "fields": "content_vector",
                "k": top,
            }
        ]
    if filter_expr:
        body["filter"] = filter_expr

    resp = requests.post(
        f"{endpoint}/indexes/{INDEX_NAME}/docs/search?api-version={API_VERSION}",
        json=body,
        headers={
            "Authorization": f"Bearer {_token()}",
            "Content-Type": "application/json",
        },
        timeout=10,
    )
    if resp.status_code == 429:
        raise RuntimeError("azure-search: throttled (429)")
    resp.raise_for_status()
    payload = resp.json()
    hits = [
        SearchHit(
            doc_id=row.get("doc_id", ""),
            score=float(row.get("@search.score", 0.0)),
            chapter_id=row.get("chapter_id"),
            snippet=row.get("snippet", ""),
        )
        for row in payload.get("value", [])
    ]
    return HybridResult(hits=hits, total_count=int(payload.get("@odata.count", len(hits))))


def upsert(documents: list[dict]) -> int:
    """Upsert a batch of documents into the index.

    Used by ``services/backend/sqs_consumers/azure_search_index.py``
    when the library publish event fires; not for request-path use.
    Returns the count of successful operations.
    """
    import requests

    if not documents:
        return 0
    endpoint = _resolver.endpoint_for("search").rstrip("/")
    payload = {
        "value": [
            {**doc, "@search.action": doc.get("@search.action", "mergeOrUpload")}
            for doc in documents
        ]
    }
    resp = requests.post(
        f"{endpoint}/indexes/{INDEX_NAME}/docs/index?api-version={API_VERSION}",
        json=payload,
        headers={
            "Authorization": f"Bearer {_token()}",
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    if resp.status_code == 429:
        raise RuntimeError("azure-search: upsert throttled (429)")
    resp.raise_for_status()
    return sum(1 for r in resp.json().get("value", []) if r.get("status"))
