"""Task #11 — Programmatic SEO/GEO/AEO engine.

Renders the seven canonical page-types per chapter at
``/board/{board}/class/{class_slug}/subject/{subject_slug}/chapter/{chapter_slug}/{page_type}``:

    notes, mcqs, flashcards, pyqs, summary, definitions, revision

Every rendered page carries:
  * <h1> = chapter topic (e.g. "Photosynthesis — Class 11 Biology AHSEC notes")
  * <h2> per sub-topic (sourced from the syllabus graph: ``db.topics``)
  * <meta name="description"> ≤ 160 chars including the chapter topic
  * <link rel="alternate" hreflang="as-IN"> + hreflang="en-IN" + x-default
  * <meta name="geo.region" content="IN-AS"> + geo.placename + geo.position + ICBM
  * JSON-LD ``LearningResource`` + ``Course`` + ``BreadcrumbList`` + ``FAQPage``;
    ``/mcqs`` and ``/pyqs`` additionally emit ``Quiz``
  * a 40–60-word "Quick answer" paragraph at the top of <main> — the
    AEO/GEO unit. Pulled from Task #12's materialised
    ``aeo_quick_answers`` collection when present; falls back to a
    deterministic stub during the rollout window.

Out of scope (Task #12 / Task #6):
  * Generating the actual textual chapter content.
  * Generating FAQ Q&A pairs (Task #12 owns ``aeo_faq_entries``).

The rendered HTML is intentionally lightweight (no JS, no SPA shell)
because these URLs are crawl-bait — they exist to feed Googlebot,
PerplexityBot, OAI-SearchBot, and the sitemap submission pipeline.
The SPA continues to serve the rich interactive view at the existing
``/{board}/{class}/{subject}/{chapter}`` URL.
"""
from __future__ import annotations

import html as _html
import json
import logging
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pathlib import Path as _Path

from deps import db

# Jinja2 environment — templates live under
# ``artifacts/syrabit-backend/templates/seo/`` so the renderer is
# decoupled from the Python f-string contract and can be
# template-evolved without touching route code (Task #11 spec, step 1).
_TEMPLATE_DIR = _Path(__file__).resolve().parent.parent / "templates" / "seo"
_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "xml", "j2"]),
    trim_blocks=False,
    lstrip_blocks=False,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["seo-pages"])

BASE_URL = "https://syrabit.ai"

# Canonical 7 page-types from spec §10.
PAGE_TYPES: List[str] = [
    "notes",
    "mcqs",
    "flashcards",
    "pyqs",
    "summary",
    "definitions",
    "revision",
]

# Per-type human label used in <h1> / <title> / Quick Answer body.
TYPE_LABEL: Dict[str, str] = {
    "notes":       "notes",
    "mcqs":        "MCQs",
    "flashcards":  "flashcards",
    "pyqs":        "previous-year questions",
    "summary":     "summary",
    "definitions": "definitions",
    "revision":    "revision",
}

# Page-types that emit a `Quiz` JSON-LD block.
QUIZ_TYPES = {"mcqs", "pyqs"}

# Mapping a Task #11 page-type to its underlying ``seo_pages.page_type``
# row in MongoDB. Only used when materialised content is available;
# missing rows still render the H1/H2/Quick-Answer/JSON-LD scaffold so
# the URL is indexable from day one.
PAGE_TYPE_TO_DB: Dict[str, str] = {
    "notes":       "notes",
    "mcqs":        "mcqs",
    "pyqs":        "important-questions",
    "definitions": "definition",
    "summary":     "notes",       # summary derived from notes content
    "flashcards":  "flashcard",
    "revision":    "notes",       # revision packs derived from notes
}

GEO_METAS = (
    '<meta name="geo.region" content="IN-AS"/>'
    '<meta name="geo.placename" content="Assam, India"/>'
    '<meta name="geo.position" content="26.2006;92.9376"/>'
    '<meta name="ICBM" content="26.2006, 92.9376"/>'
)


