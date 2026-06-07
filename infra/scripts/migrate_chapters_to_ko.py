"""
migrate_chapters_to_ko.py
=========================
Migrates published Chapter documents (with content_en) from syrabit_prod
into the knowledge_objects collection so the content API can serve them.

Also copies question_papers from the syrabit DB into syrabit_prod so the
PYQ page can link to R2-hosted images.

Usage:
    python3 infra/scripts/migrate_chapters_to_ko.py [--dry-run]
"""

import hashlib
import logging
import re
import sys
from datetime import datetime, timezone

from pymongo import MongoClient, UpdateOne

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

DRY_RUN = "--dry-run" in sys.argv

# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return re.sub(r"-+", "-", text).strip("-")


def sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()


def render_notes_html(body_markdown: str) -> str:
    """Minimal markdown → HTML for the notes page type."""
    parts = []
    for p in (body_markdown or "").split("\n\n"):
        p = p.strip()
        if not p:
            continue
        def esc(t):
            return (t.replace("&","&amp;").replace("<","&lt;")
                     .replace(">","&gt;").replace('"',"&quot;"))
        if p.startswith("### "):
            parts.append(f"<h3>{esc(p[4:])}</h3>")
        elif p.startswith("## "):
            parts.append(f"<h2>{esc(p[3:])}</h2>")
        elif p.startswith("# "):
            parts.append(f"<h1>{esc(p[2:])}</h1>")
        else:
            parts.append(f"<p>{esc(p)}</p>")
    return "\n".join(parts)


# ──────────────────────────────────────────────
# Chapter-title heuristics for multi-year subjects
# ──────────────────────────────────────────────

# HS 1st Year Physical Geography chapter titles (NCERT Fundamentals of Physical Geography)
_GEO_HS1_TITLES = {
    "origin and evolution of the earth",
    "interior of the earth",
    "distribution of oceans and continents",
    "minerals and rocks",
    "geomorphic processes",
    "landforms and their evolution",
    "composition and structure of atmosphere",
    "solar radiation, heat balance and temperature",
    "atmospheric circulation and weather systems",
    "water in the atmosphere",
    "world climate and climate change",
    "water (oceans)",
    "movements of ocean water",
    "life on earth — biodiversity and conservation",
    "geography as a discipline",
}

# HS 1st Year English (Hornbill) titles
_ENG_HS1_TITLES = {
    "the portrait of a lady",
    "we're not afraid to die",
    "discovering tut — the saga continues",
    "landscape of the soul",
    "the ailing planet — green movement's role",
    "the browning version",
    "the adventure",
    "silk road",
    "poetry — hornbill",
    "snapshots — supplementary reader",
}

def classify_geo_chapter(title: str) -> str:
    """Returns 'hs-1st-year' or 'hs-2nd-year' for Geography chapters."""
    if title.lower().strip() in _GEO_HS1_TITLES:
        return "hs-1st-year"
    return "hs-2nd-year"

def classify_eng_chapter(title: str) -> str:
    """Returns 'hs-1st-year' or 'hs-2nd-year' for English chapters."""
    if title.lower().strip() in _ENG_HS1_TITLES:
        return "hs-1st-year"
    return "hs-2nd-year"


# ──────────────────────────────────────────────
# Subject-level fallback overrides
# Maps subject_id → {board, class_level_or_fn, subject_slug}
# class_level may be a string or a callable(chapter) -> string
# ──────────────────────────────────────────────

