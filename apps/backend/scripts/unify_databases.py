"""
Unify content from test_database + syrabit_dev into syrabit_prod.

What it does
────────────
1. Walks each source DB's Board→Class→Stream→Subject hierarchy, matching
   against prod by slug/name. Inserts genuinely new hierarchy nodes.
2. For each chapter that has content (content_en OR content field) and whose
   slug is not already in prod, converts to the prod schema and inserts as
   status="draft".
3. Joins topics from the source's separate `topics` collection (old schema)
   into the embedded `published_topics` array (new schema).
4. syrabit (legacy) is skipped — all content_en is empty there.

Schema mapping (old → new)
──────────────────────────
  chapter.content      → chapter.content_en
  chapter.content_as   → chapter.content_as   (kept)
  topics[].title       → published_topics[].title
  topics[].topic_slug  → published_topics[].topic_slug
  topics[].definition  → published_topics[].definition

Usage
─────
    cd apps/backend

    # Preview — no writes
    python -m scripts.unify_databases --dry-run

    # Full run
    python -m scripts.unify_databases

    # Only migrate hierarchy (subjects/streams) — skip chapters
    python -m scripts.unify_databases --hierarchy-only

    # Only a specific source DB
    python -m scripts.unify_databases --source test_database
    python -m scripts.unify_databases --source syrabit_dev
"""

import argparse
import asyncio
import logging
import sys
import uuid
from datetime import datetime, timezone

from bson import ObjectId

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

NOW = datetime.now(timezone.utc)


# ─── helpers ─────────────────────────────────────────────────────────────────

def _slug(text: str) -> str:
    return text.lower().strip().replace(" ", "-") if text else ""


def _find_by_id(collection_map: dict, raw_id) -> dict | None:
    """Look up a document by either its _id (ObjectId) or string id field."""
    if raw_id is None:
        return None
    if isinstance(raw_id, ObjectId) and raw_id in collection_map:
        return collection_map[raw_id]
    str_id = str(raw_id)
    for doc in collection_map.values():
        if str(doc.get("_id")) == str_id or doc.get("id") == str_id:
            return doc
    return None


async def _load_all(collection, key="slug") -> dict:
    """Load all docs into a dict keyed by slug or _id."""
    result = {}
    async for doc in collection.find():
        k = doc.get(key) or str(doc["_id"])
        result[k] = doc
    return result


# ─── hierarchy sync ───────────────────────────────────────────────────────────