def _e(s: object) -> str:
    """Shorthand HTML escape that tolerates None / non-string."""
    return _html.escape("" if s is None else str(s), quote=True)


def _truncate_meta(text: str, *, limit: int = 160) -> str:
    """Trim ``text`` to ≤ ``limit`` chars without splitting a word."""
    t = re.sub(r"\s+", " ", (text or "")).strip()
    if len(t) <= limit:
        return t
    cut = t[: limit - 1].rsplit(" ", 1)[0].rstrip(",.;:")
    return f"{cut}…"


def _quick_answer_stub(chapter_title: str, page_type: str,
                       subject: str, board: str, class_name: str) -> str:
    """40–60-word AEO/GEO answer block.

    Falls back to a deterministic, syllabus-grounded stub when Task #12's
    ``aeo_quick_answers`` collection has no row for this chapter+type.
    The wording is deliberately uniform so Google AI Overviews / Bing
    Copilot / Perplexity can identify the answer-card boundaries even
    before the LLM-materialised version lands.
    """
    label = TYPE_LABEL.get(page_type, page_type)
    body = (
        f"This page presents the {label} for {chapter_title}, prepared for "
        f"{class_name} {subject} students of the {board} curriculum in Assam. "
        f"It covers every sub-topic listed in the official syllabus, with "
        f"definitions, worked examples and exam-ready revision points so "
        f"students can master {chapter_title} ahead of board exams."
    )
    words = body.split()
    if len(words) > 60:
        body = " ".join(words[:60]).rstrip(",.;:") + "."
    elif len(words) < 40:
        body = body + (
            f" Each sub-topic is cross-linked to the matching {board} "
            f"{class_name} {subject} chapter so students can revise quickly."
        )
        words = body.split()
        if len(words) > 60:
            body = " ".join(words[:60]).rstrip(",.;:") + "."
    return body


async def _resolve_chapter(board: str, class_slug: str, subject_slug: str,
                           chapter_slug: str) -> Optional[Dict]:
    """Resolve a (board, class, subject, chapter) chain via Mongo.

    Returns ``None`` when any link is missing — the route raises 404
    for the caller. Defensive against missing collections so the
    crawler routes never 500 in environments where the syllabus
    backfill hasn't finished.
    """
    try:
        brd = await db.boards.find_one({"slug": board}, {"_id": 0, "id": 1, "name": 1})
        if not brd:
            return None
        cls = await db.classes.find_one(
            {"slug": class_slug, "board_id": brd.get("id", "")},
            {"_id": 0, "id": 1, "name": 1},
        )
        if not cls:
            return None
        # Subjects can reference a class either directly via `class_id` or
        # indirectly via a stream (`stream_id -> stream.class_id`). We have
        # to walk every published subject with the requested slug and keep
        # the one whose chain matches the resolved (board, class). Without
        # this guard a subject slug shared across classes (e.g. "biology"
        # under both Class 11 and Class 12) would silently resolve to the
        # wrong chapter.
        cand_subjects = await db.subjects.find(
            {"slug": subject_slug, "status": "published"},
            {"_id": 0, "id": 1, "name": 1, "stream_id": 1,
             "class_id": 1, "board_id": 1},
        ).to_list(50)
        subj = None
        for cand in cand_subjects:
            if cand.get("class_id") == cls.get("id"):
                subj = cand
                break
            stream_id = cand.get("stream_id")
            if not stream_id:
                continue
            stream = await db.streams.find_one(
                {"id": stream_id}, {"_id": 0, "class_id": 1},
            )
            if stream and stream.get("class_id") == cls.get("id"):
                subj = cand
                break
        if not subj:
            return None
        # Optional board sanity check — when the subject row pins a board,
        # it must agree with the resolved board.
        if subj.get("board_id") and subj.get("board_id") != brd.get("id"):
            return None
        chap = await db.chapters.find_one(
            {"slug": chapter_slug, "subject_id": subj.get("id", "")},
            {"_id": 0, "id": 1, "slug": 1, "title": 1, "title_as": 1,
             "description": 1, "description_as": 1, "updated_at": 1,
             "created_at": 1, "content_as": 1},
        )
        if not chap:
            return None
        return {
            "board":   {"slug": board, "name": brd.get("name", board)},
            "class":   {"slug": class_slug, "name": cls.get("name", class_slug)},
            "subject": {"slug": subject_slug, "name": subj.get("name", subject_slug)},
            "chapter": chap,
        }
    except Exception as exc:
        logger.warning("seo_pages._resolve_chapter failed: %s", exc)
        return None