SUBJECT_OVERRIDES = {
    # Geography – HS 1st Year subjects map to hs-1st-year, HS 2nd Year to hs-2nd-year
    "6a1f87802c1a19d2142de357": {
        "board": "ahsec",
        "class_level_fn": classify_geo_chapter,
        "subject": "geography",
    },
    # English (Core) – Hornbill = HS1, Flamingo = HS2
    "6a1f87802c1a19d2142de354": {
        "board": "ahsec",
        "class_level_fn": classify_eng_chapter,
        "subject": "english-core",
    },
    # Assamese (MIL) – HS 1st Year Arts
    "6a1f87822c1a19d2142de3b1": {
        "board": "ahsec",
        "class_level": "hs-1st-year",
        "subject": "assamese-mil",
    },
    # Degree Commerce 2nd Semester
    "6a1f87802c1a19d2142de333": {
        "board": "degree",
        "class_level": "2nd-semester",
        "subject": "commerce-2nd-sem-nep",
    },
    # Degree Commerce 4th Semester
    "6a1f87802c1a19d2142de348": {
        "board": "degree",
        "class_level": "4th-semester",
        "subject": "commerce-4th-sem-nep",
    },
    # TOURISM MANAGEMENT | VAC-02022 (Degree elective)
    "6a1f87802c1a19d2142de33b": {
        "board": "degree",
        "class_level": "2nd-semester",
        "subject": "tourism-management-vac-02022",
    },
    # YOGA AND WELLNESS | VAC-02012 (Degree elective)
    "6a1f87802c1a19d2142de34c": {
        "board": "degree",
        "class_level": "2nd-semester",
        "subject": "yoga-and-wellness-vac-02012",
    },
    # Political Science 2nd Sem NEP
    "6a1f87802c1a19d2142de33d": {
        "board": "degree",
        "class_level": "2nd-semester",
        "subject": "political-science-2nd-sem-nep",
    },
    # Fundamentals of Financial Management (Major 4) – B.Com 4th Sem
    "6a1f87802c1a19d2142de349": {
        "board": "degree",
        "class_level": "4th-semester",
        "subject": "fundamentals-of-financial-management",
    },
    # Chemistry 2nd Sem NEP
    "6a1f87802c1a19d2142de334": {
        "board": "degree",
        "class_level": "2nd-semester",
        "subject": "chemistry-2nd-sem-nep",
    },
    # Zoology 2nd Sem NEP
    "6a1f87802c1a19d2142de352": {
        "board": "degree",
        "class_level": "2nd-semester",
        "subject": "zoology-2nd-sem-nep",
    },
    # Botany 2nd Sem NEP
    "6a1f87802c1a19d2142de351": {
        "board": "degree",
        "class_level": "2nd-semester",
        "subject": "botany-2nd-sem-nep",
    },
    # Physics 2nd Sem NEP
    "6a1f87802c1a19d2142de34d": {
        "board": "degree",
        "class_level": "2nd-semester",
        "subject": "physics-2nd-sem-nep",
    },
    # Statistics 2nd Sem NEP
    "6a1f87802c1a19d2142de343": {
        "board": "degree",
        "class_level": "2nd-semester",
        "subject": "statistics-2nd-sem-nep",
    },
    # History 2nd Sem NEP
    "6a1f87802c1a19d2142de353": {
        "board": "degree",
        "class_level": "2nd-semester",
        "subject": "history-2nd-sem-nep",
    },
    # Computer Science 2nd Sem NEP
    "6a1f87802c1a19d2142de32f": {
        "board": "degree",
        "class_level": "2nd-semester",
        "subject": "computer-science-2nd-sem-nep",
    },
    # Mathematics 2nd Sem NEP
    "6a1f87802c1a19d2142de32d": {
        "board": "degree",
        "class_level": "2nd-semester",
        "subject": "mathematics-2nd-sem-nep",
    },
}


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

import os
MONGO_URI = os.environ["MONGODB_URI"]

client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
client.admin.command("ping")
log.info("MongoDB connected")

prod_db = client["syrabit_prod"]
old_db  = client["syrabit"]

# ── Build hierarchy lookup tables ──────────────
boards   = {str(b["_id"]): b for b in prod_db.boards.find()}
classes  = {str(c["_id"]): c for c in prod_db.classes.find()}
streams  = {str(s["_id"]): s for s in prod_db.streams.find()}
subjects = {str(s["_id"]): s for s in prod_db.subjects.find()}

log.info(f"Loaded: {len(boards)} boards, {len(classes)} classes, "
         f"{len(streams)} streams, {len(subjects)} subjects")


