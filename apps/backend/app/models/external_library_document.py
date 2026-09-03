"""Catalog records harvested from explicitly approved public libraries."""

from datetime import datetime, timezone
from typing import Optional

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from app.models.content import FlexId


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ExternalLibraryDocument(Document):
    """Metadata and extracted text only; source binaries are never retained."""

    stable_key: str
    canonical_source_url: str
    canonical_item_url: Optional[str] = None
    content_url: Optional[str] = None
    source_root: str
    source_name: str
    title: str
    document_type: str
    content_format: str
    institution: Optional[str] = None
    board: Optional[str] = None
    class_name: Optional[str] = None
    semester: Optional[int] = None
    course: Optional[str] = None
    subject: Optional[str] = None
    medium: Optional[str] = None
    year: Optional[int] = None
    author: Optional[str] = None
    checksum_sha256: Optional[str] = None
    page_count: Optional[int] = None
    size_bytes: Optional[int] = None
    extracted_text: str = ""
    extracted_text_truncated: bool = False
    extraction_method: Optional[str] = None
    status: str = "discovered"  # discovered | extracted | metadata_only | blocked | error
    error: Optional[str] = None
    subject_ids: list[FlexId] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    discovered_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    class Settings:
        name = "external_library_documents"
        indexes = [
            IndexModel([("stable_key", ASCENDING)], unique=True, name="external_library_stable_key"),
            IndexModel([("source_name", ASCENDING)], name="external_library_source"),
            IndexModel([("document_type", ASCENDING)], name="external_library_type"),
            IndexModel(
                [("board", ASCENDING), ("class_name", ASCENDING), ("semester", ASCENDING),
                 ("course", ASCENDING), ("subject", ASCENDING)],
                name="external_library_hierarchy",
            ),
            IndexModel([("status", ASCENDING)], name="external_library_status"),
            IndexModel([("updated_at", DESCENDING)], name="external_library_updated"),
        ]