async def _load_subtopics(chapter_id: str) -> List[Dict]:
    """Load published topics for a chapter, ordered by ``order``."""
    try:
        rows = await db.topics.find(
            {"chapter_id": chapter_id, "status": "published"},
            {"_id": 0, "title": 1, "slug": 1, "summary": 1},
        ).sort("order", 1).to_list(200)
        return [r for r in rows if r.get("title")]
    except Exception:
        return []


async def _load_quick_answer(chapter_id: str, page_type: str) -> Optional[str]:
    """Pull a Task-#12-materialised AEO answer card if present."""
    try:
        row = await db.aeo_quick_answers.find_one(
            {"chapter_id": chapter_id, "page_type": page_type},
            {"_id": 0, "answer": 1},
        )
        if row and (row.get("answer") or "").strip():
            return row["answer"].strip()
    except Exception:
        pass
    return None


async def _load_keyword_expansion(chapter_id: str, chapter_title: str,
                                  subject_name: str, board_name: str,
                                  class_name: str, subtopics: List[Dict]
                                  ) -> List[str]:
    """Build the chapter-topic → keyword expansion (Task #11 step 2).

    Combines two grounded sources:
      1. Sub-topic titles from the syllabus graph (`db.topics`).
      2. Question stems from the PYQ corpus (`db.pyq_html_pages`)
         filtered to the chapter's subject — this is what real students
         have asked in past board exams, so it doubles as the
         keyword-planner-style expansion that ranks on long-tail
         "<chapter> previous-year <type>" queries.

    The output is deduped, lowercased for comparison but returned in the
    original casing, capped at 12 entries (so the HTML stays under
    Lighthouse's "DOM size" budget).
    """
    expanded: List[str] = []
    seen: set = set()

    def _push(term: str) -> None:
        t = re.sub(r"\s+", " ", (term or "")).strip()
        if not t or len(t) < 4 or len(t) > 120:
            return
        key = t.lower()
        if key in seen:
            return
        seen.add(key)
        expanded.append(t)

    # 1. Syllabus sub-topics → "<chapter> <subtopic>" formed phrases.
    for st in subtopics:
        title = (st.get("title") or "").strip()
        if title:
            _push(f"{chapter_title} {title}")

    # 2. PYQ corpus → top recent question stems for this chapter.
    try:
        rows = await db.pyq_html_pages.find(
            {"chapter_id": chapter_id},
            {"_id": 0, "title": 1, "question_stem": 1, "year": 1},
        ).sort("year", -1).to_list(20)
        for r in rows:
            _push(r.get("question_stem") or r.get("title") or "")
    except Exception:
        # Defensive — older deployments may not have a chapter_id index
        # on pyq_html_pages, in which case we fall back to the seed
        # phrases below without failing the render.
        pass

    # 3. Always seed the canonical board-grounded long-tails so even a
    # zero-PYQ chapter ships keyword content that crawlers can latch on.
    seeds = [
        f"{chapter_title} {board_name} {class_name} notes",
        f"{chapter_title} {subject_name} important questions",
        f"{chapter_title} previous year questions {board_name}",
        f"{chapter_title} MCQ {class_name} {subject_name}",
    ]
    for s in seeds:
        _push(s)
    return expanded[:12]


