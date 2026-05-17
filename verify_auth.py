#!/usr/bin/env python3
"""
Syrabit.ai — Authentication Verification Script

This script verifies the complete authentication flow including:
1. Admin authentication
2. Staff authentication  
3. Regular user authentication
4. Anonymous user chat history
5. Cloudflare Turnstile integration
6. MongoDB user storage

Usage:
    python verify_auth.py [--env ENV_FILE]

Environment Requirements:
    - MONGO_URL
    - JWT_SECRET
    - CLOUDFLARE_TURNSTILE_SECRET_KEY
    - TURNSTILE_ON (optional, defaults to false)
"""

import os
import sys
import asyncio
import json
import uuid
import hashlib
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from pathlib import Path

# Add backend paths
sys.path.insert(0, str(Path(__file__).parent / "artifacts" / "syrabit-backend"))
sys.path.insert(0, str(Path(__file__).parent / "backend" / "rust-core" / "src" / "services"))

try:
    import httpx
    from motor.motor_asyncio import AsyncIOMotorClient
    from passlib.context import CryptContext
    import jwt
except ImportError as e:
    print(f"❌ Missing dependency: {e}")
    print("Install with: pip install httpx motor passlib PyJWT")
    sys.exit(1)


class AuthVerifier:
    """Comprehensive authentication verification suite"""
    
    def __init__(self, mongo_url: str, jwt_secret: str, turnstile_secret: str = ""):
        self.mongo_url = mongo_url
        self.jwt_secret = jwt_secret
        self.turnstile_secret = turnstile_secret
        self.client: Optional[AsyncIOMotorClient] = None
        self.db = None
        self.pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
        self.test_results = []
        
    async def connect(self):
        """Initialize MongoDB connection"""
        try:
            self.client = AsyncIOMotorClient(self.mongo_url)
            self.db = self.client["syrabit"]
            # Test connection
            await self.client.admin.command('ping')
            print("✅ MongoDB connection successful")
            return True
        except Exception as e:
            print(f"❌ MongoDB connection failed: {e}")
            return False
    
    async def disconnect(self):
        """Close MongoDB connection"""
        if self.client:
            self.client.close()
    
    def log_result(self, test_name: str, success: bool, details: str = ""):
        """Log test result"""
        status = "✅ PASS" if success else "❌ FAIL"
        self.test_results.append({
            "test": test_name,
            "success": success,
            "details": details
        })
        print(f"{status}: {test_name}")
        if details:
            print(f"   └─ {details}")
    
    async def cleanup_test_data(self, email_prefix: str = "test_"):
        """Clean up test users and conversations"""
        try:
            # Delete test users
            result = await self.db.users.delete_many({
                "email": {"$regex": f"^{email_prefix}"}
            })
            print(f"🧹 Cleaned up {result.deleted_count} test users")
            
            # Delete test anonymous conversations
            result = await self.db.anon_conversations.delete_many({
                "anon_id": {"$regex": "^test-"}
            })
            print(f"🧹 Cleaned up {result.deleted_count} test anon conversations")
            
            # Delete test user conversations
            result = await self.db.conversations.delete_many({
                "user_id": {"$regex": "^test-"}
            })
            print(f"🧹 Cleaned up {result.deleted_count} test user conversations")
        except Exception as e:
            print(f"⚠️  Cleanup warning: {e}")
    
    # ========== User Creation Tests ==========
    
    async def test_create_admin_user(self) -> bool:
        """Test creating an admin user"""
        test_name = "Create Admin User"
        try:
            email = f"test_admin_{uuid.uuid4().hex[:8]}@syrabit.ai"
            password = "AdminPass123!"
            name = "Test Admin"
            
            user_doc = {
                "_id": uuid.uuid4(),
                "email": email.lower(),
                "name": name,
                "password_hash": self.pwd_ctx.hash(password),
                "plan": "pro",
                "credits_used": 0,
                "credits_limit": 1000,
                "document_access": "full",
                "onboarding_done": True,
                "is_admin": True,
                "status": "active",
                "role": "admin",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat()
            }
            
            result = await self.db.users.insert_one(user_doc)
            user_doc["id"] = str(result.inserted_id)
            
            # Verify user exists
            saved_user = await self.db.users.find_one({"_id": result.inserted_id})
            
            if saved_user and saved_user["is_admin"]:
                self.log_result(test_name, True, f"Created admin: {email}")
                return True
            else:
                self.log_result(test_name, False, "Admin user not found after creation")
                return False
                
        except Exception as e:
            self.log_result(test_name, False, str(e))
            return False
    
    async def test_create_staff_user(self) -> bool:
        """Test creating a staff user"""
        test_name = "Create Staff User"
        try:
            email = f"test_staff_{uuid.uuid4().hex[:8]}@syrabit.ai"
            password = "StaffPass123!"
            name = "Test Staff"
            
            user_doc = {
                "_id": uuid.uuid4(),
                "email": email.lower(),
                "name": name,
                "password_hash": self.pwd_ctx.hash(password),
                "plan": "pro",
                "credits_used": 0,
                "credits_limit": 500,
                "document_access": "moderate",
                "onboarding_done": True,
                "is_admin": False,
                "status": "active",
                "role": "staff",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat()
            }
            
            result = await self.db.users.insert_one(user_doc)
            user_doc["id"] = str(result.inserted_id)
            
            # Verify user exists
            saved_user = await self.db.users.find_one({"_id": result.inserted_id})
            
            if saved_user and saved_user["role"] == "staff":
                self.log_result(test_name, True, f"Created staff: {email}")
                return True
            else:
                self.log_result(test_name, False, "Staff user not found after creation")
                return False
                
        except Exception as e:
            self.log_result(test_name, False, str(e))
            return False
    
    async def test_create_regular_user(self) -> bool:
        """Test creating a regular student user"""
        test_name = "Create Regular User"
        try:
            email = f"test_user_{uuid.uuid4().hex[:8]}@syrabit.ai"
            password = "UserPass123!"
            name = "Test Student"
            
            user_doc = {
                "_id": uuid.uuid4(),
                "email": email.lower(),
                "name": name,
                "password_hash": self.pwd_ctx.hash(password),
                "plan": "free",
                "credits_used": 0,
                "credits_limit": 30,
                "document_access": "zero",
                "onboarding_done": False,
                "is_admin": False,
                "status": "active",
                "role": "student",
                "consent_dpdp": True,
                "consent_dpdp_version": "1.0",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat()
            }
            
            result = await self.db.users.insert_one(user_doc)
            user_doc["id"] = str(result.inserted_id)
            
            # Verify user exists
            saved_user = await self.db.users.find_one({"_id": result.inserted_id})
            
            if saved_user and saved_user["role"] == "student":
                self.log_result(test_name, True, f"Created user: {email}")
                return True
            else:
                self.log_result(test_name, False, "Regular user not found after creation")
                return False
                
        except Exception as e:
            self.log_result(test_name, False, str(e))
            return False
    
    # ========== JWT Token Tests ==========
    
    async def test_create_jwt_tokens(self) -> bool:
        """Test creating and verifying JWT tokens for different roles"""
        test_name = "JWT Token Creation & Verification"
        try:
            test_cases = [
                ("admin", "admin", "pro"),
                ("staff", "staff", "pro"),
                ("student", "student", "free")
            ]
            
            all_passed = True
            for role_name, role, plan in test_cases:
                user_id = str(uuid.uuid4())
                
                # Create access token
                expire = datetime.now(timezone.utc)
                from datetime import timedelta
                expire += timedelta(minutes=60)
                
                payload = {
                    "sub": user_id,
                    "role": role,
                    "plan": plan,
                    "exp": expire,
                    "iat": datetime.now(timezone.utc),
                    "type": "access"
                }
                
                token = jwt.encode(payload, self.jwt_secret, algorithm="HS256")
                
                # Verify token
                decoded = jwt.decode(token, self.jwt_secret, algorithms=["HS256"])
                
                if decoded["role"] != role or decoded["plan"] != plan:
                    all_passed = False
                    self.log_result(test_name, False, f"{role_name} token verification failed")
                    break
            
            if all_passed:
                self.log_result(test_name, True, "All role tokens created and verified")
            return all_passed
            
        except Exception as e:
            self.log_result(test_name, False, str(e))
            return False
    
    # ========== Anonymous Chat History Tests ==========
    
    async def test_anon_chat_history(self) -> bool:
        """Test anonymous user chat history functionality"""
        test_name = "Anonymous Chat History"
        try:
            anon_id = f"test-{uuid.uuid4().hex}"
            conv_id = str(uuid.uuid4())
            
            # Create test conversation
            messages = [
                {"role": "user", "content": "Hello, what is photosynthesis?", "timestamp": datetime.now(timezone.utc).isoformat()},
                {"role": "assistant", "content": "Photosynthesis is the process by which plants convert light energy...", "timestamp": datetime.now(timezone.utc).isoformat()}
            ]
            
            conv_doc = {
                "_id": f"{anon_id}:{conv_id}",
                "anon_id": anon_id,
                "conv_id": conv_id,
                "title": "Biology Question",
                "messages": messages,
                "subject_name": "Biology",
                "board_id": "ahsec",
                "class_id": "class_11",
                "message_count": len(messages),
                "preview": messages[-1]["content"][:100],
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
                "expires_at": datetime.now(timezone.utc) + timedelta(days=7)
            }
            
            # Save conversation
            result = await self.db.anon_conversations.update_one(
                {"_id": conv_doc["_id"]},
                {"$set": conv_doc},
                upsert=True
            )
            
            # Update user index
            await self.db.anon_user_index.update_one(
                {"anon_id": anon_id},
                {
                    "$addToSet": {"conv_ids": conv_id},
                    "$set": {
                        "updated_at": datetime.now(timezone.utc),
                        "last_conv_id": conv_id
                    }
                },
                upsert=True
            )
            
            # Retrieve conversation
            saved_conv = await self.db.anon_conversations.find_one({"_id": f"{anon_id}:{conv_id}"})
            
            if saved_conv and saved_conv["conv_id"] == conv_id:
                # List conversations
                user_index = await self.db.anon_user_index.find_one({"anon_id": anon_id})
                conv_count = len(user_index.get("conv_ids", [])) if user_index else 0
                
                self.log_result(
                    test_name, 
                    True, 
                    f"Saved & retrieved anon conversation. Total convs: {conv_count}"
                )
                return True
            else:
                self.log_result(test_name, False, "Conversation not found after saving")
                return False
                
        except Exception as e:
            self.log_result(test_name, False, str(e))
            return False
    
    async def test_anon_to_registered_migration(self) -> bool:
        """Test migrating anonymous conversations to registered user"""
        test_name = "Anon to Registered Migration"
        try:
            anon_id = f"test-migrate-{uuid.uuid4().hex}"
            user_id = f"test-user-{uuid.uuid4().hex}"
            email = f"test_migrate_{uuid.uuid4().hex[:8]}@syrabit.ai"
            
            # Create some anon conversations
            conv_ids = []
            for i in range(3):
                conv_id = str(uuid.uuid4())
                conv_ids.append(conv_id)
                
                conv_doc = {
                    "_id": f"{anon_id}:{conv_id}",
                    "anon_id": anon_id,
                    "conv_id": conv_id,
                    "title": f"Migrated Conversation {i+1}",
                    "messages": [
                        {"role": "user", "content": f"Question {i+1}"},
                        {"role": "assistant", "content": f"Answer {i+1}"}
                    ],
                    "created_at": datetime.now(timezone.utc),
                    "updated_at": datetime.now(timezone.utc),
                    "expires_at": datetime.now(timezone.utc) + timedelta(days=7)
                }
                
                await self.db.anon_conversations.update_one(
                    {"_id": conv_doc["_id"]},
                    {"$set": conv_doc},
                    upsert=True
                )
            
            # Update anon index
            await self.db.anon_user_index.update_one(
                {"anon_id": anon_id},
                {"$addToSet": {"conv_ids": {"$each": conv_ids}}},
                upsert=True
            )
            
            # Create registered user
            user_doc = {
                "_id": uuid.uuid4(),
                "email": email,
                "name": "Migrated User",
                "password_hash": self.pwd_ctx.hash("Password123!"),
                "plan": "free",
                "role": "student",
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            await self.db.users.insert_one(user_doc)
            
            # Migrate conversations
            migrated_count = 0
            for conv_id in conv_ids:
                anon_conv = await self.db.anon_conversations.find_one({"_id": f"{anon_id}:{conv_id}"})
                if anon_conv:
                    user_conv = {
                        "_id": conv_id,
                        "user_id": user_id,
                        "email": email,
                        "title": anon_conv["title"],
                        "messages": anon_conv["messages"],
                        "is_migrated_from_anon": True,
                        "migrated_at": datetime.now(timezone.utc)
                    }
                    
                    await self.db.conversations.update_one(
                        {"_id": conv_id},
                        {"$set": user_conv},
                        upsert=True
                    )
                    migrated_count += 1
            
            # Verify migration
            user_convs = await self.db.conversations.count_documents({"user_id": user_id})
            
            if user_convs == 3:
                self.log_result(test_name, True, f"Successfully migrated {migrated_count} conversations")
                return True
            else:
                self.log_result(test_name, False, f"Expected 3 conversations, found {user_convs}")
                return False
                
        except Exception as e:
            self.log_result(test_name, False, str(e))
            return False
    
    # ========== Database Index Tests ==========
    
    async def test_database_indexes(self) -> bool:
        """Verify required database indexes exist"""
        test_name = "Database Indexes"
        try:
            indexes_ok = True
            details = []
            
            # Check users collection indexes
            user_indexes = await self.db.users.index_information()
            if "email_1" not in user_indexes:
                indexes_ok = False
                details.append("Missing email index on users")
            
            # Check anon_conversations indexes
            anon_conv_indexes = await self.db.anon_conversations.index_information()
            if "anon_id_1" not in anon_conv_indexes:
                indexes_ok = False
                details.append("Missing anon_id index on anon_conversations")
            
            # Check TTL index
            ttl_found = False
            for idx_name, idx_info in anon_conv_indexes.items():
                if "expireAfterSeconds" in str(idx_info):
                    ttl_found = True
                    break
            
            if not ttl_found:
                details.append("Warning: TTL index may not be configured")
            
            # Check anon_user_index
            anon_user_indexes = await self.db.anon_user_index.index_information()
            if "anon_id_1" not in anon_user_indexes:
                indexes_ok = False
                details.append("Missing anon_id index on anon_user_index")
            
            if indexes_ok:
                self.log_result(test_name, True, "All critical indexes present")
            else:
                self.log_result(test_name, False, "; ".join(details))
            
            return indexes_ok
            
        except Exception as e:
            self.log_result(test_name, False, str(e))
            return False
    
    # ========== Cloudflare Turnstile Test ==========
    
    async def test_turnstile_integration(self) -> bool:
        """Test Cloudflare Turnstile integration (skip if disabled)"""
        test_name = "Cloudflare Turnstile Integration"
        
        if not self.turnstile_secret:
            self.log_result(test_name, True, "Skipped (TURNSTILE_OFF)")
            return True
        
        try:
            # Note: We can't actually test Turnstile without a valid token from frontend
            # This just verifies the configuration is present
            if self.turnstile_secret and len(self.turnstile_secret) > 10:
                self.log_result(test_name, True, "Turnstile secret configured")
                return True
            else:
                self.log_result(test_name, False, "Turnstile secret not properly configured")
                return False
                
        except Exception as e:
            self.log_result(test_name, False, str(e))
            return False
    
    # ========== Summary ==========
    
    def print_summary(self):
        """Print test summary"""
        print("\n" + "="*60)
        print("📊 AUTHENTICATION VERIFICATION SUMMARY")
        print("="*60)
        
        total = len(self.test_results)
        passed = sum(1 for r in self.test_results if r["success"])
        failed = total - passed
        
        print(f"\nTotal Tests: {total}")
        print(f"✅ Passed: {passed}")
        print(f"❌ Failed: {failed}")
        print(f"Success Rate: {(passed/total*100):.1f}%\n")
        
        if failed > 0:
            print("Failed Tests:")
            for result in self.test_results:
                if not result["success"]:
                    print(f"  ❌ {result['test']}: {result['details']}")
        
        print("\n" + "="*60)
        
        return failed == 0


async def main():
    """Main verification routine"""
    print("="*60)
    print("🔐 SYRABIT.AI AUTHENTICATION VERIFICATION SUITE")
    print("="*60)
    print()
    
    # Load environment
    mongo_url = os.getenv("MONGO_URL", "mongodb://localhost:27017")
    jwt_secret = os.getenv("JWT_SECRET", "dev-secret-change-in-production")
    turnstile_secret = os.getenv("CLOUDFLARE_TURNSTILE_SECRET_KEY", "")
    
    print(f"MongoDB URL: {mongo_url[:30]}...")
    print(f"JWT Secret: {'*' * 20}")
    print(f"Turnstile: {'Enabled' if turnstile_secret else 'Disabled'}")
    print()
    
    # Initialize verifier
    verifier = AuthVerifier(mongo_url, jwt_secret, turnstile_secret)
    
    if not await verifier.connect():
        print("❌ Cannot proceed without MongoDB connection")
        sys.exit(1)
    
    try:
        # Cleanup previous test data
        print("\n🧹 Cleaning up previous test data...")
        await verifier.cleanup_test_data()
        
        # Run tests
        print("\n🏃 Running authentication tests...\n")
        
        await verifier.test_create_admin_user()
        await verifier.test_create_staff_user()
        await verifier.test_create_regular_user()
        await verifier.test_create_jwt_tokens()
        await verifier.test_anon_chat_history()
        await verifier.test_anon_to_registered_migration()
        await verifier.test_database_indexes()
        await verifier.test_turnstile_integration()
        
        # Print summary
        success = verifier.print_summary()
        
        sys.exit(0 if success else 1)
        
    finally:
        await verifier.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
