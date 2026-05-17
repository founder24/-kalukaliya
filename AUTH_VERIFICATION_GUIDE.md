# 🔐 Syrabit.ai Authentication Verification Guide

This guide covers the complete authentication system verification for Syrabit.ai, including admin, staff, user authentication, and anonymous chat history functionality.

## 📋 Table of Contents

1. [Prerequisites](#prerequisites)
2. [Quick Start](#quick-start)
3. [Verification Script](#verification-script)
4. [Test Coverage](#test-coverage)
5. [Manual Testing](#manual-testing)
6. [Troubleshooting](#troubleshooting)

---

## 🛠️ Prerequisites

### Required Dependencies

```bash
pip install httpx motor passlib PyJWT bcrypt
```

### Environment Variables

Ensure these are set in your `.env` file:

```bash
# MongoDB (Required)
MONGO_URL=mongodb+srv://user:pass@cluster.mongodb.net/syrabit

# JWT (Required)
JWT_SECRET=your-super-secret-key-min-32-chars

# Cloudflare Turnstile (Optional but recommended)
CLOUDFLARE_TURNSTILE_SECRET_KEY=1x0000000000000000000000000000000AA
TURNSTILE_ON=true
```

---

## 🚀 Quick Start

### Run Full Verification Suite

```bash
cd /workspace
python verify_auth.py
```

### Expected Output

```
============================================================
🔐 SYRABIT.AI AUTHENTICATION VERIFICATION SUITE
============================================================

MongoDB URL: mongodb+srv://user:pass@cl...
JWT Secret: ********************
Turnstile: Enabled

✅ MongoDB connection successful

🧹 Cleaning up previous test data...
🧹 Cleaned up 5 test users
🧹 Cleaned up 12 test anon conversations
🧹 Cleaned up 8 test user conversations

🏃 Running authentication tests...

✅ PASS: Create Admin User
   └─ Created admin: test_admin_a1b2c3d4@syrabit.ai
✅ PASS: Create Staff User
   └─ Created staff: test_staff_e5f6g7h8@syrabit.ai
✅ PASS: Create Regular User
   └─ Created user: test_user_i9j0k1l2@syrabit.ai
✅ PASS: JWT Token Creation & Verification
   └─ All role tokens created and verified
✅ PASS: Anonymous Chat History
   └─ Saved & retrieved anon conversation. Total convs: 1
✅ PASS: Anon to Registered Migration
   └─ Successfully migrated 3 conversations
✅ PASS: Database Indexes
   └─ All critical indexes present
✅ PASS: Cloudflare Turnstile Integration
   └─ Turnstile secret configured

============================================================
📊 AUTHENTICATION VERIFICATION SUMMARY
============================================================

Total Tests: 8
✅ Passed: 8
❌ Failed: 0
Success Rate: 100.0%

============================================================
```

---

## 📊 Test Coverage

### 1. **User Creation Tests**

| Test | Description | Collections |
|------|-------------|-------------|
| Admin User | Creates admin with full permissions | `users` |
| Staff User | Creates staff with moderate access | `users` |
| Regular User | Creates student with free tier limits | `users` |

**User Schema Fields Verified:**
- `email`, `password_hash`, `name`
- `plan` (free/pro), `role` (admin/staff/student)
- `credits_limit`, `is_admin`, `status`
- `consent_dpdp`, `created_at`

### 2. **JWT Token Tests**

| Token Type | Expiration | Claims |
|------------|------------|--------|
| Access Token | 60 minutes | sub, role, plan, exp, iat |
| Refresh Token | 7 days | sub, exp, iat |

**Verified Roles:**
- ✅ Admin (`role: "admin"`, `plan: "pro"`)
- ✅ Staff (`role: "staff"`, `plan: "pro"`)
- ✅ Student (`role: "student"`, `plan: "free"`)

### 3. **Anonymous Chat History Tests**

| Feature | Collection | TTL |
|---------|------------|-----|
| Save Conversation | `anon_conversations` | 7 days |
| List Conversations | `anon_user_index` | N/A |
| Retrieve Single | `anon_conversations` | 7 days |
| Delete Conversation | Both collections | Immediate |

**Document Structure:**
```javascript
{
  "_id": "anon_id:conv_id",
  "anon_id": "device-token-uuid",
  "conv_id": "conversation-uuid",
  "title": "Biology Question",
  "messages": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."}
  ],
  "subject_name": "Biology",
  "board_id": "ahsec",
  "class_id": "class_11",
  "message_count": 2,
  "preview": "...",
  "created_at": ISODate(),
  "updated_at": ISODate(),
  "expires_at": ISODate() // TTL index
}
```

### 4. **Migration Tests**

**Flow: Anonymous → Registered**

1. Create 3 anon conversations
2. Register user account
3. Migrate conversations to user collection
4. Verify all 3 transferred correctly
5. Clean up anon collections

**Collections Involved:**
- Source: `anon_conversations`, `anon_user_index`
- Destination: `conversations`, `users`

### 5. **Database Index Tests**

| Collection | Required Indexes | Purpose |
|------------|------------------|---------|
| `users` | `email_1` | Fast user lookup |
| `anon_conversations` | `anon_id_1` | Find by device |
| `anon_conversations` | `expires_at_1` (TTL) | Auto-cleanup |
| `anon_user_index` | `anon_id_1` (unique) | Track convos |

---

## 🧪 Manual Testing

### Test 1: Admin Authentication Flow

```bash
# Create admin via API
curl -X POST https://api.syrabit.ai/api/auth/signup \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Admin User",
    "email": "admin@syrabit.ai",
    "password": "SecurePass123!",
    "consent_dpdp": true
  }'

# Login
curl -X POST https://api.syrabit.ai/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "email": "admin@syrabit.ai",
    "password": "SecurePass123!"
  }'
```

**Expected Response:**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer",
  "user": {
    "id": "uuid-here",
    "email": "admin@syrabit.ai",
    "role": "admin",
    "is_admin": true,
    "plan": "free"
  }
}
```

### Test 2: Anonymous Chat Flow

```javascript
// Frontend: Generate device token (automatic)
// Cookie: syrabit_device=<signed-token>

// Send first message (no auth required)
curl -X POST https://api.syrabit.ai/api/chat \
  -H "Content-Type: application/json" \
  -H "Cookie: syrabit_device=test-device-123" \
  -d '{
    "message": "What is photosynthesis?",
    "subject": "Biology"
  }'

// Response includes conversation_id
// Subsequent messages use same conversation_id
```

### Test 3: Migration Flow

```javascript
// Step 1: Anonymous user chats (creates conversations)
POST /api/chat (with device cookie)

// Step 2: User decides to register
POST /api/auth/signup
// Receives JWT tokens

// Step 3: Migrate conversations
POST /api/user/migrate-anon
// Body: { "device_token": "..." }
// Backend transfers all anon convos to user account
```

---

## 🔍 Troubleshooting

### Issue: MongoDB Connection Failed

**Symptoms:**
```
❌ MongoDB connection failed: connection timeout
```

**Solutions:**
1. Check `MONGO_URL` in `.env`
2. Verify network access to MongoDB Atlas
3. Whitelist your IP in Atlas Network Access
4. Check credentials are URL-encoded

### Issue: JWT Token Verification Failed

**Symptoms:**
```
❌ JWT Token Creation & Verification: Invalid signature
```

**Solutions:**
1. Ensure `JWT_SECRET` is identical across services
2. Minimum 32 characters recommended
3. Avoid special characters that need escaping
4. Restart backend after changing secret

### Issue: Turnstile Verification Fails

**Symptoms:**
```
⚠️ Turnstile secret not properly configured
```

**Solutions:**
1. Verify site key matches secret key (same widget)
2. Check domain whitelist in Cloudflare dashboard
3. Set `TURNSTILE_ON=false` for local development
4. Ensure frontend sends valid token in `cf-turnstile-response` header

### Issue: Missing Database Indexes

**Symptoms:**
```
❌ Database Indexes: Missing email index on users
```

**Solution - Create Indexes:**

```javascript
// Connect to MongoDB shell or use Compass
use syrabit

// Users collection
db.users.createIndex({ email: 1 }, { unique: true })

// Anonymous conversations
db.anon_conversations.createIndex({ anon_id: 1 })
db.anon_conversations.createIndex(
  { expires_at: 1 },
  { expireAfterSeconds: 0 }
)
db.anon_conversations.createIndex({ anon_id: 1, conv_id: 1 })

// User index
db.anon_user_index.createIndex({ anon_id: 1 }, { unique: true })
```

Or run the initialization script:
```bash
python /workspace/backend/scripts/init_db_indexes.py
```

---

## 📈 Performance Benchmarks

### Expected Latencies (P95)

| Operation | Target | Acceptable |
|-----------|--------|------------|
| User signup | < 200ms | < 500ms |
| User login | < 150ms | < 300ms |
| JWT creation | < 10ms | < 50ms |
| Save anon convo | < 50ms | < 100ms |
| Get anon convo | < 30ms | < 80ms |
| Migration (10 convos) | < 500ms | < 1000ms |

### Throughput Targets

| Metric | Target |
|--------|--------|
| Signups/min | 100 |
| Logins/min | 500 |
| Chat messages/min | 1000 |
| Concurrent anon users | 10,000 |

---

## 🔒 Security Checklist

- [ ] Password hashing with bcrypt (cost factor ≥ 12)
- [ ] JWT secrets rotated every 90 days
- [ ] HTTPS enforced for all endpoints
- [ ] HttpOnly cookies for tokens
- [ ] CSRF protection enabled
- [ ] Rate limiting active (30 msgs/day free tier)
- [ ] Turnstile bot protection enabled
- [ ] Input validation on all endpoints
- [ ] MongoDB authentication enabled
- [ ] TTL indexes for anon data cleanup

---

## 📚 Related Documentation

- [Master Implementation Plan](./MASTER_PLAN.md)
- [API Documentation](./docs/API.md)
- [Deployment Guide](./DEPLOYMENT.md)
- [Rate Limiting Strategy](./docs/RATE_LIMITING.md)

---

## 🆘 Support

For issues or questions:
1. Check logs: `docker logs syrabit-backend`
2. Review Sentry errors: https://sentry.io/organizations/syrabit
3. Contact: support@syrabit.ai

**Last Updated:** May 2025  
**Version:** 1.0.0