async def _load_faq_entries(chapter_id: str, page_type: str) -> List[Dict]:
    """Pull a Task-#12-materialised FAQ list if present."""
    try:
        rows = await db.aeo_faq_entries.find(
            {"chapter_id": chapter_id, "page_type": page_type},
            {"_id": 0, "question": 1, "answer": 1},
        ).sort("position", 1).to_list(20)
        return [r for r in rows if r.get("question") and r.get("answer")]
    except Exception:
        return []


def _build_jsonld(*, page_url: str, page_type: str, chapter_title: str,
                  subject_name: str, board_name: str, class_name: str,
                  description: str, subtopics: List[Dict],
                  faq_entries: List[Dict]) -> str:
    """Emit a single ``@graph`` JSON-LD block carrying every node the
    spec requires for this page-type."""
    canonical_chapter_url = page_url.rsplit("/", 1)[0]
    graph: List[Dict] = []

    # LearningResource — required on every type.
    graph.append({
        "@type": "LearningResource",
        "@id": f"{page_url}#learning-resource",
        "name": f"{chapter_title} — {TYPE_LABEL.get(page_type, page_type)}",
        "description": description,
        "url": page_url,
        "inLanguage": ["en-IN", "as-IN"],
        "learningResourceType": page_type,
        "educationalLevel": class_name,
        "audience": {"@type": "EducationalAudience",
                     "educationalRole": "student"},
        "about": {"@type": "Thing", "name": chapter_title},
        "isPartOf": {"@type": "Course",
                     "@id": f"{canonical_chapter_url}#course"},
        "publisher": {"@type": "EducationalOrganization",
                      "name": "Syrabit.ai",
                      "url": "https://syrabit.ai"},
        "teaches": [t["title"] for t in subtopics] or [chapter_title],
    })

    # Course — required on every type.
    graph.append({
        "@type": "Course",
        "@id": f"{canonical_chapter_url}#course",
        "name": f"{subject_name} — {board_name} {class_name}",
        "description": (
            f"{subject_name} curriculum for {board_name} {class_name} "
            f"students in Assam."
        ),
        "provider": {"@type": "EducationalOrganization",
                     "name": "Syrabit.ai",
                     "url": "https://syrabit.ai"},
        "inLanguage": ["en-IN", "as-IN"],
        "hasCourseInstance": {
            "@type": "CourseInstance",
            "courseMode": "online",
            "courseWorkload": "PT2H",
            "location": {"@type": "Place",
                         "address": {"@type": "PostalAddress",
                                     "addressRegion": "IN-AS",
                                     "addressLocality": "Assam",
                                     "addressCountry": "IN"}},
        },
    })

    # BreadcrumbList — every page.
    graph.append({
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home",
             "item": "https://syrabit.ai"},
            {"@type": "ListItem", "position": 2, "name": board_name,
             "item": f"https://syrabit.ai/board/{_e(board_name)}"},
            {"@type": "ListItem", "position": 3, "name": subject_name,
             "item": canonical_chapter_url.rsplit("/chapter/", 1)[0]},
            {"@type": "ListItem", "position": 4, "name": chapter_title,
             "item": canonical_chapter_url},
            {"@type": "ListItem", "position": 5,
             "name": TYPE_LABEL.get(page_type, page_type),
             "item": page_url},
        ],
    })

    # FAQPage — every page; uses materialised entries when Task #12 has
    # populated them, otherwise a deterministic two-question stub so the
    # JSON-LD shape is consistent on day one.
    fq = list(faq_entries) or [
        {"question": f"What does the {chapter_title} chapter cover?",
         "answer": (
             f"The {chapter_title} chapter in {class_name} {subject_name} "
             f"({board_name}) covers every sub-topic prescribed in the "
             f"official Assam Board syllabus, with definitions, worked "
             f"examples and revision aids.")},
        {"question": (
            f"Where can I find {chapter_title} "
            f"{TYPE_LABEL.get(page_type, page_type)} for {board_name} "
            f"{class_name}?"),
         "answer": (
             f"Syrabit.ai publishes free, syllabus-aligned "
             f"{TYPE_LABEL.get(page_type, page_type)} for {chapter_title} "
             f"in both English and Assamese.")},
    ]
    graph.append({
        "@type": "FAQPage",
        "@id": f"{page_url}#faq",
        "mainEntity": [
            {"@type": "Question",
             "name": q["question"],
             "acceptedAnswer": {"@type": "Answer", "text": q["answer"]}}
            for q in fq
        ],
    })

    # Quiz — only for /mcqs and /pyqs.
    if page_type in QUIZ_TYPES:
        graph.append({
            "@type": "Quiz",
            "@id": f"{page_url}#quiz",
            "name": f"{chapter_title} {TYPE_LABEL.get(page_type, page_type)}",
            "about": {"@type": "Thing", "name": chapter_title},
            "educationalAlignment": {
                "@type": "AlignmentObject",
                "alignmentType": "educationalSubject",
                "educationalFramework": f"{board_name} {class_name}",
                "targetName": subject_name,
            },
            "inLanguage": ["en-IN", "as-IN"],
        })

    return json.dumps(
        {"@context": "https://schema.org", "@graph": graph},
        ensure_ascii=False,
    )


