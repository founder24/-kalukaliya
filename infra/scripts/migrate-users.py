"""
MongoDB Index Migration Script
Creates required indexes for users and chats collections
"""
import asyncio
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.operations import IndexModel
import os


async def create_indexes(mongodb_uri: str, db_name: str = "syrabit_prod"):
    """Create all required MongoDB indexes"""
    
    client = MongoClient(mongodb_uri)
    db = client[db_name]
    
    print(f"Connected to MongoDB database: {db_name}")
    
    # Users Collection Indexes
    print("\nCreating indexes for 'users' collection...")
    users_collection = db.users
    
    user_indexes = [
        IndexModel([("email", ASCENDING)], unique=True, name="email_unique"),
        IndexModel([("subscription.razorpay_subscription_id", ASCENDING)], sparse=True, name="razorpay_sub_idx"),
        IndexModel([("profile.preferences.language", ASCENDING)], name="language_idx"),
        IndexModel([("created_at", DESCENDING)], name="created_at_idx"),
    ]
    
    result = users_collection.create_indexes(user_indexes)
    print(f"✓ Created {len(result)} indexes for users collection")
    
    # Chats Collection Indexes
    print("\nCreating indexes for 'chats' collection...")
    chats_collection = db.chats
    
    chat_indexes = [
        IndexModel([("user_id", ASCENDING), ("updated_at", DESCENDING)], name="user_chats_idx"),
        IndexModel([("session_id", ASCENDING)], name="session_idx"),
        IndexModel([("updated_at", DESCENDING)], name="updated_at_idx"),
    ]
    
    result = chats_collection.create_indexes(chat_indexes)
    print(f"✓ Created {len(result)} indexes for chats collection")
    
    # Audit Collection Indexes (if exists)
    print("\nCreating indexes for 'audit' collection...")
    audit_collection = db.audit
    
    audit_indexes = [
        IndexModel([("user_id", ASCENDING), ("timestamp", DESCENDING)], name="user_audit_idx"),
        IndexModel([("action", ASCENDING)], name="action_idx"),
    ]
    
    result = audit_collection.create_indexes(audit_indexes)
    print(f"✓ Created {len(result)} indexes for audit collection")
    
    print("\n✓ All MongoDB indexes created successfully!")
    client.close()


if __name__ == "__main__":
    mongodb_uri = os.getenv("MONGODB_URI")
    db_name = os.getenv("MONGODB_DB_NAME", "syrabit_prod")
    
    if not mongodb_uri:
        print("✗ Error: MONGODB_URI environment variable not set")
        exit(1)
    
    asyncio.run(create_indexes(mongodb_uri, db_name))
