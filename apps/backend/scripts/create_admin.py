#!/usr/bin/env python3
"""
Admin User Management Script
=============================
Creates or resets the admin user in MongoDB directly.
Uses raw pymongo — no Beanie dependency, works independently of app version.

Usage:
    cd apps/backend
    python scripts/create_admin.py

Environment variables required:
    MONGODB_URI      — MongoDB Atlas connection string
    ADMIN_EMAIL      — admin account email
    ADMIN_PASSWORD   — admin account password (min 8 chars, upper + lower + digit)

Optional:
    MONGODB_DB_NAME  — defaults to syrabit_prod
    ADMIN_FORCE_RESET=true — overwrite password if user already exists

Examples:
    MONGODB_URI="mongodb+srv://..." ADMIN_EMAIL="admin@syrabit.ai" \\
        ADMIN_PASSWORD="SuperSecret1!" python scripts/create_admin.py

    # Force password reset on existing admin:
    ADMIN_FORCE_RESET=true MONGODB_URI="..." ADMIN_EMAIL="admin@syrabit.ai" \\
        ADMIN_PASSWORD="NewPass1!" python scripts/create_admin.py
"""

import asyncio
import os
import sys
from datetime import datetime, timezone


async def main():
    mongodb_uri = os.environ.get("MONGODB_URI")
    admin_email = os.environ.get("ADMIN_EMAIL")
    admin_password = os.environ.get("ADMIN_PASSWORD")
    db_name = os.environ.get("MONGODB_DB_NAME", "syrabit_prod")
    force_reset = os.environ.get("ADMIN_FORCE_RESET", "").lower() in (
        "true",
        "1",
        "yes",
    )

    # ── Validate inputs ──────────────────────────────────────────────────────
    if not mongodb_uri:
        print("ERROR: MONGODB_URI is required", file=sys.stderr)
        sys.exit(1)
    if not admin_email:
        print("ERROR: ADMIN_EMAIL is required", file=sys.stderr)
        sys.exit(1)
    if not admin_password:
        print("ERROR: ADMIN_PASSWORD is required", file=sys.stderr)
        sys.exit(1)
    if len(admin_password) < 8:
        print("ERROR: ADMIN_PASSWORD must be at least 8 characters", file=sys.stderr)
        sys.exit(1)
    if not any(c.isupper() for c in admin_password):
        print("ERROR: ADMIN_PASSWORD must contain an uppercase letter", file=sys.stderr)
        sys.exit(1)
    if not any(c.islower() for c in admin_password):
        print("ERROR: ADMIN_PASSWORD must contain a lowercase letter", file=sys.stderr)
        sys.exit(1)
    if not any(c.isdigit() for c in admin_password):
        print("ERROR: ADMIN_PASSWORD must contain a digit", file=sys.stderr)
        sys.exit(1)

    # ── Hash password with bcrypt (no Beanie needed) ─────────────────────────
    import bcrypt

    hashed_password = bcrypt.hashpw(
        admin_password.encode("utf-8"), bcrypt.gensalt()
    ).decode("utf-8")

    # ── Connect via raw pymongo (no Beanie) ────────────────────────────────────
    print(f"Connecting to MongoDB ({db_name})...")
    from pymongo import AsyncMongoClient

    client = AsyncMongoClient(
        mongodb_uri,
        serverSelectionTimeoutMS=10000,
        connectTimeoutMS=10000,
    )
    db = client[db_name]
    users = db["users"]

    # Verify connection
    try:
        await client.admin.command("ping")
        print("  Connected ✓")
    except Exception as e:
        print(f"ERROR: Cannot reach MongoDB: {e}", file=sys.stderr)
        client.close()
        sys.exit(1)

    # ── Upsert admin user ────────────────────────────────────────────────────
    existing = await users.find_one({"email": admin_email})
    now = datetime.now(timezone.utc)

    if existing:
        current_role = existing.get("role", "none")
        print(f"User found: {admin_email} (role: {current_role})")

        if current_role == "admin" and not force_reset:
            print("Admin already exists and is active.")
            print("Use ADMIN_FORCE_RESET=true to reset the password.")
            client.close()
            return

        update: dict = {"role": "admin", "updated_at": now}
        if force_reset or current_role != "admin":
            update["hashed_password"] = hashed_password
            if force_reset:
                print("  Resetting password...")

        await users.update_one({"_id": existing["_id"]}, {"$set": update})
        print(f"✓ Admin updated: {admin_email}")
    else:
        doc = {
            "email": admin_email,
            "hashed_password": hashed_password,
            "auth_provider": "local",
            "role": "admin",
            "name": "Admin",
            "subscription_tier": "free",
            "subscription_status": "active",
            "monthly_message_count": 0,
            "total_lifetime_messages": 0,
            "consent_dpdp": False,
            "preferred_language": "en",
            "voice_enabled": True,
            "theme": "light",
            "cancel_at_period_end": False,
            "created_at": now,
            "updated_at": now,
        }
        result = await users.insert_one(doc)
        print(f"✓ Admin created: {admin_email} (id: {result.inserted_id})")

    print()
    print("Admin login endpoint : POST /api/v1/admin/login")
    print('  Body               : { "email": "...", "password": "..." }')
    print("  Sets cookie        : syrabit_admin_session (httpOnly, 8h TTL)")
    print("  Verify session     : GET  /api/v1/admin/verify")
    print()
    print("For production (Cloud Run), also set these env vars:")
    print("  ADMIN_EMAIL   — auto-seeds admin on every cold start (idempotent)")
    print("  ADMIN_PASSWORD")
    client.close()


if __name__ == "__main__":
    asyncio.run(main())
