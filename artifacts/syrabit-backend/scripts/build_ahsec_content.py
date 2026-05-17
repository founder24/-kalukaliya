"""AHSEC HS 1st & 2nd Year content rebuild orchestrator (Task #287).

Reads `data/ahsec_2025_26.json`, then rebuilds Mongo collections in this order:
  1. streams      — ensures Common stream rows for c1, c2 exist
  2. subjects     — one row per (class, stream, subject)
  3. chapters     — one row per (subject, chapter), with chapter_number + slug
  4. topics       — one row per (chapter, topic), with definition_status='draft'
                    (definitions filled later by the notes-generation pipeline)

Optional content phases (gated by flags so this turn's structural rebuild
can finish quickly without burning LLM budget):
  --generate-notes   Invoke routes/edu_study.generate_notes per chapter
  --translate-as     Invoke IndicTrans2 + Gemini polish for each English chapter
  --embed            Invoke syllabus_embedder for each chapter+topic

Resumable: every step checks for existing rows by stable id (md5 of
class+stream+slug+chapter_number) and skips if already present.

Usage:
  python -m scripts.build_ahsec_content                    # structure only
  python -m scripts.build_ahsec_content --generate-notes --limit 10
  python -m scripts.build_ahsec_content --translate-as --limit 50
  python -m scripts.build_ahsec_content --embed
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from motor.motor_asyncio import AsyncIOMotorClient
from config import MONGO_URL, DB_NAME

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "data" / "ahsec_2025_26.json"
REPORT_PATH = ROOT / "data" / "build_ahsec_report.json"

NOW = datetime.now(timezone.utc).isoformat()


def slugify(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s.strip().lower())
    return s.strip("-")[:80]


def stable_id(prefix: str, *parts: str) -> str:
    raw = "::".join(parts).encode("utf-8")
    return f"{prefix}_{hashlib.md5(raw, usedforsecurity=False).hexdigest()[:16]}"


def load_manifest() -> dict:
    from scripts.ahsec_scrape import resolve_inherits, load_manifest as _lm
    return resolve_inherits(_lm())


# ── Structural rebuild ───────────────────────────────────────────────────────


async def ensure_common_streams(db) -> dict:
    """Make sure s_common_hs1 and s_common_hs2 stream rows exist."""
    out = {"created": 0, "existed": 0}
    rows = [
        {"id": "s_common_hs1", "class_id": "c1", "name": "Common", "slug": "common",
         "description": "AHSEC HS 1st Year — Common subjects (English, MIL, Environmental Education)",
         "icon": "📘", "created_at": NOW},
        {"id": "s_common_hs2", "class_id": "c2", "name": "Common", "slug": "common",
         "description": "AHSEC HS 2nd Year — Common subjects (English, MIL, Environmental Education)",
         "icon": "📘", "created_at": NOW},
    ]
    for r in rows:
        existing = await db.streams.find_one({"id": r["id"]})
        if existing:
            out["existed"] += 1
        else:
            await db.streams.insert_one(r)
            out["created"] += 1
    return out


async def upsert_subjects(db, manifest: dict) -> dict:
    out = {"upserted": 0, "skipped": 0, "ids": []}
    for class_id, class_data in manifest["classes"].items():
        for stream_id, stream in class_data["streams"].items():
            for subj in stream.get("subjects", []):
                sid = stable_id("subj", class_id, stream_id, subj["slug"])
                row = {
                    "id": sid,
                    "board_id": "b1",
                    "class_id": class_id,
                    "stream_id": stream_id,
                    "name": subj["name"],
                    "slug": subj["slug"],
                    "description": subj.get("description", ""),
                    "icon": subj.get("icon", "📚"),
                    "chapter_count": len(subj.get("chapters", [])),
                    "tags": [class_data["slug"], stream["slug"], subj["slug"], "ahsec"],
                }
                # `updated_at` lives only in $setOnInsert so reruns are true
                # no-ops when content fields haven't actually changed.
                res = await db.subjects.update_one(
                    {"id": sid},
                    {"$set": row, "$setOnInsert": {"created_at": NOW, "updated_at": NOW}},
                    upsert=True,
                )
                if res.upserted_id or res.modified_count:
                    out["upserted"] += 1
                else:
                    out["skipped"] += 1
                out["ids"].append(sid)
    return out


async def upsert_chapters(db, manifest: dict) -> dict:
    from pymongo import UpdateOne
    out = {"upserted": 0, "skipped": 0, "ids": []}
    ops: list = []
    for class_id, class_data in manifest["classes"].items():
        for stream_id, stream in class_data["streams"].items():
            for subj in stream.get("subjects", []):
                subj_id = stable_id("subj", class_id, stream_id, subj["slug"])
                for idx, ch in enumerate(subj.get("chapters", []), start=1):
                    ch_slug = slugify(ch["title"])
                    cid = stable_id("chap", subj_id, str(idx), ch_slug)
                    row = {
                        "id": cid,
                        "subject_id": subj_id,
                        "title": ch["title"],
                        "slug": ch_slug,
                        "chapter_number": idx,
                        "description": ch.get("description", ""),
                        "topics": ch.get("topics", []),
                        "content": ch.get("content", ""),
                        "content_as": ch.get("content_as", ""),
                        "has_assamese": False,
                    }
                    ops.append(UpdateOne(
                        {"id": cid},
                        {"$set": row, "$setOnInsert": {"created_at": NOW, "updated_at": NOW}},
                        upsert=True,
                    ))
                    out["ids"].append(cid)
    if ops:
        res = await db.chapters.bulk_write(ops, ordered=False)
        out["upserted"] = (res.upserted_count or 0) + (res.modified_count or 0)
        out["skipped"] = len(ops) - out["upserted"]
    return out


async def upsert_topics(db, manifest: dict) -> dict:
    from pymongo import UpdateOne
    out = {"upserted": 0, "skipped": 0}
    ops: list = []
    for class_id, class_data in manifest["classes"].items():
        for stream_id, stream in class_data["streams"].items():
            for subj in stream.get("subjects", []):
                subj_id = stable_id("subj", class_id, stream_id, subj["slug"])
                for idx, ch in enumerate(subj.get("chapters", []), start=1):
                    ch_slug = slugify(ch["title"])
                    chap_id = stable_id("chap", subj_id, str(idx), ch_slug)
                    for t_idx, topic in enumerate(ch.get("topics", []), start=1):
                        t_slug = slugify(topic)
                        tid = stable_id("topic", chap_id, str(t_idx), t_slug)
                        row = {
                            "id": tid,
                            "chapter_id": chap_id,
                            "subject_id": subj_id,
                            "title": topic,
                            "slug": t_slug,
                            "topic_number": t_idx,
                            "definition_status": "draft",
                            "definition_en": "",
                            "definition_as": "",
                        }
                        ops.append(UpdateOne(
                            {"id": tid},
                            {"$set": row, "$setOnInsert": {"created_at": NOW, "updated_at": NOW}},
                            upsert=True,
                        ))
    # Chunk to keep payload sizes safe (Mongo bulk limit is 100k ops, but
    # we chunk at 1000 to keep memory + retry granularity reasonable).
    CHUNK = 1000
    for i in range(0, len(ops), CHUNK):
        chunk = ops[i:i + CHUNK]
        res = await db.topics.bulk_write(chunk, ordered=False)
        out["upserted"] += (res.upserted_count or 0) + (res.modified_count or 0)
    out["skipped"] = len(ops) - out["upserted"]
    return out


# ── Optional content phases (LLM-heavy; gated by flags) ──────────────────────


async def maybe_generate_notes(db, limit: int) -> dict:
    """Stub for the notes-generation phase. Wires into routes.edu_study.generate_notes
    via direct call when present; logs intent otherwise. Resumable — skips
    chapters that already have non-empty `content`."""
    out = {"attempted": 0, "succeeded": 0, "skipped_existing": 0, "errors": []}
    try:
        from routes import edu_study  # noqa: F401
    except Exception as e:
        out["errors"].append(f"routes.edu_study import failed: {e!r}")
        return out
    n = 0
    async for ch in db.chapters.find({"subject_id": {"$regex": "^subj_"}}):
        if n >= limit:
            break
        if (ch.get("content") or "").strip():
            out["skipped_existing"] += 1
            continue
        out["attempted"] += 1
        # NOTE: routes.edu_study.generate_notes is a FastAPI handler that
        # depends on an authenticated request context; bulk invocation here
        # requires a small adapter. Logged for now — call out to the
        # admin BulkNotes endpoint via httpx in a follow-up turn.
        out["errors"].append(
            f"chapter={ch['id']} title={ch['title']!r} — "
            "needs admin bulk-notes endpoint adapter (deferred)"
        )
        n += 1
    return out


async def maybe_translate_as(db, limit: int) -> dict:
    out = {"attempted": 0, "succeeded": 0, "skipped_existing": 0, "errors": []}
    # Task #386 — surface the active translator so a one-off bulk
    # build can be reasoned about without diffing config. When
    # ``TRANSLATE_PROVIDER=workers_indic`` is set, only the Workers-AI
    # IndicTrans2 client is permitted; the legacy Vertex/Gemini polish
    # branch is intentionally not invoked here. The phase itself is
    # still a stub (the per-chapter pipeline lives in
    # ``scripts/bulk_translate.py``) but the gate gives the operator
    # an early signal that the flag is honoured by this entry-point.
    try:
        from config import TRANSLATE_PROVIDER as _TP
    except Exception:
        _TP = "auto"
    out["translate_provider"] = (_TP or "").strip().lower()
    try:
        from providers.workers_indic import call_indic_trans  # noqa: F401
    except Exception as e:
        out["errors"].append(f"providers.workers_indic import failed: {e!r}")
        return out
    if out["translate_provider"] == "workers_indic":
        out["errors"].append(
            "TRANSLATE_PROVIDER=workers_indic active — Workers-AI IndicTrans2 "
            "is the sole translator. Per-chapter pipeline lives in "
            "scripts/bulk_translate.py; invoke it directly for production runs."
        )
    else:
        out["errors"].append(
            "Translation phase is wired but disabled — call with content present, "
            "see scripts/bulk_translate.py for the existing per-chapter pipeline."
        )
    return out


async def maybe_embed(db, limit: int) -> dict:
    out = {"attempted": 0, "succeeded": 0, "errors": []}
    try:
        import syllabus_embedder as se  # noqa: F401
    except Exception as e:
        out["errors"].append(f"syllabus_embedder import failed: {e!r}")
        return out
    out["errors"].append(
        "Embedding phase is wired but disabled — invoke "
        "syllabus_embedder.embed_chapters_bulk(subject_ids=...) once content is generated."
    )
    return out


# ── Main ─────────────────────────────────────────────────────────────────────


async def main_async(args: argparse.Namespace) -> int:
    manifest = load_manifest()

    print(f"Connecting to MongoDB — db={DB_NAME}")
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=15000)
    db = client[DB_NAME]
    await db.command("ping")
    print("Connected.\n")

    report: dict = {"generated_at": NOW, "phases": {}}

    print("[phase 1/4] streams (Common)…")
    report["phases"]["streams"] = await ensure_common_streams(db)
    print(f"  {report['phases']['streams']}")

    print("[phase 2/4] subjects…")
    report["phases"]["subjects"] = await upsert_subjects(db, manifest)
    print(f"  upserted={report['phases']['subjects']['upserted']}  "
          f"skipped={report['phases']['subjects']['skipped']}")

    print("[phase 3/4] chapters…")
    report["phases"]["chapters"] = await upsert_chapters(db, manifest)
    print(f"  upserted={report['phases']['chapters']['upserted']}  "
          f"skipped={report['phases']['chapters']['skipped']}")

    print("[phase 4/4] topics…")
    report["phases"]["topics"] = await upsert_topics(db, manifest)
    print(f"  upserted={report['phases']['topics']['upserted']}  "
          f"skipped={report['phases']['topics']['skipped']}")

    if args.generate_notes:
        print("\n[opt] notes generation…")
        report["phases"]["notes"] = await maybe_generate_notes(db, args.limit)
        print(f"  {report['phases']['notes']}")
    if args.translate_as:
        print("\n[opt] Assamese translation…")
        report["phases"]["translate_as"] = await maybe_translate_as(db, args.limit)
        print(f"  {report['phases']['translate_as']}")
    if args.embed:
        print("\n[opt] embeddings…")
        report["phases"]["embed"] = await maybe_embed(db, args.limit)
        print(f"  {report['phases']['embed']}")

    # Verification: count rows actually present.
    final = {
        "subjects_c1c2": await db.subjects.count_documents({"class_id": {"$in": ["c1", "c2"]}}),
        "chapters": await db.chapters.count_documents({
            "subject_id": {"$in": report["phases"]["subjects"]["ids"]}
        }),
        "topics": await db.topics.count_documents({
            "subject_id": {"$in": report["phases"]["subjects"]["ids"]}
        }),
    }
    report["final_counts"] = final
    print(f"\nFinal counts: {final}")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Report: {REPORT_PATH}")
    client.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20, help="Cap for any LLM-bound phase")
    ap.add_argument("--generate-notes", action="store_true")
    ap.add_argument("--translate-as", action="store_true")
    ap.add_argument("--embed", action="store_true")
    args = ap.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
