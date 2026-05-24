"""
SEO endpoints: XML sitemaps for search engine discovery.
"""

from fastapi import APIRouter
from fastapi.responses import Response

router = APIRouter()

SITEMAP_INDEX_XML = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap>
    <loc>https://syrabit.ai/api/v1/seo/sitemap-static.xml</loc>
  </sitemap>
  <sitemap>
    <loc>https://syrabit.ai/api/v1/seo/sitemap-subjects.xml</loc>
  </sitemap>
  <sitemap>
    <loc>https://syrabit.ai/api/v1/seo/sitemap-chapters.xml</loc>
  </sitemap>
</sitemapindex>
""".strip()

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
</urlset>
""".strip()

# Populated from DB in a future iteration
SITEMAP_SUBJECTS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <!-- populated from DB -->
</urlset>
""".strip()

# Populated from DB with deduplication in a future iteration
SITEMAP_CHAPTERS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <!-- populated from DB with deduplication -->
</urlset>
""".strip()


@router.get("/sitemap.xml")
async def sitemap_index():
    return Response(content=SITEMAP_INDEX_XML, media_type="application/xml")


@router.get("/sitemap-static.xml")
async def sitemap_static():
    return Response(content=SITEMAP_STATIC_XML, media_type="application/xml")


@router.get("/sitemap-subjects.xml")
async def sitemap_subjects():
    return Response(content=SITEMAP_SUBJECTS_XML, media_type="application/xml")


@router.get("/sitemap-chapters.xml")
async def sitemap_chapters():
    return Response(content=SITEMAP_CHAPTERS_XML, media_type="application/xml")
