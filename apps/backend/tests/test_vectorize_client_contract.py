"""Credential-free contract tests for the Cloudflare Vectorize REST client."""

from __future__ import annotations

import json
from collections import defaultdict
from unittest.mock import patch

import httpx
import pytest

from app.config import settings
from app.services.vectorize.client import VectorizeClient


@pytest.mark.anyio
async def test_delete_and_lookup_use_underscore_endpoints_and_safe_batches() -> None:
    """Vectorize requests stay on the current API names and 100-ID limit."""
    request_log: list[tuple[str, list[str]]] = []

    def mock_vectorize(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        ids = payload["ids"]
        endpoint = request.url.path.rsplit("/", 1)[-1]
        request_log.append((endpoint, ids))

        if endpoint == "delete_by_ids":
            result = {
                "count": len(ids),
                "mutationId": f"mutation-{len(request_log)}",
            }
        elif endpoint == "get_by_ids":
            # Return only half the requested IDs to prove lookup results from
            # every batch are aggregated rather than replaced by the last one.
            result = {
                "vectors": [{"id": vector_id} for vector_id in ids if int(vector_id[1:]) % 2 == 0]
            }
        else:
            return httpx.Response(404, json={"success": False})

        return httpx.Response(200, json={"success": True, "result": result})

    vector_ids = [f"v{index}" for index in range(205)]
    transport = httpx.MockTransport(mock_vectorize)
    client = VectorizeClient()
    await client.close()
    client._http = httpx.AsyncClient(transport=transport)

    try:
        with (
            patch.object(settings, "CF_ACCOUNT_ID", "test-account"),
            patch.object(settings, "CF_VECTORIZE_API_TOKEN", "test-token"),
        ):
            deleted = await client.delete(vector_ids)
            found = await client.get_by_ids(vector_ids)
    finally:
        await client.close()

    delete_requests = [(endpoint, ids) for endpoint, ids in request_log if endpoint == "delete_by_ids"]
    lookup_requests = [(endpoint, ids) for endpoint, ids in request_log if endpoint == "get_by_ids"]

    assert [len(ids) for _, ids in delete_requests] == [100, 100, 5]
    assert [len(ids) for _, ids in lookup_requests] == [100, 100, 5]
    assert all(len(ids) <= 100 for _, ids in request_log)
    assert deleted["count"] == len(vector_ids)
    assert len(deleted["mutationIds"]) == 3
    assert [vector["id"] for vector in found] == [
        vector_id for vector_id in vector_ids if int(vector_id[1:]) % 2 == 0
    ]
    assert {endpoint for endpoint, _ in request_log} == {"delete_by_ids", "get_by_ids"}