async def sync_hierarchy(src_db, prod_db, dry_run: bool) -> dict:
    """
    Walk Board→Class→Stream→Subject in src_db, insert any that are missing
    from prod_db (matched by slug/name).  Returns:
      {
        "boards":   {src_board_id_str:   prod_ObjectId},
        "classes":  {src_class_id_str:   prod_ObjectId},
        "streams":  {src_stream_id_str:  prod_ObjectId},
        "subjects": {src_subject_id_str: prod_ObjectId},
      }
    """
    id_map = {"boards": {}, "classes": {}, "streams": {}, "subjects": {}}
    stats  = {"boards": 0, "classes": 0, "streams": 0, "subjects": 0}

    # ── boards ────────────────────────────────────────────────────────────────
    prod_boards_by_slug: dict[str, dict] = await _load_all(prod_db["boards"])
    src_boards_by_id: dict = {}  # keyed by str(_id)

    async for board in src_db["boards"].find():
        bid = str(board["_id"])
        src_boards_by_id[bid] = board
        if board.get("id"):
            src_boards_by_id[str(board["id"])] = board

        slug = board.get("slug") or _slug(board.get("name", ""))
        if slug in prod_boards_by_slug:
            id_map["boards"][bid] = prod_boards_by_slug[slug]["_id"]
        else:
            new_id = ObjectId()
            if not dry_run:
                await prod_db["boards"].insert_one({
                    "_id": new_id,
                    "name": board.get("name", slug),
                    "slug": slug,
                    "status": board.get("status", "active"),
                    "created_at": NOW, "updated_at": NOW,
                })
                prod_boards_by_slug[slug] = {"_id": new_id}
            id_map["boards"][bid] = new_id
            stats["boards"] += 1
            logger.info(f"  Board NEW: {slug!r}")

    # ── classes ───────────────────────────────────────────────────────────────
    prod_classes_by_key: dict[str, dict] = {}
    async for c in prod_db["classes"].find():
        key = f"{c.get('name','')}::{c.get('board_id')}"
        prod_classes_by_key[key] = c

    src_classes_by_id: dict = {}

    async for cls in src_db["classes"].find():
        cid = str(cls["_id"])
        src_classes_by_id[cid] = cls
        if cls.get("id"):
            src_classes_by_id[str(cls["id"])] = cls

        src_board_id = str(cls.get("board_id", ""))
        prod_board_oid = id_map["boards"].get(src_board_id)
        key = f"{cls.get('name','')}::{prod_board_oid}"

        if key in prod_classes_by_key:
            id_map["classes"][cid] = prod_classes_by_key[key]["_id"]
        elif prod_board_oid:
            new_id = ObjectId()
            if not dry_run:
                await prod_db["classes"].insert_one({
                    "_id": new_id,
                    "name": cls.get("name", ""),
                    "board_id": prod_board_oid,
                    "status": cls.get("status", "active"),
                    "created_at": NOW, "updated_at": NOW,
                })
                prod_classes_by_key[key] = {"_id": new_id}
            id_map["classes"][cid] = new_id
            stats["classes"] += 1
            logger.info(f"  Class NEW: {cls.get('name')!r}")

    # ── streams ───────────────────────────────────────────────────────────────
    prod_streams_by_key: dict[str, dict] = {}
    async for s in prod_db["streams"].find():
        key = f"{s.get('name','')}::{s.get('class_id')}"
        prod_streams_by_key[key] = s

    src_streams_by_id: dict = {}

    async for stream in src_db["streams"].find():
        sid = str(stream["_id"])
        src_streams_by_id[sid] = stream
        if stream.get("id"):
            src_streams_by_id[str(stream["id"])] = stream

        src_class_id = str(stream.get("class_id", ""))
        prod_class_oid = id_map["classes"].get(src_class_id)
        key = f"{stream.get('name','')}::{prod_class_oid}"

        if key in prod_streams_by_key:
            id_map["streams"][sid] = prod_streams_by_key[key]["_id"]
        elif prod_class_oid:
            new_id = ObjectId()
            if not dry_run:
                await prod_db["streams"].insert_one({
                    "_id": new_id,
                    "name": stream.get("name", ""),
                    "class_id": prod_class_oid,
                    "status": stream.get("status", "active"),
                    "created_at": NOW, "updated_at": NOW,
                })
                prod_streams_by_key[key] = {"_id": new_id}
            id_map["streams"][sid] = new_id
            stats["streams"] += 1
            logger.info(f"  Stream NEW: {stream.get('name')!r}")

        # Also index by string id field (legacy DBs use string IDs like "s11")
        if stream.get("id"):
            id_map["streams"][str(stream["id"])] = id_map["streams"].get(sid)

    # ── subjects ──────────────────────────────────────────────────────────────
    prod_subjects_by_slug: dict[str, dict] = await _load_all(prod_db["subjects"])

    async for subj in src_db["subjects"].find():
        sjid = str(subj["_id"])
        slug = subj.get("slug") or _slug(subj.get("name", ""))

        if slug in prod_subjects_by_slug:
            id_map["subjects"][sjid] = prod_subjects_by_slug[slug]["_id"]
            if subj.get("id"):
                id_map["subjects"][str(subj["id"])] = prod_subjects_by_slug[slug]["_id"]
            continue

        # resolve stream → prod stream ObjectId
        src_stream_id = str(subj.get("stream_id", ""))
        prod_stream_oid = id_map["streams"].get(src_stream_id)

        new_id = ObjectId()
        if not dry_run:
            if not prod_stream_oid:
                logger.warning(
                    f"  Subject {slug!r} — stream_id {src_stream_id!r} unresolved, "
                    f"inserting without stream link"
                )
            await prod_db["subjects"].insert_one({
                "_id": new_id,
                "name": subj.get("name", slug),
                "slug": slug,
                "stream_id": prod_stream_oid,  # may be None — OK
                "status": subj.get("status", "active"),
                "description": subj.get("description"),
                "tags": subj.get("tags", []),
                "icon": subj.get("icon"),
                "gradient": subj.get("gradient"),
                "thumbnail_url": subj.get("thumbnail_url"),
                "has_document": subj.get("has_document", False),
                "created_at": NOW, "updated_at": NOW,
            })
            prod_subjects_by_slug[slug] = {"_id": new_id}

        id_map["subjects"][sjid] = new_id
        if subj.get("id"):
            id_map["subjects"][str(subj["id"])] = new_id
        stats["subjects"] += 1
        logger.info(f"  Subject NEW: {slug!r} (stream resolved: {bool(prod_stream_oid)})")

    logger.info(
        f"  Hierarchy sync done: "
        f"+{stats['boards']} boards, +{stats['classes']} classes, "
        f"+{stats['streams']} streams, +{stats['subjects']} subjects"
    )
    return id_map