def resolve_metadata(chapter: dict) -> dict | None:
    """Return {board, class_level, subject, chapter} slug dict or None."""
    sid = str(chapter.get("subject_id", ""))
    subj = subjects.get(sid)
    if not subj:
        log.warning(f"  Chapter '{chapter.get('title')}': subject {sid} not found")
        return None

    # ── Check subject override first ──
    if sid in SUBJECT_OVERRIDES:
        ov = SUBJECT_OVERRIDES[sid]
        if "class_level_fn" in ov:
            class_level = ov["class_level_fn"](chapter.get("title", ""))
        else:
            class_level = ov["class_level"]
        return {
            "board":       ov["board"],
            "class_level": class_level,
            "subject":     ov["subject"],
            "chapter":     chapter.get("slug", ""),
        }

    # ── Normal hierarchy traversal ──
    stream_id = str(subj.get("stream_id", ""))
    stream = streams.get(stream_id)
    if not stream:
        log.warning(f"  Skipping '{chapter.get('title')}': subject '{subj.get('name')}' has no stream")
        return None

    class_id = str(stream.get("class_id", ""))
    cls = classes.get(class_id)
    if not cls:
        log.warning(f"  Skipping '{chapter.get('title')}': stream '{stream.get('name')}' has no class")
        return None

    board_id = str(cls.get("board_id", ""))
    board = boards.get(board_id)
    if not board:
        log.warning(f"  Skipping '{chapter.get('title')}': class '{cls.get('name')}' has no board")
        return None

    return {
        "board":       board.get("slug", ""),
        "class_level": slugify(cls.get("name", "")),
        "subject":     subj.get("slug") or slugify(subj.get("name", "")),
        "chapter":     chapter.get("slug", ""),
    }


# ══════════════════════════════════════════════
# PART 1 – Promote chapters → knowledge_objects
# ══════════════════════════════════════════════

log.info("\n" + "="*60)
log.info("PART 1: Migrating chapters → knowledge_objects")
log.info("="*60)

published_chapters = list(prod_db.chapters.find(
    {"status": "published", "content_en": {"$nin": [None, ""]}}
))
log.info(f"Found {len(published_chapters)} published chapters with content_en")

now = datetime.now(timezone.utc)
ko_ops = []
skipped = 0
mapped = 0

for ch in published_chapters:
    meta = resolve_metadata(ch)
    if not meta:
        skipped += 1
        continue

    body_md   = ch.get("content_en", "")
    notes_html = render_notes_html(body_md)

    # KO slug: board-classlevel-subject-chapter
    ko_slug = f"{meta['board']}-{meta['class_level']}-{meta['subject']}-{meta['chapter']}"

    # Keywords
    raw_kw = ch.get("keywords") or ""
    if isinstance(raw_kw, str):
        keywords = [k.strip() for k in raw_kw.split(",") if k.strip()]
    else:
        keywords = list(raw_kw)

    word_count = ch.get("word_count") or len(body_md.split())
    read_time  = max(1, round(word_count / 200))

    # Preserve content_as and any existing content_blocks from the source chapter
    content_as = ch.get("content_as") or None
    existing_blocks = ch.get("content_blocks") or []

    ko_doc = {
        "slug":        ko_slug,
        "title":       ch.get("title", ""),
        "description": (ch.get("meta_description")
                        or f"{ch.get('title','')} – notes for "
                           f"{meta['subject'].replace('-',' ').title()}."),
        "body_markdown": body_md,
        "content_as":    content_as,
        "content_blocks": existing_blocks,
        "metadata": {
            "board":       meta["board"],
            "class_level": meta["class_level"],
            "subject":     meta["subject"],
            "chapter":     meta["chapter"],
            "chapter_number": ch.get("chapter_number"),
            "topic":       None,
            "difficulty":  "medium",
            "language":    "en",
            "estimated_read_time_minutes": read_time,
            "keywords":    keywords,
        },
        "generated": {
            "mcqs":                [],
            "summary":             "",
            "definitions":         [],
            "important_questions": [],
        },
        "derivative_hashes": {
            "notes_html":               sha256(notes_html),
            "mcqs_html":                None,
            "summary_html":             None,
            "definitions_html":         None,
            "important_questions_html": None,
            "search_index":             sha256(body_md),
        },
        "rendered_html": {
            "notes":               notes_html,
            "mcqs":                "<p>No MCQs available yet.</p>",
            "summary":             "<p>No summary available yet.</p>",
            "definitions":         "<p>No definitions available yet.</p>",
            "important-questions": "<p>No important questions available yet.</p>",
        },
        "status":            "published",
        "published_at":      ch.get("updated_at") or now,
        "last_pipeline_run": now,
        "page_views":        0,
        "search_impressions": 0,
        "created_at":        ch.get("created_at") or now,
        "updated_at":        now,
    }

    ko_ops.append(UpdateOne(
        {"slug": ko_slug},
        {"$set": ko_doc},
        upsert=True,
    ))
    mapped += 1

log.info(f"Mapped: {mapped}  |  Skipped (unresolvable hierarchy): {skipped}")

