"""Document model for the Documents/Library feature."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from beanie import Document
from pydantic import Field


class LibraryDocument(Document):
    """A PDF document (book, notes, manual, etc.) uploaded by an admin."""

    title: str
    description: Optional[str] = None
    category: Optional[str] = None
    status: Literal["published", "draft"] = "draft"

    # PDF file
    pdf_url: str
    pdf_filename: str
    pdf_size_bytes: int

    # Optional cover image
    cover_url: Optional[str] = None

    # Audit fields
    created_by: str  # admin email
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "library_documents"
        indexes = [
            "status",
            "category",
            "created_at",
        ]
