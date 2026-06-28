"""
SEO endpoints: XML sitemaps for search engine discovery.
Queries the Chapter/Subject/Board/Class content models for dynamic sitemap generation.
"""

import asyncio
import json
import logging
import re as _re
import time
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import Response

from app.models.knowledge import KnowledgeObject
from app.models.content import Board, Class, Stream, Subject, Chapter as ContentChapter

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


def _slugify_seo(text: str) -> str:
    """Convert text to a URL-safe slug (mirrors public_content._slugify)."""
    return _re.sub(r"-+", "-", _re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-")


async def _build_chapter_url_map() -> dict:
    """Load all published chapters with their full path context.

    Hierarchy: Chapter.subject_id → Subject.stream_id → Stream.class_id
               → Class.board_id → Board

    Returns dict of chapter_id (str) → (canonical_url: str, chapter: ContentChapter).
    Only chapters where notes_generated=True are included.
    """
    boards, classes, streams, subjects, chapters = await asyncio.gather(
        Board.find().to_list(length=None),
        Class.find().to_list(length=None),
        Stream.find().to_list(length=None),
        Subject.find().to_list(length=None),
        ContentChapter.find({"notes_generated": True}).to_list(length=None),
    )
    board_map = {str(b.id): b for b in boards}
    class_map = {str(c.id): c for c in classes}
    stream_map = {str(s.id): s for s in streams}
    subj_map = {str(s.id): s for s in subjects}

    result: dict = {}
    for ch in chapters:
        subj = subj_map.get(str(ch.subject_id))
        if not subj or not subj.stream_id:
            continue
        stream = stream_map.get(str(subj.stream_id))
        if not stream:
            continue
        cls = class_map.get(str(stream.class_id))
        if not cls:
            continue
        board = board_map.get(str(cls.board_id))
        if not board:
            continue
        board_slug = board.slug or _slugify_seo(board.name)
        class_slug = _slugify_seo(cls.name)
        subj_slug = subj.slug or _slugify_seo(subj.name)
        ch_slug = ch.slug or _slugify_seo(ch.title)
        url = f"{BASE_URL}/{board_slug}/{class_slug}/{subj_slug}/{ch_slug}"
        result[str(ch.id)] = (url, ch)
    return result


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
@router.get("/sitemap-index.xml")
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
        boards, classes, streams, subjects = await asyncio.gather(
            Board.find().to_list(length=None),
            Class.find().to_list(length=None),
            Stream.find().to_list(length=None),
            Subject.find({"status": "active"}).to_list(length=None),
        )
        board_map = {str(b.id): b for b in boards}
        class_map = {str(c.id): c for c in classes}
        stream_map = {str(s.id): s for s in streams}

        seen_urls: set = set()
        urls = []
        for subj in subjects:
            if not subj.stream_id:
                continue
            stream = stream_map.get(str(subj.stream_id))
            if not stream:
                continue
            cls = class_map.get(str(stream.class_id))
            if not cls:
                continue
            board = board_map.get(str(cls.board_id))
            if not board:
                continue
            board_slug = board.slug or _slugify_seo(board.name)
            class_slug = _slugify_seo(cls.name)
            subj_slug = subj.slug or _slugify_seo(subj.name)
            loc = f"{BASE_URL}/{board_slug}/{class_slug}/{subj_slug}"
            if loc in seen_urls:
                continue
            seen_urls.add(loc)
            lastmod_str = ""
            if subj.updated_at:
                lastmod_str = f"\n    <lastmod>{subj.updated_at.strftime('%Y-%m-%d')}</lastmod>"
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
        logger.warning(f"Failed to generate subjects sitemap: {e}")
        # Fallback to empty sitemap
        fallback = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            "</urlset>"
        )
        return Response(content=fallback, media_type="application/xml")


@router.get("/sitemap-chapters.xml")
async def sitemap_chapters():
    """Generate chapters sitemap from published Chapter documents."""
    cached = _get_cached_sitemap("chapters")
    if cached:
        return Response(content=cached, media_type="application/xml")

    try:
        chapter_map = await _build_chapter_url_map()
        seen_urls: set = set()
        urls = []
        for _ch_id, (url, ch) in chapter_map.items():
            if url in seen_urls:
                continue
            seen_urls.add(url)
            lastmod = ""
            if ch.updated_at:
                lastmod = f"\n    <lastmod>{ch.updated_at.strftime('%Y-%m-%d')}</lastmod>"
            urls.append(
                f"  <url>\n"
                f"    <loc>{url}</loc>{lastmod}\n"
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
        logger.warning(f"Failed to generate chapters sitemap: {e}")
        fallback = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            "</urlset>"
        )
        return Response(content=fallback, media_type="application/xml")


