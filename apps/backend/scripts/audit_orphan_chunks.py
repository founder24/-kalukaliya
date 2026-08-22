"""
One-time audit script: find and remove Vectorize-only orphan chunks left by
interrupted RAG indexing runs.

Background
----------
The retrieval pipeline queries Cloudflare Vectorize first, receives a list of
vector IDs, then hydrates each hit from the MongoDB ``chunks`` collection.
If a vector exists in Vectorize but its matching MongoDB document was deleted
(e.g. a reindex was interrupted between the Mongo-delete and Vectorize-delete
steps), the hydration step returns nothing for that hit → the chat answer is
silently empty.  These are "Vectorize-only orphans" and are the root cause
of the empty-answer condition.

What this script can detect
---------------------------
Because Cloudflare Vectorize Standard tier has no "list all vectors" API,
we CANNOT directly enumerate all vector IDs that exist in Vectorize.  What
we CAN do is the inverse check:

  For every chunk in MongoDB, verify its vector_id exists in Vectorize.
  Any chunk whose vector_id is ABSENT from Vectorize is a Mongo-only orphan
  (harmless — Vectorize will never return it, so chat is unaffected).

This script therefore removes *Mongo-only* orphans, which reclaims MongoDB
storage but does NOT fix empty-answer conditions (those require Vectorize-only
orphan cleanup).

Remediating Vectorize-only orphans (the real empty-answer cause)
----------------------------------------------------------------
Because we cannot enumerate Vectorize-only orphans without a "list all" API,
the remediation path is a full chapter reindex via the admin panel or the
AHSEC ingestion script.  The revised purge ordering in ingestion_v2.py now
ensures that reindexes delete Vectorize BEFORE Mongo, so:

  • Future reindexes cannot produce new Vectorize-only orphans.
  • Re-indexing a chapter that had a previous interrupted run will attempt to
    delete the OLD vector IDs (collected from Mongo) before writing new ones.
    NOTE: if the old vector IDs are no longer in MongoDB (they were already
    deleted in a previous failed run), the pre-purge step will collect no IDs,
    and the stale Vectorize vectors will REMAIN until a full index rebuild.

A full index rebuild (delete all Vectorize vectors, re-run all chapter ingest)
is the only complete remediation for pre-existing stale vectors whose Mongo
rows no longer exist.  That is out of scope for this script.

Usage
-----
    # Dry-run (report only, no deletes):
    python3 -m scripts.audit_orphan_chunks

    # Live run (delete Mongo-only orphans from MongoDB):
    python3 -m scripts.audit_orphan_chunks --delete

    # Limit to a specific chapter:
    python3 -m scripts.audit_orphan_chunks --chapter-id <id> --delete

    # Adjust Vectorize batch size (default 100):
    python3 -m scripts.audit_orphan_chunks --batch 50 --delete

Safety guarantee
----------------
The script is fail-CLOSED: if *any* Vectorize getByIds batch fails (network
error, rate limit, auth error), the entire audit aborts WITHOUT deleting
anything from MongoDB.  A partial verification is indistinguishable from
"vector absent", so partial results must never drive deletions.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
logger = logging.getLogger(__name__)

_VECTORIZE_BATCH = 100  # getByIds accepts up to 100 IDs per call

# ---------------------------------------------------------------------------
# Vectorize batch check — FAIL-CLOSED
# ---------------------------------------------------------------------------


class VectorizeVerificationError(RuntimeError):
    """Raised when any Vectorize getByIds batch fails.

    Callers must abort the delete run when this is raised — a failed batch
    is indistinguishable from "vectors absent", so proceeding with deletions
    would risk destroying valid chunks.
    """


async def _vectorize_existing_ids(vector_ids: list[str], batch_size: int) -> set[str]:
    """
    Return the subset of vector_ids confirmed to exist in Cloudflare Vectorize.

    Fail-closed: raises ``VectorizeVerificationError`` on ANY batch failure so
    that the caller can abort without deleting anything.  A caught exception
    must never be treated as "vector not found".
    """
    from app.services.vectorize.client import vectorize_client

    found: set[str] = set()
    for i in range(0, len(vector_ids), batch_size):
        batch = vector_ids[i : i + batch_size]
        try:
            result = await vectorize_client.get_by_ids(batch)
        except Exception as exc:
            raise VectorizeVerificationError(
                f"Vectorize getByIds failed for batch {i}–{i + len(batch)}: {exc}. "
                "Aborting audit — partial verification must not drive deletions."
            ) from exc

        if isinstance(result, list):
            for v in result:
                vid = v.get("id") if isinstance(v, dict) else None
                if vid:
                    found.add(vid)
        elif isinstance(result, dict):
            for v in result.get("result", []):
                vid = v.get("id") if isinstance(v, dict) else None
                if vid:
                    found.add(vid)

    return found


# ---------------------------------------------------------------------------
# Main audit logic
# ---------------------------------------------------------------------------


async def audit(
    delete: bool = False,
    chapter_id: Optional[str] = None,
    batch_size: int = _VECTORIZE_BATCH,
) -> dict:
    os.environ.setdefault("APP_ENV", "development")
    from app.db.mongo import get_mongo_client
    from app.config import settings as _s

    col = get_mongo_client()[_s.MONGODB_DB_NAME]["chunks"]

    # ── 1. Load chunk metadata from MongoDB ──────────────────────────────────
    query: dict = {}
    if chapter_id:
        query["chapter_id"] = chapter_id

    projection = {
        "_id": 1, "vector_id": 1, "document_id": 1,
        "chapter_id": 1, "medium": 1, "source_type": 1,
        # run_id is included so deletes are generation-conditional: a reindex that
        # replaces a row with the same _id but a different run_id between the
        # verify step and the delete step will NOT be touched.
        "run_id": 1,
    }
    logger.info(
        "Loading chunks from MongoDB%s …",
        f" (chapter_id={chapter_id})" if chapter_id else "",
    )
    all_docs = await col.find(query, projection).to_list(length=None)
    logger.info("  → %d chunk documents loaded", len(all_docs))

    if not all_docs:
        logger.info("Nothing to audit.")
        return {"total": 0, "orphans": 0, "deleted": 0, "aborted": False}

    # ── 2. Batch-check against Vectorize (FAIL-CLOSED) ───────────────────────
    vector_ids = [str(d.get("vector_id") or d["_id"]) for d in all_docs]
    # Map vector_id → (mongo_id, run_id) so the delete step can be
    # generation-conditional.  A concurrent reindex that replaces a chunk
    # between verify and delete will write a new run_id; the conditional delete
    # {_id: old_id, run_id: old_run} won't match the replacement row.
    vid_to_record: dict[str, dict] = {
        str(d.get("vector_id") or d["_id"]): {
            "_id": str(d["_id"]),
            "run_id": d.get("run_id"),
        }
        for d in all_docs
    }

    logger.info(
        "Verifying %d vector IDs against Cloudflare Vectorize (batch=%d) …",
        len(vector_ids), batch_size,
    )
    try:
        found_in_vectorize = await _vectorize_existing_ids(vector_ids, batch_size)
    except VectorizeVerificationError as exc:
        logger.error("AUDIT ABORTED: %s", exc)
        logger.error("No MongoDB chunks were deleted.  Fix Vectorize connectivity and retry.")
        return {
            "total": len(all_docs),
            "found_in_vectorize": 0,
            "orphans": 0,
            "deleted": 0,
            "aborted": True,
            "abort_reason": str(exc),
        }

    logger.info(
        "  → %d / %d vectors confirmed in Vectorize",
        len(found_in_vectorize), len(vector_ids),
    )

    # ── 3. Identify Mongo-only orphans ────────────────────────────────────────
    # These are chunks whose vector_id is absent from Vectorize — harmless for
    # chat (Vectorize will never return them), but they waste MongoDB storage.
    orphan_vids = [vid for vid in vector_ids if vid not in found_in_vectorize]
    # Build generation-aware records for the delete step.  Each record captures
    # the {_id, run_id} snapshot taken at load time so a reindex that replaces
    # the row between verify and delete (writing a new run_id) won't be touched.
    orphan_records = [vid_to_record[vid] for vid in orphan_vids]
    orphan_mids = [r["_id"] for r in orphan_records]

    logger.info("Mongo-only orphan chunks (absent from Vectorize): %d", len(orphan_mids))

    doc_summary: dict[str, int] = {}
    orphan_vid_set = set(orphan_vids)
    for d in all_docs:
        vid = str(d.get("vector_id") or d["_id"])
        if vid in orphan_vid_set:
            dk = d.get("document_id", "unknown")
            doc_summary[dk] = doc_summary.get(dk, 0) + 1

    if doc_summary:
        logger.info("Orphans by document_id:")
        for dk, cnt in sorted(doc_summary.items(), key=lambda x: -x[1])[:50]:
            logger.info("  %s  →  %d orphan chunk(s)", dk, cnt)
        if len(doc_summary) > 50:
            logger.info("  … and %d more document IDs", len(doc_summary) - 50)

    # ── 4. Delete (optional, generation-conditional) ──────────────────────────
    # Each delete is conditioned on the run_id captured at load time.  If a
    # reindex has since replaced the row (same _id, new run_id), the filter
    # {_id: old, run_id: old_run} won't match the replacement → no orphan created.
    deleted = 0
    if orphan_records and delete:
        logger.info("Deleting %d Mongo-only orphan chunk(s) from MongoDB …", len(orphan_records))
        from collections import defaultdict
        by_run_id: dict = defaultdict(list)
        for rec in orphan_records:
            by_run_id[rec.get("run_id")].append(rec["_id"])
        for run_id_val, ids in by_run_id.items():
            if run_id_val is not None:
                r = await col.delete_many({"_id": {"$in": ids}, "run_id": run_id_val})
            else:
                # Legacy chunks (no run_id field)
                r = await col.delete_many({"_id": {"$in": ids}, "run_id": {"$exists": False}})
            deleted += r.deleted_count
        logger.info("  → %d deleted", deleted)
    elif orphan_records:
        logger.info(
            "Dry-run: pass --delete to remove these %d Mongo-only orphan chunks",
            len(orphan_records),
        )

    return {
        "total": len(all_docs),
        "found_in_vectorize": len(found_in_vectorize),
        "orphans": len(orphan_mids),
        "deleted": deleted,
        "aborted": False,
        "orphan_document_ids": list(doc_summary.keys()),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Audit MongoDB chunks against Cloudflare Vectorize and optionally "
            "remove Mongo-only orphans.  Fails closed: aborts without deleting "
            "anything if any Vectorize verification batch fails."
        )
    )
    parser.add_argument(
        "--delete", action="store_true",
        help="Delete Mongo-only orphan chunks from MongoDB (default: dry-run report only)",
    )
    parser.add_argument(
        "--chapter-id", default=None,
        help="Limit audit to a single chapter_id",
    )
    parser.add_argument(
        "--batch", type=int, default=_VECTORIZE_BATCH,
        help=f"Vectorize getByIds batch size (default {_VECTORIZE_BATCH})",
    )
    args = parser.parse_args()

    result = asyncio.run(
        audit(delete=args.delete, chapter_id=args.chapter_id, batch_size=args.batch)
    )

    print("\n=== Audit Summary ===")
    if result.get("aborted"):
        print(f"  ✗  ABORTED: {result.get('abort_reason', 'unknown error')}")
        print("     No MongoDB chunks were modified.")
        sys.exit(2)

    print(f"  Total MongoDB chunks checked : {result['total']}")
    print(f"  Confirmed in Vectorize       : {result['found_in_vectorize']}")
    print(f"  Mongo-only orphan chunks     : {result['orphans']}")
    print(f"  Deleted from MongoDB         : {result['deleted']}")
    print()
    print("  Note: Vectorize-only orphans (the real empty-answer cause) are handled")
    print("  by re-running the chapter reindex, which overwrites stale vectors.")

    if result["orphans"] and not args.delete:
        print(f"\n  ⚠  Run with --delete to remove {result['orphans']} Mongo-only orphan(s).")
    elif result["orphans"] == 0:
        print("\n  ✓  No Mongo-only orphans found.")

    sys.exit(0 if not result.get("aborted") else 2)


if __name__ == "__main__":
    main()
