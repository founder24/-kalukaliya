import { Hono } from 'hono';
import { healthRouter } from './health';
import { authRouter } from './auth';
import { chatRouter } from './chat';
import { contentRouter } from './content';
import { staffRouter } from './staff';
import { usersRouter } from './users';
import { subscriptionRouter } from './subscription';
import { paymentsRouter } from './payments';
import { webhookRouter } from './webhook';
import { internalRouter } from './internal';
import { conversationsRouter } from './conversations';
import { analyticsRouter, changelogRouter, configRouter, indexNowRouter } from './operations';
import { seoRouter } from './seo';
import { adminContentRouter } from './admin-content';
import { proxyToCloudRun } from './fallback';
import type { Env } from '../types';

const api = new Hono<{ Bindings: Env }>();

// Health — edge probes arrive as /health/* (no /api prefix)
api.route('/health', healthRouter);

// All API routes are mounted at their full /api/v1/… path so that:
//   1. The edge Worker can forward requests without any path rewriting.
//   2. Browser and scheduler callers keep their stable, unmodified paths.
//
// ── Phase 6 (this cutover): D1-backed via Cloudflare Workers ─────────────────
// These routes read/write D1 exclusively — MongoDB is no longer involved.
api.route('/api/v1/auth',         authRouter);        // login, signup, refresh, logout, me
api.route('/api/v1/chat',         chatRouter);        // streaming chat, history, feedback
api.route('/api/v1/content',      contentRouter);     // boards, classes, streams, subjects, chapters
api.route('/api/v1/staff',        staffRouter);       // staff CRUD: chapters, subjects, reindex, PYQ
// The legacy admin panel keeps its /admin/content URL family. Mount the
// native publish/seed operations first, then the shared D1 content editor.
api.route('/api/v1/admin',        adminContentRouter);
api.route('/api/v1/admin',        staffRouter);
// Deliberately scoped compatibility bridge: publishing, content editing, RAG,
// and scheduled seed routes above are Worker-native. Other established admin
// operations remain on Cloud Run until their independently-owned replacements
// are deployed, rather than failing with a Worker 404 during cutover.
api.all('/api/v1/admin/*',        proxyToCloudRun);
api.all('/api/v1/seed/*',         proxyToCloudRun);
api.route('/api/v1/users',        usersRouter);       // profile, memories, onboarding, credits, stats
api.route('/api/v1/user',         usersRouter);       // alias — frontend uses /user/profile, /user/me
api.route('/api/v1/subscription', subscriptionRouter); // plans, status, create-order, cancel
api.route('/api/v1/payments',     paymentsRouter);    // create-order, verify, credit-topup, history
api.route('/api/webhooks',        webhookRouter);     // Razorpay HMAC-verified webhook
api.route('/api/v1/internal',     internalRouter);    // internal Worker-to-Worker endpoints
api.route('/api/v1/conversations', conversationsRouter); // saved history, rename/star/archive
api.route('/api/v1/analytics', analyticsRouter);     // public browser beacons
api.route('/api/analytics', analyticsRouter);        // legacy beacon path
api.route('/api/v1/config', configRouter);           // public Trustpilot configuration
api.route('/api/config', configRouter);              // legacy public configuration path
api.route('/api/v1/indexnow', indexNowRouter);       // authenticated search-engine submission
api.route('/api/v1/changelog', changelogRouter);     // public API release history
api.route('/api/changelog', changelogRouter);        // legacy release-history path
api.route('/api/v1/seo', seoRouter);                 // sitemaps, feeds, robots, LLM index
api.route('/api/seo', seoRouter);                    // legacy public sitemap path

// Catch-all: 404 for any path not implemented or proxied above
api.all('*', (c) => c.json({ detail: 'Not found' }, 404));

export { api };
