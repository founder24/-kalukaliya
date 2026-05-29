"""
Seed Vertex AI Search (Discovery Engine) Datastore with Educational Content
Usage: python seed-search.py --project <project_id> --location <location> --datastore-id <id> --data-dir <path>
"""
import argparse
import hashlib
import json
from pathlib import Path
from typing import List

from google.cloud import discoveryengine_v1 as discoveryengine


def chunk_text(text: str, max_tokens: int = 512) -> List[str]:
    """Split text into chunks of approximately max_tokens"""
    # Simple character-based chunking (replace with token-based for production)
    chars_per_token = 4  # Approximate
    max_chars = max_tokens * chars_per_token

    chunks = []
    for i in range(0, len(text), max_chars):
        chunk = text[i:i + max_chars]
        if len(chunk) > 100:  # Only add meaningful chunks
            chunks.append(chunk)
    return chunks


def seed_datastore(project_id: str, location: str, datastore_id: str, data_dir: str):
    """Seed a Discovery Engine datastore with educational content"""

    data_path = Path(data_dir)
    if not data_path.exists():
        print(f"Error: Data directory not found: {data_dir}")
        return False

    client = discoveryengine.DocumentServiceClient()
    parent = f"projects/{project_id}/locations/{location}/dataStores/{datastore_id}/branches/default_branch"

    documents_imported = 0

    # Process all text files in the data directory
    for file_path in data_path.glob("**/*.txt"):
        print(f"Processing {file_path}...")

        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        chunks = chunk_text(content)

        for i, chunk in enumerate(chunks):
            doc_id = hashlib.md5(f"{file_path}:{i}".encode()).hexdigest()

            document = discoveryengine.Document(
                id=doc_id,
                struct_data={
                    "id": doc_id,
                    "title": file_path.stem,
                    "content": chunk,
                    "language": "as",  # Assamese
                    "tier_access": "free",
                    "source_url": f"file://{file_path}",
                    "last_updated": "2024-01-01T00:00:00Z",
                },
            )

            try:
                request = discoveryengine.CreateDocumentRequest(
                    parent=parent,
                    document=document,
                    document_id=doc_id,
                )
                client.create_document(request=request)
                documents_imported += 1
            except Exception as e:
                # Try update if document already exists
                try:
                    document.name = f"{parent}/documents/{doc_id}"
                    request = discoveryengine.UpdateDocumentRequest(
                        document=document,
                        allow_missing=True,
                    )
                    client.update_document(request=request)
                    documents_imported += 1
                except Exception as update_err:
                    print(f"  Warning: Failed to import chunk {i} of {file_path}: {update_err}")

    if documents_imported == 0:
        print("Error: No documents were imported")
        return False

    print(f"Successfully imported {documents_imported} document chunks")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed Vertex AI Search Datastore with Educational Content")
    parser.add_argument("--project", required=True, help="GCP Project ID")
    parser.add_argument("--location", default="global", help="Datastore location (default: global)")
    parser.add_argument("--datastore-id", required=True, help="Discovery Engine datastore ID")
    parser.add_argument("--data-dir", required=True, help="Path to directory containing educational content")

    args = parser.parse_args()

    success = seed_datastore(args.project, args.location, args.datastore_id, args.data_dir)
    exit(0 if success else 1)
