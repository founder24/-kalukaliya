"""Tests for the SEO sitemap endpoints."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.anyio
async def test_robots_txt_publishes_content_signals(client: AsyncClient):
    resp = await client.get("/api/v1/seo/robots.txt")
    assert resp.status_code == 200
    assert "Content-Signal: search=yes, ai-input=yes, ai-train=no" in resp.text


@pytest.mark.anyio
async def test_sitemap_index_returns_xml(client: AsyncClient):
    resp = await client.get("/api/v1/seo/sitemap.xml")
    assert resp.status_code == 200
    assert "application/xml" in resp.headers["content-type"]
    assert "<sitemapindex" in resp.text
    assert "sitemap-static.xml" in resp.text
    assert "sitemap-subjects.xml" in resp.text
    assert "sitemap-chapters.xml" in resp.text


@pytest.mark.anyio
async def test_sitemap_static_returns_xml(client: AsyncClient):
    resp = await client.get("/api/v1/seo/sitemap-static.xml")
    assert resp.status_code == 200
    assert "application/xml" in resp.headers["content-type"]
    assert "<urlset" in resp.text
    assert "https://syrabit.ai/" in resp.text
    assert "https://syrabit.ai/library" in resp.text
    assert "https://syrabit.ai/chat" in resp.text
    assert "https://syrabit.ai/pricing" in resp.text
    assert "https://syrabit.ai/about" in resp.text


@pytest.mark.anyio
async def test_sitemap_subjects_returns_xml(client: AsyncClient):
    resp = await client.get("/api/v1/seo/sitemap-subjects.xml")
    assert resp.status_code == 200
    assert "application/xml" in resp.headers["content-type"]
    assert "<urlset" in resp.text


@pytest.mark.anyio
async def test_sitemap_chapters_returns_xml(client: AsyncClient):
    resp = await client.get("/api/v1/seo/sitemap-chapters.xml")
    assert resp.status_code == 200
    assert "application/xml" in resp.headers["content-type"]
    assert "<urlset" in resp.text


@pytest.mark.anyio
async def test_chapter_sitemap_includes_legacy_content_without_generated_flag():
    from app.api.v1 import seo

    board = SimpleNamespace(id="board-id", slug="degree", name="Degree")
    cls = SimpleNamespace(id="class-id", board_id="board-id", name="2nd Semester")
    stream = SimpleNamespace(id="stream-id", class_id="class-id", name="B.Com")
    subject = SimpleNamespace(
        id="subject-id",
        stream_id="stream-id",
        slug="office-management",
        name="Office Management",
    )
    chapter = SimpleNamespace(
        id="chapter-id",
        subject_id="subject-id",
        slug=(
            "office-management-unit-iv-office-communication-and-use-of-technology-20-marks"
        ),
        title="Office Communication",
        notes_generated=False,
        notes_en="Published notes exist despite the legacy flag.",
        content_en=None,
        notes_as=None,
        content_as=None,
        updated_at=None,
    )

    def query_for(items):
        query = MagicMock()
        query.to_list = AsyncMock(return_value=items)
        return query

    chapter_queries = []

    def find_chapters(query):
        chapter_queries.append(query)
        return query_for([chapter])

    with (
        patch("app.api.v1.seo.Board.find", return_value=query_for([board])),
        patch("app.api.v1.seo.Class.find", return_value=query_for([cls])),
        patch("app.api.v1.seo.Stream.find", return_value=query_for([stream])),
        patch("app.api.v1.seo.Subject.find", return_value=query_for([subject])),
        patch("app.api.v1.seo.ContentChapter.find", side_effect=find_chapters),
    ):
        urls = await seo._build_chapter_url_map()

    assert urls["chapter-id"][0] == (
        "https://syrabit.ai/degree/2nd-semester/office-management/"
        "office-management-unit-iv-office-communication-and-use-of-technology-20-marks"
    )
    assert chapter_queries == [
        {
            "$or": [
                {"notes_generated": True},
                {"notes_en": {"$exists": True, "$nin": [None, ""]}},
                {"content_en": {"$exists": True, "$nin": [None, ""]}},
                {"notes_as": {"$exists": True, "$nin": [None, ""]}},
                {"content_as": {"$exists": True, "$nin": [None, ""]}},
            ]
        }
    ]
