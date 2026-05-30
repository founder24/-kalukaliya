"""
SEO endpoints: XML sitemaps for search engine discovery.
Queries KnowledgeObject collection for dynamic sitemap generation.
"""

import json
import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import Response

from app.models.knowledge import KnowledgeObject
from app.models.content import Chapter

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
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;")


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
    """Generate topic-level sitemap from published chapters with per-topic URLs."""
    cached = _get_cached_sitemap("topics")
    if cached:
        return Response(content=cached, media_type="application/xml")
    try:
        chapters = (
            await Chapter.find({"status": "published"})
            .project({
                "slug": 1,
                "published_topics": 1,
                "updated_at": 1,
                "subject_id": 1,
            })
            .to_list()
        )
        urls = []
        for ch in chapters:
            updated = ch.get("updated_at")
            lastmod = ""
            if updated:
                if isinstance(updated, datetime):
                    lastmod = f"\n    <lastmod>{updated.strftime('%Y-%m-%d')}</lastmod>"
                elif isinstance(updated, str):
                    lastmod = f"\n    <lastmod>{updated[:10]}</lastmod>"
            for topic in (ch.get("published_topics") or []):
                topic_slug = topic.get("topic_slug") if isinstance(topic, dict) else getattr(topic, "topic_slug", "")
                if not topic_slug:
                    continue
                # Use chapter slug + topic anchor for deep-link
                loc = f"{BASE_URL}/{ch.get('slug', '')}#topic-{topic_slug}"
                urls.append(
                    f"  <url>\n"
                    f"    <loc>{loc}</loc>{lastmod}\n"
                    f"    <changefreq>weekly</changefreq>\n"
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
async def feed_xml():
    """RSS 2.0 feed of recently published content."""
    cached = _get_cached_sitemap("feed_xml")
    if cached:
        return Response(content=cached, media_type="application/rss+xml")
    try:
        objects = (
            await KnowledgeObject.find({"status": "published"})
            .sort("-updated_at")
            .limit(50)
            .project({"slug": 1, "title": 1, "description": 1, "metadata": 1, "updated_at": 1, "published_at": 1})
            .to_list()
        )
        items_xml = []
        for obj in objects:
            title = obj.get("title", "")
            desc = obj.get("description", "")[:500]
            meta = obj.get("metadata", {})
            link = f"{BASE_URL}/render/{meta.get('board', '')}/{meta.get('class_level', '')}/{meta.get('subject', '')}/{meta.get('chapter', '')}/notes"
            pub_date = obj.get("published_at") or obj.get("updated_at")
            pub_str = ""
            if pub_date:
                if isinstance(pub_date, datetime):
                    pub_str = pub_date.strftime("%a, %d %b %Y %H:%M:%S +0000")
                elif isinstance(pub_date, str):
                    pub_str = pub_date
            items_xml.append(
                f"    <item>\n"
                f"      <title>{_xml_escape(title)}</title>\n"
                f"      <link>{link}</link>\n"
                f"      <description>{_xml_escape(desc)}</description>\n"
                f"      <pubDate>{pub_str}</pubDate>\n"
                f"      <guid isPermaLink=\"true\">{link}</guid>\n"
                f"    </item>"
            )
        now_str = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
        xml_content = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n'
            '  <channel>\n'
            f'    <title>Syrabit.ai - Study Notes &amp; Exam Prep</title>\n'
            f'    <link>{BASE_URL}</link>\n'
            f'    <description>Latest study notes, definitions, and exam prep for Assam Board students</description>\n'
            f'    <language>en-in</language>\n'
            f'    <lastBuildDate>{now_str}</lastBuildDate>\n'
            f'    <atom:link href="{BASE_URL}/feed.xml" rel="self" type="application/rss+xml" />\n'
            + "\n".join(items_xml) + "\n"
            '  </channel>\n'
            '</rss>'
        )
        _set_cached_sitemap("feed_xml", xml_content)
        return Response(content=xml_content, media_type="application/rss+xml")
    except Exception as e:
        logger.warning(f"Failed to generate RSS feed: {e}")
        fallback = '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel><title>Syrabit.ai</title></channel></rss>'
        return Response(content=fallback, media_type="application/rss+xml")


