"""Contract tests for prerender cache validation against the real API router."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1 import public_content

LIBRARY_BUNDLE_CACHE_CONTROL = public_content.LIBRARY_BUNDLE_CACHE_CONTROL
LIBRARY_BUNDLE_SCHEMA_SIGNAL = public_content.LIBRARY_BUNDLE_SCHEMA_SIGNAL


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    app = FastAPI()
    app.include_router(public_content.router, prefix="/api/v1/content")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.anyio
async def test_library_bundle_head_exposes_stable_schema_signal(client: AsyncClient):
    first = await client.head("/api/v1/content/library-bundle?slim=1")
    second = await client.head("/api/v1/content/library-bundle?slim=1")

    assert first.status_code == 200
    assert first.content == b""
    assert first.headers["cache-control"] == LIBRARY_BUNDLE_CACHE_CONTROL
    assert first.headers["x-schema-version"] == LIBRARY_BUNDLE_SCHEMA_SIGNAL
    assert second.headers["x-schema-version"] == first.headers["x-schema-version"]


@pytest.mark.anyio
async def test_library_bundle_get_and_head_share_schema_signal(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    class EmptyQuery:
        async def to_list(self, *, length):
            return []

    def empty_find(*_args, **_kwargs):
        return EmptyQuery()

    for model in (
        public_content.Board,
        public_content.Class,
        public_content.Stream,
        public_content.Subject,
        public_content.Chapter,
    ):
        monkeypatch.setattr(model, "find", empty_find)

    head = await client.head("/api/v1/content/library-bundle?slim=1")
    get = await client.get("/api/v1/content/library-bundle?slim=1")

    assert get.status_code == 200
    assert get.json()["boards"] == []
    assert get.headers["x-schema-version"] == head.headers["x-schema-version"]


def test_library_bundle_schema_signal_changes_with_deployment_revision(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("K_REVISION", "revision-a")
    first = public_content._build_library_bundle_schema_signal()
    assert public_content._build_library_bundle_schema_signal() == first

    monkeypatch.setenv("K_REVISION", "revision-b")
    assert public_content._build_library_bundle_schema_signal() != first