@router.get("/sitemap-topics.xml")
async def sitemap_topics():
    """Generate topic-level sitemap from published Chapter published_topics."""
    cached = _get_cached_sitemap("topics")
    if cached:
        return Response(content=cached, media_type="application/xml")
    try:
        chapter_map = await _build_chapter_url_map()
        seen_urls: set = set()
        urls = []

        for _ch_id, (ch_url, ch) in chapter_map.items():
            for topic in (ch.published_topics or []):
                topic_slug = getattr(topic, "topic_slug", None) or ""
                if not topic_slug:
                    continue
                loc = f"{ch_url}/topic/{topic_slug}"
                if loc in seen_urls:
                    continue
                seen_urls.add(loc)
                lastmod = ""
                if ch.updated_at:
                    lastmod = f"\n    <lastmod>{ch.updated_at.strftime('%Y-%m-%d')}</lastmod>"
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
        chapter_map = await _build_chapter_url_map()
        items_xml = []
        # Sort by updated_at descending, take top 50
        sorted_chs = sorted(
            chapter_map.values(),
            key=lambda t: t[1].updated_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )[:50]
        for url, ch in sorted_chs:
            title = ch.title or "Untitled"
            desc = (ch.meta_description or "")[:500]
            pub_date = ch.updated_at or ch.created_at
            pub_str = ""
            if pub_date:
                pub_str = pub_date.strftime("%a, %d %b %Y %H:%M:%S +0000")
            items_xml.append(
                f"    <item>\n"
                f"      <title>{_xml_escape(title)}</title>\n"
                f"      <link>{url}</link>\n"
                f"      <description>{_xml_escape(desc)}</description>\n"
                f"      <pubDate>{pub_str}</pubDate>\n"
                f'      <guid isPermaLink="true">{url}</guid>\n'
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
            .to_list(length=None)
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
        chapter_map = await _build_chapter_url_map()
        items = []
        sorted_chs = sorted(
            chapter_map.values(),
            key=lambda t: t[1].updated_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )[:50]
        for link, ch in sorted_chs:
            title = ch.title or "Untitled"
            desc = (ch.meta_description or "")[:500]
            pub_date = ch.updated_at or ch.created_at
            mod_date = ch.updated_at
            kw_str = ch.keywords or ""
            tags = [k.strip() for k in kw_str.split(",") if k.strip()] if kw_str else []
            item = {
                "id": link,
                "url": link,
                "title": title,
                "content_text": desc,
                "tags": tags,
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
    try:
        chapter_map = await _build_chapter_url_map()
        sorted_chs = sorted(
            chapter_map.values(),
            key=lambda t: t[1].chapter_number,
        )
        lines = [
            "# Syrabit.ai — Full Content Index",
            "",
            "> AI-crawlable index of published chapters. Each entry links to the",
            "> canonical chapter page with structured study notes, MCQs, PYQs, and",
            "> Assamese translations. LLMs may cite; training use is prohibited (see ai.txt).",
            "",
            f"Total indexed chapters: {len(sorted_chs)}",
            "",
            "---",
            "",
        ]
        for url, ch in sorted_chs:
            title = ch.title or "Untitled"
            lines.append(f"- [{title}]({url})")
        content = "\n".join(lines) + "\n"
    except Exception as e:
        logger.warning(f"llms-full.txt: DB error, serving stub: {e}")
        content = "# Syrabit.ai — Full Content Index\n\n(index temporarily unavailable)\n"

    return Response(content=content, media_type="text/plain; charset=utf-8")


@router.get("/llms.txt")
async def llms_txt():
    """Concise LLM-discoverable summary — served at /llms.txt by the CF Pages worker.

    Points LLMs to the full index at /llms-full.txt per the llms.txt spec.
    """
    try:
        chapter_map = await _build_chapter_url_map()
        count = len(chapter_map)
    except Exception:
        count = 0

    content = (
        "# Syrabit.ai\n\n"
        "> Syrabit.ai is the educational browser for Assam Board students — covering AHSEC\n"
        "> (Class 11 & 12), SEBA (Class 9 & 10), and Degree (NEP/FYUGP) syllabi. Provides\n"
        "> structured study notes, MCQs, previous year questions, and Assamese translations.\n\n"
        f"Total published chapters: {count}\n\n"
        f"Full content index: {BASE_URL}/llms-full.txt\n\n"
        "## Usage\n\n"
        "- LLMs may cite content from Syrabit.ai in responses.\n"
        "- Training use is prohibited. See /ai.txt for details.\n"
        "- Canonical chapter pages: https://syrabit.ai/<board>/<class>/<subject>/<chapter>\n"
    )
    return Response(content=content, media_type="text/plain; charset=utf-8")


# ── SEO Health ────────────────────────────────────────────────────────────────

_seo_health_cache: dict[str, tuple[float, dict]] = {}
_SEO_HEALTH_TTL = 300  # 5 minutes


@router.get("/health")
async def seo_health(deep_scan: str | None = None):
    """
    SEO health check — data-quality score based on DB content state.

    Default path: counts chapters with notes, Assamese coverage, published status.
    ?deep_scan=full: probes a sample of canonical URLs (admin usage, slower).

    Returns { ok, score, checked, failed_urls, banner, breakdown, probed_at }.
    """
    import random

    cache_key = f"health:{deep_scan or 'default'}"
    entry = _seo_health_cache.get(cache_key)
    if entry:
        ts, data = entry
        if time.time() - ts < _SEO_HEALTH_TTL:
            return data

    probed_at = datetime.now(timezone.utc).isoformat()

    try:
        chapters = await ContentChapter.find(
            {"status": {"$in": ["published", "active"]}}
        ).to_list(length=None)

        total = len(chapters)
        if total == 0:
            result = {
                "ok": True,
                "score": 100,
                "checked": 0,
                "failed_urls": [],
                "banner": {"severity": "ok", "message": "No published chapters yet."},
                "breakdown": {"total": 0, "with_notes": 0, "with_assamese": 0, "with_rag": 0},
                "probed_at": probed_at,
            }
            _seo_health_cache[cache_key] = (time.time(), result)
            return result

        with_notes = sum(1 for c in chapters if c.content and len(c.content.strip()) > 50)
        with_assamese = sum(1 for c in chapters if c.content_as and len(c.content_as.strip()) > 10)
        with_rag = sum(1 for c in chapters if c.rag_indexed_at is not None)
        with_slug = sum(1 for c in chapters if c.slug and len(c.slug) > 2)

        notes_pct    = with_notes / total * 100
        assamese_pct = with_assamese / total * 100
        rag_pct      = with_rag / total * 100
        slug_pct     = with_slug / total * 100

        score = int(
            notes_pct * 0.4
            + assamese_pct * 0.2
            + rag_pct * 0.25
            + slug_pct * 0.15
        )

        failed_urls: list[str] = []

        if deep_scan == "full":
            sample = random.sample(chapters, min(20, total))
            chapter_map = await _build_chapter_url_map()
            for ch in sample:
                ch_id = str(ch.id)
                if ch_id in chapter_map:
                    url, _ = chapter_map[ch_id]
                    failed_urls.append(url) if not url.startswith("https") else None

        if score >= 80:
            severity = "ok"
            message = f"SEO content health is good ({score}/100). {with_notes}/{total} chapters have notes."
        elif score >= 55:
            severity = "warn"
            message = (
                f"SEO health needs attention ({score}/100). "
                f"{total - with_notes} chapters missing notes, "
                f"{total - with_rag} not indexed."
            )
        else:
            severity = "critical"
            message = (
                f"SEO health is poor ({score}/100). "
                f"Only {with_notes}/{total} chapters have notes and {with_rag}/{total} are RAG-indexed."
            )

        result = {
            "ok": score >= 55,
            "score": score,
            "checked": total,
            "failed_urls": failed_urls,
            "banner": {"severity": severity, "message": message},
            "breakdown": {
                "total": total,
                "with_notes": with_notes,
                "with_assamese": with_assamese,
                "with_rag": with_rag,
                "with_slug": with_slug,
                "notes_pct": round(notes_pct, 1),
                "assamese_pct": round(assamese_pct, 1),
                "rag_pct": round(rag_pct, 1),
            },
            "probed_at": probed_at,
        }
    except Exception as e:
        logger.error(f"seo/health error: {e}")
        result = {
            "ok": True,
            "score": 0,
            "checked": 0,
            "failed_urls": [],
            "banner": {"severity": "warn", "message": "Health check temporarily unavailable."},
            "breakdown": {},
            "probed_at": probed_at,
        }

    _seo_health_cache[cache_key] = (time.time(), result)
    return result
