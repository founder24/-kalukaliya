"""
GCSContentStore - Writes and reads content objects from Google Cloud Storage.
Used to serve pre-rendered static content via Cloudflare Pages.
"""

import asyncio
import json
import logging
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)


class GCSContentStore:
    """Service for reading/writing content JSON to a GCS bucket."""

    def __init__(self):
        self._client = None
        self._bucket = None
        self._configured = False

        credentials = settings.google_credentials
        if not credentials:
            logger.warning(
                "GCS credentials not configured - GCS content store disabled"
            )
            return

        try:
            self._init_client(credentials)
            self._configured = True
        except Exception as e:
            logger.warning(f"Failed to initialize GCS content store: {e}")

    def _init_client(self, credentials: dict):
        """Initialize the GCS client with service account credentials."""
        from google.cloud import storage
        from google.oauth2 import service_account

        creds = service_account.Credentials.from_service_account_info(
            credentials,
            scopes=["https://www.googleapis.com/auth/devstorage.read_write"],
        )

        self._client = storage.Client(credentials=creds)

        bucket_name = settings.GCS_CONTENT_BUCKET
        if not bucket_name:
            bucket_name = f"{settings.VERTEX_PROJECT_ID}-syrabit-content"

        self._bucket = self._client.bucket(bucket_name)
        logger.info(f"GCS content store initialized with bucket: {bucket_name}")

    def _write_json(self, path: str, data: dict) -> None:
        """Synchronously write a JSON object to GCS."""
        blob = self._bucket.blob(path)
        blob.upload_from_string(
            json.dumps(data, default=str, ensure_ascii=False),
            content_type="application/json",
        )

    def _write_string(self, path: str, content: str, content_type: str) -> None:
        """Synchronously write a string to GCS."""
        blob = self._bucket.blob(path)
        blob.upload_from_string(content, content_type=content_type)

    def _read_json(self, path: str) -> dict:
        """Synchronously read and parse a JSON object from GCS."""
        blob = self._bucket.blob(path)
        content = blob.download_as_text()
        return json.loads(content)

    def _list_blobs(self, prefix: str) -> list[str]:
        """Synchronously list blob names under a prefix."""
        blobs = self._client.list_blobs(self._bucket, prefix=prefix)
        return [blob.name for blob in blobs]

    async def write_knowledge_object(self, chapter_id: str, data: dict) -> None:
        """Write a chapter content object to GCS at content/chapters/{chapter_id}.json."""
        if not self._configured:
            return
        path = f"content/chapters/{chapter_id}.json"
        try:
            await asyncio.to_thread(self._write_json, path, data)
            logger.info(f"Wrote knowledge object to GCS: {path}")
        except Exception as e:
            logger.error(f"Failed to write knowledge object {path}: {e}")

    async def write_hierarchy(
        self, entity_type: str, entity_id: str, data: dict
    ) -> None:
        """Write a hierarchy entity to GCS at hierarchy/{entity_type}/{entity_id}.json."""
        if not self._configured:
            return
        path = f"hierarchy/{entity_type}/{entity_id}.json"
        try:
            await asyncio.to_thread(self._write_json, path, data)
            logger.info(f"Wrote hierarchy entity to GCS: {path}")
        except Exception as e:
            logger.error(f"Failed to write hierarchy entity {path}: {e}")

    async def write_library_bundle(self, data: dict) -> None:
        """Write the full library bundle to GCS at static/library-bundle.json."""
        if not self._configured:
            return
        path = "static/library-bundle.json"
        try:
            await asyncio.to_thread(self._write_json, path, data)
            logger.info(f"Wrote library bundle to GCS: {path}")
        except Exception as e:
            logger.error(f"Failed to write library bundle: {e}")

    async def write_sitemap(self, filename: str, xml_content: str) -> None:
        """Write a sitemap XML file to GCS at static/{filename}."""
        if not self._configured:
            return
        path = f"static/{filename}"
        try:
            await asyncio.to_thread(
                self._write_string, path, xml_content, "application/xml"
            )
            logger.info(f"Wrote sitemap to GCS: {path}")
        except Exception as e:
            logger.error(f"Failed to write sitemap {path}: {e}")

    async def read_json(self, path: str) -> Optional[dict]:
        """Read and return parsed JSON from a GCS path."""
        if not self._configured:
            return None
        try:
            return await asyncio.to_thread(self._read_json, path)
        except Exception as e:
            logger.error(f"Failed to read JSON from GCS {path}: {e}")
            return None

    async def list_knowledge_objects(self) -> list[str]:
        """List all blob names under content/chapters/."""
        if not self._configured:
            return []
        try:
            return await asyncio.to_thread(self._list_blobs, "content/chapters/")
        except Exception as e:
            logger.error(f"Failed to list knowledge objects: {e}")
            return []


# Singleton
gcs_content_store = GCSContentStore()