# ─── chapter migration ────────────────────────────────────────────────────────

async def migrate_chapters(src_db, prod_db, id_map: dict, dry_run: bool, db_name: str) -> dict:
    """
    Migrate chapters from src_db to prod_db.
    Returns stats dict.
    """
    has_topics_coll = "topics" in await src_db.list_collection_names()

    # Build a topics lookup: chapter uuid/str_id → list of topic dicts
    topics_by_chapter: dict[str, list] = {}
    if has_topics_coll:
        async for topic in src_db["topics"].find():
            ch_id = str(topic.get("chapter_id", ""))
            topics_by_chapter.setdefault(ch_id, []).append(topic)

    # Existing prod slugs
    prod_slugs = {d["slug"] async for d in prod_db["chapters"].find({}, {"slug": 1})}

    stats = {"inserted": 0, "skipped_dup": 0, "skipped_no_content": 0,
             "skipped_no_subject": 0, "errors": 0}

    async for chapter in src_db["chapters"].find():
        slug = chapter.get("slug", "")

        # De-duplicate by slug
        if slug in prod_slugs:
            stats["skipped_dup"] += 1
            continue

        # Gather content — try content_en first, then legacy content field
        content_en = (chapter.get("content_en") or "").strip()
        if not content_en:
            content_en = (chapter.get("content") or "").strip()
        if not content_en:
            stats["skipped_no_content"] += 1
            continue

        # Resolve subject_id
        src_subj_id = str(chapter.get("subject_id", ""))
        prod_subj_oid = id_map["subjects"].get(src_subj_id)
        if not prod_subj_oid:
            stats["skipped_no_subject"] += 1
            logger.debug(f"  Chapter {slug!r} — subject_id {src_subj_id!r} not in id_map, skipping")
            continue

        # Build published_topics from old schema
        published_topics: list[dict] = []

        # Try embedded topics list first (list of dicts in the chapter doc)
        embedded_topics = chapter.get("topics") or []
        if isinstance(embedded_topics, list) and embedded_topics and isinstance(embedded_topics[0], dict):
            for t in embedded_topics:
                title = t.get("title", "")
                tslug = t.get("topic_slug") or t.get("slug") or _slug(title)
                published_topics.append({
                    "id": str(uuid.uuid4()),
                    "title": title,
                    "topic_slug": tslug,
                    "definition": t.get("definition") or t.get("summary") or None,
                    "definition_status": t.get("definition_status", "pending"),
                    "wikidata_uri": t.get("wikidata_uri"),
                })

        # If no embedded, look up separate topics collection
        if not published_topics and has_topics_coll:
            ch_uuid = chapter.get("id") or str(chapter["_id"])
            for t in topics_by_chapter.get(str(ch_uuid), []):
                title = t.get("title", "")
                tslug = t.get("topic_slug") or t.get("slug") or _slug(title)
                published_topics.append({
                    "id": str(uuid.uuid4()),
                    "title": title,
                    "topic_slug": tslug,
                    "definition": t.get("definition") or t.get("summary") or None,
                    "definition_status": t.get("definition_status", "pending"),
                    "wikidata_uri": None,
                })

        word_count = chapter.get("word_count") or len(content_en.split())
        new_doc = {
            "_id": ObjectId(),
            "title": chapter.get("title", ""),
            "title_as": chapter.get("title_as"),
            "slug": slug,
            "subject_id": prod_subj_oid,
            "chapter_number": chapter.get("chapter_number") or chapter.get("order") or chapter.get("order_index") or 0,
            "status": "draft",
            "content_en": content_en,
            "content_as": chapter.get("content_as") or None,
            "meta_description": chapter.get("meta_description") or chapter.get("description") or None,
            "keywords": chapter.get("keywords") or chapter.get("bing_keywords") or None,
            "word_count": word_count,
            "notes_generated": bool(content_en),
            "published_topics": published_topics,
            "faq_jsonld": chapter.get("faq_jsonld"),
            "created_at": chapter.get("created_at") or NOW,
            "updated_at": NOW,
            "_migrated_from": db_name,
        }

        if not dry_run:
            try:
                await prod_db["chapters"].insert_one(new_doc)
                prod_slugs.add(slug)
                stats["inserted"] += 1
                logger.info(
                    f"  ✓ {chapter.get('title','?')[:55]:55s} "
                    f"topics={len(published_topics)}  words≈{word_count}"
                )
            except Exception as e:
                stats["errors"] += 1
                logger.error(f"  ✗ {slug!r}: {e}")
        else:
            stats["inserted"] += 1
            logger.info(
                f"  [DRY] {chapter.get('title','?')[:55]:55s} "
                f"topics={len(published_topics)}  words≈{word_count}"
            )

    return stats


