"""
GCS Content Store - Source of truth for educational content.

Writes content documents to Google Cloud Storage.
Vertex AI Search Datastore indexes from this bucket.
Cloudflare Pages rebuild reads from this bucket at build time.
"""

import json
import logging
from typing import Optional

from google.cloud import storage
from google.oauth2 import service_account

from app.config import settings

logger = logging.getLogger(__name__)


class GCSContentStore:
    """Manages educational content in Google Cloud Storage."""

    def __init__(self):
        self._client: Optional[storage.Client] = None

    @property
    def _bucket_name(self) -> str:
        return (
            settings.GCS_CONTENT_BUCKET
            or f"{settings.VERTEX_PROJECT_ID}-syrabit-content"
        )

    def _get_client(self) -> storage.Client:
        if self._client is None:
            if settings.google_credentials:
                credentials = service_account.Credentials.from_service_account_info(
                    settings.google_credentials
                )
                self._client = storage.Client(
                    project=settings.VERTEX_PROJECT_ID, credentials=credentials
                )
            else:
                # Use Application Default Credentials (e.g., on Cloud Run)
                self._client = storage.Client(project=settings.VERTEX_PROJECT_ID)
        return self._client

    def _get_bucket(self):
        return self._get_client().bucket(self._bucket_name)

    async def write_knowledge_object(self, slug: str, data: dict) -> str:
        """Write a knowledge object JSON to GCS."""
        import asyncio

        bucket = self._get_bucket()
        blob = bucket.blob(f"knowledge/{slug}.json")

        def _upload():
            blob.upload_from_string(
                json.dumps(data, ensure_ascii=False, default=str),
                content_type="application/json",
            )

        await asyncio.to_thread(_upload)
        logger.info(
            f"Written knowledge object to gs://{self._bucket_name}/knowledge/{slug}.json"
        )
        return f"gs://{self._bucket_name}/knowledge/{slug}.json"

    async def write_hierarchy(self, hierarchy_type: str, data: list) -> str:
        """Write hierarchy data (boards, classes, streams, subjects, chapters)."""
        import asyncio

        bucket = self._get_bucket()
        blob = bucket.blob(f"hierarchy/{hierarchy_type}.json")

        def _upload():
            blob.upload_from_string(
                json.dumps(data, ensure_ascii=False, default=str),
                content_type="application/json",
            )

        await asyncio.to_thread(_upload)
        logger.info(
            f"Written hierarchy to gs://{self._bucket_name}/hierarchy/{hierarchy_type}.json"
        )
        return f"gs://{self._bucket_name}/hierarchy/{hierarchy_type}.json"

    async def write_library_bundle(self, bundle: dict) -> str:
        """Write the full library bundle JSON."""
        import asyncio

        bucket = self._get_bucket()
        blob = bucket.blob("derived/library-bundle.json")

        def _upload():
            blob.upload_from_string(
                json.dumps(bundle, ensure_ascii=False, default=str),
                content_type="application/json",
            )

        await asyncio.to_thread(_upload)
        logger.info(
            f"Written library-bundle to gs://{self._bucket_name}/derived/library-bundle.json"
        )
        return f"gs://{self._bucket_name}/derived/library-bundle.json"

    async def write_library_bundle_slim(self, bundle: dict) -> str:
        """Write the slim library bundle JSON."""
        import asyncio

        bucket = self._get_bucket()
        blob = bucket.blob("derived/library-bundle-slim.json")

        def _upload():
            blob.upload_from_string(
                json.dumps(bundle, ensure_ascii=False, default=str),
                content_type="application/json",
            )

        await asyncio.to_thread(_upload)
        return f"gs://{self._bucket_name}/derived/library-bundle-slim.json"

    async def write_plans(self, plans: list) -> str:
        """Write subscription plans JSON."""
        import asyncio

        bucket = self._get_bucket()
        blob = bucket.blob("derived/plans.json")

        def _upload():
            blob.upload_from_string(
                json.dumps(plans, ensure_ascii=False, default=str),
                content_type="application/json",
            )

        await asyncio.to_thread(_upload)
        return f"gs://{self._bucket_name}/derived/plans.json"

    async def write_sitemap(self, name: str, xml_content: str) -> str:
        """Write a sitemap XML to GCS."""
        import asyncio

        bucket = self._get_bucket()
        blob = bucket.blob(f"sitemaps/{name}")

        def _upload():
            blob.upload_from_string(xml_content, content_type="application/xml")

        await asyncio.to_thread(_upload)
        return f"gs://{self._bucket_name}/sitemaps/{name}"

    async def read_json(self, path: str) -> Optional[dict]:
        """Read a JSON file from GCS. Returns None if not found."""
        import asyncio

        bucket = self._get_bucket()
        blob = bucket.blob(path)

        def _download():
            if not blob.exists():
                return None
            return json.loads(blob.download_as_text())

        return await asyncio.to_thread(_download)

    async def list_knowledge_objects(self) -> list[str]:
        """List all knowledge object slugs."""
        import asyncio

        bucket = self._get_bucket()

        def _list():
            return [
                blob.name.replace("knowledge/", "").replace(".json", "")
                for blob in bucket.list_blobs(prefix="knowledge/")
                if blob.name.endswith(".json")
            ]

        return await asyncio.to_thread(_list)


gcs_content_store = GCSContentStore()
