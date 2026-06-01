"""
Database Migration Runner

Simple forward-only migration system for MongoDB.
Tracks applied migrations in a 'schema_versions' collection.
Uses Motor directly (not Beanie) since migrations run at startup before Beanie init.
"""

import logging
from datetime import datetime, timezone
from typing import Callable, Awaitable, Optional

from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

logger = logging.getLogger(__name__)


async def get_current_version(db: AsyncDatabase) -> Optional[str]:
    """Get the latest applied migration version."""
    doc = await db.schema_versions.find_one(sort=[("applied_at", -1)])
    return doc["version"] if doc else None


async def get_applied_migrations(db: AsyncDatabase) -> list[dict]:
    """Get all applied migrations sorted by applied_at."""
    cursor = db.schema_versions.find().sort("applied_at", 1)
    return await cursor.to_list(length=100)


async def apply_migration(
    db: AsyncDatabase,
    version: str,
    description: str,
    up_fn: Callable[[AsyncDatabase], Awaitable[None]],
) -> bool:
    """
    Apply a single migration if not already applied.

    Returns True if the migration was applied, False if already applied.
    """
    # Check if already applied
    existing = await db.schema_versions.find_one({"version": version})
    if existing:
        logger.debug(f"Migration {version} already applied, skipping")
        return False

    logger.info(f"Applying migration {version}: {description}")
    try:
        await up_fn(db)
        await db.schema_versions.insert_one(
            {
                "version": version,
                "description": description,
                "applied_at": datetime.now(timezone.utc),
            }
        )
        logger.info(f"Migration {version} applied successfully")
        return True
    except DuplicateKeyError:
        logger.info(f"Migration {version} was applied by another instance, skipping")
        return False
    except Exception as e:
        logger.error(f"Migration {version} failed: {e}")
        raise


async def check_and_apply_migrations(db: AsyncDatabase) -> None:
    """
    Run all pending migrations in order.
    Called during application startup after MongoDB connection is established.
    """
    from app.db.migrations.versions import MIGRATIONS

    applied_count = 0
    for migration in MIGRATIONS:
        was_applied = await apply_migration(
            db,
            version=migration["version"],
            description=migration["description"],
            up_fn=migration["up"],
        )
        if was_applied:
            applied_count += 1

    if applied_count > 0:
        logger.info(f"Applied {applied_count} new migration(s)")
    else:
        logger.info("All migrations are up to date")


async def check_schema_version(db: AsyncDatabase) -> str:
    """
    Verify and return current schema version.
    Useful for health checks and startup verification.
    """
    version = await get_current_version(db)
    if version:
        logger.info(f"Current schema version: {version}")
    else:
        logger.info("No migrations applied yet")
    return version or "none"
