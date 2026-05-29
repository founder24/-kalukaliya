"""
Bulk Translation Script: English to Assamese
Fetches English content from MongoDB or Discovery Engine, translates to Assamese
using Google Cloud Translation API, and seeds translated documents into Vertex AI
Search (Discovery Engine) datastore.

Usage:
  # From MongoDB source
  python bulk-translate.py --project blissful-acumen-495019-t6 \
    --datastore-id syrabit-edu-datastore \
    --mongodb-uri "mongodb+srv://..." \
    --source mongodb

  # From existing datastore documents
  python bulk-translate.py --project blissful-acumen-495019-t6 \
    --datastore-id syrabit-edu-datastore \
    --source datastore

  # Dry run (no writes)
  python bulk-translate.py --project blissful-acumen-495019-t6 \
    --datastore-id syrabit-edu-datastore \
    --source mongodb --dry-run

  # Limit for testing
  python bulk-translate.py --project blissful-acumen-495019-t6 \
    --datastore-id syrabit-edu-datastore \
    --source mongodb --limit 5
"""
import argparse
import logging
import os
import sys
import time
from typing import Dict, List, Optional

from google.cloud import discoveryengine_v1 as discoveryengine
from google.cloud import translate_v2 as translate
from pymongo import MongoClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Rate limiting constants
TRANSLATE_DELAY_SECONDS = 0.1  # 100ms between translation calls
SEED_DELAY_SECONDS = 0.2  # 200ms between document seed calls
BATCH_PAUSE_SECONDS = 2.0  # 2s pause between batches


def get_translate_client() -> translate.Client:
    """Create a Google Cloud Translation API v2 client."""
    return translate.Client()


def get_discovery_client() -> discoveryengine.DocumentServiceClient:
    """Create a Discovery Engine document service client."""
    return discoveryengine.DocumentServiceClient()


def get_mongodb_client(uri: str) -> MongoClient:
    """Create a MongoDB client."""
    return MongoClient(uri)


def translate_text(client: translate.Client, text: str, target_lang: str = "as") -> Optional[str]:
    """Translate text to the target language using Google Cloud Translation API v2."""
    if not text or not text.strip():
        return text

    try:
        result = client.translate(text, target_language=target_lang, source_language="en")
        return result["translatedText"]
    except Exception as e:
        logger.error(f"Translation failed for text [{text[:50]}...]: {e}")
        return None


def fetch_english_docs_from_mongodb(
    uri: str, db_name: str, collection_name: str, limit: Optional[int] = None
) -> List[Dict]:
    """Fetch all English content documents from MongoDB."""
    logger.info(f"Connecting to MongoDB (collection: {collection_name})...")
    client = get_mongodb_client(uri)

    # Parse the database name from the URI if not provided
    db = client.get_default_database() if db_name is None else client[db_name]
    collection = db[collection_name]

    query = {"language": "en"}
    cursor = collection.find(query)

    if limit:
        cursor = cursor.limit(limit)

    docs = list(cursor)
    logger.info(f"Fetched {len(docs)} English documents from MongoDB.")
    client.close()
    return docs


def fetch_english_docs_from_datastore(
    project_id: str, location: str, datastore_id: str, limit: Optional[int] = None
) -> List[Dict]:
    """Fetch existing English documents from the Discovery Engine datastore."""
    logger.info("Fetching English documents from Discovery Engine datastore...")
    client = get_discovery_client()
    parent = (
        f"projects/{project_id}/locations/{location}"
        f"/dataStores/{datastore_id}/branches/default_branch"
    )

    docs = []
    try:
        request = discoveryengine.ListDocumentsRequest(parent=parent, page_size=100)
        page_result = client.list_documents(request=request)

        for document in page_result:
            struct_data = dict(document.struct_data) if document.struct_data else {}
            # Only include English documents
            if struct_data.get("language") == "en":
                docs.append(
                    {
                        "_id": document.id,
                        "title": struct_data.get("title", ""),
                        "content": struct_data.get("content", ""),
                        "subject": struct_data.get("subject", ""),
                        "language": "en",
                        "slug": struct_data.get("slug", ""),
                    }
                )
                if limit and len(docs) >= limit:
                    break
    except Exception as e:
        logger.error(f"Failed to fetch documents from datastore: {e}")

    logger.info(f"Fetched {len(docs)} English documents from datastore.")
    return docs