def _render_html(*, page_url: str, page_type: str,
                 chapter_title: str, subject_name: str, board_name: str,
                 class_name: str, description: str, quick_answer: str,
                 subtopics: List[Dict], jsonld: str,
                 related_keywords: Optional[List[str]] = None) -> str:
    """Render the SEO-page HTML via Jinja2 (Task #11 spec, step 1).

    Per spec, every page advertises both ``as-IN`` and ``en-IN``
    unconditionally — the Assamese sibling URL exists at ``/as/<path>``
    even before the Assamese body is materialised; the SPA serves
    bilingual chrome until Task #12 ships the translated body.
    """
    title = (
        f"{chapter_title} — {class_name} {subject_name} {board_name} "
        f"{TYPE_LABEL.get(page_type, page_type)}"
    )
    meta_desc = _truncate_meta(description)
    as_url = f"{BASE_URL}/as{page_url[len(BASE_URL):]}"
    related = related_keywords or []
    meta_keywords = ", ".join(related) if related else ""
    template = _jinja_env.get_template("chapter.html.j2")
    return template.render(
        title=title,
        meta_desc=meta_desc,
        meta_keywords=meta_keywords,
        page_url=page_url,
        as_url=as_url,
        base_url=BASE_URL,
        chapter_title=chapter_title,
        subject_name=subject_name,
        board_name=board_name,
        class_name=class_name,
        type_label=TYPE_LABEL.get(page_type, page_type),
        quick_answer=quick_answer,
        subtopics=subtopics,
        description=description,
        jsonld=jsonld,
        related_keywords=related,
    )


