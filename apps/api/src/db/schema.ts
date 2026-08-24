import { sqliteTable, text, integer, index, uniqueIndex } from 'drizzle-orm/sqlite-core';
import { sql } from 'drizzle-orm';

// ─────────────────────────────────────────────────────────────────────────────
// USERS
// ─────────────────────────────────────────────────────────────────────────────

export const users = sqliteTable('users', {
  id: text('id').primaryKey(),                                        // UUID
  email: text('email'),
  hashedPassword: text('hashed_password'),
  authProvider: text('auth_provider').default('anonymous'),           // local | anonymous
  role: text('role').default('student'),                              // student | educator | staff | admin

  // Subscription
  subscriptionTier: text('subscription_tier').default('free'),        // free | starter | pro | premium
  subscriptionStatus: text('subscription_status').default('active'),  // active | past_due | cancelled
  razorpaySubscriptionId: text('razorpay_subscription_id'),
  razorpayCustomerId: text('razorpay_customer_id'),
  currentPeriodStart: integer('current_period_start'),                // unix epoch
  currentPeriodEnd: integer('current_period_end'),
  cancelAtPeriodEnd: integer('cancel_at_period_end').default(0),      // 0|1

  // Usage quotas
  monthlyMessageCount: integer('monthly_message_count').default(0),
  lastResetDate: integer('last_reset_date').default(sql`(unixepoch())`),
  totalLifetimeMessages: integer('total_lifetime_messages').default(0),
  creditsRemaining: integer('credits_remaining').default(0),
  creditsUsed: integer('credits_used').default(0),
  totalTokensUsed: integer('total_tokens_used').default(0),

  // Profile
  name: text('name'),
  avatarUrl: text('avatar_url'),
  consentDpdp: integer('consent_dpdp').default(0),
  preferredLanguage: text('preferred_language').default('as'),        // en | as
  voiceEnabled: integer('voice_enabled').default(1),
  theme: text('theme').default('light'),
  savedSubjects: text('saved_subjects').default('[]'),                // JSON string[]
  phone: text('phone'),

  // Onboarding
  onboardingDone: integer('onboarding_done').default(0),
  adsOptOut: integer('ads_opt_out').default(0),
  grade: text('grade'),
  boardId: text('board_id'),
  boardName: text('board_name'),
  classId: text('class_id'),
  className: text('class_name'),
  streamId: text('stream_id'),
  streamName: text('stream_name'),

  // Deletion
  deletedAt: integer('deleted_at'),
  deletionReason: text('deletion_reason'),
  // Strongly-consistent D1 cutoff for invalidating all older JWT/cookie sessions.
  sessionValidAfter: integer('session_valid_after').default(0),

  createdAt: integer('created_at').default(sql`(unixepoch())`),
  updatedAt: integer('updated_at').default(sql`(unixepoch())`),
}, (t) => [
  uniqueIndex('users_email_idx').on(t.email),
  index('users_subscription_idx').on(t.subscriptionTier),
]);

// ─────────────────────────────────────────────────────────────────────────────
// CONTENT HIERARCHY: Board → Class → Stream → Subject → Chapter
// ─────────────────────────────────────────────────────────────────────────────

export const boards = sqliteTable('boards', {
  id: text('id').primaryKey(),
  name: text('name').notNull(),
  slug: text('slug').notNull(),
  description: text('description'),
  status: text('status').default('published'),
  createdAt: integer('created_at').default(sql`(unixepoch())`),
  updatedAt: integer('updated_at').default(sql`(unixepoch())`),
}, (t) => [
  uniqueIndex('boards_slug_idx').on(t.slug),
]);

export const classes = sqliteTable('classes', {
  id: text('id').primaryKey(),
  boardId: text('board_id').notNull().references(() => boards.id),
  name: text('name').notNull(),
  slug: text('slug').notNull(),
  level: text('level'),                                               // hs-1st-year | hs-2nd-year
  status: text('status').default('published'),
  createdAt: integer('created_at').default(sql`(unixepoch())`),
}, (t) => [
  index('classes_board_idx').on(t.boardId),
  uniqueIndex('classes_board_slug_idx').on(t.boardId, t.slug),
]);