if ko_ops:
    if DRY_RUN:
        log.info(f"[DRY RUN] Would upsert {len(ko_ops)} knowledge_objects")
        # Show sample
        for op in ko_ops[:5]:
            upd = op._doc["$set"]
            m = upd["metadata"]
            log.info(f"  slug={upd['slug']}")
            log.info(f"    board={m['board']} class={m['class_level']} "
                     f"subj={m['subject']} chapter={m['chapter']}")
    else:
        result = prod_db.knowledge_objects.bulk_write(ko_ops, ordered=False)
        log.info(f"Upserted: {result.upserted_count} new  |  "
                 f"Modified: {result.modified_count}  |  "
                 f"Matched: {result.matched_count}")
else:
    log.warning("No operations to perform for knowledge_objects")


# ══════════════════════════════════════════════
# PART 2 – Copy question_papers → syrabit_prod
# ══════════════════════════════════════════════

log.info("\n" + "="*60)
log.info("PART 2: Migrating question_papers → syrabit_prod")
log.info("="*60)

old_papers = list(old_db.question_papers.find())
log.info(f"Found {len(old_papers)} question_papers in syrabit DB")

qp_ops = []
for paper in old_papers:
    qp_slug = paper.get("slug") or slugify(paper.get("title", ""))
    # Normalise class_level to match the slug format
    raw_class = paper.get("class_level", "")
    if raw_class and not re.search(r"[a-z]", raw_class):
        # Already a slug like "class-10"
        class_level_slug = raw_class
    else:
        class_level_slug = slugify(raw_class) if raw_class else "class-10"

    qp_doc = {
        "title":       paper.get("title", ""),
        "slug":        qp_slug,
        "r2_key":      paper.get("r2_key", ""),
        "board":       paper.get("board", "seba"),
        "class_level": class_level_slug,
        "subject":     paper.get("subject", ""),
        "year":        paper.get("year"),
        "status":      "published",
        "created_at":  paper.get("created_at") or now,
        "updated_at":  now,
    }
    qp_ops.append(UpdateOne(
        {"slug": qp_slug},
        {"$set": qp_doc},
        upsert=True,
    ))

if qp_ops:
    if DRY_RUN:
        log.info(f"[DRY RUN] Would upsert {len(qp_ops)} question_papers:")
        for op in qp_ops:
            upd = op._doc["$set"]
            log.info(f"  {upd['title']} → r2_key={upd['r2_key']}")
    else:
        result = prod_db.question_papers.bulk_write(qp_ops, ordered=False)
        log.info(f"Question papers upserted: {result.upserted_count}  |  "
                 f"Modified: {result.modified_count}")
        for op in qp_ops:
            upd = op._doc["$set"]
            log.info(f"  ✓ {upd['title']} → r2_key={upd['r2_key']}")
else:
    log.info("No question papers to migrate")


# ══════════════════════════════════════════════
# PART 3 – Verify results
# ══════════════════════════════════════════════

log.info("\n" + "="*60)
log.info("PART 3: Verification")
log.info("="*60)

ko_total = prod_db.knowledge_objects.count_documents({})
ko_pub   = prod_db.knowledge_objects.count_documents({"status": "published"})
qp_prod  = prod_db.question_papers.count_documents({})

log.info(f"knowledge_objects total={ko_total}  published={ko_pub}")
log.info(f"question_papers in syrabit_prod: {qp_prod}")

# Breakdown by subject
pipeline = [
    {"$match": {"status": "published"}},
    {"$group": {"_id": "$metadata.subject", "count": {"$sum": 1}}},
    {"$sort": {"count": -1}},
]
log.info("\nKnowledge objects by subject:")
for row in prod_db.knowledge_objects.aggregate(pipeline):
    log.info(f"  {row['_id']}: {row['count']}")

# Sample KOs
sample_kos = list(prod_db.knowledge_objects.find(
    {"status": "published"},
    {"slug": 1, "title": 1, "metadata": 1, "_id": 0}
).limit(8))
log.info("\nSample knowledge_objects:")
for ko in sample_kos:
    m = ko.get("metadata", {})
    log.info(f"  [{m.get('board')}/{m.get('class_level')}/{m.get('subject')}]  "
             f"slug={ko['slug']}")

# Sample QPs
log.info("\nQuestion papers in syrabit_prod:")
for qp in prod_db.question_papers.find({}, {"title":1,"r2_key":1,"_id":0}):
    log.info(f"  {qp.get('title')}  →  r2_key={qp.get('r2_key')}")

client.close()
log.info("\n✓ Migration complete")
