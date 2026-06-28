"""
One-shot script to reset the admin user's password.

Usage:
  cd apps/backend
  APP_ENV=development python3 scripts/reset_admin_password.py <email> <new_password>

Example:
  python3 scripts/reset_admin_password.py founder@syrabit.ai MyNewPass123!
"""
import asyncio
import sys


async def main():
    if len(sys.argv) != 3:
        print("Usage: python3 reset_admin_password.py <email> <new_password>")
        sys.exit(1)

    email = sys.argv[1]
    new_password = sys.argv[2]

    if len(new_password) < 8:
        print("Error: password must be at least 8 characters")
        sys.exit(1)

    from app.db.mongo import init_mongo
    await init_mongo()

    from app.models.user import User, _bcrypt_safe
    import bcrypt

    user = await User.find_one({"email": email})
    if not user:
        print(f"No user found with email: {email}")
        sys.exit(1)

    if user.role != "admin":
        print(f"User {email} has role '{user.role}', not 'admin'.")
        answer = input("Reset password anyway? [y/N] ").strip().lower()
        if answer != "y":
            sys.exit(0)

    new_hash = User.hash_password(new_password)
    user.hashed_password = new_hash
    await user.save()

    ok = bcrypt.checkpw(_bcrypt_safe(new_password), new_hash.encode())
    print(f"Password reset for {email}. Self-verify: {'OK ✓' if ok else 'FAILED ✗'}")


if __name__ == "__main__":
    asyncio.run(main())
