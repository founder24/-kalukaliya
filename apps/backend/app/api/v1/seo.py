"""
SEO Endpoints: Sitemap generation for search engines and AI crawlers.
"""

from fastapi import APIRouter, Response

router = APIRouter()

BASE_URL = "https://syrabit.ai"


@router.get("/sitemap.xml")
async def sitemap_index() -> Response:
    """Sitemap index referencing all sub-sitemaps."""
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap>
    <loc>{BASE_URL}/sitemap-static.xml</loc>
  </sitemap>
  <sitemap>
    <loc>{BASE_URL}/sitemap-subjects.xml</loc>
  </sitemap>
  <sitemap>
    <loc>{BASE_URL}/sitemap-chapters.xml</loc>
  </sitemap>
</sitemapindex>"""
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/sitemap-static.xml")
async def sitemap_static() -> Response:
    """Sitemap for static pages."""
    static_pages = [
        ("/", "1.0", "weekly"),
        ("/home", "0.8", "weekly"),
        ("/library", "0.8", "weekly"),
        ("/about", "0.5", "monthly"),
        ("/pricing", "0.5", "monthly"),
        ("/login", "0.3", "monthly"),
        ("/signup", "0.3", "monthly"),
        ("/terms", "0.2", "yearly"),
        ("/privacy", "0.2", "yearly"),
    ]

    urls = ""
    for path, priority, changefreq in static_pages:
        urls += f"""  <url>
    <loc>{BASE_URL}{path}</loc>
    <changefreq>{changefreq}</changefreq>
    <priority>{priority}</priority>
  </url>
"""

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{urls}</urlset>"""
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/sitemap-subjects.xml")
async def sitemap_subjects() -> Response:
    """Sitemap for subject landing pages.

    TODO: Populate from database (library-bundle or MongoDB collections)
    to enumerate all boards/classes/subjects dynamically.
    """
    # Placeholder structure - should be populated from the database
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!-- TODO: Populate subject URLs from the database -->
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>{BASE_URL}/library</loc>
    <changefreq>weekly</changefreq>
    <priority>0.7</priority>
  </url>
</urlset>"""
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/sitemap-chapters.xml")
async def sitemap_chapters() -> Response:
    """Sitemap for chapter pages with URL deduplication.

    TODO: Populate from database (library-bundle or MongoDB collections)
    to enumerate all chapter URLs dynamically.
    """
    # Use a set to deduplicate URLs
    seen_urls: set[str] = set()
    urls_xml = ""

    # TODO: Replace with database query to get all chapter URLs
    chapter_urls = [
        f"{BASE_URL}/library",
    ]

    for url in chapter_urls:
        if url not in seen_urls:
            seen_urls.add(url)
            urls_xml += f"""  <url>
    <loc>{url}</loc>
    <changefreq>weekly</changefreq>
    <priority>0.6</priority>
  </url>
"""

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!-- TODO: Populate chapter URLs from the database -->
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{urls_xml}</urlset>"""
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Cache-Control": "public, max-age=3600"},
    )
