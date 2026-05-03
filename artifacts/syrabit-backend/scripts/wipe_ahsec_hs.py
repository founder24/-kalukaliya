"""Destructive wipe of AHSEC HS 1st & 2nd Year content (Task #287).

Removes from MongoDB:
  * subjects   where class_id ∈ {c1, c2}
  * chapters   where subject is in those classes (joined via subject ids)
  * topics     where chapter is in those classes
  * seo_pages  where chapter/subject is in those classes (best-effort)
  * qa_pairs   where chapter/subject is in those classes (best-effort)
  * chunks     where subject_id is in those classes

And from Cloudflare Vectorize syllabus-index:
  * Vectors filtered by metadata.class_id ∈ {c1, c2}  (best-effort —
    Vectorize delete-by-filter is invoked through `syllabus_embedder`'s
    public helper if available; otherwise logged for manual cleanup.)

Usage:
  python -m scripts.wipe_ahsec_hs                  # dry-run (default)
  python -m scripts.wipe_ahsec_hs --execute        # actually delete
  python -m scripts.wipe_ahsec_hs --execute --skip-vectorize

Always writes a JSON report to data/wipe_ahsec_hs_report.json.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from motor.motor_asyncio import AsyncIOMotorClient
from config import MONGO_URL, DB_NAME

ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "data" / "wipe_ahsec_hs_report.json"

TARGET_CLASSES = ["c1", "c2"]


async def gather_ids(db) -> dict:
    subj_ids: list[str] = []
    async for s in db.subjects.find({"class_id": {"$in": TARGET_CLASSES}}, {"id": 1, "_id": 0}):
        if s.get("id"):
            subj_ids.append(s["id"])
    chap_ids: list[str] = []
    async for c in db.chapters.find({"subject_id": {"$in": subj_ids}}, {"id": 1, "_id": 0}):
        if c.get("id"):
            chap_ids.append(c["id"])
    return {"subject_ids": subj_ids, "chapter_ids": chap_ids}


async def count_targets(db, subj_ids: list[str], chap_ids: list[str]) -> dict:
    counts: dict[str, int] = {}
    counts["subjects"] = await db.subjects.count_documents({"class_id": {"$in": TARGET_CLASSES}})
    counts["chapters"] = await db.chapters.count_documents({"subject_id": {"$in": subj_ids}}) if subj_ids else 0
    counts["topics"] = await db.topics.count_documents({"chapter_id": {"$in": chap_ids}}) if chap_ids else 0
    counts["seo_pages"] = await db.seo_pages.count_documents({
        "$or": [
            {"chapter_id": {"$in": chap_ids}} if chap_ids else {"_": "_"},
            {"subject_id": {"$in": subj_ids}} if subj_ids else {"_": "_"},
        ]
    }) if subj_ids or chap_ids else 0
    counts["qa_pairs"] = await db.qa_pairs.count_documents({
        "$or": [
            {"chapter_id": {"$in": chap_ids}} if chap_ids else {"_": "_"},
            {"subject_id": {"$in": subj_ids}} if subj_ids else {"_": "_"},
        ]
    }) if subj_ids or chap_ids else 0
    counts["chunks"] = await db.chunks.count_documents({"subject_id": {"$in": subj_ids}}) if subj_ids else 0
    return counts


async def perform_wipe(db, subj_ids: list[str], chap_ids: list[str]) -> dict:
    deleted: dict[str, int] = {}
    deleted["subjects"] = (await db.subjects.delete_many({"class_id": {"$in": TARGET_CLASSES}})).deleted_count
    deleted["chapters"] = (await db.chapters.delete_many({"subject_id": {"$in": subj_ids}})).deleted_count if subj_ids else 0
    deleted["topics"] = (await db.topics.delete_many({"chapter_id": {"$in": chap_ids}})).deleted_count if chap_ids else 0
    deleted["seo_pages"] = (
        await db.seo_pages.delete_many({"$or": [
            {"chapter_id": {"$in": chap_ids}},
            {"subject_id": {"$in": subj_ids}},
        ]})
    ).deleted_count if (subj_ids or chap_ids) else 0
    deleted["qa_pairs"] = (
        await db.qa_pairs.delete_many({"$or": [
            {"chapter_id": {"$in": chap_ids}},
            {"subject_id": {"$in": subj_ids}},
        ]})
    ).deleted_count if (subj_ids or chap_ids) else 0
    deleted["chunks"] = (await db.chunks.delete_many({"subject_id": {"$in": subj_ids}})).deleted_count if subj_ids else 0
    return deleted


async def vectorize_wipe_best_effort(subj_ids: list[str]) -> dict:
    """Best-effort Vectorize delete-by-filter using syllabus_embedder helpers.
    If the helper isn't available or auth fails we just log and continue.
    """
    info = {"attempted": False, "deleted_keys": [], "errors": []}
    try:
        import syllabus_embedder as se
    except Exception as e:
        info["errors"].append(f"import syllabus_embedder failed: {e!r}")
        return info
    info["attempted"] = True
    deleter = getattr(se, "delete_by_subject_id", None)
    if not callable(deleter):
        info["errors"].append("syllabus_embedder.delete_by_subject_id not found — skipping; "
                              "manual `wrangler vectorize delete-vectors --filter` required")
        return info
    for sid in subj_ids:
        try:
            res = await deleter(sid)
            info["deleted_keys"].append({"subject_id": sid, "result": res})
        except Exception as e:
            info["errors"].append(f"delete sid={sid}: {e!r}")
    return info


async def main_async(args: argparse.Namespace) -> int:
    print(f"Connecting to MongoDB — db={DB_NAME}")
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=15000)
    db = client[DB_NAME]
    await db.command("ping")
    print("Connected.\n")

    ids = await gather_ids(db)
    subj_ids, chap_ids = ids["subject_ids"], ids["chapter_ids"]
    print(f"Found {len(subj_ids)} subjects + {len(chap_ids)} chapters under c1/c2")

    before = await count_targets(db, subj_ids, chap_ids)
    print("\nBEFORE counts:")
    for k, v in before.items():
        print(f"  {k:<12} {v:>8,}")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "executed": bool(args.execute),
        "target_classes": TARGET_CLASSES,
        "subject_ids": subj_ids,
        "chapter_ids_count": len(chap_ids),
        "before": before,
    }

    if not args.execute:
        print("\n[DRY-RUN] No deletes performed. Re-run with --execute to actually wipe.")
        report["after"] = before
        report["deleted"] = {k: 0 for k in before}
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Report: {REPORT_PATH}")
        client.close()
        return 0

    print("\n*** EXECUTING WIPE ***")
    deleted = await perform_wipe(db, subj_ids, chap_ids)
    print("\nDELETED counts:")
    for k, v in deleted.items():
        print(f"  {k:<12} {v:>8,}")
    report["deleted"] = deleted

    if not args.skip_vectorize:
        print("\nVectorize cleanup (best-effort)…")
        vec = await vectorize_wipe_best_effort(subj_ids)
        report["vectorize"] = vec
        print(f"  attempted={vec['attempted']}  errors={len(vec['errors'])}")
    else:
        report["vectorize"] = {"skipped": True}

    after = await count_targets(db, [], [])  # nothing left to match; counts will reflect remainder
    after = await count_targets(db, await _ids(db), await _chids(db, await _ids(db)))
    report["after"] = after
    print("\nAFTER counts (residual under c1/c2):")
    for k, v in after.items():
        print(f"  {k:<12} {v:>8,}")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nReport: {REPORT_PATH}")
    client.close()
    return 0


async def _ids(db) -> list[str]:
    out: list[str] = []
    async for s in db.subjects.find({"class_id": {"$in": TARGET_CLASSES}}, {"id": 1}):
        if s.get("id"):
            out.append(s["id"])
    return out


async def _chids(db, subj_ids: list[str]) -> list[str]:
    if not subj_ids:
        return []
    out: list[str] = []
    async for c in db.chapters.find({"subject_id": {"$in": subj_ids}}, {"id": 1}):
        if c.get("id"):
            out.append(c["id"])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="Actually perform deletes (default: dry-run)")
    ap.add_argument("--skip-vectorize", action="store_true", help="Skip Vectorize delete-by-filter step")
    args = ap.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
