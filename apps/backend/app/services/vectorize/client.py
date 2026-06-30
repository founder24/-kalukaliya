"""
Cloudflare Vectorize REST client.

Wraps the Cloudflare Vectorize REST API to:
  - upsert vectors (with metadata for pre-filter)
  - query top-K vectors with optional metadata filter
  - delete vectors by ID

Endpoint base:
  https://api.cloudflare.com/client/v4/accounts/{account_id}/vectorize/v2/indexes/{index_name}

Auth: Bearer token — prefers CF_VECTORIZE_API_TOKEN, falls back to
      CF_WORKER_AI_TOKEN → CF_API_TOKEN (same token used for Workers AI embeddings).

Metadata filtering: only fields indexed in CF (subjectId, chapterId, topicId,
medium, sourceType, chunkType) should appear in `filter`. Non-indexed fields
are silently ignored by the API.

Ref: https://developers.cloudflare.com/vectorize/reference/client-api/
     https://developers.cloudflare.com/vectorize/reference/metadata-filtering/
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_BATCH_SIZE = 100


class VectorizeClient:
    def __init__(self) -> None:
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=5.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    # ── Auth / URL helpers ────────────────────────────────────────────────────

    @property
    def _token(self) -> Optional[str]:
        return (
            settings.CF_VECTORIZE_API_TOKEN
            or settings.CF_WORKER_AI_TOKEN
            or settings.CF_API_TOKEN
        )

    @property
    def _account_id(self) -> Optional[str]:
        return settings.CF_ACCOUNT_ID or settings.CLOUDFLARE_ACCOUNT_ID

    @property
    def _index(self) -> str:
        return settings.CF_VECTORIZE_INDEX_NAME

    @property
    def _base(self) -> str:
        return (
            f"https://api.cloudflare.com/client/v4/accounts/{self._account_id}"
            f"/vectorize/v2/indexes/{self._index}"
        )

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    def _check_configured(self) -> None:
        if not self._account_id or not self._token:
            raise RuntimeError(
                "Cloudflare Vectorize not configured — "
                "CF_ACCOUNT_ID and CF_API_TOKEN / CF_WORKER_AI_TOKEN are required."
            )

    # ── Public API ────────────────────────────────────────────────────────────

    async def upsert(self, vectors: list[dict]) -> dict:
        """
        Upsert a batch of vectors into the Cloudflare Vectorize index.

        Each vector dict must have:
          id       (str)            — unique vector ID (e.g. MongoDB Chunk._id)
          values   (list[float])    — 1024-dim embedding
          metadata (dict, optional) — filterable metadata fields

        Vectors are sent in batches of up to 100 (CF limit).
        Returns aggregated {mutationId, count} across all batches.
        """
        self._check_configured()
        if not vectors:
            return {"count": 0}

        total_count = 0
        mutation_ids: list[str] = []

        for i in range(0, len(vectors), _BATCH_SIZE):
            batch = vectors[i : i + _BATCH_SIZE]
            resp = await self._http.post(
                f"{self._base}/upsert",
                headers=self._headers,
                json={"vectors": batch},
            )
            resp.raise_for_status()
            body = resp.json()
            if not body.get("success"):
                errors = body.get("errors", [])
                raise RuntimeError(f"Vectorize upsert failed: {errors}")
            result = body.get("result", {})
            total_count += result.get("count", len(batch))
            if mid := result.get("mutationId"):
                mutation_ids.append(mid)

        logger.info(
            f"Vectorize upsert: {total_count} vectors upserted to index={self._index}"
        )
        return {"count": total_count, "mutationIds": mutation_ids}

    async def query(
        self,
        vector: list[float],
        top_k: int = 5,
        filter: Optional[dict] = None,
        return_values: bool = False,
        return_metadata: bool = True,
    ) -> list[dict]:
        """
        Query the Vectorize index for the top-K nearest neighbours.

        Args:
            vector: 1024-dim query embedding.
            top_k: number of results to return (max 20 without Enterprise tier).
            filter: metadata filter dict using CF Vectorize filter syntax:
                    e.g. {"medium": {"$eq": "english"}, "subjectId": {"$eq": "subj_phy"}}
                    Only use indexed metadata fields.
            return_values: include embedding values in response (usually False).
            return_metadata: include metadata in response (needed for hydration).

        Returns:
            List of match dicts: {id, score, metadata?, values?}
        """
        self._check_configured()

        payload: dict[str, Any] = {
            "vector": vector,
            "topK": top_k,
            "returnValues": return_values,
            "returnMetadata": "all" if return_metadata else "none",
        }
        if filter:
            payload["filter"] = filter

        resp = await self._http.post(
            f"{self._base}/query",
            headers=self._headers,
            json=payload,
        )
        resp.raise_for_status()
        body = resp.json()

        if not body.get("success"):
            errors = body.get("errors", [])
            raise RuntimeError(f"Vectorize query failed: {errors}")

        matches: list[dict] = body.get("result", {}).get("matches", [])
        logger.debug(
            f"Vectorize query: top_k={top_k}, filter={filter}, "
            f"got {len(matches)} matches"
        )
        return matches

    async def delete(self, vector_ids: list[str]) -> dict:
        """
        Delete vectors by ID from the Cloudflare Vectorize index.

        Args:
            vector_ids: list of vector IDs to delete (max 1000 per request).

        Returns:
            {count: int, mutationId: str}
        """
        self._check_configured()
        if not vector_ids:
            return {"count": 0}

        resp = await self._http.post(
            f"{self._base}/delete-by-ids",
            headers=self._headers,
            json={"ids": vector_ids},
        )
        resp.raise_for_status()
        body = resp.json()

        if not body.get("success"):
            errors = body.get("errors", [])
            raise RuntimeError(f"Vectorize delete failed: {errors}")

        result = body.get("result", {})
        logger.info(
            f"Vectorize delete: {result.get('count', len(vector_ids))} vectors deleted"
        )
        return result

    async def get_index_info(self) -> dict:
        """Return metadata about the Vectorize index (dimensions, metric, count)."""
        self._check_configured()
        resp = await self._http.get(self._base, headers=self._headers)
        resp.raise_for_status()
        body = resp.json()
        if not body.get("success"):
            raise RuntimeError(f"Vectorize index info failed: {body.get('errors')}")
        return body.get("result", {})

    async def get_metadata_indexes(self) -> list[dict]:
        """
        List all metadata indexes configured on this Vectorize index.

        CF endpoint: GET .../metadata-index/list
        Returns a list of dicts: [{propertyName: str, indexType: str}, ...]

        Silently returns [] if the API call fails (e.g. index not yet configured),
        so callers can safely compare against REQUIRED_INDEXES without crashing.
        """
        self._check_configured()
        try:
            resp = await self._http.get(
                f"{self._base}/metadata-index/list",
                headers=self._headers,
            )
            resp.raise_for_status()
            body = resp.json()
            if not body.get("success"):
                logger.warning(
                    f"Vectorize metadata-index/list returned success=false: "
                    f"{body.get('errors')}"
                )
                return []
            result = body.get("result", {})
            # API may return {"metadataIndexes": [...]} or a bare list
            if isinstance(result, list):
                return result
            return result.get("metadataIndexes", [])
        except Exception as exc:
            logger.warning(f"Vectorize get_metadata_indexes failed: {exc}")
            return []

    async def close(self) -> None:
        await self._http.aclose()


vectorize_client = VectorizeClient()
