#!/usr/bin/env python3
"""
Admin User Management Script
=============================
Creates or resets the admin user in MongoDB directly.

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
    MONGODB_URI="mongodb+srv://..." ADMIN_EMAIL="admin@syrabit.ai" \
        ADMIN_PASSWORD="SuperSecret1!" python scripts/create_admin.py

    # Force password reset on existing admin:
    ADMIN_FORCE_RESET=true MONGODB_URI="..." ADMIN_EMAIL="admin@syrabit.ai" \
        ADMIN_PASSWORD="NewPass1!" python scripts/create_admin.py
"""

import asyncio
import os
import sys


async def main():
    mongodb_uri = os.environ.get("MONGODB_URI")
    admin_email = os.environ.get("ADMIN_EMAIL")
    admin_password = os.environ.get("ADMIN_PASSWORD")
    db_name = os.environ.get("MONGODB_DB_NAME", "syrabit_prod")
    force_reset = os.environ.get("ADMIN_FORCE_RESET", "").lower() in ("true", "1", "yes")

    # Validate inputs
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

    print(f"Connecting to MongoDB ({db_name})...")

    import motor.motor_asyncio
    from beanie import init_beanie

    # Add parent to path so we can import app modules
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from app.models.user import User

    client = motor.motor_asyncio.AsyncIOMotorClient(mongodb_uri)
    db = client[db_name]
    await init_beanie(database=db, document_models=[User])

    existing = await User.find_one({"email": admin_email})

    if existing:
        print(f"User found: {admin_email} (role: {existing.role or 'none'})")
        if existing.role == "admin" and not force_reset:
            print("Admin already exists. Use ADMIN_FORCE_RESET=true to reset the password.")
            return
        from datetime import datetime, timezone
        update: dict = {
            "role": "admin",
            "updated_at": datetime.now(timezone.utc),
        }
        if force_reset or existing.role != "admin":
            update["hashed_password"] = User.hash_password(admin_password)
        await existing.update({"$set": update})
        print(f"✓ Admin updated: {admin_email}")
        if force_reset:
            print("  Password has been reset.")
    else:
        from datetime import datetime, timezone
        admin_user = User(
            email=admin_email,
            hashed_password=User.hash_password(admin_password),
            role="admin",
            auth_provider="local",
            name="Admin",
        )
        await admin_user.insert()
        print(f"✓ Admin created: {admin_email}")

    print("\nAdmin login endpoint: POST /api/v1/admin/login")
    print("  Body: { \"email\": \"...\", \"password\": \"...\" }")
    print("  Sets httpOnly cookie: syrabit_admin_session (8h TTL)")
    print("  Verify session: GET /api/v1/admin/verify")
    client.close()


if __name__ == "__main__":
    asyncio.run(main())
