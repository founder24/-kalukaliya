#!/usr/bin/env python3
"""
Seed AHSEC HS 1st Year Business Studies (2023-24 revised syllabus).

Hierarchy: AHSEC > HS 1st Year > Commerce > Business Studies > Chapter > Topic

Steps:
  1. Update the 10 existing draft chapters to match the official PDF syllabus
     (correct titles, slugs, and topics per unit).
  2. Insert the missing 11th chapter: International Business.
  3. Run the full auto-publish pipeline on every chapter:
       generate_notes() → GCS + Vertex Search + topic embeddings → published

Usage:
  cd /home/runner/workspace
  python3 infra/scripts/seed-business-studies-hs1.py
"""
import asyncio
import logging
import re
import sys
from datetime import datetime, timezone
from uuid import uuid4

sys.path.insert(0, "apps/backend")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


def make_topic(title: str) -> dict:
    return {
        "id": str(uuid4()),
        "title": title,
        "definition": None,
        "topic_slug": slugify(title),
        "definition_status": "pending",
        "wikidata_uri": None,
    }


# ---------------------------------------------------------------------------
# Official AHSEC 2023-24 syllabus — 11 units
# ---------------------------------------------------------------------------
SYLLABUS = [
    (
        "Business, Trade and Commerce",
        [
            "Concept of Business, Characteristics and Objectives",
            "Comparison of Business, Profession and Employment",
            "Classification of Business Activities: Industry and Commerce",
            "Industry Types: Primary, Secondary, Tertiary",
            "Commerce: Trade and Auxiliaries to Trade",
            "Business Risk: Concept, Nature and Causes",
            "Starting a Business: Basic Factors",
            "Role of Business in Development of Economy",
        ],
    ),
    (
        "Forms of Business Organisation",
        [
            "Sole Proprietorship: Concept, Features, Merits and Limitations",
            "Joint Hindu Family Business: Concept, Features, Merits and Limitations",
            "Partnership: Types, Partnership Deed, Registration",
            "Cooperative Society: Types, Features, Merits and Limitations",
            "Joint Stock Company: Private vs Public Company",
            "Choice of Forms of Business Organisation",
        ],
    ),
    (
        "Public, Private and Global Enterprises",
        [
            "Public Sector and Private Sector: Concept",
            "Departmental Undertakings: Features, Merits and Limitations",
            "Statutory Corporation: Features, Merits and Limitations",
            "Government Company: Features, Merits and Limitations",
            "Changing Role of Public Sector",
            "Global Enterprise: Concept and Features",
            "Joint Ventures: Meaning, Types, Benefits",
            "Public Private Partnership (PPP)",
        ],
    ),
    (
        "Business Services",
        [
            "Nature of Services: Difference between Services and Goods",
            "Banking: Types of Banks, Functions of Commercial Banks, e-Banking",
            "Insurance: Principles and Types (Life, Fire, Marine)",
            "Communication Services: Postal and Telecom",
            "Warehousing: Concept, Types and Functions",
        ],
    ),
    (
        "Emerging Modes of Business",
        [
            "E-Business: Concept, Scope, Benefits and Limitations",
            "Traditional Business vs E-Business",
            "Online Transactions: Security and Safety of e-Transactions",
        ],
    ),
    (
        "Social Responsibilities of Business and Business Ethics",
        [
            "Concept of Social Responsibility",
            "Arguments For and Against Social Responsibility",
            "Responsibility towards Stakeholders (Owners, Employees, Consumers, Government)",
            "Business and Environmental Protection: Types of Pollution",
            "Business Ethics: Concept and Elements",
        ],
    ),
    (
        "Formation of a Company",
        [
            "Stages in Formation of a Company",
            "Promotion of a Company: Promoter, Meaning and Functions",
            "Memorandum of Association: Meaning and Clauses",
            "Articles of Association: Meaning and Contents",
            "Capital Subscription Stage and Prospectus",
        ],
    ),
    (
        "Sources of Business Finance",
        [
            "Meaning, Nature and Significance of Business Finance",
            "Retained Earnings, Trade Credit and Public Deposits",
            "Factoring and Lease Financing",
            "Shares: Equity and Preference Shares",
            "Debentures: Types",
            "International Financing: GDRs, ADRs, IDRs and FCCBs",
            "Factors Affecting Choice of Sources of Finance",
        ],
    ),
    (
        "MSME and Business Entrepreneurship",
        [
            "Micro, Small and Medium Enterprises: Concept, Role and Problems",
            "MSME and Entrepreneurship Development",
            "Intellectual Property Rights (IPR): Importance for Entrepreneurs",
            "Types of Intellectual Property: Trademark, Patent, Geographical Indication, Design",
        ],
    ),
    (
        "Internal Trade",
        [
            "Internal Trade: Wholesale Trade and Retail Trade",
            "Types of Retail Trade: Itinerant and Fixed Shop Retailers",
            "Fixed Shop Large Stores: Departmental Stores and Chain Stores",
            "Mail Order Houses, Super Markets and Vending Machines",
            "Goods and Services Tax (GST): Concept and Key Features",
            "Role of Commerce and Industry Associations in Internal Trade",
        ],
    ),
    (
        "International Business",
        [
            "Meaning of International Business vs Domestic Business",
            "Scope and Benefits of International Business",
            "Modes of Entry: Exporting, Contract Manufacturing, Licensing",
            "Joint Ventures and Wholly Owned Subsidiaries",
            "Export Procedure and Documentation",
            "Import Procedure and Documents",
        ],
    ),
]