# ─── main ─────────────────────────────────────────────────────────────────────

async def main(sources: list[str], dry_run: bool, hierarchy_only: bool):
    from pymongo import AsyncMongoClient
    from app.config import settings

    if not settings.MONGODB_URI:
        logger.error("MONGODB_URI not set")
        sys.exit(1)

    client = AsyncMongoClient(settings.MONGODB_URI)
    prod_db = client["syrabit_prod"]

    mode = "DRY RUN — no writes" if dry_run else "LIVE — writing to syrabit_prod"
    logger.info(f"\n{'═'*60}")
    logger.info(f"Syrabit DB Unification  [{mode}]")
    logger.info(f"Sources: {sources}")
    logger.info(f"{'═'*60}\n")

    grand_total = {"inserted": 0, "skipped_dup": 0, "skipped_no_content": 0,
                   "skipped_no_subject": 0, "errors": 0}

    for db_name in sources:
        src_db = client[db_name]
        logger.info(f"\n{'─'*60}")
        logger.info(f"SOURCE: {db_name}")
        logger.info(f"{'─'*60}")

        logger.info("Step 1: Syncing hierarchy (boards/classes/streams/subjects)...")
        id_map = await sync_hierarchy(src_db, prod_db, dry_run)

        if hierarchy_only:
            logger.info("  --hierarchy-only: skipping chapter migration")
            continue

        logger.info("Step 2: Migrating chapters...")
        stats = await migrate_chapters(src_db, prod_db, id_map, dry_run, db_name)

        logger.info(
            f"\n  {db_name} chapter results:\n"
            f"    Inserted          : {stats['inserted']}\n"
            f"    Skipped (dup slug): {stats['skipped_dup']}\n"
            f"    Skipped (no content): {stats['skipped_no_content']}\n"
            f"    Skipped (no subject): {stats['skipped_no_subject']}\n"
            f"    Errors            : {stats['errors']}"
        )

        for k in grand_total:
            grand_total[k] += stats[k]

    # Final prod counts
    final_subjects  = await prod_db["subjects"].count_documents({})
    final_chapters  = await prod_db["chapters"].count_documents({})
    final_drafts    = await prod_db["chapters"].count_documents({"status": "draft"})
    final_published = await prod_db["chapters"].count_documents({"status": "published"})

    logger.info(f"\n{'═'*60}")
    logger.info(f"COMPLETE {'(DRY RUN — nothing written)' if dry_run else ''}")
    logger.info(f"  Chapters inserted : {grand_total['inserted']}")
    logger.info(f"  Errors            : {grand_total['errors']}")
    logger.info(f"\n  syrabit_prod after:")
    logger.info(f"    subjects  : {final_subjects}")
    logger.info(f"    chapters  : {final_chapters}  (draft={final_drafts}, published={final_published})")
    logger.info(f"{'═'*60}\n")

    client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Unify Syrabit databases into syrabit_prod")
    parser.add_argument(
        "--source", default=None,
        help="Only process one source DB (test_database or syrabit_dev). Default: both.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview only — no writes")
    parser.add_argument("--hierarchy-only", action="store_true", help="Sync subjects/streams only, skip chapters")
    args = parser.parse_args()

    sources = (
        [args.source]
        if args.source
        else ["test_database", "syrabit_dev"]
    )
    asyncio.run(main(sources=sources, dry_run=args.dry_run, hierarchy_only=args.hierarchy_only))
