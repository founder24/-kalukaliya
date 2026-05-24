"""
SEO endpoints: XML sitemaps for search engine discovery.
Queries KnowledgeObject collection for dynamic sitemap generation.
"""

import logging
from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import Response

from app.models.knowledge import KnowledgeObject

logger = logging.getLogger(__name__)

router = APIRouter()

BASE_URL = "https://syrabit.ai"

SITEMAP_INDEX_XML = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap>
    <loc>{base_url}/api/v1/seo/sitemap-static.xml</loc>
  </sitemap>
  <sitemap>
    <loc>{base_url}/api/v1/seo/sitemap-subjects.xml</loc>
  </sitemap>
  <sitemap>
    <loc>{base_url}/api/v1/seo/sitemap-chapters.xml</loc>
  </sitemap>
</sitemapindex>"""

SITEMAP_STATIC_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://syrabit.ai/</loc>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>https://syrabit.ai/library</loc>
    <changefreq>daily</changefreq>
    <priority>0.9</priority>
  </url>
  <url>
    <loc>https://syrabit.ai/chat</loc>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
  </url>
  <url>
    <loc>https://syrabit.ai/pricing</loc>
    <changefreq>weekly</changefreq>
    <priority>0.7</priority>
  </url>
  <url>
    <loc>https://syrabit.ai/about</loc>
    <changefreq>monthly</changefreq>
    <priority>0.5</priority>
  </url>
</urlset>"""


@router.get("/sitemap.xml")
async def sitemap_index():
    content = SITEMAP_INDEX_XML.format(base_url=BASE_URL)
    return Response(content=content.strip(), media_type="application/xml")


@router.get("/sitemap-static.xml")
async def sitemap_static():
    return Response(content=SITEMAP_STATIC_XML.strip(), media_type="application/xml")


@router.get("/sitemap-subjects.xml")
async def sitemap_subjects():
    """Generate subjects sitemap from published knowledge objects."""
    try:
        # Use aggregation to get distinct board/class/subject combinations
        pipeline = [
            {"$match": {"status": "published"}},
            {
                "$group": {
                    "_id": {
                        "board": "$metadata.board",
                        "class_level": "$metadata.class_level",
                        "subject": "$metadata.subject",
                    }
                }
            },
        ]
        results = await KnowledgeObject.aggregate(pipeline).to_list()

        urls = []
        for item in results:
            group = item["_id"]
            board = group.get("board", "")
            class_level = group.get("class_level", "")
            subject = group.get("subject", "")
            if board and class_level and subject:
                loc = f"{BASE_URL}/render/{board}/{class_level}/{subject}"
                urls.append(
                    f"  <url>\n"
                    f"    <loc>{loc}</loc>\n"
                    f"    <changefreq>weekly</changefreq>\n"
                    f"    <priority>0.8</priority>\n"
                    f"  </url>"
                )

        xml_content = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "\n".join(urls)
            + "\n</urlset>"
        )
        return Response(content=xml_content, media_type="application/xml")

    except Exception as e:
        logger.warning(f"Failed to generate subjects sitemap from DB: {e}")
        # Fallback to empty sitemap
        fallback = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            "</urlset>"
        )
        return Response(content=fallback, media_type="application/xml")


@router.get("/sitemap-chapters.xml")
async def sitemap_chapters():
    """Generate chapters sitemap from published knowledge objects with deduplication."""
    try:
        # Fetch published objects with only the fields we need
        objects = (
            await KnowledgeObject.find({"status": "published"})
            .project(
                {
                    "metadata.board": 1,
                    "metadata.class_level": 1,
                    "metadata.subject": 1,
                    "metadata.chapter": 1,
                    "updated_at": 1,
                }
            )
            .to_list()
        )

        seen = set()
        urls = []
        for obj in objects:
            meta = obj.get("metadata", {})
            board = meta.get("board", "")
            class_level = meta.get("class_level", "")
            subject = meta.get("subject", "")
            chapter = meta.get("chapter", "")

            key = f"{board}/{class_level}/{subject}/{chapter}"
            if key in seen or not all([board, class_level, subject, chapter]):
                continue
            seen.add(key)

            updated = obj.get("updated_at")
            lastmod = ""
            if updated:
                if isinstance(updated, datetime):
                    lastmod = f"\n    <lastmod>{updated.strftime('%Y-%m-%d')}</lastmod>"
                elif isinstance(updated, str):
                    lastmod = f"\n    <lastmod>{updated[:10]}</lastmod>"

            loc = f"{BASE_URL}/render/{board}/{class_level}/{subject}/{chapter}/notes"
            urls.append(
                f"  <url>\n"
                f"    <loc>{loc}</loc>{lastmod}\n"
                f"    <changefreq>weekly</changefreq>\n"
                f"    <priority>0.7</priority>\n"
                f"  </url>"
            )

        xml_content = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "\n".join(urls)
            + "\n</urlset>"
        )
        return Response(content=xml_content, media_type="application/xml")

    except Exception as e:
        logger.warning(f"Failed to generate chapters sitemap from DB: {e}")
        fallback = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            "</urlset>"
        )
        return Response(content=fallback, media_type="application/xml")
