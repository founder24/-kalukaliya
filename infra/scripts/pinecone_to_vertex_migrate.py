"""
Pinecone → Vertex AI Search + MongoDB TopicEmbedding Migration

Recovers 271 chapter-level records from the syrabit-ahsec Pinecone index
and migrates them into:
  1. MongoDB  — TopicEmbedding collection (re-embedded with text-embedding-005, 768-dim)
  2. Vertex AI Search — Discovery Engine data store (struct_data documents)

Usage (from repo root):
    cd apps/backend
    python -m scripts.pinecone_to_vertex_migrate [--dry-run] [--skip-mongo] [--skip-vertex]

Environment variables required:
    PINECONE_API_KEY        — Pinecone API key
    PINECONE_INDEX          — Pinecone index name (e.g. syrabit-ahsec)
    MONGODB_URI             — MongoDB connection string
    MONGODB_DB_NAME         — MongoDB database name
    GOOGLE_APPLICATION_CREDENTIALS_JSON  — GCP service account JSON
    VERTEX_PROJECT_ID       — GCP project ID
    VERTEX_LOCATION         — Vertex AI location (e.g. us-central1)
    VERTEX_SEARCH_DATASTORE_ID — Discovery Engine data store ID
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

BACKUP_PATH = Path(__file__).parent / "pinecone_backup.json"
NAMESPACES = ["en", "as"]
BATCH_SIZE = 10          # vectors fetched per Pinecone fetch call
EMBED_CONCURRENCY = 5    # concurrent Vertex AI embedding calls
VERTEX_CONCURRENCY = 10  # concurrent Vertex AI Search upserts
EMBED_DELAY = 0.15       # seconds between embedding calls (rate-limit buffer)


# ---------------------------------------------------------------------------
# Step 1 — Extract all records from Pinecone
# ---------------------------------------------------------------------------

def extract_pinecone_records() -> list[dict]:
    """Fetch all vectors + metadata from every namespace."""
    from pinecone import Pinecone

    api_key = os.environ.get("PINECONE_API_KEY")
    index_name = os.environ.get("PINECONE_INDEX")
    if not api_key or not index_name:
        raise RuntimeError("PINECONE_API_KEY and PINECONE_INDEX must be set")

    pc = Pinecone(api_key=api_key)
    index = pc.Index(index_name)

    all_records: list[dict] = []

    for ns in NAMESPACES:
        logger.info(f"Listing IDs in namespace '{ns}' …")
        ids: list[str] = []
        for id_batch in index.list(namespace=ns):
            ids.extend(
                item.id if hasattr(item, "id") else item
                for item in id_batch
            )
        logger.info(f"  {len(ids)} records found")

        # Fetch in batches of BATCH_SIZE
        for i in range(0, len(ids), BATCH_SIZE):
            batch = ids[i : i + BATCH_SIZE]
            result = index.fetch(ids=batch, namespace=ns)
            for vid, vec in result.vectors.items():
                meta = dict(vec.metadata) if vec.metadata else {}
                all_records.append({
                    "pinecone_id": vid,
                    "namespace": ns,
                    "metadata": meta,
                })

        logger.info(f"  Done fetching namespace '{ns}'")

    logger.info(f"Total extracted: {len(all_records)} records")
    return all_records


# ---------------------------------------------------------------------------
# Step 2 — Save JSON backup
# ---------------------------------------------------------------------------

def save_backup(records: list[dict]) -> None:
    BACKUP_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(BACKUP_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    logger.info(f"Backup saved → {BACKUP_PATH}  ({len(records)} records)")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_doc_id(chapter_id: str, lang: str) -> str:
    """Build a Vertex AI Search-safe document ID (alphanumeric + hyphens only)."""
    raw = f"ch-{chapter_id}-{lang}"
    return re.sub(r"[^a-zA-Z0-9_-]", "-", raw)[:63]


def _embed_text_for_record(meta: dict, ns: str) -> str:
    """Build the text to embed: join aeo_question_variants + title."""
    variants = meta.get("aeo_question_variants", [])
    if isinstance(variants, list):
        variants_text = " ".join(variants)
    else:
        variants_text = str(variants)
    title = meta.get("title", "")
    text = f"{title}. {variants_text}".strip()
    return text or title


def _subject_slug(meta: dict) -> str:
    subject = meta.get("subject", "")
    return re.sub(r"[^a-z0-9]+", "-", subject.lower()).strip("-")[:60]


def _class_level(meta: dict) -> str:
    return meta.get("class", "")


def _board_slug(meta: dict) -> str:
    # canonical_url pattern: https://syrabit.ai/...
    # No explicit board field — infer AHSEC (all records are AHSEC)
    return "ahsec"


# ---------------------------------------------------------------------------
# Step 3 — Populate MongoDB TopicEmbedding
# ---------------------------------------------------------------------------

async def migrate_to_mongo(records: list[dict], dry_run: bool) -> dict:
    """
    Upsert Pinecone records into MongoDB TopicEmbedding.
    Embeddings are stored as empty lists — run backfill_topic_embeddings.py
    in production (where Vertex AI creds are available) to populate them.
    """
    from app.config import settings
    from app.models.content import TopicEmbedding
    from beanie import init_beanie, PydanticObjectId
    from bson import ObjectId
    from pymongo import AsyncMongoClient

    client = AsyncMongoClient(settings.MONGODB_URI)
    await init_beanie(
        database=client[settings.MONGODB_DB_NAME],
        document_models=[TopicEmbedding],
    )

    stats = {"inserted": 0, "updated": 0, "skipped": 0, "errors": 0}

    for i, rec in enumerate(records):
        meta = rec["metadata"]
        ns = rec["namespace"]
        chapter_id_str = meta.get("chapter_id", "")
        content_id = meta.get("content_id", "")
        content_type = meta.get("content_type", "chapter")

        # Use content_id as topic_id for topic/iq records so they don't
        # collide with their parent chapter record which shares chapter_id.
        if content_type in ("topic", "important_questions") and content_id:
            topic_id = f"{content_id}:{ns}"
        elif chapter_id_str:
            topic_id = f"{chapter_id_str}:{ns}"
        else:
            stats["skipped"] += 1
            continue

        # topic-level records have a richer title in the "topic" field
        title = meta.get("topic") or meta.get("title", "")
        if not title:
            title = meta.get("chapter", "")

        try:
            if dry_run:
                logger.info(f"  [DRY-RUN] [{i+1}/{len(records)}] [{ns}] {title[:70]}")
                stats["inserted"] += 1
                continue

            existing = await TopicEmbedding.find_one(
                TopicEmbedding.topic_id == topic_id
            )
            now = datetime.now(timezone.utc)

            if existing:
                existing.topic_title = title
                existing.chapter_title = title
                existing.subject_slug = _subject_slug(meta)
                existing.board_slug = _board_slug(meta)
                existing.class_level = _class_level(meta)
                existing.updated_at = now
                await existing.save()
                stats["updated"] += 1
                logger.info(f"  UPDATED  [{i+1}/{len(records)}] [{ns}] {title[:70]}")
            else:
                doc = TopicEmbedding(
                    topic_id=topic_id,
                    topic_title=title,
                    chapter_id=PydanticObjectId(ObjectId()),
                    chapter_title=title,
                    subject_slug=_subject_slug(meta),
                    board_slug=_board_slug(meta),
                    class_level=_class_level(meta),
                    embedding=[],
                )
                await doc.insert()
                stats["inserted"] += 1
                logger.info(f"  INSERTED [{i+1}/{len(records)}] [{ns}] {title[:70]}")

        except Exception as e:
            stats["errors"] += 1
            logger.error(f"  ERROR [{ns}] {title[:60]}: {e}")

    try:
        client.close()
    except Exception:
        pass
    return stats


# ---------------------------------------------------------------------------
# Step 4 — Populate Vertex AI Search
# ---------------------------------------------------------------------------

async def migrate_to_vertex_search(records: list[dict], dry_run: bool) -> dict:
    """Upsert each record as a document in the Vertex AI Search data store."""
    from app.config import settings

    if not (settings.VERTEX_PROJECT_ID and settings.VERTEX_SEARCH_DATASTORE_ID):
        logger.warning("Vertex AI Search not configured — skipping")
        return {"upserted": 0, "skipped": len(records), "errors": 0}

    from google.cloud import discoveryengine_v1
    from google.oauth2 import service_account
    from google.protobuf import struct_pb2

    creds_info = settings.google_credentials
    if not creds_info:
        logger.error("Google credentials not configured — skipping Vertex AI Search")
        return {"upserted": 0, "skipped": len(records), "errors": 0}

    credentials = service_account.Credentials.from_service_account_info(
        creds_info,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )

    doc_client = discoveryengine_v1.DocumentServiceClient(credentials=credentials)
    parent = doc_client.branch_path(
        project=settings.VERTEX_PROJECT_ID,
        location=settings.VERTEX_SEARCH_LOCATION,
        data_store=settings.VERTEX_SEARCH_DATASTORE_ID,
        branch="default_branch",
    )

    sem = asyncio.Semaphore(VERTEX_CONCURRENCY)
    stats = {"upserted": 0, "skipped": 0, "errors": 0}

    async def upsert_one(rec: dict):
        meta = rec["metadata"]
        ns = rec["namespace"]
        chapter_id_str = meta.get("chapter_id", "")
        if not chapter_id_str:
            stats["skipped"] += 1
            return

        doc_id = _safe_doc_id(chapter_id_str, ns)
        title = meta.get("title", "")
        variants = meta.get("aeo_question_variants", [])
        content = (
            "\n".join(variants) if isinstance(variants, list) else str(variants)
        )
        canonical_url = meta.get("canonical_url", "")

        struct_data = struct_pb2.Struct()
        struct_data.update({
            "title": title,
            "content": f"{title}\n\n{content}",
            "source_url": canonical_url,
            "board": _board_slug(meta),
            "class_level": _class_level(meta),
            "subject": meta.get("subject", ""),
            "chapter": title,
            "subject_id": meta.get("subject_id", ""),
            "chapter_id": chapter_id_str,
            "lang": ns,
            "hreflang": meta.get("hreflang", ""),
            "content_hash": meta.get("content_hash", ""),
            "geo_region": meta.get("geo_region", "IN-AS"),
            "tier_access": "free",
            "slug": canonical_url.rstrip("/").split("/")[-1] if canonical_url else "",
        })

        doc = discoveryengine_v1.Document(id=doc_id, struct_data=struct_data)

        async with sem:
            try:
                if dry_run:
                    logger.info(f"  [DRY-RUN] would upsert to Vertex: {title[:60]} [{ns}]")
                    stats["upserted"] += 1
                    return

                try:
                    req = discoveryengine_v1.CreateDocumentRequest(
                        parent=parent,
                        document=doc,
                        document_id=doc_id,
                    )
                    await asyncio.to_thread(doc_client.create_document, request=req)
                except Exception:
                    doc.name = f"{parent}/documents/{doc_id}"
                    req = discoveryengine_v1.UpdateDocumentRequest(
                        document=doc,
                        allow_missing=True,
                    )
                    await asyncio.to_thread(doc_client.update_document, request=req)

                logger.info(f"  UPSERTED [{ns}] {title[:60]}")
                stats["upserted"] += 1

            except Exception as e:
                stats["errors"] += 1
                logger.error(f"  ERROR [{ns}] {title[:60]}: {e}")

    await asyncio.gather(*[upsert_one(r) for r in records])
    return stats


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    parser = argparse.ArgumentParser(description="Migrate Pinecone data to Vertex AI Search + MongoDB")
    parser.add_argument("--dry-run", action="store_true", help="Log what would happen without writing anything")
    parser.add_argument("--skip-mongo", action="store_true", help="Skip MongoDB TopicEmbedding migration")
    parser.add_argument("--skip-vertex", action="store_true", help="Skip Vertex AI Search migration")
    parser.add_argument("--from-backup", action="store_true", help="Load records from pinecone_backup.json instead of fetching live")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("Pinecone → Vertex AI Search + MongoDB Migration")
    logger.info("=" * 60)

    # --- Extract ---
    if args.from_backup and BACKUP_PATH.exists():
        logger.info(f"Loading from backup: {BACKUP_PATH}")
        with open(BACKUP_PATH, encoding="utf-8") as f:
            records = json.load(f)
        logger.info(f"Loaded {len(records)} records from backup")
    else:
        logger.info("Extracting from Pinecone …")
        records = extract_pinecone_records()
        save_backup(records)

    en_count = sum(1 for r in records if r["namespace"] == "en")
    as_count = sum(1 for r in records if r["namespace"] == "as")
    logger.info(f"Records: {en_count} en / {as_count} as / {len(records)} total")

    # --- MongoDB ---
    if not args.skip_mongo:
        logger.info("\n--- MongoDB TopicEmbedding ---")
        mongo_stats = await migrate_to_mongo(records, dry_run=args.dry_run)
        logger.info(
            f"MongoDB: inserted={mongo_stats['inserted']} "
            f"updated={mongo_stats['updated']} "
            f"skipped={mongo_stats['skipped']} errors={mongo_stats['errors']}"
        )
    else:
        logger.info("Skipping MongoDB migration")

    # --- Vertex AI Search ---
    if not args.skip_vertex:
        logger.info("\n--- Vertex AI Search ---")
        vertex_stats = await migrate_to_vertex_search(records, dry_run=args.dry_run)
        logger.info(
            f"Vertex: upserted={vertex_stats['upserted']} "
            f"skipped={vertex_stats['skipped']} errors={vertex_stats['errors']}"
        )
    else:
        logger.info("Skipping Vertex AI Search migration")

    logger.info("\nMigration complete.")


if __name__ == "__main__":
    # Allow running from apps/backend directory as:
    #   python -m scripts.pinecone_to_vertex_migrate
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "apps" / "backend"))
    asyncio.run(main())
