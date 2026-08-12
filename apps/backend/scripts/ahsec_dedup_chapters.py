"""
AHSEC Chapter Deduplication Script
====================================
Finds Chapter documents with duplicate (subject_id, title) pairs created when
the ingestion restarted mid-run, merges their content into the richest record,
and deletes the stale duplicates.

"Richest" = the document whose (len(notes_en) + len(qa_rag_sections_en) * 200)
score is highest.  All non-empty content fields from the losers are merged into
the winner before losers are deleted.

Before each loser chapter is deleted the script removes all dependent records
keyed by that chapter_id:
  • TopicEmbedding docs          (topic_embeddings collection)
  • v2 Chunk docs + Vectorize vectors  (chunks collection + CF Vectorize)
    ─ Vectorize vectors are deleted BEFORE the Mongo chunk rows so a
      Vectorize outage cannot leave stale vectors pointing at deleted docs.
  • v1 rag_chunks docs           (rag_chunks collection)

A loser is only deleted if EVERY required cleanup step succeeds.  If any step
fails the loser chapter is left intact and a warning is logged.

After all losers are cleaned up the winner is reindexed for all searchable
scopes (notes + important_questions Q&A) and topic embeddings are refreshed,
mirroring the existing reindex_chapter(scope="all") path.

Usage (run from apps/backend/):
    python3 -m scripts.ahsec_dedup_chapters [--dry-run] [--subject SLUG]
                                             [--no-reindex]

Options:
    --dry-run     Report duplicates and dependent-record counts without
                  touching the DB.
    --subject     Limit to a single subject slug (e.g. 'english-core').
    --no-reindex  Skip the post-merge ingest_chapter_v2 reindex step.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("ahsec_dedup")


# ── Scoring & merging ──────────────────────────────────────────────────────────

def _content_score(ch) -> int:
    """Higher = more content.  Used to pick the winner in a duplicate group."""
    score = 0
    score += len(ch.notes_en or "")
    score += len(ch.notes_as or "")
    score += len(ch.qa_rag_sections_en or []) * 200
    score += len(ch.qa_rag_sections_as or []) * 200
    score += len(ch.rag_sections_en or []) * 100
    score += len(ch.rag_sections_as or []) * 100
    score += len(ch.content_en or "")
    score += len(ch.content_as or "")
    return score


def _merge_into_winner(winner, loser) -> list[str]:
    """Copy non-empty fields from loser → winner (never overwrite non-empty).

    Returns a list of field names that were actually copied.
    """
    copied: list[str] = []

    text_fields = [
        "notes_en", "notes_as",
        "content_en", "content_as",
        "rag_text_en", "rag_text_as",
        "qa_text_en", "qa_text_as",
        "qa_rag_text_en", "qa_rag_text_as",
        "pyq_rag_text", "pyq_rag_text_as",
        "meta_description", "meta_description_as",
        "keywords", "source_pdf_url", "pyq_pdf_url",
    ]
    list_fields = [
        "rag_sections_en", "rag_sections_as",
        "qa_rag_sections_en", "qa_rag_sections_as",
        "pyq_papers", "published_topics",
    ]

    for field in text_fields:
        if getattr(loser, field, None) and not getattr(winner, field, None):
            setattr(winner, field, getattr(loser, field))
            copied.append(field)

    for field in list_fields:
        loser_val = getattr(loser, field, None) or []
        if loser_val and not (getattr(winner, field, None) or []):
            setattr(winner, field, loser_val)
            copied.append(field)

    # Timestamps: keep the earliest created_at
    if loser.created_at and (
        not winner.created_at or loser.created_at < winner.created_at
    ):
        winner.created_at = loser.created_at
        copied.append("created_at")

    return copied


# ── Dependent-record helpers ──────────────────────────────────────────────────

def _chapter_id_filter(chapter_id_str: str) -> dict:
    """Build a MongoDB filter that matches chapter_id stored as either a
    BSON ObjectId OR a plain string — required because TopicEmbedding.chapter_id
    is a FlexId field that content_publisher writes as an ObjectId (chapter.id),
    while other collections (Chunk, rag_chunks) store it as a plain string.

    Uses ``$in`` with both forms so neither representation is missed.
    A non-ObjectId string ID (e.g. a legacy 's13') is handled gracefully by
    only including the string form when bson import is unavailable or the value
    isn't a valid hex ObjectId.
    """
    filters: list = [chapter_id_str]
    try:
        from bson import ObjectId as _OID
        if len(chapter_id_str) == 24:
            filters.append(_OID(chapter_id_str))
    except Exception:
        pass
    return {"chapter_id": {"$in": filters}}


async def _count_dependents(db, chapter_id_str: str) -> dict:
    """Return counts of dependent records (used for dry-run output).

    Uses dual-representation filter for topic_embeddings (FlexId field);
    plain string filter for v2 chunks and v1 rag_chunks (str fields).
    """
    te_filter = _chapter_id_filter(chapter_id_str)
    return {
        "topic_embeddings": await db.topic_embeddings.count_documents(te_filter),
        "v2_chunks": await db.chunks.count_documents(
            {"chapter_id": chapter_id_str}
        ),
        "v1_rag_chunks": await db.rag_chunks.count_documents(
            {"chapter_id": chapter_id_str}
        ),
    }


async def _cleanup_loser_dependents(db, chapter_id_str: str) -> tuple[bool, dict]:
    """Delete all records dependent on a loser chapter_id.

    Operation order is designed so that a mid-step failure cannot leave stale
    search data behind:

      1. Collect vector_ids from chunks (read-only, no deletion yet).
      2. Delete Vectorize vectors FIRST.  If this fails the Mongo chunk rows
         remain and can be retried; nothing is orphaned.
      3. Delete the Mongo chunk rows only after Vectorize confirms success.
      4. Delete TopicEmbedding docs.
      5. Delete v1 rag_chunks docs.

    Returns (success: bool, summary: dict).
    success=False means at least one required step failed; the loser chapter
    should NOT be deleted.
    """
    summary: dict = {}
    ok = True  # flipped to False on any required-step failure

    # ── 1. Collect v2 chunk metadata (read-only) ──────────────────────────────
    try:
        chunk_docs = await db.chunks.find(
            {"chapter_id": chapter_id_str}, {"_id": 1, "vector_id": 1}
        ).to_list(length=None)
        vector_ids = [d["vector_id"] for d in chunk_docs if d.get("vector_id")]
        summary["v2_chunk_docs_found"] = len(chunk_docs)
        summary["vectorize_ids_found"] = len(vector_ids)
    except Exception as e:
        log.warning(f"      Cannot read v2 chunks for {chapter_id_str}: {e}")
        summary["read_chunks_error"] = str(e)
        # Can't proceed safely if we can't enumerate vectors to delete
        return False, summary

    # ── 2. Delete Vectorize vectors FIRST ────────────────────────────────────
    if vector_ids:
        try:
            from app.services.vectorize.client import vectorize_client
            BATCH = 1000
            total_deleted = 0
            for i in range(0, len(vector_ids), BATCH):
                resp = await vectorize_client.delete(vector_ids[i : i + BATCH])
                total_deleted += resp.get("count", len(vector_ids[i : i + BATCH]))
            summary["vectorize_deleted"] = total_deleted
            log.info(f"      Deleted {total_deleted} Vectorize vector(s)")
        except Exception as e:
            log.warning(
                f"      Vectorize deletion failed for {chapter_id_str}: {e}\n"
                f"      Keeping Mongo chunks intact to allow retry — loser will not be deleted."
            )
            summary["vectorize_error"] = str(e)
            ok = False

    # ── 3. Delete Mongo v2 chunks (only if Vectorize step succeeded/was empty) ─
    if ok and chunk_docs:
        try:
            result = await db.chunks.delete_many({"chapter_id": chapter_id_str})
            summary["v2_chunks_deleted"] = result.deleted_count
            log.info(f"      Deleted {result.deleted_count} v2 Chunk(s)")
        except Exception as e:
            log.warning(f"      v2 Chunk Mongo deletion failed: {e}")
            summary["v2_chunks_error"] = str(e)
            ok = False

    # ── 4. Delete TopicEmbedding docs ─────────────────────────────────────────
    # TopicEmbedding.chapter_id is a FlexId written as an ObjectId by the
    # publisher; use the dual-representation filter so both storage forms
    # (ObjectId and string) are matched and deleted.
    if ok:
        try:
            te_filter = _chapter_id_filter(chapter_id_str)
            result = await db.topic_embeddings.delete_many(te_filter)
            summary["topic_embeddings_deleted"] = result.deleted_count
            if result.deleted_count:
                log.info(f"      Deleted {result.deleted_count} TopicEmbedding(s)")
        except Exception as e:
            log.warning(f"      TopicEmbedding cleanup failed: {e}")
            summary["topic_embeddings_error"] = str(e)
            ok = False

    # ── 5. Delete v1 rag_chunks ───────────────────────────────────────────────
    if ok:
        try:
            result = await db.rag_chunks.delete_many({"chapter_id": chapter_id_str})
            summary["v1_rag_chunks_deleted"] = result.deleted_count
            if result.deleted_count:
                log.info(f"      Deleted {result.deleted_count} v1 rag_chunk(s)")
        except Exception as e:
            log.warning(f"      v1 rag_chunks cleanup failed: {e}")
            summary["v1_rag_chunks_error"] = str(e)
            ok = False

    return ok, summary


# ── Winner reindex ─────────────────────────────────────────────────────────────

async def _reindex_winner(winner) -> None:
    """Reindex the winner for all searchable scopes (notes + Q&A) and refresh
    topic embeddings.  Mirrors reindex_chapter(scope="all") in ahsec_ingest.py.

    Errors are logged as warnings and do not propagate — the merge/delete work
    is already committed at this point.
    """
    from app.services.rag.ingestion_v2 import ingest_chapter_v2
    from beanie import PydanticObjectId

    chapter_id_str = str(winner.id)
    now = datetime.now(timezone.utc)
    meta = {
        "subject_id": str(winner.subject_id),
        "chapter_id": chapter_id_str,
        "chapter_slug": winner.slug or "",
    }

    def _flatten_notes(sections: list) -> str:
        parts = []
        for s in sections or []:
            t = (s.get("title") or "").strip()
            c = (s.get("content") or "").strip()
            if t:
                parts.append(f"## {t}")
            if c:
                parts.append(c)
        return "\n\n".join(parts)

    def _flatten_qa(sections: list) -> str:
        parts = []
        for s in sections or []:
            q = (s.get("question") or "").strip()
            a = (s.get("answer") or "").strip()
            if q:
                parts.append(f"Q: {q}")
            if a:
                parts.append(f"A: {a}")
            if q or a:
                parts.append("")
        return "\n".join(parts).strip()

    # ── Notes scope ───────────────────────────────────────────────────────────
    en_notes = _flatten_notes(winner.rag_sections_en) or winner.rag_text_en or None
    as_notes = _flatten_notes(winner.rag_sections_as) or winner.rag_text_as or None
    if en_notes or as_notes:
        try:
            await ingest_chapter_v2(
                chapter_id=chapter_id_str,
                content_en=en_notes,
                content_as=as_notes,
                metadata={**meta, "source_type": "notes"},
                source_type="notes",
            )
            winner.notes_rag_indexed_at = now
            winner.rag_indexed_at = now
            log.info(f"    Winner: notes reindexed")
        except Exception as e:
            log.warning(f"    Winner notes reindex failed (non-fatal): {e}")

    # ── Q&A / important_questions scope ───────────────────────────────────────
    en_qa = _flatten_qa(winner.qa_rag_sections_en) or None
    as_qa = _flatten_qa(winner.qa_rag_sections_as) or None
    if en_qa or as_qa:
        try:
            await ingest_chapter_v2(
                chapter_id=chapter_id_str,
                content_en=en_qa,
                content_as=as_qa,
                metadata={**meta, "source_type": "important_questions"},
                source_type="important_questions",
            )
            winner.qa_rag_indexed_at = now
            log.info(f"    Winner: Q&A reindexed")
        except Exception as e:
            log.warning(f"    Winner Q&A reindex failed (non-fatal): {e}")

    # Persist updated index timestamps
    winner.updated_at = now
    try:
        await winner.save()
    except Exception as e:
        log.warning(f"    Winner timestamp save failed: {e}")

    # ── Refresh topic embeddings ──────────────────────────────────────────────
    if winner.published_topics:
        try:
            from app.services.content_publisher import (
                content_publisher_service as _cp,
            )
            hierarchy = await _cp._resolve_hierarchy(winner)
            await _cp._generate_topic_embeddings(winner, hierarchy)
            log.info(f"    Winner: topic embeddings refreshed")
        except Exception as e:
            log.warning(f"    Winner topic embedding refresh failed (non-fatal): {e}")


# ── Main run ───────────────────────────────────────────────────────────────────

async def run(
    dry_run: bool = False,
    subject_filter: str | None = None,
    no_reindex: bool = False,
) -> None:
    import motor.motor_asyncio
    from beanie import init_beanie
    from app.core.config import get_settings
    from app.models.content import (
        Board, Class, Stream, Subject, Chapter,
        ContentAuditLog, TopicEmbedding, QuestionPaper,
    )
    from app.models.rag import RagDocument, Chunk, ContentNode

    settings = get_settings()
    mongo_url = settings.MONGODB_URL
    db_name = mongo_url.split("/")[-1].split("?")[0] or "syrabit"
    client = motor.motor_asyncio.AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    await init_beanie(
        database=db,
        document_models=[
            Board, Class, Stream, Subject, Chapter,
            ContentAuditLog, TopicEmbedding, QuestionPaper,
            RagDocument, Chunk, ContentNode,
        ],
    )
    log.info(f"Connected to MongoDB database '{db_name}'")

    # ── Build filter ──────────────────────────────────────────────────────────
    query: dict = {}
    if subject_filter:
        subj = await Subject.find_one({"slug": subject_filter})
        if not subj:
            log.error(f"Subject slug '{subject_filter}' not found in DB")
            return
        query["subject_id"] = subj.id
        log.info(f"Filtering to subject: {subj.name} (id={subj.id})")

    # ── Load all matching chapters ────────────────────────────────────────────
    all_chapters = await Chapter.find(query).to_list(length=50000)
    log.info(f"Loaded {len(all_chapters)} chapters")

    # ── Group by (subject_id, normalised_title) ───────────────────────────────
    groups: dict[tuple, list] = {}
    for ch in all_chapters:
        key = (str(ch.subject_id), ch.title.strip().lower())
        groups.setdefault(key, []).append(ch)

    dup_groups = {k: v for k, v in groups.items() if len(v) > 1}
    log.info(f"Found {len(dup_groups)} duplicate group(s)")

    if not dup_groups:
        log.info("No duplicates found — nothing to do.")
        return

    total_deleted = 0
    total_skipped = 0
    total_merged = 0

    for (subject_id, _title_key), dupes in dup_groups.items():
        # Sort: richest first
        dupes.sort(key=_content_score, reverse=True)
        winner = dupes[0]
        losers = dupes[1:]

        log.info(
            f"\n[DUP] subject={subject_id} title='{winner.title}'\n"
            f"  Winner : #{winner.chapter_number} id={winner.id} "
            f"score={_content_score(winner)}\n"
            + "\n".join(
                f"  Loser  : #{l.chapter_number} id={l.id} "
                f"score={_content_score(l)}"
                for l in losers
            )
        )

        # Merge content from each loser into winner
        for loser in losers:
            copied = _merge_into_winner(winner, loser)
            if copied:
                log.info(f"    Merged from #{loser.chapter_number}: {copied}")

        # Count and report dependents for each loser
        for loser in losers:
            deps = await _count_dependents(db, str(loser.id))
            log.info(
                f"    Dependents of loser #{loser.chapter_number} (id={loser.id}): "
                f"topic_embeddings={deps['topic_embeddings']}, "
                f"v2_chunks={deps['v2_chunks']}, "
                f"v1_rag_chunks={deps['v1_rag_chunks']}"
            )

        if dry_run:
            log.info(
                f"    [dry-run] Would delete {len(losers)} loser(s), "
                f"save winner, and "
                f"{'skip' if no_reindex else 'run'} reindex"
            )
            continue

        # ── Save winner with merged content ───────────────────────────────────
        winner.updated_at = datetime.now(timezone.utc)
        await winner.save()
        total_merged += 1

        # ── For each loser: clean up dependents, then delete ──────────────────
        for loser in losers:
            loser_id = str(loser.id)
            log.info(
                f"    Cleaning up dependents of loser "
                f"#{loser.chapter_number} (id={loser_id})"
            )
            cleanup_ok, summary = await _cleanup_loser_dependents(db, loser_id)

            if not cleanup_ok:
                log.warning(
                    f"    One or more cleanup steps failed for loser {loser_id}. "
                    f"Summary: {summary}\n"
                    f"    Skipping deletion of this loser — fix the error and re-run."
                )
                total_skipped += 1
                continue

            await loser.delete()
            log.info(
                f"    Deleted loser chapter id={loser_id} "
                f"(#{loser.chapter_number})"
            )
            total_deleted += 1

        # ── Reindex winner for all scopes ─────────────────────────────────────
        if not no_reindex:
            await _reindex_winner(winner)

    log.info(
        f"\n{'='*60}\n"
        f"Dedup complete: {total_merged} winner(s) updated, "
        f"{total_deleted} duplicate(s) deleted, "
        f"{total_skipped} loser(s) skipped due to cleanup errors"
        + (" [dry-run — no changes written]" if dry_run else "")
    )


# ── Entry point ────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Deduplicate AHSEC chapter records")
    p.add_argument(
        "--dry-run", action="store_true",
        help="Report duplicates and dependent counts without modifying the DB",
    )
    p.add_argument(
        "--subject", type=str, default=None,
        help="Limit to one subject slug (e.g. 'english-core')",
    )
    p.add_argument(
        "--no-reindex", action="store_true",
        help="Skip the post-merge ingest_chapter_v2 reindex step",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(run(
        dry_run=args.dry_run,
        subject_filter=args.subject,
        no_reindex=args.no_reindex,
    ))
