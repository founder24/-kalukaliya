"""Tests for the SEO sitemap endpoints."""

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
