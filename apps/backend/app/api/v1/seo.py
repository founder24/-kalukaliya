"""
SEO endpoints: XML sitemaps for search engine discovery.
Queries KnowledgeObject collection for dynamic sitemap generation.
"""

import json
import logging
import time
from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import Response

from app.models.knowledge import KnowledgeObject

logger = logging.getLogger(__name__)

router = APIRouter()

BASE_URL = "https://syrabit.ai"

_sitemap_cache: dict[str, tuple[float, str]] = {}
SITEMAP_CACHE_TTL = 600  # 10 minutes


def _get_cached_sitemap(key: str) -> str | None:
    if key in _sitemap_cache:
        ts, content = _sitemap_cache[key]
        if time.time() - ts < SITEMAP_CACHE_TTL:
            return content
    return None


def _set_cached_sitemap(key: str, content: str) -> None:
    _sitemap_cache[key] = (time.time(), content)


def _xml_escape(text: str) -> str:
    """Escape special XML characters."""
    if not text:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


SITEMAP_INDEX_XML = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap>
    <loc>{base_url}/sitemap-static.xml</loc>
  </sitemap>
  <sitemap>
    <loc>{base_url}/sitemap-subjects.xml</loc>
  </sitemap>
  <sitemap>
    <loc>{base_url}/sitemap-chapters.xml</loc>
  </sitemap>
  <sitemap>
    <loc>{base_url}/sitemap-topics.xml</loc>
  </sitemap>
</sitemapindex>"""

SITEMAP_STATIC_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://syrabit.ai/</loc>
    <lastmod>2025-01-01</lastmod>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>https://syrabit.ai/library</loc>
    <lastmod>2025-01-01</lastmod>
    <changefreq>daily</changefreq>
    <priority>0.9</priority>
  </url>
  <url>
    <loc>https://syrabit.ai/chat</loc>
    <lastmod>2025-01-01</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
  </url>
  <url>
    <loc>https://syrabit.ai/pricing</loc>
    <lastmod>2025-01-01</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.7</priority>
  </url>
  <url>
    <loc>https://syrabit.ai/about</loc>
    <lastmod>2025-01-01</lastmod>
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
    cached = _get_cached_sitemap("subjects")
    if cached:
        return Response(content=cached, media_type="application/xml")

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
                    },
                    "max_updated_at": {"$max": "$updated_at"},
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
                lastmod_str = ""
                if item.get("max_updated_at"):
                    lastmod_str = f"\n    <lastmod>{item['max_updated_at'].strftime('%Y-%m-%d')}</lastmod>"
                urls.append(
                    f"  <url>\n"
                    f"    <loc>{loc}</loc>{lastmod_str}\n"
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
        _set_cached_sitemap("subjects", xml_content)
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
    cached = _get_cached_sitemap("chapters")
    if cached:
        return Response(content=cached, media_type="application/xml")

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
        _set_cached_sitemap("chapters", xml_content)
        return Response(content=xml_content, media_type="application/xml")

    except Exception as e:
        logger.warning(f"Failed to generate chapters sitemap from DB: {e}")
        fallback = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            "</urlset>"
        )
        return Response(content=fallback, media_type="application/xml")


@router.get("/sitemap-topics.xml")
async def sitemap_topics():
    """Generate topics sitemap from published knowledge objects' topics."""
    cached = _get_cached_sitemap("topics")
    if cached:
        return Response(content=cached, media_type="application/xml")
    try:
        objects = await KnowledgeObject.find({"status": "published"}).project({
            "metadata.board": 1, "metadata.class_level": 1,
            "metadata.subject": 1, "metadata.chapter": 1,
            "metadata.topics": 1, "updated_at": 1,
        }).to_list()

        urls = []
        seen = set()
        for obj in objects:
            meta = obj.get("metadata", {})
            board = meta.get("board", "")
            class_level = meta.get("class_level", "")
            subject = meta.get("subject", "")
            chapter = meta.get("chapter", "")
            topics = meta.get("topics", [])
            if not all([board, class_level, subject, chapter]):
                continue
            updated = obj.get("updated_at")
            lastmod = ""
            if updated:
                if isinstance(updated, datetime):
                    lastmod = f"\n    <lastmod>{updated.strftime('%Y-%m-%d')}</lastmod>"
                elif isinstance(updated, str):
                    lastmod = f"\n    <lastmod>{updated[:10]}</lastmod>"

            # Generate entry for topic anchors on the chapter page
            base_loc = f"{BASE_URL}/render/{board}/{class_level}/{subject}/{chapter}/notes"
            if isinstance(topics, list):
                for topic in topics:
                    if isinstance(topic, dict):
                        slug = topic.get("topic_slug") or topic.get("slug", "")
                    elif isinstance(topic, str):
                        slug = topic.lower().replace(" ", "-")
                    else:
                        continue
                    if not slug:
                        continue
                    key = f"{board}/{class_level}/{subject}/{chapter}/{slug}"
                    if key in seen:
                        continue
                    seen.add(key)
                    loc = f"{base_loc}#topic-{slug}"
                    urls.append(
                        f"  <url>\n"
                        f"    <loc>{loc}</loc>{lastmod}\n"
                        f"    <changefreq>monthly</changefreq>\n"
                        f"    <priority>0.6</priority>\n"
                        f"  </url>"
                    )

        xml_content = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "\n".join(urls)
            + "\n</urlset>"
        )
        _set_cached_sitemap("topics", xml_content)
        return Response(content=xml_content, media_type="application/xml")
    except Exception as e:
        logger.warning(f"Failed to generate topics sitemap: {e}")
        fallback = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n</urlset>'
        return Response(content=fallback, media_type="application/xml")


