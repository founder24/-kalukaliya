"""
Tests for SEO sitemap endpoints.
Uses isolated FastAPI app with just the SEO router to avoid credential issues.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.seo import router

app = FastAPI()
app.include_router(router, prefix="/api/seo")
client = TestClient(app)


def test_sitemap_index_returns_xml():
    """GET /api/seo/sitemap.xml returns valid XML sitemap index."""
    response = client.get("/api/seo/sitemap.xml")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/xml"
    assert "sitemapindex" in response.text
    assert "sitemap-static.xml" in response.text
    assert "sitemap-subjects.xml" in response.text
    assert "sitemap-chapters.xml" in response.text


def test_sitemap_index_cache_control():
    """Sitemap index has Cache-Control header."""
    response = client.get("/api/seo/sitemap.xml")
    assert response.headers["cache-control"] == "public, max-age=3600"


def test_sitemap_static_returns_expected_urls():
    """GET /api/seo/sitemap-static.xml returns static page URLs."""
    response = client.get("/api/seo/sitemap-static.xml")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/xml"
    assert "https://syrabit.ai/" in response.text
    assert "https://syrabit.ai/home" in response.text
    assert "https://syrabit.ai/library" in response.text
    assert "https://syrabit.ai/about" in response.text
    assert "https://syrabit.ai/pricing" in response.text
    assert "https://syrabit.ai/login" in response.text
    assert "https://syrabit.ai/signup" in response.text
    assert "https://syrabit.ai/terms" in response.text
    assert "https://syrabit.ai/privacy" in response.text


def test_sitemap_static_has_priority():
    """Static sitemap includes priority values."""
    response = client.get("/api/seo/sitemap-static.xml")
    assert "<priority>1.0</priority>" in response.text
    assert "<priority>0.5</priority>" in response.text


def test_sitemap_subjects_returns_valid_xml():
    """GET /api/seo/sitemap-subjects.xml returns valid XML."""
    response = client.get("/api/seo/sitemap-subjects.xml")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/xml"
    assert "urlset" in response.text


def test_sitemap_chapters_returns_valid_xml():
    """GET /api/seo/sitemap-chapters.xml returns valid XML with deduplication."""
    response = client.get("/api/seo/sitemap-chapters.xml")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/xml"
    assert "urlset" in response.text


def test_sitemap_chapters_deduplicates_urls():
    """Chapter sitemap should not contain duplicate URLs."""
    response = client.get("/api/seo/sitemap-chapters.xml")
    # Extract all <loc> values
    import re

    locs = re.findall(r"<loc>(.*?)</loc>", response.text)
    assert len(locs) == len(set(locs)), "Duplicate URLs found in chapters sitemap"