@router.get(
    "/board/{board}/class/{class_slug}/subject/{subject_slug}"
    "/chapter/{chapter_slug}/{page_type}",
    response_class=HTMLResponse,
)
async def render_seo_page(
    board: str, class_slug: str, subject_slug: str,
    chapter_slug: str, page_type: str,
    lang: Optional[str] = Query(None),
):
    """Render one of the seven canonical chapter page-types.

    See module docstring for the full contract. 404s on:
      * unknown ``page_type`` (only the seven canonical types are served);
      * unresolvable (board, class, subject, chapter) chain.
    """
    if page_type not in PAGE_TYPES:
        raise HTTPException(status_code=404, detail="Unknown SEO page type")

    chain = await _resolve_chapter(board, class_slug, subject_slug, chapter_slug)
    if not chain:
        raise HTTPException(status_code=404, detail="Chapter chain not found")

    chap = chain["chapter"]
    chapter_title = chap.get("title") or chapter_slug
    description = (
        chap.get("description")
        or f"{chapter_title} — {TYPE_LABEL.get(page_type, page_type)} for "
           f"{chain['class']['name']} {chain['subject']['name']} "
           f"({chain['board']['name']}) students in Assam."
    )
    subtopics = await _load_subtopics(chap.get("id", ""))
    quick_answer = await _load_quick_answer(chap.get("id", ""), page_type) or \
        _quick_answer_stub(chapter_title, page_type,
                           chain["subject"]["name"], chain["board"]["name"],
                           chain["class"]["name"])
    faq_entries = await _load_faq_entries(chap.get("id", ""), page_type)

    page_url = (
        f"{BASE_URL}/board/{board}/class/{class_slug}/subject/{subject_slug}"
        f"/chapter/{chapter_slug}/{page_type}"
    )
    jsonld = _build_jsonld(
        page_url=page_url, page_type=page_type, chapter_title=chapter_title,
        subject_name=chain["subject"]["name"], board_name=chain["board"]["name"],
        class_name=chain["class"]["name"], description=description,
        subtopics=subtopics, faq_entries=faq_entries,
    )
    related_keywords = await _load_keyword_expansion(
        chap.get("id", ""), chapter_title,
        chain["subject"]["name"], chain["board"]["name"],
        chain["class"]["name"], subtopics,
    )
    html_out = _render_html(
        page_url=page_url, page_type=page_type, chapter_title=chapter_title,
        subject_name=chain["subject"]["name"], board_name=chain["board"]["name"],
        class_name=chain["class"]["name"], description=description,
        quick_answer=quick_answer, subtopics=subtopics, jsonld=jsonld,
        related_keywords=related_keywords,
    )
    resp = HTMLResponse(content=html_out)
    resp.headers["Cache-Control"] = (
        "public, max-age=3600, s-maxage=86400, stale-while-revalidate=86400"
    )
    resp.headers["Cache-Tag"] = (
        f"syrabit-html syrabit-seo-pages "
        f"syrabit-subject-{subject_slug} syrabit-chapter-{chapter_slug} "
        f"syrabit-seo-{page_type}"
    )
    return resp


@router.get("/board/{board}/class/{class_slug}/subject/{subject_slug}"
            "/chapter/{chapter_slug}")
async def render_seo_chapter_index(
    board: str, class_slug: str, subject_slug: str, chapter_slug: str,
):
    """Index page listing every available SEO page-type for the chapter.

    Lightweight HTML — used as the canonical landing for the explicit
    ``/board/.../chapter/.../`` URL prefix. Each link feeds the
    sitemap pipeline so all 7 sub-pages are discoverable from a single
    entry point.
    """
    chain = await _resolve_chapter(board, class_slug, subject_slug, chapter_slug)
    if not chain:
        raise HTTPException(status_code=404, detail="Chapter chain not found")
    title = chain["chapter"].get("title") or chapter_slug
    base = (
        f"/board/{board}/class/{class_slug}/subject/{subject_slug}"
        f"/chapter/{chapter_slug}"
    )
    entries = [
        {"href": f"{base}/{pt}", "label": TYPE_LABEL[pt]}
        for pt in PAGE_TYPES
    ]
    template = _jinja_env.get_template("index.html.j2")
    body = template.render(
        title=title,
        canonical=f"{BASE_URL}{base}",
        entries=entries,
    )
    return HTMLResponse(content=body, headers={
        "Cache-Control": "public, max-age=3600, s-maxage=86400",
    })