@router.get("/feed/{subject_slug}.xml")
async def feed_subject_xml(subject_slug: str):
    """RSS 2.0 feed filtered by subject slug."""
    cache_key = f"feed_subject_{subject_slug}"
    cached = _get_cached_sitemap(cache_key)
    if cached:
        return Response(content=cached, media_type="application/rss+xml")
    try:
        objects = (
            await KnowledgeObject.find({"status": "published", "metadata.subject": subject_slug})
            .sort("-updated_at")
            .limit(50)
            .project({"slug": 1, "title": 1, "description": 1, "metadata": 1, "updated_at": 1, "published_at": 1})
            .to_list()
        )
        items_xml = []
        for obj in objects:
            title = obj.get("title", "")
            desc = obj.get("description", "")[:500]
            meta = obj.get("metadata", {})
            link = f"{BASE_URL}/render/{meta.get('board', '')}/{meta.get('class_level', '')}/{meta.get('subject', '')}/{meta.get('chapter', '')}/notes"
            pub_date = obj.get("published_at") or obj.get("updated_at")
            pub_str = ""
            if pub_date:
                if isinstance(pub_date, datetime):
                    pub_str = pub_date.strftime("%a, %d %b %Y %H:%M:%S +0000")
                elif isinstance(pub_date, str):
                    pub_str = pub_date
            items_xml.append(
                f"    <item>\n"
                f"      <title>{_xml_escape(title)}</title>\n"
                f"      <link>{link}</link>\n"
                f"      <description>{_xml_escape(desc)}</description>\n"
                f"      <pubDate>{pub_str}</pubDate>\n"
                f"      <guid isPermaLink=\"true\">{link}</guid>\n"
                f"    </item>"
            )
        subject_name = subject_slug.replace("-", " ").title()
        now_str = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
        xml_content = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n'
            '  <channel>\n'
            f'    <title>Syrabit.ai - {_xml_escape(subject_name)}</title>\n'
            f'    <link>{BASE_URL}</link>\n'
            f'    <description>{_xml_escape(subject_name)} study notes and exam prep for Assam Board</description>\n'
            f'    <language>en-in</language>\n'
            f'    <lastBuildDate>{now_str}</lastBuildDate>\n'
            f'    <atom:link href="{BASE_URL}/feed/{subject_slug}.xml" rel="self" type="application/rss+xml" />\n'
            + "\n".join(items_xml) + "\n"
            '  </channel>\n'
            '</rss>'
        )
        _set_cached_sitemap(cache_key, xml_content)
        return Response(content=xml_content, media_type="application/rss+xml")
    except Exception as e:
        logger.warning(f"Failed to generate subject RSS feed for {subject_slug}: {e}")
        fallback = '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel><title>Syrabit.ai</title></channel></rss>'
        return Response(content=fallback, media_type="application/rss+xml")


@router.get("/feed.json")
async def feed_json():
    """JSON Feed 1.1 of recently published content."""
    cached = _get_cached_sitemap("feed_json")
    if cached:
        return Response(content=cached, media_type="application/feed+json")
    try:
        objects = (
            await KnowledgeObject.find({"status": "published"})
            .sort("-updated_at")
            .limit(50)
            .project({"slug": 1, "title": 1, "description": 1, "metadata": 1, "updated_at": 1, "published_at": 1})
            .to_list()
        )
        items = []
        for obj in objects:
            title = obj.get("title", "")
            desc = obj.get("description", "")[:500]
            meta = obj.get("metadata", {})
            link = f"{BASE_URL}/render/{meta.get('board', '')}/{meta.get('class_level', '')}/{meta.get('subject', '')}/{meta.get('chapter', '')}/notes"
            pub_date = obj.get("published_at") or obj.get("updated_at")
            mod_date = obj.get("updated_at")
            item = {
                "id": link,
                "url": link,
                "title": title,
                "content_text": desc,
                "tags": [kw for kw in meta.get("keywords", []) if kw],
            }
            if pub_date:
                item["date_published"] = pub_date.isoformat() if isinstance(pub_date, datetime) else str(pub_date)
            if mod_date:
                item["date_modified"] = mod_date.isoformat() if isinstance(mod_date, datetime) else str(mod_date)
            items.append(item)

        feed = {
            "version": "https://jsonfeed.org/version/1.1",
            "title": "Syrabit.ai - Study Notes & Exam Prep",
            "home_page_url": BASE_URL,
            "feed_url": f"{BASE_URL}/api/v1/seo/feed.json",
            "description": "Latest study notes, definitions, and exam prep for Assam Board students",
            "language": "en-IN",
            "items": items,
        }
        content = json.dumps(feed, ensure_ascii=False)
        _set_cached_sitemap("feed_json", content)
        return Response(content=content, media_type="application/feed+json")
    except Exception as e:
        logger.warning(f"Failed to generate JSON feed: {e}")
        fallback = json.dumps({"version": "https://jsonfeed.org/version/1.1", "title": "Syrabit.ai", "items": []})
        return Response(content=fallback, media_type="application/feed+json")
