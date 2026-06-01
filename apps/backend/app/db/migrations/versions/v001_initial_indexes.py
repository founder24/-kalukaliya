"""
Migration v001: Initial Indexes

Documents the baseline indexes that exist in the database.
This is a no-op migration since indexes are created by create_indexes() in mongo.py,
but it establishes the migration tracking system baseline.
"""

from pymongo.asynchronous.database import AsyncDatabase


async def up(db: AsyncDatabase) -> None:
    """
    Baseline migration - indexes already managed by create_indexes().
    This migration exists to establish the schema_versions collection
    and provide a reference point for future migrations.
    """
    # Ensure schema_versions collection has an index for efficient lookups
    await db.schema_versions.create_index("version", unique=True)
    await db.schema_versions.create_index("applied_at")
