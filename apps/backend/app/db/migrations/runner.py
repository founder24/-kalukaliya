"""
Database Migration Runner

Forward-only migration system for MongoDB with optional rollback (down_fn) support.
Tracks applied migrations in a 'schema_versions' collection with status field:
  - "pending"  — claimed but not yet finished (crash-safe claim-first pattern)
  - "applied"  — completed successfully
  - "failed"   — up_fn raised an exception
  - "rolled_back" — down_fn ran successfully

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

    # Claim the migration slot BEFORE running up_fn (M-13 fix).
    # If the app crashes between up_fn and the record insert, the next boot would
    # find a "pending" record and skip re-running — preventing double application.
    try:
        await db.schema_versions.insert_one(
            {
                "version": version,
                "description": description,
                "status": "pending",
                "started_at": datetime.now(timezone.utc),
            }
        )
    except DuplicateKeyError:
        # Another instance already claimed this migration; let it finish.
        logger.info(f"Migration {version} was claimed by another instance, skipping")
        return False

    logger.info(f"Applying migration {version}: {description}")
    try:
        await up_fn(db)
        await db.schema_versions.update_one(
            {"version": version},
            {
                "$set": {
                    "status": "applied",
                    "applied_at": datetime.now(timezone.utc),
                }
            },
        )
        logger.info(f"Migration {version} applied successfully")
        return True
    except Exception as e:
        # Mark the slot as failed so operators can diagnose; does not re-raise
        # to allow other migrations to proceed where safe.
        logger.error(f"Migration {version} failed: {e}")
        try:
            await db.schema_versions.update_one(
                {"version": version},
                {"$set": {"status": "failed", "error": str(e)[:500]}},
            )
        except Exception:
            pass
        raise


async def rollback_migration(
    db: AsyncDatabase,
    version: str,
    down_fn: Callable[[AsyncDatabase], Awaitable[None]],
) -> bool:
    """
    Roll back a single applied migration by running its down_fn.

    Returns True if the rollback ran, False if the migration was not in
    'applied' state (already rolled back, pending, or never applied).

    Usage (typically from a management script, never called at startup):
        from app.db.migrations.runner import rollback_migration
        await rollback_migration(db, "20240101_add_index", down_20240101)
    """
    existing = await db.schema_versions.find_one({"version": version})
    if not existing or existing.get("status") != "applied":
        logger.info(f"Migration {version} is not in 'applied' state — skipping rollback")
        return False

    logger.info(f"Rolling back migration {version}")
    try:
        await down_fn(db)
        await db.schema_versions.update_one(
            {"version": version},
            {
                "$set": {
                    "status": "rolled_back",
                    "rolled_back_at": datetime.now(timezone.utc),
                }
            },
        )
        logger.info(f"Migration {version} rolled back successfully")
        return True
    except Exception as e:
        logger.error(f"Rollback of migration {version} failed: {e}")
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