@router.get("/feed.xml")
async def rss_feed():
    """RSS 2.0 feed of recently published/updated chapters for AI crawlers."""
    try:
        chapters = await KnowledgeObject.find({"status": "published"}).sort("-updated_at").limit(50).to_list()

        items = []
        for ch in chapters:
            meta = ch.metadata or {}
            board = meta.get("board", "")
            class_level = meta.get("class_level", "")
            subject = meta.get("subject", "")
            chapter = meta.get("chapter", "")
            title = f"{chapter} - {subject} ({board} {class_level})"
            link = f"{BASE_URL}/render/{board}/{class_level}/{subject}/{chapter}/notes" if all([board, class_level, subject, chapter]) else BASE_URL
            pub_date = ch.updated_at.strftime("%a, %d %b %Y %H:%M:%S +0000") if ch.updated_at else ""
            description = f"Study notes for {chapter} in {subject} for {board} {class_level} students."

            items.append(f"""    <item>
      <title>{_xml_escape(title)}</title>
      <link>{link}</link>
      <description>{_xml_escape(description)}</description>
      <pubDate>{pub_date}</pubDate>
      <category>{_xml_escape(subject)}</category>
      <guid isPermaLink="true">{link}</guid>
    </item>""")

        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>Syrabit.ai - Study Notes Feed</title>
    <link>{BASE_URL}</link>
    <description>Latest study notes, MCQs, and exam prep material for AHSEC, SEBA, and CBSE students.</description>
    <language>en-IN</language>
    <lastBuildDate>{datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0000")}</lastBuildDate>
    <atom:link href="{BASE_URL}/feed.xml" rel="self" type="application/rss+xml"/>
{chr(10).join(items)}
  </channel>
</rss>"""
        return Response(content=xml.strip(), media_type="application/xml")
    except Exception as e:
        logger.warning(f"Failed to generate RSS feed: {e}")
        # Return minimal valid RSS
        fallback = f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>Syrabit.ai</title><link>{BASE_URL}</link><description>Study notes feed</description></channel></rss>'
        return Response(content=fallback, media_type="application/xml")


@router.get("/feed.json")
async def json_feed():
    """JSON Feed v1.1 of recently published chapters for AI crawlers."""
    try:
        chapters = await KnowledgeObject.find({"status": "published"}).sort("-updated_at").limit(50).to_list()
        items = []
        for ch in chapters:
            meta = ch.metadata or {}
            board = meta.get("board", "")
            class_level = meta.get("class_level", "")
            subject = meta.get("subject", "")
            chapter = meta.get("chapter", "")
            link = f"{BASE_URL}/render/{board}/{class_level}/{subject}/{chapter}/notes" if all([board, class_level, subject, chapter]) else BASE_URL
            items.append({
                "id": link,
                "url": link,
                "title": f"{chapter} - {subject} ({board} {class_level})",
                "content_text": f"Study notes for {chapter} in {subject} for {board} {class_level} students.",
                "date_modified": ch.updated_at.isoformat() if ch.updated_at else None,
                "tags": [subject, board, class_level],
            })

        feed = {
            "version": "https://jsonfeed.org/version/1.1",
            "title": "Syrabit.ai - Study Notes Feed",
            "home_page_url": BASE_URL,
            "feed_url": f"{BASE_URL}/feed.json",
            "description": "Latest study notes for AHSEC, SEBA, and CBSE students.",
            "language": "en-IN",
            "items": items,
        }
        return Response(content=json.dumps(feed, default=str), media_type="application/json")
    except Exception as e:
        logger.warning(f"Failed to generate JSON feed: {e}")
        fallback = {"version": "https://jsonfeed.org/version/1.1", "title": "Syrabit.ai", "items": []}
        return Response(content=json.dumps(fallback), media_type="application/json")
