"""
SEO endpoints: XML sitemaps for search engine discovery.
Queries KnowledgeObject collection for dynamic sitemap generation.
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import Response

from app.models.knowledge import KnowledgeObject

logger = logging.getLogger(__name__)

router = APIRouter()

BASE_URL = "https://syrabit.ai"

_sitemap_cache: dict[str, tuple[float, str]] = {}
SITEMAP_CACHE_TTL = 600  # 10 minutes
_sitemap_cache_lock = asyncio.Lock()


def _get_cached_sitemap(key: str) -> str | None:
    """Non-blocking read; Lock only needed on write."""
    entry = _sitemap_cache.get(key)
    if entry:
        ts, content = entry
        if time.time() - ts < SITEMAP_CACHE_TTL:
            return content
    return None


async def _set_cached_sitemap(key: str, content: str) -> None:
    """Lock on write to prevent concurrent updates causing RuntimeError."""
    async with _sitemap_cache_lock:
        _sitemap_cache[key] = (time.time(), content)


def _xml_escape(text: str) -> str:
    """Escape special XML characters."""
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


SITEMAP_INDEX_XML = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap>
    <loc>{base_url}/sitemap-static.xml</loc>
    <lastmod>{today}</lastmod>
  </sitemap>
  <sitemap>
    <loc>{base_url}/sitemap-subjects.xml</loc>
    <lastmod>{today}</lastmod>
  </sitemap>
  <sitemap>
    <loc>{base_url}/sitemap-chapters.xml</loc>
    <lastmod>{today}</lastmod>
  </sitemap>
  <sitemap>
    <loc>{base_url}/sitemap-topics.xml</loc>
    <lastmod>{today}</lastmod>
  </sitemap>
</sitemapindex>"""

SITEMAP_STATIC_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://syrabit.ai/</loc>
    <lastmod>2026-06-07</lastmod>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>https://syrabit.ai/library</loc>
    <lastmod>2026-06-07</lastmod>
    <changefreq>daily</changefreq>
    <priority>0.9</priority>
  </url>
  <url>
    <loc>https://syrabit.ai/chat</loc>
    <lastmod>2026-06-07</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
  </url>
  <url>
    <loc>https://syrabit.ai/pricing</loc>
    <lastmod>2026-06-07</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.7</priority>
  </url>
  <url>
    <loc>https://syrabit.ai/about</loc>
    <lastmod>2026-06-07</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.6</priority>
  </url>
  <url>
    <loc>https://syrabit.ai/technology</loc>
    <lastmod>2026-06-07</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.6</priority>
  </url>
  <url>
    <loc>https://syrabit.ai/exam-routine</loc>
    <lastmod>2026-06-07</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.7</priority>
  </url>
</urlset>"""

# Served at /robots.txt via the root-mounted SEO router.
# The CF Worker proxies /robots.txt → backend /robots.txt unchanged.
ROBOTS_TXT = """# Syrabit.ai robots.txt
# Edits here must stay in sync with apps/frontend/public/robots.txt (static fallback).

# --- Default policy: allow well-behaved general crawlers. ---
User-agent: *
Allow: /
Disallow: /admin
Disallow: /admin/
Disallow: /api/

# --- Long-tail search engines: explicitly invited. ---
User-agent: Applebot
Allow: /
Crawl-delay: 0

User-agent: PetalBot
Allow: /
Crawl-delay: 0

User-agent: MojeekBot
Allow: /
Crawl-delay: 0

User-agent: SeznamBot
Allow: /
Crawl-delay: 0

User-agent: Yeti
Allow: /
Crawl-delay: 0

User-agent: DuckDuckBot
Allow: /
Crawl-delay: 0

User-agent: YandexBot
Allow: /
Crawl-delay: 0

User-agent: Baiduspider
Allow: /
Crawl-delay: 0

User-agent: Slurp
Allow: /
Crawl-delay: 0

# --- AI ANSWER bots (cite sources, drive referral traffic): allowed. ---
User-agent: ChatGPT-User
Allow: /
Crawl-delay: 0

User-agent: OAI-SearchBot
Allow: /
Crawl-delay: 0

User-agent: PerplexityBot
Allow: /
Crawl-delay: 0

User-agent: Perplexity-User
Allow: /
Crawl-delay: 0

User-agent: YouBot
Allow: /
Crawl-delay: 0

