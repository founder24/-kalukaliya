"""
AHSEC Orphaned Subject Migration
=================================
The initial ingestion run created subjects under "Class 11 / General" instead
of the canonical "HS 1st Year / Science|Arts|Commerce" hierarchy because
`upsert_subject()` used "Class 11" while AHSEC stores "HS 1st Year".

This script applies two strategies per orphaned subject:

  A. CONTENT MIGRATION (canonical subject + matching chapter numbers exist)
     Copy notes_en/as, rag_sections, qa_rag_sections, published_topics from
     each orphaned chapter into the matching canonical chapter, then delete
     the orphaned chapters and subject.
     Applies to: Chemistry (ch1-ch6), Accountancy (ch1-ch5), Business Studies.

  B. SUBJECT RELOCATION (no canonical match, or chapter structure differs)
     Move the orphaned subject itself into a "General" stream under the proper
     canonical class (HS 1st Year), so students can reach it via the correct
     board → class → subject path.
     Applies to: English Core (different chapter structure), Finance,
     Swadesh Adhyayan, Education (empty canonical).

Run from apps/backend/:
    python3 -m scripts.ahsec_migrate_orphaned [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S", handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("ahsec_migrate")

CONTENT_FIELDS = [
    "notes_en", "notes_as",
    "rag_sections_en", "rag_sections_as",
    "rag_text_en", "rag_text_as",
    "qa_rag_sections_en", "qa_rag_sections_as",
    "qa_rag_text_en", "qa_rag_text_as",
    "qa_text_en", "qa_text_as",
    "published_topics",
    "notes_rag_updated_at", "notes_rag_indexed_at",
    "qa_rag_updated_at", "qa_rag_indexed_at",
    "rag_updated_at", "rag_indexed_at",
    "notes_generated", "word_count",
]

# IDs of the orphaned hierarchy created by the bad ingestion run
ORPHAN_STREAM_ID = "6a735099d00b86a912eda1ed"   # Class 11 / General
ORPHAN_CLASS_ID  = "6a735099d00b86a912eda1ec"   # Class 11 (wrong)

# Canonical class IDs for AHSEC  
CANONICAL_CLASS_11_NAME = "HS 1st Year"
CANONICAL_CLASS_12_NAME = "HS 2nd Year"


def _slugify(text: str) -> str:
    import re, unicodedata
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[\s_-]+", "-", text).strip("-")


async def run(dry_run: bool = False) -> None:
    import motor.motor_asyncio
    from bson import ObjectId

    client = motor.motor_asyncio.AsyncIOMotorClient(os.environ["MONGODB_URL"])
    db = client["syrabit_prod"]
    subjects_col = db["subjects"]
    chapters_col = db["chapters"]
    streams_col  = db["streams"]
    classes_col  = db["classes"]
    boards_col   = db["boards"]

    orphan_stream_oid = ObjectId(ORPHAN_STREAM_ID)
    orphan_class_oid  = ObjectId(ORPHAN_CLASS_ID)

    # ── 1. Collect orphaned subjects ──────────────────────────────────────────
    orphan_subjs = await subjects_col.find(
        {"stream_id": orphan_stream_oid}
    ).to_list(100)

    if not orphan_subjs:
        log.info("No orphaned subjects found — nothing to migrate.")
        return
    log.info(f"Found {len(orphan_subjs)} orphaned subjects to migrate.")

    # ── 2. Identify canonical class hierarchy ─────────────────────────────────
    ahsec = await boards_col.find_one({"slug": "ahsec"})
    all_classes = await classes_col.find(
        {"board_id": ahsec["_id"], "_id": {"$ne": orphan_class_oid}}
    ).to_list(20)

    # We assume all orphaned subjects belong to Class 11 (the only orphaned class).
    # Find "HS 1st Year" as the canonical target class.
    canonical_cls = next(
        (c for c in all_classes if "1st" in c.get("name", "") or c.get("name","") == "HS 1st Year"),
        None
    )
    if not canonical_cls:
        log.error("Cannot find canonical 'HS 1st Year' class — aborting")
        return
    log.info(f"Canonical class: '{canonical_cls['name']}' id={canonical_cls['_id']}")

    all_canonical_streams = await streams_col.find(
        {"class_id": canonical_cls["_id"]}
    ).to_list(20)
    canonical_stream_ids = [st["_id"] for st in all_canonical_streams]

    # Get or create a "General" stream under canonical class for relocated subjects
    general_stream = next(
        (st for st in all_canonical_streams if st.get("name") == "General"), None
    )
    if not general_stream:
        if dry_run:
            log.info("[DRY RUN] Would create 'General' stream under canonical class")
            general_stream = {"_id": ObjectId(), "name": "General"}  # placeholder
        else:
            doc = {
                "name": "General",
                "class_id": canonical_cls["_id"],
                "board_id": ahsec["_id"],
                "status": "active",
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
            result = await streams_col.insert_one(doc)
            general_stream = {**doc, "_id": result.inserted_id}
            log.info(f"Created 'General' stream under canonical class (id={result.inserted_id})")

    migrated_content_chapters = 0
    relocated_subjects = 0
    skipped = 0

    for orphan_subj in orphan_subjs:
        slug = orphan_subj.get("slug", "")
        name = orphan_subj.get("name", "")
        log.info(f"\n── Subject '{name}' (slug={slug!r}) ──")

        # ── Find canonical subject (any proper stream under canonical class) ──
        canon_subj = await subjects_col.find_one({
            "slug": slug,
            "stream_id": {"$in": canonical_stream_ids},
        })
        if not canon_subj:
            for sid in canonical_stream_ids:
                all_s = await subjects_col.find({"stream_id": sid}).to_list(200)
                for s in all_s:
                    if _slugify(s.get("name", "")) == slug:
                        canon_subj = s
                        break
                if canon_subj:
                    break

        # ── Strategy A: canonical exists + chapter numbers can match ──────────
        if canon_subj:
            orphan_chs = await chapters_col.find(
                {"subject_id": orphan_subj["_id"]}
            ).to_list(200)
            canon_chs = await chapters_col.find(
                {"subject_id": canon_subj["_id"]}
            ).to_list(200)
            canon_by_num = {c.get("chapter_number"): c for c in canon_chs}

            # Check if at least one orphaned chapter number matches a canonical chapter
            matching_nums = [
                och.get("chapter_number")
                for och in orphan_chs
                if och.get("chapter_number") in canon_by_num
                and any(och.get(f) for f in CONTENT_FIELDS)
            ]

            if matching_nums:
                cs = next((st for st in all_canonical_streams if st["_id"] == canon_subj.get("stream_id")), None)
                log.info(f"  Strategy A (content migration) → canonical stream={cs.get('name') if cs else '?'}")
                for och in orphan_chs:
                    ch_num = och.get("chapter_number")
                    canon_ch = canon_by_num.get(ch_num)
                    if not canon_ch:
                        log.warning(f"  ch{ch_num} '{och.get('title')}': no canonical chapter — skipping")
                        skipped += 1
                        continue
                    has_content = any(och.get(f) for f in CONTENT_FIELDS)
                    if not has_content:
                        log.info(f"  ch{ch_num}: no content to copy — skipping")
                        skipped += 1
                        continue
                    update = {"updated_at": datetime.now(timezone.utc)}
                    for field in CONTENT_FIELDS:
                        val = och.get(field)
                        if val is not None and val != "" and val != []:
                            update[field] = val
                    log.info(f"  ch{ch_num} '{och.get('title')}': copy "
                             f"notes={len(och.get('notes_en') or '')}c "
                             f"rag={len(och.get('rag_sections_en') or [])} "
                             f"qa={len(och.get('qa_rag_sections_en') or [])}")
                    if not dry_run:
                        await chapters_col.update_one(
                            {"_id": canon_ch["_id"]}, {"$set": update}
                        )
                    migrated_content_chapters += 1

                # Delete orphaned chapters + subject
                orphan_ch_ids = [och["_id"] for och in orphan_chs]
                if not dry_run and orphan_ch_ids:
                    res = await chapters_col.delete_many({"_id": {"$in": orphan_ch_ids}})
                    log.info(f"  Deleted {res.deleted_count} orphaned chapters")
                if not dry_run:
                    await subjects_col.delete_one({"_id": orphan_subj["_id"]})
                    log.info(f"  Deleted orphaned subject '{name}'")
                continue

        # ── Strategy B: no canonical match OR chapters structure differs ──────
        # Relocate the orphaned subject into the canonical General stream
        log.info(f"  Strategy B (relocation) → moving to canonical 'General' stream")
        if not dry_run:
            await subjects_col.update_one(
                {"_id": orphan_subj["_id"]},
                {"$set": {
                    "stream_id": general_stream["_id"],
                    "updated_at": datetime.now(timezone.utc),
                }},
            )
            log.info(f"  Relocated '{name}' to canonical General stream")
        else:
            log.info(f"  [DRY RUN] Would relocate '{name}' to canonical General stream")
        relocated_subjects += 1

    # ── 3. Clean up orphaned stream + class if now empty ─────────────────────
    if not dry_run:
        remaining = await subjects_col.count_documents({"stream_id": orphan_stream_oid})
        if remaining == 0:
            await streams_col.delete_one({"_id": orphan_stream_oid})
            log.info("\nDeleted orphaned 'General' stream under Class 11")
            cls_stream_count = await streams_col.count_documents({"class_id": orphan_class_oid})
            if cls_stream_count == 0:
                await classes_col.delete_one({"_id": orphan_class_oid})
                log.info("Deleted orphaned 'Class 11' class")
        else:
            log.warning(f"Orphaned stream still has {remaining} subjects — skipping stream/class delete")

    pfx = "[DRY RUN] " if dry_run else ""
    log.info(f"\n{pfx}Done: content-migrated {migrated_content_chapters} chapters, "
             f"relocated {relocated_subjects} subjects, skipped {skipped}.")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    asyncio.run(run(dry_run=args.dry_run))