# ─── Sitemap helpers ────────────────────────────────────────────────────────
#
# ``seo_engine.get_sitemap_chapters`` calls into ``build_chapter_sitemap_entries``
# so the canonical sitemap-chapters.xml carries one row per (chapter,
# page_type) combination — i.e. 7× the chapter count under the new
# Task #11 URL pattern, in addition to the legacy one-row-per-chapter
# entries the existing chapter pages still serve.
async def build_chapter_sitemap_entries() -> List[Dict]:
    """Return one sitemap entry per (chapter, page_type) under the
    Task #11 URL pattern. Always emits the chapter's own root URL
    too so the existing chapter landing remains discoverable."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    entries: List[Dict] = []
    try:
        chapters = await db.chapters.find(
            {}, {"_id": 0, "id": 1, "subject_id": 1, "slug": 1, "slug_as": 1,
                 "title": 1, "updated_at": 1, "created_at": 1, "content_as": 1},
        ).to_list(5000)
        subj_ids = list({c.get("subject_id", "") for c in chapters if c.get("subject_id")})
        if not subj_ids:
            return entries
        subj_rows = await db.subjects.find(
            {"id": {"$in": subj_ids}, "status": "published"},
            {"_id": 0, "id": 1, "slug": 1, "stream_id": 1, "class_id": 1, "board_id": 1},
        ).to_list(2000)
        subj_map = {s["id"]: s for s in subj_rows}
        stream_ids = list({s.get("stream_id", "") for s in subj_rows if s.get("stream_id")})
        cls_ids_direct = {s.get("class_id", "") for s in subj_rows if s.get("class_id")}
        streams = await db.streams.find(
            {"id": {"$in": stream_ids}}, {"_id": 0, "id": 1, "class_id": 1},
        ).to_list(2000) if stream_ids else []
        stream_map = {s["id"]: s for s in streams}
        cls_ids = list({*cls_ids_direct, *(s.get("class_id", "") for s in streams)})
        cls_rows = await db.classes.find(
            {"id": {"$in": cls_ids}}, {"_id": 0, "id": 1, "slug": 1, "board_id": 1},
        ).to_list(2000) if cls_ids else []
        cls_map = {c["id"]: c for c in cls_rows}
        brd_ids = list({c.get("board_id", "") for c in cls_rows if c.get("board_id")})
        brd_rows = await db.boards.find(
            {"id": {"$in": brd_ids}}, {"_id": 0, "id": 1, "slug": 1},
        ).to_list(500) if brd_ids else []
        brd_map = {b["id"]: b for b in brd_rows}

        for ch in chapters:
            sub = subj_map.get(ch.get("subject_id", ""))
            if not sub or not ch.get("slug"):
                continue
            cls = cls_map.get(sub.get("class_id", ""))
            if not cls and sub.get("stream_id"):
                stream = stream_map.get(sub.get("stream_id", ""))
                cls = cls_map.get((stream or {}).get("class_id", ""))
            if not cls:
                continue
            brd = brd_map.get(cls.get("board_id", ""))
            if not (brd and brd.get("slug") and cls.get("slug") and sub.get("slug")):
                continue
            b_slug = brd["slug"]
            c_slug = cls["slug"]
            s_slug = sub["slug"]
            ch_slug = ch["slug"]
            raw = ch.get("updated_at", "") or ch.get("created_at", "")
            lastmod = raw[:10] if raw else today
            base = (f"{BASE_URL}/board/{b_slug}/class/{c_slug}"
                    f"/subject/{s_slug}/chapter/{ch_slug}")
            has_as = bool((ch.get("content_as") or "").strip())
            entries.append({
                "loc": base, "lastmod": lastmod, "pri": "0.8",
                "freq": "weekly", "has_assamese": has_as,
                "slug_as": (ch.get("slug_as") or "").strip(),
            })
            for pt in PAGE_TYPES:
                entries.append({
                    "loc": f"{base}/{pt}", "lastmod": lastmod,
                    "pri": "0.7", "freq": "weekly",
                    "has_assamese": has_as,
                    "slug_as": (ch.get("slug_as") or "").strip(),
                })
    except Exception as exc:
        logger.warning("seo_pages.build_chapter_sitemap_entries failed: %s", exc)
    return entries


def list_seo_page_types() -> List[str]:
    """Public accessor used by tests + sitemap parity checks."""
    return list(PAGE_TYPES)
