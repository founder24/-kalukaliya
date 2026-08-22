-- Syrabit D1 Initial Schema
-- Generated from apps/api/src/db/schema.ts
-- Run: wrangler d1 migrations apply syrabit-db --remote --env production

-- ─────────────────────────────────────────────────────────────────────────────
-- USERS
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  email TEXT UNIQUE,
  hashed_password TEXT,
  auth_provider TEXT DEFAULT 'anonymous',
  role TEXT DEFAULT 'student',
  subscription_tier TEXT DEFAULT 'free',
  subscription_status TEXT DEFAULT 'active',
  razorpay_subscription_id TEXT,
  razorpay_customer_id TEXT,
  current_period_start INTEGER,
  current_period_end INTEGER,
  cancel_at_period_end INTEGER DEFAULT 0,
  monthly_message_count INTEGER DEFAULT 0,
  last_reset_date INTEGER DEFAULT (unixepoch()),
  total_lifetime_messages INTEGER DEFAULT 0,
  credits_remaining INTEGER DEFAULT 0,
  credits_used INTEGER DEFAULT 0,
  total_tokens_used INTEGER DEFAULT 0,
  name TEXT,
  avatar_url TEXT,
  consent_dpdp INTEGER DEFAULT 0,
  preferred_language TEXT DEFAULT 'as',
  voice_enabled INTEGER DEFAULT 1,
  theme TEXT DEFAULT 'light',
  saved_subjects TEXT DEFAULT '[]',
  phone TEXT,
  onboarding_done INTEGER DEFAULT 0,
  ads_opt_out INTEGER DEFAULT 0,
  grade TEXT,
  board_id TEXT,
  board_name TEXT,
  class_id TEXT,
  class_name TEXT,
  stream_id TEXT,
  stream_name TEXT,
  deleted_at INTEGER,
  deletion_reason TEXT,
  created_at INTEGER DEFAULT (unixepoch()),
  updated_at INTEGER DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS users_subscription_idx ON users(subscription_tier);

-- ─────────────────────────────────────────────────────────────────────────────
-- CONTENT HIERARCHY
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS boards (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  slug TEXT NOT NULL UNIQUE,
  description TEXT,
  created_at INTEGER DEFAULT (unixepoch()),
  updated_at INTEGER DEFAULT (unixepoch())
);