def get_existing_assamese_doc_ids(
    project_id: str, location: str, datastore_id: str
) -> set:
    """Get the set of document IDs that already have Assamese translations."""
    logger.info("Checking for existing Assamese translations...")
    client = get_discovery_client()
    parent = (
        f"projects/{project_id}/locations/{location}"
        f"/dataStores/{datastore_id}/branches/default_branch"
    )

    existing_ids = set()
    try:
        request = discoveryengine.ListDocumentsRequest(parent=parent, page_size=100)
        page_result = client.list_documents(request=request)

        for document in page_result:
            # Assamese docs have "_as" suffix
            if document.id.endswith("_as"):
                existing_ids.add(document.id)
    except Exception as e:
        logger.warning(f"Could not check existing translations: {e}")

    logger.info(f"Found {len(existing_ids)} existing Assamese documents.")
    return existing_ids


def make_assamese_doc_id(original_id: str) -> str:
    """Generate the Assamese document ID from the original document ID."""
    return f"{original_id}_as"


def seed_document_to_datastore(
    client: discoveryengine.DocumentServiceClient,
    parent: str,
    doc_id: str,
    translated_doc: Dict,
    dry_run: bool = False,
) -> bool:
    """Seed a single translated document into the Discovery Engine datastore."""
    if dry_run:
        logger.info(f"  [DRY RUN] Would seed document: {doc_id}")
        return True

    # Build content field with raw_bytes (required for CONTENT_REQUIRED datastores)
    content_text = translated_doc.get("content", "")
    content = discoveryengine.Document.Content(
        raw_bytes=content_text.encode("utf-8"),
        mime_type="text/plain",
    )

    document = discoveryengine.Document(
        id=doc_id,
        content=content,
        struct_data={
            "id": doc_id,
            "title": translated_doc.get("title", ""),
            "content": content_text,
            "language": "as",
            "subject": translated_doc.get("subject", ""),
            "tier_access": translated_doc.get("tier_access", "free"),
            "source_url": translated_doc.get("source_url", ""),
            "last_updated": translated_doc.get("last_updated", ""),
        },
    )

    try:
        request = discoveryengine.CreateDocumentRequest(
            parent=parent,
            document=document,
            document_id=doc_id,
        )
        client.create_document(request=request)
        return True
    except Exception:
        # Try update if document already exists
        try:
            document.name = f"{parent}/documents/{doc_id}"
            request = discoveryengine.UpdateDocumentRequest(
                document=document,
                allow_missing=True,
            )
            client.update_document(request=request)
            return True
        except Exception as update_err:
            logger.error(f"  Failed to seed document {doc_id}: {update_err}")
            return False


