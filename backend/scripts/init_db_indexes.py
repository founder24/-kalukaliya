"""
Database Index Initialization Script
Run this once after deploying to ensure optimal query performance and TTL expiration.
"""
import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
DB_NAME = os.getenv("MONGODB_DB_NAME", "syrabit_prod")

async def create_indexes():
    client = AsyncIOMotorClient(MONGO_URI)
    db = client[DB_NAME]

    print(f"Connected to MongoDB: {DB_NAME}")

    # 1. Users Collection
    users = db["users"]
    await users.create_index("email", unique=True, background=True)
    await users.create_index("device_id", sparse=True, background=True)
    await users.create_index("cloudflare_turnstile_id", sparse=True, background=True)
    print("✓ Users indexes created")

    # 2. Conversations Collection (Registered Users)
    conversations = db["conversations"]
    await conversations.create_index("user_id", background=True)
    await conversations.create_index("created_at", background=True)
    await conversations.create_index([("user_id", 1), ("created_at", -1)], background=True)
    print("✓ Conversations indexes created")

    # 3. Anonymous Conversations Collection (with TTL)
    anon_convos = db["anon_conversations"]
    # TTL Index: Documents expire 7 days after 'expires_at'
    await anon_convos.create_index("expires_at", expireAfterSeconds=0, background=True)
    await anon_convos.create_index("device_id", background=True)
    await anon_convos.create_index([("device_id", 1), ("created_at", -1)], background=True)
    print("✓ Anon Conversations indexes created (TTL enabled)")

    # 4. Anon User Index (Mapping)
    anon_index = db["anon_user_index"]
    await anon_index.create_index("device_id", unique=True, background=True)
    print("✓ Anon User Index created")

    # 5. Rate Limit Counters (Upstash is primary, but Mongo backup if needed)
    # Usually handled in Redis, but if we fallback to Mongo:
    rate_limits = db["rate_limits"]
    await rate_limits.create_index("identifier", expireAfterSeconds=86400, background=True) # 24h TTL
    print("✓ Rate Limits indexes created")

    print("\n🎉 Database initialization complete!")
    client.close()

if __name__ == "__main__":
    try:
        asyncio.run(create_indexes())
    except Exception as e:
        print(f"❌ Error creating indexes: {e}")
        exit(1)