export const streams = sqliteTable('streams', {
  id: text('id').primaryKey(),
  classId: text('class_id').notNull().references(() => classes.id),
  name: text('name').notNull(),
  slug: text('slug').notNull(),
  status: text('status').default('published'),
  createdAt: integer('created_at').default(sql`(unixepoch())`),
}, (t) => [
  index('streams_class_idx').on(t.classId),
]);

export const subjects = sqliteTable('subjects', {
  id: text('id').primaryKey(),
  // Migration 0002 (2026-08-17): removed .notNull() and .references().
  // 45 MongoDB subjects (NEP college programs) had no stream association;
  // NULL stream_id is valid for subjects outside the board→class→stream hierarchy.
  // SQLite UNIQUE index treats (NULL, slug) as unique per slug, so no conflicts arise.
  streamId: text('stream_id'),
  name: text('name').notNull(),
  slug: text('slug').notNull(),
  description: text('description'),
  imageUrl: text('image_url'),
  pyqPapers: text('pyq_papers').default('[]'),                        // JSON PYQPaper[]
  isPublished: integer('is_published').default(0),
  createdAt: integer('created_at').default(sql`(unixepoch())`),
  updatedAt: integer('updated_at').default(sql`(unixepoch())`),
}, (t) => [
  index('subjects_stream_idx').on(t.streamId),
  uniqueIndex('subjects_stream_slug_idx').on(t.streamId, t.slug),
]);

export const chapters = sqliteTable('chapters', {
  id: text('id').primaryKey(),
  subjectId: text('subject_id').notNull(),   // no FK — legacy MongoDB UUID subject IDs (see migration 0001)
  title: text('title').notNull(),
  slug: text('slug').notNull(),
  slugAs: text('slug_as'),                                            // Assamese slug
  chapterNumber: integer('chapter_number'),
  status: text('status').default('draft'),                            // draft | published
  contentType: text('content_type').default('standard'),

  // Notes content
  notesEn: text('notes_en'),
  notesAs: text('notes_as'),

  // RAG text (plain text for embedding, populated by staff)
  ragText: text('rag_text'),
  ragTextAs: text('rag_text_as'),
  ragUpdatedAt: integer('rag_updated_at'),
  ragIndexedAt: integer('rag_indexed_at'),

  // RAG sections (structured chunks)
  ragSectionsEn: text('rag_sections_en').default('[]'),               // JSON RagSection[]
  ragSectionsAs: text('rag_sections_as').default('[]'),

  // Published topics (public curriculum structure)
  publishedTopics: text('published_topics').default('[]'),            // JSON Topic[]

  // Q&A
  qaEn: text('qa_en').default('[]'),                                  // JSON QA[]
  qaAs: text('qa_as').default('[]'),

  // Word counts
  wordCountEn: integer('word_count_en').default(0),
  wordCountAs: integer('word_count_as').default(0),

  // PYQ — added migration 0004
  pyqPdfUrl:  text('pyq_pdf_url'),                                    // single PYQ PDF/image URL
  pyqPapers:  text('pyq_papers').default('[]'),                       // JSON PYQPageImage[]

  createdAt: integer('created_at').default(sql`(unixepoch())`),
  updatedAt: integer('updated_at').default(sql`(unixepoch())`),
}, (t) => [
  index('chapters_subject_idx').on(t.subjectId),
  uniqueIndex('chapters_subject_slug_idx').on(t.subjectId, t.slug),
  index('chapters_status_idx').on(t.status),
]);

// ─────────────────────────────────────────────────────────────────────────────
// AUTH TOKENS
// ─────────────────────────────────────────────────────────────────────────────

export const passwordResetTokens = sqliteTable('password_reset_tokens', {
  id: text('id').primaryKey(),                                        // UUID
  userId: text('user_id').notNull().references(() => users.id),
  tokenHash: text('token_hash').notNull(),                            // SHA-256 of token
  cutoverNonce: text('cutover_nonce'),                                // post-deploy reset proof binding
  expiresAt: integer('expires_at').notNull(),                         // unix epoch
  usedAt: integer('used_at'),
  createdAt: integer('created_at').default(sql`(unixepoch())`),
}, (t) => [
  index('prt_user_idx').on(t.userId),
  index('prt_expires_idx').on(t.expiresAt),
]);

