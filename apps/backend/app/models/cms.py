"""
CmsDocument - Standalone CMS blog/doc model for SEO content management.
Used by the AdminCmsDocEditor panel and exposed via the public /cms/posts endpoint.
"""

from beanie import Document
from pydantic import Field
from typing import Optional
from datetime import datetime, timezone


class CmsDocument(Document):
    title: str = ""
    content: str = ""
    meta_description: str = ""
    description: str = ""
    seo_tags: str = ""
    primary_keyword: str = ""
    seo_slug: str = ""
    category: str = ""
    geo_tags: str = ""
    schema_type: str = "Article"
    status: str = "draft"
    thumbnail_url: str = ""
    alt_text: str = ""

    # Scope link: "{board_id}/{class_id}/{stream_id}/{subject_id}" (partial is fine)
    linked_scope: str = ""

    # Computed on save
    word_count: int = 0

    # Extracted from linked_scope for fast public queries
    board_slug: str = ""
    subject_id: str = ""

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "cms_documents"
        indexes = [
            [("status", 1), ("updated_at", -1)],
            [("seo_slug", 1)],
            [("linked_scope", 1)],
            [("board_slug", 1), ("status", 1)],
        ]