CREATE TABLE IF NOT EXISTS classes (
  id TEXT PRIMARY KEY,
  board_id TEXT NOT NULL REFERENCES boards(id),
  name TEXT NOT NULL,
  slug TEXT NOT NULL,
  level TEXT,
  created_at INTEGER DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS classes_board_idx ON classes(board_id);
CREATE UNIQUE INDEX IF NOT EXISTS classes_board_slug_idx ON classes(board_id, slug);

CREATE TABLE IF NOT EXISTS streams (
  id TEXT PRIMARY KEY,
  class_id TEXT NOT NULL REFERENCES classes(id),
  name TEXT NOT NULL,
  slug TEXT NOT NULL,
  created_at INTEGER DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS streams_class_idx ON streams(class_id);

CREATE TABLE IF NOT EXISTS subjects (
  id TEXT PRIMARY KEY,
  stream_id TEXT NOT NULL REFERENCES streams(id),
  name TEXT NOT NULL,
  slug TEXT NOT NULL,
  description TEXT,
  image_url TEXT,
  pyq_papers TEXT DEFAULT '[]',
  is_published INTEGER DEFAULT 0,
  created_at INTEGER DEFAULT (unixepoch()),
  updated_at INTEGER DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS subjects_stream_idx ON subjects(stream_id);
CREATE UNIQUE INDEX IF NOT EXISTS subjects_stream_slug_idx ON subjects(stream_id, slug);

CREATE TABLE IF NOT EXISTS chapters (
  id TEXT PRIMARY KEY,
  subject_id TEXT NOT NULL REFERENCES subjects(id),
  title TEXT NOT NULL,
  slug TEXT NOT NULL,
  slug_as TEXT,
  chapter_number INTEGER,
  status TEXT DEFAULT 'draft',
  content_type TEXT DEFAULT 'standard',
  notes_en TEXT,
  notes_as TEXT,
  rag_text TEXT,
  rag_text_as TEXT,
  rag_updated_at INTEGER,
  rag_indexed_at INTEGER,
  rag_sections_en TEXT DEFAULT '[]',
  rag_sections_as TEXT DEFAULT '[]',
  published_topics TEXT DEFAULT '[]',
  qa_en TEXT DEFAULT '[]',
  qa_as TEXT DEFAULT '[]',
  word_count_en INTEGER DEFAULT 0,
  word_count_as INTEGER DEFAULT 0,
  created_at INTEGER DEFAULT (unixepoch()),
  updated_at INTEGER DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS chapters_subject_idx ON chapters(subject_id);
CREATE UNIQUE INDEX IF NOT EXISTS chapters_subject_slug_idx ON chapters(subject_id, slug);
CREATE INDEX IF NOT EXISTS chapters_status_idx ON chapters(status);

-- ─────────────────────────────────────────────────────────────────────────────
-- AUTH TOKENS
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS password_reset_tokens (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  token_hash TEXT NOT NULL,
  expires_at INTEGER NOT NULL,
  used_at INTEGER,
  created_at INTEGER DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS prt_user_idx ON password_reset_tokens(user_id);
CREATE INDEX IF NOT EXISTS prt_expires_idx ON password_reset_tokens(expires_at);

-- ─────────────────────────────────────────────────────────────────────────────
-- CHAT
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS chats (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  lang TEXT DEFAULT 'en',
  subject_id TEXT,
  chapter_id TEXT,
  metadata TEXT DEFAULT '{}',
  expires_at INTEGER,
  created_at INTEGER DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS chats_user_idx ON chats(user_id);
CREATE INDEX IF NOT EXISTS chats_session_idx ON chats(session_id);
CREATE INDEX IF NOT EXISTS chats_expires_idx ON chats(expires_at);

CREATE TABLE IF NOT EXISTS chat_feedback (
  id TEXT PRIMARY KEY,
  chat_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  rating INTEGER,
  comment TEXT,
  expires_at INTEGER,
  created_at INTEGER DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS cf_user_idx ON chat_feedback(user_id);
CREATE INDEX IF NOT EXISTS cf_expires_idx ON chat_feedback(expires_at);

-- ─────────────────────────────────────────────────────────────────────────────
-- QUOTA USAGE
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS quota_usage (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  period TEXT NOT NULL,
  count INTEGER DEFAULT 0,
  updated_at INTEGER DEFAULT (unixepoch())
);

CREATE UNIQUE INDEX IF NOT EXISTS quota_user_period_idx ON quota_usage(user_id, period);

-- ─────────────────────────────────────────────────────────────────────────────
-- PAYMENTS
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS payments (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  razorpay_payment_id TEXT,
  razorpay_order_id TEXT,
  razorpay_subscription_id TEXT,
  amount INTEGER,
  currency TEXT DEFAULT 'INR',
  status TEXT NOT NULL,
  plan TEXT,
  metadata TEXT DEFAULT '{}',
  created_at INTEGER DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS payments_user_idx ON payments(user_id);
CREATE INDEX IF NOT EXISTS payments_rzp_payment_idx ON payments(razorpay_payment_id);

CREATE TABLE IF NOT EXISTS transactions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  type TEXT NOT NULL,
  amount INTEGER NOT NULL,
  metadata TEXT DEFAULT '{}',
  created_at INTEGER DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS transactions_user_idx ON transactions(user_id);

CREATE TABLE IF NOT EXISTS refund_requests (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  payment_id TEXT,
  reason TEXT,
  status TEXT DEFAULT 'pending',
  created_at INTEGER DEFAULT (unixepoch()),
  updated_at INTEGER DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS refund_user_idx ON refund_requests(user_id);

CREATE TABLE IF NOT EXISTS payments_pending (
  id TEXT PRIMARY KEY,
  order_id TEXT NOT NULL UNIQUE,
  user_id TEXT NOT NULL,
  metadata TEXT DEFAULT '{}',
  expires_at INTEGER NOT NULL,
  created_at INTEGER DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS pp_expires_idx ON payments_pending(expires_at);

-- ─────────────────────────────────────────────────────────────────────────────
-- RAG PIPELINE
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rag_documents (
  id TEXT PRIMARY KEY,
  chapter_id TEXT,
  subject_id TEXT,
  source_type TEXT NOT NULL,
  medium TEXT NOT NULL,
  content TEXT,
  metadata TEXT DEFAULT '{}',
  indexed_at INTEGER,
  created_at INTEGER DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS rag_docs_chapter_idx ON rag_documents(chapter_id);
CREATE INDEX IF NOT EXISTS rag_docs_subject_idx ON rag_documents(subject_id);

CREATE TABLE IF NOT EXISTS chunks (
  id TEXT PRIMARY KEY,
  document_id TEXT REFERENCES rag_documents(id),
  chapter_id TEXT,
  subject_id TEXT,
  source_type TEXT NOT NULL,
  medium TEXT NOT NULL,
  chunk_type TEXT,
  content TEXT NOT NULL,
  vector_id TEXT,
  metadata TEXT DEFAULT '{}',
  created_at INTEGER DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS chunks_chapter_idx ON chunks(chapter_id);
CREATE INDEX IF NOT EXISTS chunks_subject_idx ON chunks(subject_id);
CREATE INDEX IF NOT EXISTS chunks_vector_idx ON chunks(vector_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- JOBS & PIPELINE
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS publish_jobs (
  id TEXT PRIMARY KEY,
  chapter_id TEXT NOT NULL,
  status TEXT DEFAULT 'pending',
  progress TEXT DEFAULT '{}',
  error_log TEXT,
  created_at INTEGER DEFAULT (unixepoch()),
  updated_at INTEGER DEFAULT (unixepoch()),
  completed_at INTEGER
);

CREATE INDEX IF NOT EXISTS pj_chapter_idx ON publish_jobs(chapter_id);
CREATE INDEX IF NOT EXISTS pj_status_idx ON publish_jobs(status);

CREATE TABLE IF NOT EXISTS seed_runs (
  id TEXT PRIMARY KEY,
  medium TEXT NOT NULL,
  status TEXT DEFAULT 'running',
  total_chapters INTEGER DEFAULT 0,
  processed INTEGER DEFAULT 0,
  failed INTEGER DEFAULT 0,
  log TEXT DEFAULT '[]',
  started_at INTEGER DEFAULT (unixepoch()),
  completed_at INTEGER,
  expires_at INTEGER
);

CREATE INDEX IF NOT EXISTS sr_expires_idx ON seed_runs(expires_at);

-- ─────────────────────────────────────────────────────────────────────────────
-- ANALYTICS & AUDIT
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ai_usage_logs (
  id TEXT PRIMARY KEY,
  user_id TEXT,
  provider TEXT,
  model TEXT,
  input_tokens INTEGER DEFAULT 0,
  output_tokens INTEGER DEFAULT 0,
  latency_ms INTEGER,
  request_id TEXT,
  expires_at INTEGER,
  created_at INTEGER DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS ail_user_idx ON ai_usage_logs(user_id);
CREATE INDEX IF NOT EXISTS ail_expires_idx ON ai_usage_logs(expires_at);
CREATE INDEX IF NOT EXISTS ail_created_idx ON ai_usage_logs(created_at);

CREATE TABLE IF NOT EXISTS content_audit_log (
  id TEXT PRIMARY KEY,
  user_id TEXT,
  action TEXT NOT NULL,
  target_type TEXT,
  target_id TEXT,
  diff TEXT,
  expires_at INTEGER,
  created_at INTEGER DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS cal_target_idx ON content_audit_log(target_type, target_id);
CREATE INDEX IF NOT EXISTS cal_expires_idx ON content_audit_log(expires_at);

-- ─────────────────────────────────────────────────────────────────────────────
-- OPERATIONAL
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS email_failure_events (
  id TEXT PRIMARY KEY,
  recipient TEXT,
  error_code TEXT,
  detail TEXT,
  expires_at INTEGER NOT NULL,
  created_at INTEGER DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS efe_expires_idx ON email_failure_events(expires_at);

CREATE TABLE IF NOT EXISTS email_alert_state (
  id TEXT PRIMARY KEY DEFAULT 'singleton',
  alert_active INTEGER DEFAULT 0,
  last_alert_at INTEGER,
  updated_at INTEGER DEFAULT (unixepoch())
);

CREATE TABLE IF NOT EXISTS dead_letters (
  id TEXT PRIMARY KEY,
  job_type TEXT NOT NULL,
  payload TEXT DEFAULT '{}',
  error TEXT,
  attempts INTEGER DEFAULT 1,
  expires_at INTEGER NOT NULL,
  created_at INTEGER DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS dl_expires_idx ON dead_letters(expires_at);

CREATE TABLE IF NOT EXISTS admin_config (
  key TEXT PRIMARY KEY,
  value TEXT,
  updated_at INTEGER DEFAULT (unixepoch())
);

CREATE TABLE IF NOT EXISTS memory_brain (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  key TEXT NOT NULL,
  value TEXT,
  updated_at INTEGER DEFAULT (unixepoch())
);

CREATE UNIQUE INDEX IF NOT EXISTS mb_user_key_idx ON memory_brain(user_id, key);

CREATE TABLE IF NOT EXISTS schema_migrations (
  version TEXT PRIMARY KEY,
  applied_at INTEGER DEFAULT (unixepoch())
);

-- Mark this migration as applied
INSERT OR IGNORE INTO schema_migrations(version) VALUES ('0000_initial_schema');
