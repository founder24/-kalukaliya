/**
 * MongoDB Index & TTL Setup Script
 *
 * Run: mongosh "$MONGODB_URI" scripts/mongo-setup.js
 *
 * Creates:
 * - Compound index for accuracy aggregation (lang + model_provider + timestamp)
 * - User feedback lookup index
 * - TTL index (auto-delete feedback after 30 days)
 */

const db = db.getSiblingDB('syrabit_prod');

print("═══════════════════════════════════════");
print("  SYRABIT — MongoDB Index Setup");
print("═══════════════════════════════════════\n");

// ── chat_feedback collection indexes ──

print("📊 Creating chat_feedback indexes...\n");

// 1. Primary aggregation index: accuracy by lang + model over time
db.chat_feedback.createIndex(
  { lang: 1, model_provider: 1, timestamp: 1 },
  { name: "idx_feedback_lang_model_time", background: true }
);
print("  ✅ idx_feedback_lang_model_time (compound)");

// 2. User feedback history (for per-user queries)
db.chat_feedback.createIndex(
  { user_id: 1, timestamp: -1 },
  { name: "idx_feedback_user_time", background: true }
);
print("  ✅ idx_feedback_user_time");

// 3. Message dedup check (prevent double-voting)
db.chat_feedback.createIndex(
  { user_id: 1, message_id: 1 },
  { name: "idx_feedback_user_msg", unique: true, background: true }
);
print("  ✅ idx_feedback_user_msg (unique — prevents double-voting)");

// 4. TTL: auto-delete after 30 days
db.chat_feedback.createIndex(
  { timestamp: 1 },
  { expireAfterSeconds: 30 * 24 * 60 * 60, name: "ttl_feedback_30d" }
);
print("  ✅ ttl_feedback_30d (TTL: 30 days)\n");

// ── Verify ──
print("📋 All chat_feedback indexes:\n");
db.chat_feedback.getIndexes().forEach(function(idx) {
  print("  • " + idx.name + " → " + JSON.stringify(idx.key));
});

// ── Also ensure chats indexes exist ──
print("\n📊 Verifying chats collection indexes...\n");

db.chats.createIndex(
  { user_id: 1, updated_at: -1 },
  { name: "idx_chats_user_updated", background: true }
);
db.chats.createIndex(
  { session_id: 1 },
  { name: "idx_chats_session", background: true }
);
print("  ✅ chats indexes verified\n");

print("═══════════════════════════════════════");
print("  ✅ All indexes created successfully!");
print("═══════════════════════════════════════\n");