# Map from old draft titles → new official titles (for updates)
TITLE_MAP = {
    "Nature and Purpose of Business": "Business, Trade and Commerce",
    "Public Private and Global Enterprises": "Public, Private and Global Enterprises",
    "Social Responsibility of Business": "Social Responsibilities of Business and Business Ethics",
    "Small Business": "MSME and Business Entrepreneurship",
}


async def seed_chapters(subject_id):
    from app.models.content import Chapter
    from beanie import PydanticObjectId

    now = datetime.now(timezone.utc)

    existing = await Chapter.find({"subject_id": subject_id}).to_list(None)
    existing_by_title = {ch.title: ch for ch in existing}

    # Build normalised lookup: map new title → existing chapter (via TITLE_MAP)
    existing_by_new_title = {}
    for ch in existing:
        new_title = TITLE_MAP.get(ch.title, ch.title)
        existing_by_new_title[new_title] = ch

    chapter_ids = []
    for idx, (title, topics) in enumerate(SYLLABUS, start=1):
        slug = slugify(title)
        topic_docs = [make_topic(t) for t in topics]

        ch = existing_by_new_title.get(title) or existing_by_title.get(title)
        if ch:
            old_title = ch.title
            ch.title = title
            ch.slug = slug
            ch.chapter_number = idx
            ch.published_topics = [
                type("T", (), t)() if False else __import__("app.models.content", fromlist=["Topic"]).Topic(**t)
                for t in topic_docs
            ]
            ch.updated_at = now
            await ch.save()
            verb = "updated" if old_title != title else "refreshed topics for"
            logger.info(f"  [{idx:02d}] {verb} → {title!r}  ({len(topics)} topics)")
        else:
            from app.models.content import Topic as TopicModel
            new_ch = Chapter(
                title=title,
                slug=slug,
                subject_id=subject_id,
                chapter_number=idx,
                status="draft",
                published_topics=[TopicModel(**t) for t in topic_docs],
                created_at=now,
                updated_at=now,
            )
            await new_ch.insert()
            ch = new_ch
            logger.info(f"  [{idx:02d}] inserted → {title!r}  ({len(topics)} topics)")

        chapter_ids.append(str(ch.id))

    return chapter_ids


async def main():
    from app.db.mongo import init_mongo
    from app.models.content import Subject
    from app.services.content_generation import content_generation_service

    await init_mongo()

    # Locate Business Studies subject under AHSEC > HS 1st Year > Commerce
    subj = await Subject.find_one({"slug": "business-studies"})
    if not subj:
        logger.error("Business Studies subject not found in DB. Run seed-content.py first.")
        return
    logger.info(f"Subject: {subj.name!r}  id={subj.id}")

    # ── Step 1: seed / update chapters ─────────────────────────────────────
    logger.info("Seeding 11 chapters from official AHSEC 2023-24 syllabus...")
    chapter_ids = await seed_chapters(subj.id)
    logger.info(f"Seed complete: {len(chapter_ids)} chapters ready")

    # ── Step 2: generate notes + auto-publish for every chapter ────────────
    logger.info("Starting generate-notes pipeline (sequential to respect API quotas)...")
    results = {"ok": 0, "err": 0}
    for i, cid in enumerate(chapter_ids, 1):
        title = SYLLABUS[i - 1][0]
        logger.info(f"[{i:02d}/{len(chapter_ids)}] Generating: {title!r}")
        try:
            chapter = await content_generation_service.generate_notes(cid, force=True)
            pr = getattr(chapter, "_publish_result", {})
            gcs = pr.get("gcs", {}).get("status", "?")
            vtx = pr.get("vertex_search", {}).get("status", "?")
            chunks = pr.get("vertex_search", {}).get("chunks", 0)
            tdocs = pr.get("vertex_search", {}).get("topic_docs", 0)
            emb = pr.get("topic_embeddings", {}).get("count", 0)
            en_wc = len((chapter.content_en or "").split())
            as_wc = len((chapter.content_as or "").split())
            logger.info(
                f"  ✓ status={chapter.status}  en={en_wc}w  as={as_wc}w  "
                f"gcs={gcs}  vtx={vtx}({chunks}c+{tdocs}t)  emb={emb}"
            )
            results["ok"] += 1
        except Exception as e:
            logger.error(f"  ✗ FAILED: {e}")
            results["err"] += 1

    logger.info(
        f"\n{'='*60}\n"
        f"Business Studies HS 1st Year seeding complete.\n"
        f"  Published: {results['ok']}/{len(chapter_ids)}\n"
        f"  Errors:    {results['err']}\n"
        f"{'='*60}"
    )


if __name__ == "__main__":
    asyncio.run(main())