// ─────────────────────────────────────────────────────────────────────────────
// CHAT
// ─────────────────────────────────────────────────────────────────────────────

export const chats = sqliteTable('chats', {
  id: text('id').primaryKey(),
  userId: text('user_id').notNull(),
  sessionId: text('session_id').notNull(),
  role: text('role').notNull(),                                       // user | assistant
  content: text('content').notNull(),
  lang: text('lang').default('en'),
  subjectId: text('subject_id'),
  chapterId: text('chapter_id'),
  metadata: text('metadata').default('{}'),                           // JSON
  expiresAt: integer('expires_at'),                                   // unix epoch — cleaned by cron
  createdAt: integer('created_at').default(sql`(unixepoch())`),
}, (t) => [
  index('chats_user_idx').on(t.userId),
  index('chats_session_idx').on(t.sessionId),
  index('chats_expires_idx').on(t.expiresAt),
]);

export const chatFeedback = sqliteTable('chat_feedback', {
  id: text('id').primaryKey(),
  chatId: text('chat_id').notNull(),
  userId: text('user_id').notNull(),
  rating: integer('rating'),                                          // 1-5
  comment: text('comment'),
  expiresAt: integer('expires_at'),
  createdAt: integer('created_at').default(sql`(unixepoch())`),
}, (t) => [
  index('cf_user_idx').on(t.userId),
  index('cf_expires_idx').on(t.expiresAt),
]);

// ─────────────────────────────────────────────────────────────────────────────
// QUOTA USAGE
// ─────────────────────────────────────────────────────────────────────────────

export const quotaUsage = sqliteTable('quota_usage', {
  id: text('id').primaryKey(),
  userId: text('user_id').notNull().references(() => users.id),
  period: text('period').notNull(),                                   // YYYY-MM
  count: integer('count').default(0),
  updatedAt: integer('updated_at').default(sql`(unixepoch())`),
}, (t) => [
  uniqueIndex('quota_user_period_idx').on(t.userId, t.period),
]);

// ─────────────────────────────────────────────────────────────────────────────
// PAYMENTS
// ─────────────────────────────────────────────────────────────────────────────

export const payments = sqliteTable('payments', {
  id: text('id').primaryKey(),
  userId: text('user_id').notNull().references(() => users.id),
  razorpayPaymentId: text('razorpay_payment_id'),
  razorpayOrderId: text('razorpay_order_id'),
  razorpaySubscriptionId: text('razorpay_subscription_id'),
  amount: integer('amount'),                                          // paise
  currency: text('currency').default('INR'),
  status: text('status').notNull(),                                   // captured | failed | refunded
  plan: text('plan'),
  metadata: text('metadata').default('{}'),                           // JSON
  createdAt: integer('created_at').default(sql`(unixepoch())`),
}, (t) => [
  index('payments_user_idx').on(t.userId),
  index('payments_rzp_payment_idx').on(t.razorpayPaymentId),
]);

export const transactions = sqliteTable('transactions', {
  id: text('id').primaryKey(),
  userId: text('user_id').notNull().references(() => users.id),
  type: text('type').notNull(),                                       // credit_topup | subscription | refund
  amount: integer('amount').notNull(),                                // credits
  metadata: text('metadata').default('{}'),
  createdAt: integer('created_at').default(sql`(unixepoch())`),
}, (t) => [
  index('transactions_user_idx').on(t.userId),
]);

export const refundRequests = sqliteTable('refund_requests', {
  id: text('id').primaryKey(),
  userId: text('user_id').notNull().references(() => users.id),
  paymentId: text('payment_id'),
  reason: text('reason'),
  status: text('status').default('pending'),                          // pending | approved | rejected
  createdAt: integer('created_at').default(sql`(unixepoch())`),
  updatedAt: integer('updated_at').default(sql`(unixepoch())`),
}, (t) => [
  index('refund_user_idx').on(t.userId),
]);

