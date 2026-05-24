from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import BaseModel
from app.models.knowledge import KnowledgeObject
from datetime import datetime
import logging

logger = logging.getLogger(__name__)
router = APIRouter(tags=["SEO"])
BASE_URL = "https://syrabit.ai"


class SitemapProjection(BaseModel):
    """Projection model for sitemap queries - only URL-relevant fields."""
    board: str
    class_level: str
    subject: str
    chapter: str

    class Settings:
        projection = {"board": 1, "class_level": 1, "subject": 1, "chapter": 1}


@router.get("/sitemap-index.xml")
async def sitemap_index():
    """Sitemap index linking to all sub-sitemaps."""
    now = datetime.utcnow().strftime("%Y-%m-%d")
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <sitemap><loc>{BASE_URL}/api/v1/seo/sitemap-subjects.xml</loc><lastmod>{now}</lastmod></sitemap>
    <sitemap><loc>{BASE_URL}/api/v1/seo/sitemap-chapters.xml</loc><lastmod>{now}</lastmod></sitemap>
    <sitemap><loc>{BASE_URL}/api/v1/seo/sitemap-mcqs.xml</loc><lastmod>{now}</lastmod></sitemap>
    <sitemap><loc>{BASE_URL}/api/v1/seo/sitemap-notes.xml</loc><lastmod>{now}</lastmod></sitemap>
</sitemapindex>"""
    return Response(content=xml, media_type="application/xml")


@router.get("/sitemap-subjects.xml")
async def sitemap_subjects():
    """Sitemap for distinct subjects."""
    objects = await KnowledgeObject.find(
        KnowledgeObject.is_published == True
    ).project(SitemapProjection).to_list()
    seen = set()
    urls = []
    for ko in objects:
        key = (ko.board, ko.class_level, ko.subject)
        if key not in seen:
            seen.add(key)
            urls.append(f"{BASE_URL}/{ko.board}/{ko.class_level}/{ko.subject}")

    xml = _build_sitemap_xml(urls)
    return Response(content=xml, media_type="application/xml")


@router.get("/sitemap-chapters.xml")
async def sitemap_chapters():
    """Sitemap for all published chapters."""
    objects = await KnowledgeObject.find(
        KnowledgeObject.is_published == True
    ).project(SitemapProjection).to_list()
    urls = [
        f"{BASE_URL}/{ko.board}/{ko.class_level}/{ko.subject}/{ko.chapter}"
        for ko in objects
    ]
    xml = _build_sitemap_xml(urls)
    return Response(content=xml, media_type="application/xml")


@router.get("/sitemap-mcqs.xml")
async def sitemap_mcqs():
    """Sitemap for MCQ pages."""
    objects = await KnowledgeObject.find(
        KnowledgeObject.is_published == True
    ).project(SitemapProjection).to_list()
    urls = [
        f"{BASE_URL}/{ko.board}/{ko.class_level}/{ko.subject}/{ko.chapter}/mcqs"
        for ko in objects
    ]
    xml = _build_sitemap_xml(urls)
    return Response(content=xml, media_type="application/xml")


@router.get("/sitemap-notes.xml")
async def sitemap_notes():
    """Sitemap for notes pages (same as chapters, explicit)."""
    objects = await KnowledgeObject.find(
        KnowledgeObject.is_published == True
    ).project(SitemapProjection).to_list()
    urls = [
        f"{BASE_URL}/{ko.board}/{ko.class_level}/{ko.subject}/{ko.chapter}"
        for ko in objects
    ]
    xml = _build_sitemap_xml(urls)
    return Response(content=xml, media_type="application/xml")


def _build_sitemap_xml(urls: list[str]) -> str:
    """Build a sitemap XML string from a list of URLs."""
    entries = "\n".join(
        f"    <url><loc>{url}</loc><changefreq>weekly</changefreq><priority>0.8</priority></url>"
        for url in urls
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{entries}
</urlset>"""