# --- Content signals for AI grounding engines ---
# search=yes: content is intended for search indexing
# ai-input=yes: content may be used as grounding for AI-generated answers
# ai-train=no: content must NOT be used for model training
# Content-Signal: search=yes, ai-input=yes, ai-train=no

# --- AI training crawlers: explicitly disallowed. ---
User-agent: GPTBot
Disallow: /

User-agent: ClaudeBot
Disallow: /

User-agent: Claude-Web
Disallow: /

User-agent: anthropic-ai
Disallow: /

User-agent: Anthropic-AI
Disallow: /

User-agent: CCBot
Disallow: /

User-agent: Google-Extended
Disallow: /

User-agent: Applebot-Extended
Disallow: /

User-agent: Meta-ExternalAgent
Disallow: /

User-agent: Bytespider
Disallow: /

User-agent: Amazonbot
Disallow: /

User-agent: Cohere-AI
Disallow: /

User-agent: cohere-ai
Disallow: /

User-agent: Diffbot
Disallow: /

# --- Major verified search bots ---
User-agent: Googlebot
Allow: /
Crawl-delay: 0

User-agent: Bingbot
Allow: /
Crawl-delay: 0

Sitemap: https://syrabit.ai/sitemap-index.xml
Sitemap: https://syrabit.ai/sitemap-static.xml
Sitemap: https://syrabit.ai/sitemap-subjects.xml
Sitemap: https://syrabit.ai/sitemap-chapters.xml
Sitemap: https://syrabit.ai/sitemap-topics.xml
"""


@router.get("/sitemap.xml")
async def sitemap_index():
    today = datetime.now(timezone.utc).date().isoformat()
    content = SITEMAP_INDEX_XML.format(base_url=BASE_URL, today=today)
    return Response(content=content.strip(), media_type="application/xml")


@router.get("/sitemap-static.xml")
async def sitemap_static():
    today = datetime.now(timezone.utc).date().isoformat()
    content = SITEMAP_STATIC_XML.replace("2026-06-07", today)
    return Response(content=content.strip(), media_type="application/xml")


@router.get("/robots.txt")
async def robots_txt():
    """Serve robots.txt. The CF Worker proxies /robots.txt → this endpoint."""
    return Response(content=ROBOTS_TXT.strip(), media_type="text/plain; charset=utf-8")


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
        await _set_cached_sitemap("subjects", xml_content)
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
        await _set_cached_sitemap("chapters", xml_content)
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
    """Generate topic-level sitemap from published knowledge objects with topic metadata.

    Uses KnowledgeObject (which has full board/class/subject/chapter hierarchy)
    to produce proper routable URLs. Google does not process #fragment URLs in
    sitemaps, so we use the chapter notes path instead.
    """
    cached = _get_cached_sitemap("topics")
    if cached:
        return Response(content=cached, media_type="application/xml")
    try:
        # Query KnowledgeObjects that have a topic field set
        objects = (
            await KnowledgeObject.find(
                {"status": "published", "metadata.topic": {"$ne": None}}
            )
            .project(
                {
                    "metadata.board": 1,
                    "metadata.class_level": 1,
                    "metadata.subject": 1,
                    "metadata.chapter": 1,
                    "metadata.topic": 1,
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
            topic = meta.get("topic", "")

            if not all([board, class_level, subject, chapter, topic]):
                continue

            key = f"{board}/{class_level}/{subject}/{chapter}/{topic}"
            if key in seen:
                continue
            seen.add(key)

            updated = obj.get("updated_at")
            lastmod = ""
            if updated:
                if isinstance(updated, datetime):
                    lastmod = f"\n    <lastmod>{updated.strftime('%Y-%m-%d')}</lastmod>"
                elif isinstance(updated, str):
                    lastmod = f"\n    <lastmod>{updated[:10]}</lastmod>"

            # Use the chapter notes URL without fragment - Google ignores fragments
            # in sitemaps but the page renders all topic content
            loc = f"{BASE_URL}/render/{board}/{class_level}/{subject}/{chapter}/notes"
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
        await _set_cached_sitemap("topics", xml_content)
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
            .project(
                {
                    "slug": 1,
                    "title": 1,
                    "description": 1,
                    "metadata": 1,
                    "updated_at": 1,
                    "published_at": 1,
                }
            )
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
                f'      <guid isPermaLink="true">{link}</guid>\n'
                f"    </item>"
            )
        now_str = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
        xml_content = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n'
            "  <channel>\n"
            f"    <title>Syrabit.ai - Study Notes &amp; Exam Prep</title>\n"
            f"    <link>{BASE_URL}</link>\n"
            f"    <description>Latest study notes, definitions, and exam prep for Assam Board students</description>\n"
            f"    <language>en-in</language>\n"
            f"    <lastBuildDate>{now_str}</lastBuildDate>\n"
            f'    <atom:link href="{BASE_URL}/feed.xml" rel="self" type="application/rss+xml" />\n'
            + "\n".join(items_xml)
            + "\n"
            "  </channel>\n"
            "</rss>"
        )
        await _set_cached_sitemap("feed_xml", xml_content)
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
            await KnowledgeObject.find(
                {"status": "published", "metadata.subject": subject_slug}
            )
            .sort("-updated_at")
            .limit(50)
            .project(
                {
                    "slug": 1,
                    "title": 1,
                    "description": 1,
                    "metadata": 1,
                    "updated_at": 1,
                    "published_at": 1,
                }
            )
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
                f'      <guid isPermaLink="true">{link}</guid>\n'
                f"    </item>"
            )
        subject_name = subject_slug.replace("-", " ").title()
        now_str = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
        xml_content = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n'
            "  <channel>\n"
            f"    <title>Syrabit.ai - {_xml_escape(subject_name)}</title>\n"
            f"    <link>{BASE_URL}</link>\n"
            f"    <description>{_xml_escape(subject_name)} study notes and exam prep for Assam Board</description>\n"
            f"    <language>en-in</language>\n"
            f"    <lastBuildDate>{now_str}</lastBuildDate>\n"
            f'    <atom:link href="{BASE_URL}/feed/{subject_slug}.xml" rel="self" type="application/rss+xml" />\n'
            + "\n".join(items_xml)
            + "\n"
            "  </channel>\n"
            "</rss>"
        )
        await _set_cached_sitemap(cache_key, xml_content)
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
            .project(
                {
                    "slug": 1,
                    "title": 1,
                    "description": 1,
                    "metadata": 1,
                    "updated_at": 1,
                    "published_at": 1,
                }
            )
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
                item["date_published"] = (
                    pub_date.isoformat()
                    if isinstance(pub_date, datetime)
                    else str(pub_date)
                )
            if mod_date:
                item["date_modified"] = (
                    mod_date.isoformat()
                    if isinstance(mod_date, datetime)
                    else str(mod_date)
                )
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
        await _set_cached_sitemap("feed_json", content)
        return Response(content=content, media_type="application/feed+json")
    except Exception as e:
        logger.warning(f"Failed to generate JSON feed: {e}")
        fallback = json.dumps(
            {
                "version": "https://jsonfeed.org/version/1.1",
                "title": "Syrabit.ai",
                "items": [],
            }
        )
        return Response(content=fallback, media_type="application/feed+json")


@router.get("/llms-full.txt")
async def llms_full_txt():
    """Extended LLM-discoverable text — full structured content index.
    Served at /llms-full.txt via the CF Worker SEO passthrough proxy."""
    from app.models.knowledge import KnowledgeObject

    try:
        docs = await KnowledgeObject.find(
            {"status": "published"},
            projection={"title": 1, "subject": 1, "board": 1, "class_name": 1, "slug": 1},
        ).limit(500).to_list()
        lines = [
            "# Syrabit.ai — Full Content Index",
            "",
            "> AI-crawlable index of published chapters. Each entry links to the",
            "> canonical chapter page with structured study notes, MCQs, PYQs, and",
            "> Assamese translations. LLMs may cite; training use is prohibited (see ai.txt).",
            "",
            f"Total indexed chapters: {len(docs)}",
            "",
            "---",
            "",
        ]
        for doc in docs:
            slug = doc.slug or ""
            title = doc.title or "Untitled"
            board = getattr(doc, "board", "") or ""
            cls = getattr(doc, "class_name", "") or ""
            subj = getattr(doc, "subject", "") or ""
            if slug:
                lines.append(f"- [{title}](https://syrabit.ai/{slug}) — {board} {cls} {subj}".strip())
            else:
                lines.append(f"- {title} — {board} {cls} {subj}".strip())
        content = "\n".join(lines) + "\n"
    except Exception as e:
        logger.warning(f"llms-full.txt: DB error, serving stub: {e}")
        content = "# Syrabit.ai — Full Content Index\n\n(index temporarily unavailable)\n"

    return Response(content=content, media_type="text/plain; charset=utf-8")