// Pending payment fallback (replaces MongoDB payments_pending with TTL)
export const paymentsPending = sqliteTable('payments_pending', {
  id: text('id').primaryKey(),
  orderId: text('order_id').notNull(),
  userId: text('user_id').notNull(),
  metadata: text('metadata').default('{}'),
  expiresAt: integer('expires_at').notNull(),
  createdAt: integer('created_at').default(sql`(unixepoch())`),
}, (t) => [
  uniqueIndex('pp_order_idx').on(t.orderId),
  index('pp_expires_idx').on(t.expiresAt),
]);

// Razorpay webhook event ledger. The event ID is the provider's retry-safe
// idempotency key, so a redelivery cannot apply an entitlement twice.
export const webhookEvents = sqliteTable('webhook_events', {
  eventId: text('event_id').primaryKey(),
  eventType: text('event_type').notNull(),
  receivedAt: integer('received_at').default(sql`(unixepoch())`),
  processedAt: integer('processed_at'),
});

// ─────────────────────────────────────────────────────────────────────────────
// RAG PIPELINE
// ─────────────────────────────────────────────────────────────────────────────

export const ragDocuments = sqliteTable('rag_documents', {
  id: text('id').primaryKey(),
  chapterId: text('chapter_id'),
  subjectId: text('subject_id'),
  sourceType: text('source_type').notNull(),                          // notes | qa | pyq
  medium: text('medium').notNull(),                                   // english | assamese
  content: text('content'),
  metadata: text('metadata').default('{}'),
  indexedAt: integer('indexed_at'),
  createdAt: integer('created_at').default(sql`(unixepoch())`),
}, (t) => [
  index('rag_docs_chapter_idx').on(t.chapterId),
  index('rag_docs_subject_idx').on(t.subjectId),
]);

export const chunks = sqliteTable('chunks', {
  id: text('id').primaryKey(),
  documentId: text('document_id'),           // no FK — legacy MongoDB UUID doc IDs (see migration 0001)
  chapterId: text('chapter_id'),
  subjectId: text('subject_id'),
  sourceType: text('source_type').notNull(),
  medium: text('medium').notNull(),
  chunkType: text('chunk_type'),
  content: text('content').notNull(),
  vectorId: text('vector_id'),                                        // Vectorize vector ID
  metadata: text('metadata').default('{}'),
  createdAt: integer('created_at').default(sql`(unixepoch())`),
}, (t) => [
  index('chunks_chapter_idx').on(t.chapterId),
  index('chunks_subject_idx').on(t.subjectId),
  index('chunks_vector_idx').on(t.vectorId),
]);

// ─────────────────────────────────────────────────────────────────────────────
// JOBS & PIPELINE
// ─────────────────────────────────────────────────────────────────────────────

export const publishJobs = sqliteTable('publish_jobs', {
  id: text('id').primaryKey(),
  chapterId: text('chapter_id').notNull(),
  status: text('status').default('pending'),                          // pending | running | done | failed | partial
  progress: text('progress').default('{}'),                           // JSON step progress
  errorLog: text('error_log'),
  createdAt: integer('created_at').default(sql`(unixepoch())`),
  updatedAt: integer('updated_at').default(sql`(unixepoch())`),
  completedAt: integer('completed_at'),
  leaseToken: text('lease_token'),
  leaseExpiresAt: integer('lease_expires_at'),
}, (t) => [
  index('pj_chapter_idx').on(t.chapterId),
  index('pj_status_idx').on(t.status),
]);

export const seedRuns = sqliteTable('seed_runs', {
  id: text('id').primaryKey(),
  medium: text('medium').notNull(),                                   // en | as
  status: text('status').default('running'),
  isForced: integer('is_forced').default(0),                           // regenerate populated notes when explicitly requested
  totalChapters: integer('total_chapters').default(0),
  processed: integer('processed').default(0),
  failed: integer('failed').default(0),
  log: text('log').default('[]'),                                     // JSON log entries
  startedAt: integer('started_at').default(sql`(unixepoch())`),
  leaseToken: text('lease_token'),
  leaseExpiresAt: integer('lease_expires_at'),
  completedAt: integer('completed_at'),
  expiresAt: integer('expires_at'),                                   // 90d TTL
}, (t) => [
  index('sr_expires_idx').on(t.expiresAt),
]);

