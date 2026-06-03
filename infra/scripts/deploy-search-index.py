"""
Upload a search/index JSON to a GCS bucket for downstream GCP indexing pipelines.
Usage: python deploy-search-index.py --bucket <gcs-bucket> --index-file infra/azure/search-index.json
"""
import argparse
from pathlib import Path
from google.cloud import storage


def upload_index_to_gcs(bucket_name: str, index_file: str = "infra/azure/search-index.json", dest_name: str = None) -> bool:
    """Upload the index JSON to the specified GCS bucket.

    This makes no assumptions about the downstream indexing process —
    it simply stores the JSON where a GCP-based pipeline can consume it.
    """
    index_path = Path(index_file)
    if not index_path.exists():
        print(f"✗ Index file not found: {index_file}")
        return False

    dest_name = dest_name or index_path.name

    client = storage.Client()
    try:
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(dest_name)
        blob.upload_from_filename(str(index_path))
        print(f"✓ Uploaded '{index_file}' to gs://{bucket_name}/{dest_name}")
        return True
    except Exception as e:
        print(f"✗ Failed to upload to GCS: {e}")
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Upload search index JSON to GCS for GCP pipelines")
    parser.add_argument("--bucket", required=True, help="GCS bucket name to upload the index into")
    parser.add_argument("--index-file", default="infra/azure/search-index.json", help="Path to index JSON file")
    parser.add_argument("--dest-name", default=None, help="Destination object name in the bucket")

    args = parser.parse_args()

    success = upload_index_to_gcs(args.bucket, args.index_file, args.dest_name)
    exit(0 if success else 1)