def run_bulk_translate(
    project_id: str,
    location: str,
    datastore_id: str,
    source: str,
    mongodb_uri: Optional[str] = None,
    db_name: Optional[str] = None,
    collection: str = "content",
    batch_size: int = 10,
    dry_run: bool = False,
    limit: Optional[int] = None,
):
    """Main bulk translation workflow."""
    # Step 1: Fetch English documents
    if source == "mongodb":
        if not mongodb_uri:
            logger.error("MongoDB URI is required when source is 'mongodb'.")
            logger.error("Set MONGODB_URI env var or pass --mongodb-uri.")
            sys.exit(1)
        english_docs = fetch_english_docs_from_mongodb(
            mongodb_uri, db_name, collection, limit
        )
    elif source == "datastore":
        english_docs = fetch_english_docs_from_datastore(
            project_id, location, datastore_id, limit
        )
    else:
        logger.error(f"Unknown source: {source}")
        sys.exit(1)

    if not english_docs:
        logger.warning("No English documents found. Nothing to translate.")
        return

    # Step 2: Check existing Assamese translations for resumability
    existing_as_ids = get_existing_assamese_doc_ids(project_id, location, datastore_id)

    # Step 3: Initialize clients
    translate_client = get_translate_client()
    discovery_client = get_discovery_client()
    parent = (
        f"projects/{project_id}/locations/{location}"
        f"/dataStores/{datastore_id}/branches/default_branch"
    )

    # Step 4: Process documents in batches
    total_found = len(english_docs)
    translated_count = 0
    skipped_count = 0
    error_count = 0

    logger.info(f"Starting translation: {total_found} documents to process.")
    logger.info(f"Batch size: {batch_size} | Dry run: {dry_run}")

    for batch_start in range(0, total_found, batch_size):
        batch_end = min(batch_start + batch_size, total_found)
        batch = english_docs[batch_start:batch_end]
        batch_num = (batch_start // batch_size) + 1
        total_batches = (total_found + batch_size - 1) // batch_size

        logger.info(f"Processing batch {batch_num}/{total_batches} "
                    f"(docs {batch_start + 1}-{batch_end})...")

        for doc in batch:
            doc_id_raw = str(doc.get("_id", ""))
            as_doc_id = make_assamese_doc_id(doc_id_raw)

            # Skip if already translated (resumability)
            if as_doc_id in existing_as_ids:
                logger.info(f"  Skipping (already translated): {doc.get('title', doc_id_raw)[:60]}")
                skipped_count += 1
                continue

            title = doc.get("title", "")
            content = doc.get("content", "")

            if not title and not content:
                logger.warning(f"  Skipping empty document: {doc_id_raw}")
                skipped_count += 1
                continue

            # Translate title
            translated_title = title
            if title:
                time.sleep(TRANSLATE_DELAY_SECONDS)
                translated_title = translate_text(translate_client, title)
                if translated_title is None:
                    logger.error(f"  Failed to translate title for: {doc_id_raw}")
                    error_count += 1
                    continue

            # Translate content
            translated_content = content
            if content:
                time.sleep(TRANSLATE_DELAY_SECONDS)
                translated_content = translate_text(translate_client, content)
                if translated_content is None:
                    logger.error(f"  Failed to translate content for: {doc_id_raw}")
                    error_count += 1
                    continue

            # Build translated document
            translated_doc = {
                "title": translated_title,
                "content": translated_content,
                "subject": doc.get("subject", ""),
                "tier_access": doc.get("tier_access", "free"),
                "source_url": doc.get("source_url", ""),
                "last_updated": doc.get("last_updated", ""),
            }

            # Seed to datastore
            time.sleep(SEED_DELAY_SECONDS)
            success = seed_document_to_datastore(
                discovery_client, parent, as_doc_id, translated_doc, dry_run
            )

            if success:
                translated_count += 1
                logger.info(f"  Translated: {title[:60]} -> {translated_title[:60]}")
            else:
                error_count += 1

        # Pause between batches to respect rate limits
        if batch_end < total_found:
            logger.info(f"  Batch complete. Pausing {BATCH_PAUSE_SECONDS}s...")
            time.sleep(BATCH_PAUSE_SECONDS)

    # Print summary
    logger.info("=" * 60)
    logger.info("BULK TRANSLATION SUMMARY")
    logger.info("=" * 60)
    logger.info(f"  Total documents found:  {total_found}")
    logger.info(f"  Translated:             {translated_count}")
    logger.info(f"  Skipped:                {skipped_count}")
    logger.info(f"  Errors:                 {error_count}")
    logger.info("=" * 60)

    if dry_run:
        logger.info("(Dry run - no documents were actually written)")


def main():
    parser = argparse.ArgumentParser(
        description="Bulk translate English content to Assamese and seed into Vertex AI Search"
    )
    parser.add_argument(
        "--project",
        default="blissful-acumen-495019-t6",
        help="GCP Project ID (default: blissful-acumen-495019-t6)",
    )
    parser.add_argument(
        "--datastore-id",
        default="syrabit-edu-datastore",
        help="Discovery Engine datastore ID (default: syrabit-edu-datastore)",
    )
    parser.add_argument(
        "--location",
        default="global",
        help="Datastore location (default: global)",
    )
    parser.add_argument(
        "--mongodb-uri",
        default=os.environ.get("MONGODB_URI"),
        help="MongoDB connection URI (or set MONGODB_URI env var)",
    )
    parser.add_argument(
        "--db-name",
        default=None,
        help="MongoDB database name (default: uses default from URI)",
    )
    parser.add_argument(
        "--collection",
        default="content",
        help="MongoDB collection name (default: content)",
    )
    parser.add_argument(
        "--source",
        choices=["mongodb", "datastore"],
        default="mongodb",
        help="Source of English documents: 'mongodb' or 'datastore' (default: mongodb)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Number of documents per batch (default: 10)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of documents to translate (useful for testing)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview translations without writing to datastore",
    )

    args = parser.parse_args()

    run_bulk_translate(
        project_id=args.project,
        location=args.location,
        datastore_id=args.datastore_id,
        source=args.source,
        mongodb_uri=args.mongodb_uri,
        db_name=args.db_name,
        collection=args.collection,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
        limit=args.limit,
    )


if __name__ == "__main__":
    main()