// ─────────────────────────────────────────────────────────────────────────────
// ANALYTICS & AUDIT
// ─────────────────────────────────────────────────────────────────────────────

export const aiUsageLogs = sqliteTable('ai_usage_logs', {
  id: text('id').primaryKey(),
  userId: text('user_id'),
  provider: text('provider'),                                         // gemini | sarvam | workers-ai
  model: text('model'),
  inputTokens: integer('input_tokens').default(0),
  outputTokens: integer('output_tokens').default(0),
  latencyMs: integer('latency_ms'),
  requestId: text('request_id'),
  expiresAt: integer('expires_at'),                                   // 90d TTL
  createdAt: integer('created_at').default(sql`(unixepoch())`),
}, (t) => [
  index('ail_user_idx').on(t.userId),
  index('ail_expires_idx').on(t.expiresAt),
  index('ail_created_idx').on(t.createdAt),
]);

export const contentAuditLog = sqliteTable('content_audit_log', {
  id: text('id').primaryKey(),
  userId: text('user_id'),
  action: text('action').notNull(),
  targetType: text('target_type'),                                    // chapter | subject | pyq_paper
  targetId: text('target_id'),
  diff: text('diff'),                                                 // JSON diff
  expiresAt: integer('expires_at'),                                   // 180d TTL
  createdAt: integer('created_at').default(sql`(unixepoch())`),
}, (t) => [
  index('cal_target_idx').on(t.targetType, t.targetId),
  index('cal_expires_idx').on(t.expiresAt),
]);

// ─────────────────────────────────────────────────────────────────────────────
// OPERATIONAL
// ─────────────────────────────────────────────────────────────────────────────

// Email failure tracking (replaces MongoDB email_failure_events)
export const emailFailureEvents = sqliteTable('email_failure_events', {
  id: text('id').primaryKey(),
  recipient: text('recipient'),
  errorCode: text('error_code'),
  detail: text('detail'),
  expiresAt: integer('expires_at').notNull(),                         // 1 hour TTL
  createdAt: integer('created_at').default(sql`(unixepoch())`),
}, (t) => [
  index('efe_expires_idx').on(t.expiresAt),
]);

// Alert state singleton
export const emailAlertState = sqliteTable('email_alert_state', {
  id: text('id').primaryKey().default('singleton'),
  alertActive: integer('alert_active').default(0),
  lastAlertAt: integer('last_alert_at'),
  updatedAt: integer('updated_at').default(sql`(unixepoch())`),
});

// Dead letter queue (failed async jobs)
export const deadLetters = sqliteTable('dead_letters', {
  id: text('id').primaryKey(),
  jobType: text('job_type').notNull(),
  payload: text('payload').default('{}'),
  error: text('error'),
  attempts: integer('attempts').default(1),
  expiresAt: integer('expires_at').notNull(),                         // 30d TTL
  createdAt: integer('created_at').default(sql`(unixepoch())`),
}, (t) => [
  index('dl_expires_idx').on(t.expiresAt),
]);

// Admin config key-value store
export const adminConfig = sqliteTable('admin_config', {
  key: text('key').primaryKey(),
  value: text('value'),
  updatedAt: integer('updated_at').default(sql`(unixepoch())`),
});

// Memory brain (per-user AI memory)
export const memoryBrain = sqliteTable('memory_brain', {
  id: text('id').primaryKey(),
  userId: text('user_id').notNull(),
  key: text('key').notNull(),
  value: text('value'),
  updatedAt: integer('updated_at').default(sql`(unixepoch())`),
}, (t) => [
  uniqueIndex('mb_user_key_idx').on(t.userId, t.key),
]);

// Migrations bookkeeping (replaces schema_versions)
export const schemaMigrations = sqliteTable('schema_migrations', {
  version: text('version').primaryKey(),
  appliedAt: integer('applied_at').default(sql`(unixepoch())`),
});
