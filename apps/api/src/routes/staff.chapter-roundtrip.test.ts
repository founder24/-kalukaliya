/**
 * Integration test: staff chapter edit round-trip through D1
 *
 * Strategy
 * ────────
 * • Uses wrangler's `getPlatformProxy()` to get a real local D1 (SQLite via
 *   Miniflare) and the other required bindings, without starting a network server.
 * • Applies all Drizzle migrations from disk before any test runs.
 * • Calls the worker's fetch handler directly (app.fetch / ExportedHandler.fetch)
 *   so the full Hono route stack is exercised.
 * • Generates a short-lived HS256 JWT signed with the test JWT_SECRET so the
 *   staff auth guard approves the requests.
 * • A single Miniflare proxy is shared across all suites so migrations run
 *   exactly once — re-applying them to the same SQLite file would fail on
 *   intermediate ALTER-TABLE steps that the first run already applied.
 *
 * What is verified
 * ────────────────
 * EN suite
 *   1. POST /staff/content/chapters — creates a chapter (201)
 *   2. PATCH /staff/content/chapter/:id — writes notes_en, rag_sections_en, qa_en
 *   3. GET  /staff/content/chapter/:id — asserts those values are persisted
 *   4. PATCH with empty-string / empty-array — asserts content fields NOT cleared
 *   5-8. PYQ upload / delete / detail endpoints
 *
 * AS suite (parallel describe)
 *   1. POST — creates a separate chapter
 *   2. PATCH — writes notes_as, rag_sections_as, qa_as
 *   3. GET — asserts those Assamese values are persisted
 *   4. PATCH with empty-string / empty-array — asserts NOT cleared
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { getPlatformProxy } from 'wrangler';
import { SignJWT } from 'jose';
import type { Env } from '../types';

// ── Path helpers ───────────────────────────────────────────────────────────────

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const API_ROOT   = path.resolve(__dirname, '../../');

// ── Constants ──────────────────────────────────────────────────────────────────

const TEST_JWT_SECRET = 'test-jwt-secret-for-unit-tests';

// ── Helpers ────────────────────────────────────────────────────────────────────

/** Mint a short-lived HS256 access token with role=staff. */
async function staffToken(): Promise<string> {
  return new SignJWT({ role: 'staff', type: 'access' })
    .setProtectedHeader({ alg: 'HS256' })
    .setSubject('test-staff-user')
    .setIssuedAt()
    .setExpirationTime('1h')
    .sign(new TextEncoder().encode(TEST_JWT_SECRET));
}

function authHeaders(token: string): Record<string, string> {
  return {
    Authorization: `Bearer ${token}`,
    'Content-Type': 'application/json',
  };
}

/**
 * Read all migration SQL files in order and return individual statements.
 *
 * Splits on `;` then strips leading `--` comment lines from each fragment so
 * that blocks like:
 *   -- Syrabit D1 Initial Schema\n...\nCREATE TABLE users (...)
 * are not accidentally dropped because the fragment *starts* with a comment.
 */
function loadMigrations(): string[] {
  const dir = path.join(API_ROOT, 'drizzle/migrations');
  const files = fs
    .readdirSync(dir)
    .filter(f => f.endsWith('.sql'))
    .sort((a, b) => {
      const na = parseInt(a.split('_')[0] ?? '0', 10);
      const nb = parseInt(b.split('_')[0] ?? '0', 10);
      return na - nb;
    });

  const statements: string[] = [];
  for (const file of files) {
    const sql = fs.readFileSync(path.join(dir, file), 'utf8');
    for (const fragment of sql.split(';')) {
      // Strip leading comment lines and blank lines from the fragment, then
      // check if anything executable remains.
      const executable = fragment
        .split('\n')
        .filter(line => {
          const t = line.trim();
          return t.length > 0 && !t.startsWith('--');
        })
        .join('\n')
        .trim();

      if (executable) {
        statements.push(executable);
      }
    }
  }
  return statements;
}

// ── Module-level shared fixture ────────────────────────────────────────────────
//
// Both describe blocks share a single getPlatformProxy() / Miniflare instance.
// `persist: false` gives a fresh in-memory SQLite on every test run so
// migrations always execute against a blank schema and intermediate ALTER TABLE /
// table-rename steps never encounter a stale pre-existing state.

let sharedWorkerFetch: (req: Request) => Promise<Response>;
let sharedToken: string;
let sharedEnv: Env;
let sharedDisposeProxy: () => Promise<void>;

beforeAll(async () => {
  const proxy = await getPlatformProxy<Env>({
    configPath: path.join(API_ROOT, 'wrangler.toml'),
    // Wrangler enables remote binding proxies by default. Keep this integration
    // test entirely local so it can run on untrusted fork PRs without any
    // Cloudflare credentials or remote API requests.
    remoteBindings: false,
    // Do not write SQLite to disk — each test run starts with a clean schema.
    persist: false,
  });

  sharedDisposeProxy = proxy.dispose;

  // Build the env the worker expects, overriding JWT_SECRET with the test
  // value so guard() can verify tokens signed by staffToken() below.
  sharedEnv = {
    ...proxy.env,
    JWT_SECRET:              TEST_JWT_SECRET,
    ADMIN_JWT_SECRET:        'test-admin-jwt-secret',
    RESET_TOKEN_SECRET:      'test-reset-secret',
    EDGE_SHARED_SECRET:      'test-edge-secret',
    RAZORPAY_KEY_ID:         'test-rzp-key',
    RAZORPAY_KEY_SECRET:     'test-rzp-secret',
    RAZORPAY_WEBHOOK_SECRET: 'test-rzp-webhook-secret',
    RESEND_API_KEY:          'test-resend-key',
    ALLOWED_ORIGINS:         '*',
    APP_ENV:                 'test',
    // The local R2 binding supplied by getPlatformProxy is real enough to
    // verify put/get round-trips.  A deterministic base URL lets these tests
    // also verify the exact public URL returned by the handler.
    R2_PUBLIC_URL:           'https://assets.syrabit.ai',
  };

  // Apply schema migrations so the tables exist.
  // Run each statement individually (not in a batch) because D1 validates
  // every statement in a batch before execution — FK references to tables
  // that will be created later in the same batch would fail at prep time.
  const statements = loadMigrations();
  for (const sql of statements) {
    await sharedEnv.DB.prepare(sql).run();
  }

  // Lazy-import the Hono worker entry point (avoids loading CF types at module
  // level before the proxy provides the runtime environment).
  const { default: worker } = await import('../index.js');

  // Minimal execution context — waitUntil is fire-and-forget in tests.
  const ctx = {
    waitUntil: (_p: Promise<unknown>) => { /* no-op */ },
    passThroughOnException: () => { /* no-op */ },
  } as unknown as ExecutionContext;

  // Bind env so each step only needs to pass a Request.
  sharedWorkerFetch = (req: Request) =>
    (worker.fetch as (r: Request, e: Env, c: ExecutionContext) => Promise<Response>)(req, sharedEnv, ctx);

  sharedToken = await staffToken();
});

afterAll(async () => {
  await sharedDisposeProxy?.();
});

// ── EN Suite ───────────────────────────────────────────────────────────────────

describe('Staff chapter edit round-trip through D1', () => {
  // Shared across all test steps — set in beforeAll.
  let workerFetch: (req: Request) => Promise<Response>;
  let token: string;
  let subjectId: string;
  let chapterId: string;

  // ── Setup ──────────────────────────────────────────────────────────────────

  beforeAll(async () => {
    workerFetch = sharedWorkerFetch;
    token       = sharedToken;

    // Seed a minimal hierarchy so the subject FK resolves.
    const sid = crypto.randomUUID();
    await sharedEnv.DB.batch([
      sharedEnv.DB.prepare(`INSERT OR IGNORE INTO boards (id, name, slug) VALUES ('b-test','Test Board','test-board')`),
      sharedEnv.DB.prepare(`INSERT OR IGNORE INTO classes (id, board_id, name, slug) VALUES ('c-test','b-test','Class 12','class-12')`),
      sharedEnv.DB.prepare(`INSERT OR IGNORE INTO streams (id, class_id, name, slug) VALUES ('s-test','c-test','Science','science')`),
      sharedEnv.DB.prepare(`INSERT OR IGNORE INTO subjects (id, stream_id, name, slug, is_published) VALUES (?,  's-test','Physics','physics',1)`).bind(sid),
    ]);
    subjectId = sid;
  });

  // ── Step 1: Create chapter ──────────────────────────────────────────────────

  it('Step 1 — POST creates a chapter and returns 201', async () => {
    const res = await workerFetch(new Request('http://worker/api/v1/staff/content/chapters', {
      method: 'POST',
      headers: authHeaders(token),
      body: JSON.stringify({
        title:      'Round-trip Test Chapter',
        subject_id: subjectId,
        status:     'draft',
      }),
    }));

    expect(res.status).toBe(201);
    const body = await res.json() as { id: string };
    expect(typeof body.id).toBe('string');
    chapterId = body.id;
  });

  // ── Step 2: PATCH with real content ────────────────────────────────────────

  it('Step 2 — PATCH with non-empty notes_en and rag_sections_en succeeds', async () => {
    const res = await workerFetch(new Request(`http://worker/api/v1/staff/content/chapter/${chapterId}`, {
      method: 'PATCH',
      headers: authHeaders(token),
      body: JSON.stringify({
        notes_en: 'These are the English notes for the round-trip test chapter.',
        rag_sections_en: [
          { title: 'Introduction', content: 'An introduction to the topic.' },
          { title: 'Details',      content: 'Deeper detail about the subject matter.' },
        ],
        qa_en: [
          { question: 'What is this chapter about?', answer: 'The round-trip test.' },
        ],
      }),
    }));

    expect(res.status).toBe(200);
    const body = await res.json() as { ok: boolean };
    expect(body.ok).toBe(true);
  });

  // ── Step 3: GET asserts values preserved ───────────────────────────────────

  it('Step 3 — GET returns the content fields that were just PATCHed', async () => {
    const res = await workerFetch(new Request(`http://worker/api/v1/staff/content/chapter/${chapterId}`, {
      headers: authHeaders(token),
    }));

    expect(res.status).toBe(200);
    const body = await res.json() as {
      notes_en: string;
      rag_sections_en: Array<{ title: string; content: string }>;
      qa_en: Array<{ question: string; answer: string }>;
    };

    // notes_en must reflect the written value, not '' or null.
    expect(body.notes_en).toBe(
      'These are the English notes for the round-trip test chapter.',
    );

    // rag_sections_en must be the array we sent, not [] or null.
    expect(Array.isArray(body.rag_sections_en)).toBe(true);
    expect(body.rag_sections_en).toHaveLength(2);
    expect(body.rag_sections_en[0]?.title).toBe('Introduction');
    expect(body.rag_sections_en[1]?.title).toBe('Details');

    // qa_en must be preserved.
    expect(Array.isArray(body.qa_en)).toBe(true);
    expect(body.qa_en).toHaveLength(1);
    expect(body.qa_en[0]?.question).toBe('What is this chapter about?');
  });

  // ── Step 4: PATCH with empty values must NOT clear content ─────────────────

  it('Step 4 — PATCH with empty-string / empty-array values does not clear content fields', async () => {
    // These are the values the GET serialiser returns when a field is null in
    // D1.  Sending them back in a round-trip PATCH must be a no-op for
    // non-clearable content fields.
    const patchRes = await workerFetch(new Request(`http://worker/api/v1/staff/content/chapter/${chapterId}`, {
      method: 'PATCH',
      headers: authHeaders(token),
      body: JSON.stringify({
        notes_en:        '',  // empty string — must be ignored
        rag_sections_en: [], // empty array  — must be ignored
        qa_en:           [], // empty array  — must be ignored
        // Include a harmless scalar change to confirm the PATCH actually fired.
        status: 'draft',
      }),
    }));

    expect(patchRes.status).toBe(200);
    const patchBody = await patchRes.json() as { ok: boolean };
    expect(patchBody.ok).toBe(true);

    // Re-fetch and assert all content fields are still intact.
    const getRes = await workerFetch(new Request(`http://worker/api/v1/staff/content/chapter/${chapterId}`, {
      headers: authHeaders(token),
    }));

    expect(getRes.status).toBe(200);
    const body = await getRes.json() as {
      notes_en: string;
      rag_sections_en: Array<{ title: string; content: string }>;
      qa_en: Array<{ question: string; answer: string }>;
    };

    // notes_en must NOT have been cleared to ''.
    expect(body.notes_en).toBe(
      'These are the English notes for the round-trip test chapter.',
    );

    // rag_sections_en must NOT have been cleared to [].
    expect(Array.isArray(body.rag_sections_en)).toBe(true);
    expect(body.rag_sections_en.length).toBeGreaterThan(0);
    expect(body.rag_sections_en[0]?.title).toBe('Introduction');

    // qa_en must NOT have been cleared to [].
    expect(Array.isArray(body.qa_en)).toBe(true);
    expect(body.qa_en.length).toBeGreaterThan(0);
    expect(body.qa_en[0]?.question).toBe('What is this chapter about?');
  });

  it('Step 5 — qa_rag_sections_en takes priority over qa_en when both are submitted', async () => {
    const canonicalContent = [
      { question: 'What does qa_en contain?', answer: 'The canonical English value.' },
    ];
    const dashboardContent = [
      { question: 'What does qa_rag_sections_en contain?', answer: 'The dashboard English value.' },
    ];

    const patchRes = await workerFetch(new Request(`http://worker/api/v1/staff/content/chapter/${chapterId}`, {
      method: 'PATCH',
      headers: authHeaders(token),
      body: JSON.stringify({
        qa_en: canonicalContent,
        qa_rag_sections_en: dashboardContent,
      }),
    }));

    expect(patchRes.status).toBe(200);

    const getRes = await workerFetch(new Request(`http://worker/api/v1/staff/content/chapter/${chapterId}`, {
      headers: authHeaders(token),
    }));

    expect(getRes.status).toBe(200);
    const body = await getRes.json() as {
      qa_en: Array<{ question: string; answer: string }>;
      qa_rag_sections_en: Array<{ question: string; answer: string }>;
    };

    expect(body.qa_en).toEqual(dashboardContent);
    expect(body.qa_rag_sections_en).toEqual(dashboardContent);
  });

  // ── Step 6: PYQ single-file upload ─────────────────────────────────────────

  it('Step 5 — POST upload-pyq stores a PDF in R2 and persists its public URL in D1', async () => {
    const pdf = new TextEncoder().encode('%PDF-1.4\nround-trip test PDF\n%%EOF\n');
    const form = new FormData();
    form.append('file', new File([pdf], 'test-question-paper.pdf', { type: 'application/pdf' }));

    const res = await workerFetch(new Request(`http://worker/api/v1/staff/content/chapter/${chapterId}/upload-pyq`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
      body: form,
    }));

    expect(res.status).toBe(200);
    const body = await res.json() as { ok: boolean; pyq_pdf_url: string; key: string };
    expect(body.ok).toBe(true);
    expect(body.key).toBe(`pyq/${chapterId}/test-question-paper.pdf`);
    expect(body.pyq_pdf_url).toBe(`https://assets.syrabit.ai/${body.key}`);

    const stored = await sharedEnv.R2_BUCKET.get(body.key);
    expect(stored).not.toBeNull();
    expect(stored?.httpMetadata?.contentType).toBe('application/pdf');
    expect(new Uint8Array(await stored!.arrayBuffer())).toEqual(pdf);

    const row = await sharedEnv.DB
      .prepare('SELECT pyq_pdf_url FROM chapters WHERE id = ?')
      .bind(chapterId)
      .first<{ pyq_pdf_url: string }>();
    expect(row?.pyq_pdf_url).toBe(body.pyq_pdf_url);
  });

  // ── Step 6: PYQ paper (page image) upload ──────────────────────────────────

  it('Step 6 — POST pyq-papers stores a JPEG in R2 and returns its paper entry', async () => {
    // Build a minimal 1×1 JPEG (smallest valid JPEG, ~107 bytes).
    const minimalJpeg = new Uint8Array([
      0xFF,0xD8,0xFF,0xE0,0x00,0x10,0x4A,0x46,0x49,0x46,0x00,0x01,0x01,0x00,0x00,0x01,
      0x00,0x01,0x00,0x00,0xFF,0xDB,0x00,0x43,0x00,0x08,0x06,0x06,0x07,0x06,0x05,0x08,
      0x07,0x07,0x07,0x09,0x09,0x08,0x0A,0x0C,0x14,0x0D,0x0C,0x0B,0x0B,0x0C,0x19,0x12,
      0x13,0x0F,0x14,0x1D,0x1A,0x1F,0x1E,0x1D,0x1A,0x1C,0x1C,0x20,0x24,0x2E,0x27,0x20,
      0x22,0x2C,0x23,0x1C,0x1C,0x28,0x37,0x29,0x2C,0x30,0x31,0x34,0x34,0x34,0x1F,0x27,
      0x39,0x3D,0x38,0x32,0x3C,0x2E,0x33,0x34,0x32,0xFF,0xC0,0x00,0x0B,0x08,0x00,0x01,
      0x00,0x01,0x01,0x01,0x11,0x00,0xFF,0xC4,0x00,0x1F,0x00,0x00,0x01,0x05,0x01,0x01,
      0x01,0x01,0x01,0x01,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x01,0x02,0x03,0x04,
      0x05,0x06,0x07,0x08,0x09,0x0A,0x0B,0xFF,0xDA,0x00,0x08,0x01,0x01,0x00,0x00,0x3F,
      0x00,0xFB,0xD0,0xFF,0xD9,
    ]);
    const form = new FormData();
    form.append('file', new File([minimalJpeg], 'page.jpg', { type: 'image/jpeg' }));
    form.append('title', '2024 Question Paper');
    form.append('year', '2024');

    const res = await workerFetch(new Request(`http://worker/api/v1/staff/content/chapter/${chapterId}/pyq-papers`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
      body: form,
    }));

    expect(res.status).toBe(201);
    const body = await res.json() as {
      ok: boolean;
      paper: { id: string; title?: string; year?: number; url: string; uploaded_at: string };
      pyq_papers: Array<{ id: string; url: string }>;
    };
    expect(body.ok).toBe(true);
    expect(body.paper.id).toEqual(expect.any(String));
    expect(body.paper.title).toBe('2024 Question Paper');
    expect(body.paper.year).toBe(2024);
    expect(body.paper.uploaded_at).toEqual(expect.any(String));
    expect(body.paper.url).toBe(`https://assets.syrabit.ai/pyq/${chapterId}/papers/${body.paper.id}.jpg`);
    expect(body.pyq_papers).toContainEqual(expect.objectContaining({
      id: body.paper.id,
      url: body.paper.url,
    }));

    const key = `pyq/${chapterId}/papers/${body.paper.id}.jpg`;
    const stored = await sharedEnv.R2_BUCKET.get(key);
    expect(stored).not.toBeNull();
    expect(stored?.httpMetadata?.contentType).toBe('image/jpeg');
    expect(new Uint8Array(await stored!.arrayBuffer())).toEqual(minimalJpeg);
  });

  it('Step 7 — POST pyq-papers rejects a non-image file with 400', async () => {
    const form = new FormData();
    form.append('file', new File(['not an image'], 'notes.txt', { type: 'text/plain' }));

    const res = await workerFetch(new Request(`http://worker/api/v1/staff/content/chapter/${chapterId}/pyq-papers`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
      body: form,
    }));

    expect(res.status).toBe(400);
    const body = await res.json() as { detail: string };
    expect(body.detail).toMatch(/image/i);
  });

  it('Step 8 — POST upload-pyq rejects a 26 MB file with 413', async () => {
    const form = new FormData();
    form.append('file', new File([new Uint8Array(26 * 1024 * 1024)], 'too-large.pdf', {
      type: 'application/pdf',
    }));

    const res = await workerFetch(new Request(`http://worker/api/v1/staff/content/chapter/${chapterId}/upload-pyq`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
      body: form,
    }));

    expect(res.status).toBe(413);
    const body = await res.json() as { detail: string };
    expect(body.detail).toMatch(/25 MB/i);
  });

  it('Step 9 — DELETE pyq-papers/:paperId clears chapter PYQ vectors before removing the page', async () => {
    const row = await sharedEnv.DB
      .prepare('SELECT pyq_papers FROM chapters WHERE id = ?')
      .bind(chapterId)
      .first<{ pyq_papers: string }>();
    const [paper] = JSON.parse(row?.pyq_papers ?? '[]') as Array<{ id: string }>;
    if (!paper) throw new Error('Expected the uploaded PYQ page to exist');

    const originalVectorize = sharedEnv.VECTORIZE;
    const deletedIds: string[][] = [];
    sharedEnv.VECTORIZE = {
      deleteByIds: async (ids: string[]) => { deletedIds.push(ids); },
    } as unknown as typeof sharedEnv.VECTORIZE;

    try {
      const res = await workerFetch(new Request(`http://worker/api/v1/staff/content/chapter/${chapterId}/pyq-papers/${paper.id}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      }));

      expect(res.status).toBe(200);
      const body = await res.json() as { ok: boolean; pyq_papers: Array<{ id: string }> };
      expect(body.ok).toBe(true);
      expect(body.pyq_papers).not.toContainEqual(expect.objectContaining({ id: paper.id }));
      expect(deletedIds).toHaveLength(1);
      expect(deletedIds[0]).toEqual(expect.arrayContaining([
        `${chapterId}_english_pyq_0`,
        `${chapterId}_assamese_pyq_499`,
      ]));
    } finally {
      sharedEnv.VECTORIZE = originalVectorize;
    }
  });

  it('Step 10 — DELETE pyq-papers/:paperId preserves the page when Vectorize cleanup fails', async () => {
    const paperId = 'purge-failure-page';
    const papers = [{
      id: paperId,
      url: 'https://assets.syrabit.ai/pyq/purge-failure-page.jpg',
      uploaded_at: new Date().toISOString(),
    }];
    await sharedEnv.DB
      .prepare('UPDATE chapters SET pyq_papers = ? WHERE id = ?')
      .bind(JSON.stringify(papers), chapterId)
      .run();

    const originalVectorize = sharedEnv.VECTORIZE;
    sharedEnv.VECTORIZE = {
      deleteByIds: async () => { throw new Error('Vectorize unavailable'); },
    } as unknown as typeof sharedEnv.VECTORIZE;

    try {
      const res = await workerFetch(new Request(`http://worker/api/v1/staff/content/chapter/${chapterId}/pyq-papers/${paperId}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      }));

      expect(res.status).toBe(502);
      const body = await res.json() as { detail: string };
      expect(body.detail).toMatch(/not deleted/i);

      const row = await sharedEnv.DB
        .prepare('SELECT pyq_papers FROM chapters WHERE id = ?')
        .bind(chapterId)
        .first<{ pyq_papers: string }>();
      expect(JSON.parse(row?.pyq_papers ?? '[]')).toContainEqual(expect.objectContaining({ id: paperId }));
    } finally {
      sharedEnv.VECTORIZE = originalVectorize;
    }
  });

  it('Step 11 — DELETE pyq-papers/:paperId returns ok:true even when id is unknown', async () => {
    // Deleting a non-existent paper should be idempotent (filter-out is a no-op).
    const res = await workerFetch(new Request(`http://worker/api/v1/staff/content/chapter/${chapterId}/pyq-papers/nonexistent-id`, {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${token}` },
    }));

    expect(res.status).toBe(200);
    const body = await res.json() as { ok: boolean; pyq_papers: unknown[] };
    expect(body.ok).toBe(true);
    expect(Array.isArray(body.pyq_papers)).toBe(true);
  });

  // ── Step 12: GET chapter detail exposes PYQ fields ─────────────────────────

  it('Step 12 — GET chapter detail exposes pyq_pdf_url and pyq_papers fields', async () => {
    const res = await workerFetch(new Request(`http://worker/api/v1/staff/content/chapter/${chapterId}`, {
      headers: { Authorization: `Bearer ${token}` },
    }));

    expect(res.status).toBe(200);
    const body = await res.json() as {
      pyq_pdf_url: string;
      pyq_papers: unknown[];
      has_pyq_pdf: boolean;
      has_pyq_papers: boolean;
      pyq_papers_count: number;
    };

    // Fields must be present — initial values are empty/false before any upload.
    expect(typeof body.pyq_pdf_url).toBe('string');
    expect(Array.isArray(body.pyq_papers)).toBe(true);
    expect(typeof body.has_pyq_pdf).toBe('boolean');
    expect(typeof body.has_pyq_papers).toBe('boolean');
    expect(typeof body.pyq_papers_count).toBe('number');
  });
});

describe('D1 conversation history round-trip', () => {
  const sessionId = 'history-roundtrip-session';

  beforeAll(async () => {
    await sharedEnv.DB.batch([
      sharedEnv.DB.prepare(`
        INSERT INTO chats (id, user_id, session_id, role, content, lang, created_at)
        VALUES ('history-roundtrip-user', 'test-staff-user', ?, 'user', 'Explain photosynthesis', 'en', 100)
      `).bind(sessionId),
      sharedEnv.DB.prepare(`
        INSERT INTO chats (id, user_id, session_id, role, content, lang, created_at)
        VALUES ('history-roundtrip-assistant', 'test-staff-user', ?, 'assistant', 'Plants make food from light.', 'en', 101)
      `).bind(sessionId),
    ]);
  });

  it('lists, updates, reads, and deletes only the owner’s saved conversation', async () => {
    const headers = { Authorization: `Bearer ${sharedToken}`, 'Content-Type': 'application/json' };

    const listed = await sharedWorkerFetch(new Request('http://worker/api/v1/conversations', { headers }));
    expect(listed.status).toBe(200);
    expect(listed.headers.get('X-Syrabit-Route')).toBe('worker-native');
    const listBody = await listed.json() as { conversations: Array<{ id: string; message_count: number }> };
    expect(listBody.conversations).toContainEqual(expect.objectContaining({ id: sessionId, message_count: 2 }));

    const updated = await sharedWorkerFetch(new Request(`http://worker/api/v1/conversations/${sessionId}`, {
      method: 'PATCH',
      headers,
      body: JSON.stringify({ title: 'Photosynthesis revision', starred: true, archived: false }),
    }));
    expect(updated.status).toBe(200);
    expect(await updated.json()).toMatchObject({
      id: sessionId, title: 'Photosynthesis revision', starred: true, message_count: 2,
    });

    const detail = await sharedWorkerFetch(new Request(
      `http://worker/api/v1/conversations/${sessionId}`, { headers },
    ));
    expect(detail.status).toBe(200);
    expect(await detail.json()).toMatchObject({
      id: sessionId,
      messages: [
        { role: 'user', content: 'Explain photosynthesis' },
        { role: 'assistant', content: 'Plants make food from light.' },
      ],
    });

    const removed = await sharedWorkerFetch(new Request(`http://worker/api/v1/conversations/${sessionId}`, {
      method: 'DELETE', headers,
    }));
    expect(removed.status).toBe(200);
    const missing = await sharedWorkerFetch(new Request(
      `http://worker/api/v1/conversations/${sessionId}`, { headers },
    ));
    expect(missing.status).toBe(404);
  });

  it('returns a custom migrated title instead of replacing it with message text', async () => {
    const migratedSession = 'history-migrated-title-session';
    await sharedEnv.DB.batch([
      sharedEnv.DB.prepare(`
        INSERT INTO chats (id, user_id, session_id, role, content, lang, created_at)
        VALUES ('history-migrated-title-message', 'test-staff-user', ?, 'user', 'Fallback prompt text', 'en', 200)
      `).bind(migratedSession),
      sharedEnv.DB.prepare(`
        INSERT INTO conversation_metadata (user_id, session_id, title, starred, archived, updated_at)
        VALUES ('test-staff-user', ?, 'Custom exam revision plan', 0, 0, 201)
      `).bind(migratedSession),
    ]);

    const res = await sharedWorkerFetch(new Request(
      `http://worker/api/v1/conversations/${migratedSession}`,
      { headers: { Authorization: `Bearer ${sharedToken}` } },
    ));
    expect(res.status).toBe(200);
    expect(await res.json()).toMatchObject({
      title: 'Custom exam revision plan',
      updated_at: '1970-01-01T00:03:21.000Z',
    });

    const listed = await sharedWorkerFetch(new Request(
      'http://worker/api/v1/conversations?limit=100',
      { headers: { Authorization: `Bearer ${sharedToken}` } },
    ));
    const body = await listed.json() as { conversations: Array<{ id: string; updated_at: string }> };
    expect(body.conversations).toContainEqual(expect.objectContaining({
      id: migratedSession,
      updated_at: '1970-01-01T00:03:21.000Z',
    }));
  });
});

// ── Assamese field round-trip ───────────────────────────────────────────────────
//
// Mirrors the EN suite above but exercises notes_as, rag_sections_as, and qa_as.
// Uses the same shared Miniflare proxy so migrations are not re-applied.
// Creates a separate chapter so the two suites are fully independent.

describe('Staff chapter edit round-trip — Assamese fields', () => {
  let workerFetch: (req: Request) => Promise<Response>;
  let token: string;
  let subjectId: string;
  let chapterId: string;

  // ── Setup ────────────────────────────────────────────────────────────────────

  beforeAll(async () => {
    workerFetch = sharedWorkerFetch;
    token       = sharedToken;

    // Seed a separate subject for the Assamese suite so chapter IDs don't clash.
    const sid = crypto.randomUUID();
    await sharedEnv.DB.batch([
      sharedEnv.DB.prepare(`INSERT OR IGNORE INTO boards (id, name, slug) VALUES ('b-as-test','AS Test Board','as-test-board')`),
      sharedEnv.DB.prepare(`INSERT OR IGNORE INTO classes (id, board_id, name, slug) VALUES ('c-as-test','b-as-test','Class 12','as-class-12')`),
      sharedEnv.DB.prepare(`INSERT OR IGNORE INTO streams (id, class_id, name, slug) VALUES ('s-as-test','c-as-test','Arts','as-arts')`),
      sharedEnv.DB.prepare(`INSERT OR IGNORE INTO subjects (id, stream_id, name, slug, is_published) VALUES (?, 's-as-test','Assamese','assamese',1)`).bind(sid),
    ]);
    subjectId = sid;
  });

  // ── AS-Step 1: Create chapter ─────────────────────────────────────────────────

  it('AS-Step 1 — POST creates a chapter and returns 201', async () => {
    const res = await workerFetch(new Request('http://worker/api/v1/staff/content/chapters', {
      method: 'POST',
      headers: authHeaders(token),
      body: JSON.stringify({
        title:      'Assamese Round-trip Test Chapter',
        subject_id: subjectId,
        status:     'draft',
      }),
    }));

    expect(res.status).toBe(201);
    const body = await res.json() as { id: string };
    expect(typeof body.id).toBe('string');
    chapterId = body.id;
  });

  // ── AS-Step 2: PATCH with real Assamese content ───────────────────────────────

  it('AS-Step 2 — PATCH with non-empty notes_as, rag_sections_as, qa_as succeeds', async () => {
    const res = await workerFetch(new Request(`http://worker/api/v1/staff/content/chapter/${chapterId}`, {
      method: 'PATCH',
      headers: authHeaders(token),
      body: JSON.stringify({
        notes_as: 'এইটো অসমীয়া ভাষাত লিখা টোকা।',
        rag_sections_as: [
          { title: 'পৰিচয়', content: 'বিষয়টোৰ এক পৰিচয়।' },
          { title: 'বিৱৰণ', content: 'বিষয়টোৰ গভীৰ বিৱৰণ।' },
        ],
        qa_as: [
          { question: 'এই অধ্যায়টো কিহৰ বিষয়ে?', answer: 'ৰাউণ্ড-ট্ৰিপ পৰীক্ষাৰ বিষয়ে।' },
        ],
      }),
    }));

    expect(res.status).toBe(200);
    const body = await res.json() as { ok: boolean };
    expect(body.ok).toBe(true);
  });

  // ── AS-Step 3: GET asserts values preserved ───────────────────────────────────

  it('AS-Step 3 — GET returns the Assamese content fields that were just PATCHed', async () => {
    const res = await workerFetch(new Request(`http://worker/api/v1/staff/content/chapter/${chapterId}`, {
      headers: authHeaders(token),
    }));

    expect(res.status).toBe(200);
    const body = await res.json() as {
      notes_as: string;
      rag_sections_as: Array<{ title: string; content: string }>;
      qa_as: Array<{ question: string; answer: string }>;
    };

    // notes_as must reflect the written value, not '' or null.
    expect(body.notes_as).toBe('এইটো অসমীয়া ভাষাত লিখা টোকা।');

    // rag_sections_as must be the array we sent, not [] or null.
    expect(Array.isArray(body.rag_sections_as)).toBe(true);
    expect(body.rag_sections_as).toHaveLength(2);
    expect(body.rag_sections_as[0]?.title).toBe('পৰিচয়');
    expect(body.rag_sections_as[0]?.content).toBe('বিষয়টোৰ এক পৰিচয়।');
    expect(body.rag_sections_as[1]?.title).toBe('বিৱৰণ');
    expect(body.rag_sections_as[1]?.content).toBe('বিষয়টোৰ গভীৰ বিৱৰণ।');

    // qa_as must be preserved including both question and answer.
    expect(Array.isArray(body.qa_as)).toBe(true);
    expect(body.qa_as).toHaveLength(1);
    expect(body.qa_as[0]?.question).toBe('এই অধ্যায়টো কিহৰ বিষয়ে?');
    expect(body.qa_as[0]?.answer).toBe('ৰাউণ্ড-ট্ৰিপ পৰীক্ষাৰ বিষয়ে।');
  });

  // ── AS-Step 4: PATCH with empty values must NOT clear Assamese content ─────────

  it('AS-Step 4 — PATCH with empty-string / empty-array values does not clear Assamese content fields', async () => {
    // Sending the serialised-null forms back in a round-trip PATCH must be a
    // no-op for non-clearable content fields — same guard as for EN fields.
    const patchRes = await workerFetch(new Request(`http://worker/api/v1/staff/content/chapter/${chapterId}`, {
      method: 'PATCH',
      headers: authHeaders(token),
      body: JSON.stringify({
        notes_as:        '',  // empty string — must be ignored
        rag_sections_as: [], // empty array  — must be ignored
        qa_as:           [], // empty array  — must be ignored
        // Include a harmless scalar change to confirm the PATCH actually fired.
        status: 'draft',
      }),
    }));

    expect(patchRes.status).toBe(200);
    const patchBody = await patchRes.json() as { ok: boolean };
    expect(patchBody.ok).toBe(true);

    // Re-fetch and assert all Assamese content fields are still intact.
    const getRes = await workerFetch(new Request(`http://worker/api/v1/staff/content/chapter/${chapterId}`, {
      headers: authHeaders(token),
    }));

    expect(getRes.status).toBe(200);
    const body = await getRes.json() as {
      notes_as: string;
      rag_sections_as: Array<{ title: string; content: string }>;
      qa_as: Array<{ question: string; answer: string }>;
    };

    // notes_as must NOT have been cleared to ''.
    expect(body.notes_as).toBe('এইটো অসমীয়া ভাষাত লিখা টোকা।');

    // rag_sections_as must NOT have been cleared to [].
    expect(Array.isArray(body.rag_sections_as)).toBe(true);
    expect(body.rag_sections_as.length).toBeGreaterThan(0);
    expect(body.rag_sections_as[0]?.title).toBe('পৰিচয়');
    expect(body.rag_sections_as[0]?.content).toBe('বিষয়টোৰ এক পৰিচয়।');

    // qa_as must NOT have been cleared to [].
    expect(Array.isArray(body.qa_as)).toBe(true);
    expect(body.qa_as.length).toBeGreaterThan(0);
    expect(body.qa_as[0]?.question).toBe('এই অধ্যায়টো কিহৰ বিষয়ে?');
    expect(body.qa_as[0]?.answer).toBe('ৰাউণ্ড-ট্ৰিপ পৰীক্ষাৰ বিষয়ে।');
  });

  // ── AS-Step 5: Dashboard Q&A alias must also survive empty round-trips ───────

  it('AS-Step 5 — PATCH using qa_rag_sections_as preserves content and ignores an empty array', async () => {
    const aliasContent = [
      { question: 'ড্যাশবোর্ডৰ পৰা এই অধ্যায়টো কিহৰ বিষয়ে?', answer: 'অসমীয়া Q&A এলিয়াছ পৰীক্ষা।' },
    ];

    const writeRes = await workerFetch(new Request(`http://worker/api/v1/staff/content/chapter/${chapterId}`, {
      method: 'PATCH',
      headers: authHeaders(token),
      body: JSON.stringify({ qa_rag_sections_as: aliasContent }),
    }));

    expect(writeRes.status).toBe(200);
    const writeBody = await writeRes.json() as { ok: boolean };
    expect(writeBody.ok).toBe(true);

    const afterWriteRes = await workerFetch(new Request(`http://worker/api/v1/staff/content/chapter/${chapterId}`, {
      headers: authHeaders(token),
    }));

    expect(afterWriteRes.status).toBe(200);
    const afterWriteBody = await afterWriteRes.json() as {
      qa_rag_sections_as: Array<{ question: string; answer: string }>;
      qa_as: Array<{ question: string; answer: string }>;
    };
    expect(afterWriteBody.qa_rag_sections_as).toEqual(aliasContent);
    expect(afterWriteBody.qa_as).toEqual(aliasContent);

    const emptyRes = await workerFetch(new Request(`http://worker/api/v1/staff/content/chapter/${chapterId}`, {
      method: 'PATCH',
      headers: authHeaders(token),
      body: JSON.stringify({ qa_rag_sections_as: [] }),
    }));

    expect(emptyRes.status).toBe(200);
    const emptyBody = await emptyRes.json() as { ok: boolean };
    expect(emptyBody.ok).toBe(true);

    const afterEmptyRes = await workerFetch(new Request(`http://worker/api/v1/staff/content/chapter/${chapterId}`, {
      headers: authHeaders(token),
    }));

    expect(afterEmptyRes.status).toBe(200);
    const afterEmptyBody = await afterEmptyRes.json() as {
      qa_rag_sections_as: Array<{ question: string; answer: string }>;
      qa_as: Array<{ question: string; answer: string }>;
    };
    expect(afterEmptyBody.qa_rag_sections_as).toEqual(aliasContent);
    expect(afterEmptyBody.qa_as).toEqual(aliasContent);
  });

  it('AS-Step 6 — qa_rag_sections_as takes priority over qa_as when both are submitted', async () => {
    const canonicalContent = [
      { question: 'qa_as-ৰ প্ৰশ্ন?', answer: 'qa_as-ৰ উত্তৰ।' },
    ];
    const dashboardContent = [
      { question: 'qa_rag_sections_as-ৰ প্ৰশ্ন?', answer: 'qa_rag_sections_as-ৰ উত্তৰ।' },
    ];

    const patchRes = await workerFetch(new Request(`http://worker/api/v1/staff/content/chapter/${chapterId}`, {
      method: 'PATCH',
      headers: authHeaders(token),
      body: JSON.stringify({
        qa_as: canonicalContent,
        qa_rag_sections_as: dashboardContent,
      }),
    }));

    expect(patchRes.status).toBe(200);

    const getRes = await workerFetch(new Request(`http://worker/api/v1/staff/content/chapter/${chapterId}`, {
      headers: authHeaders(token),
    }));

    expect(getRes.status).toBe(200);
    const body = await getRes.json() as {
      qa_as: Array<{ question: string; answer: string }>;
      qa_rag_sections_as: Array<{ question: string; answer: string }>;
    };

    expect(body.qa_as).toEqual(dashboardContent);
    expect(body.qa_rag_sections_as).toEqual(dashboardContent);
  });

  // ── AS-Step 7: Combined content + RAG PATCH must persist every field ─────────

  it('AS-Step 7 — PATCH persists notes_as, rag_sections_as, and qa_as together', async () => {
    // notes_as sets contentChanged while rag_sections_as sets ragChanged. Keep
    // all three fields in the same request to guard against a future early
    // return or field-specific update overwriting the other Assamese values.
    const combinedNotes = 'একেটা PATCH-ত সংৰক্ষণ কৰা অসমীয়া টোকা।';
    const combinedRag = [
      { title: 'মূল ধাৰণা', content: 'একেলগে সংৰক্ষণ কৰা বিষয়টোৰ মূল ধাৰণা।' },
      { title: 'উদাহৰণ', content: 'একেলগে সংৰক্ষণ কৰা এটা উদাহৰণ।' },
    ];
    const combinedQa = [
      { question: 'একেলগে সংৰক্ষণ কৰা প্ৰশ্নটো কি?', answer: 'এইটো এটা সংযুক্ত PATCH পৰীক্ষা।' },
    ];

    const patchRes = await workerFetch(new Request(`http://worker/api/v1/staff/content/chapter/${chapterId}`, {
      method: 'PATCH',
      headers: authHeaders(token),
      body: JSON.stringify({
        notes_as: combinedNotes,
        rag_sections_as: combinedRag,
        qa_as: combinedQa,
      }),
    }));

    expect(patchRes.status).toBe(200);
    const patchBody = await patchRes.json() as { ok: boolean };
    expect(patchBody.ok).toBe(true);

    const getRes = await workerFetch(new Request(`http://worker/api/v1/staff/content/chapter/${chapterId}`, {
      headers: authHeaders(token),
    }));

    expect(getRes.status).toBe(200);
    const body = await getRes.json() as {
      notes_as: string;
      rag_sections_as: Array<{ title: string; content: string }>;
      qa_as: Array<{ question: string; answer: string }>;
    };

    expect(body.notes_as).toBe(combinedNotes);
    expect(body.rag_sections_as).toEqual(combinedRag);
    expect(body.qa_as).toEqual(combinedQa);
  });

  it('AS-Step 8 — combined PATCH returns an error and preserves content when D1 update fails', async () => {
    const beforeFailureRes = await workerFetch(new Request(`http://worker/api/v1/staff/content/chapter/${chapterId}`, {
      headers: authHeaders(token),
    }));

    expect(beforeFailureRes.status).toBe(200);
    const beforeFailure = await beforeFailureRes.json() as {
      notes_as: string;
      rag_sections_as: Array<{ title: string; content: string }>;
      qa_as: Array<{ question: string; answer: string }>;
    };

    const failureTrigger = `staff_combined_patch_failure_${chapterId.replace(/-/g, '_')}`;
    const triggerSql = `
      CREATE TRIGGER ${failureTrigger}
      BEFORE UPDATE ON chapters
      WHEN NEW.id = '${chapterId}'
      BEGIN
        SELECT RAISE(ABORT, 'simulated D1 chapter update failure');
      END
    `;

    await sharedEnv.DB.prepare(triggerSql).run();

    try {
      const failedNotes = 'এই টোকা সংৰক্ষণ হোৱা উচিত নহয়।';
      const failedRag = [
        { title: 'ব্যৰ্থ ধাৰণা', content: 'এই RAG অংশ সংৰক্ষণ হোৱা উচিত নহয়।' },
      ];
      const failedQa = [
        { question: 'এই Q&A সংৰক্ষণ হ’বনে?', answer: 'নহয়।' },
      ];

      const patchRes = await workerFetch(new Request(`http://worker/api/v1/staff/content/chapter/${chapterId}`, {
        method: 'PATCH',
        headers: authHeaders(token),
        body: JSON.stringify({
          notes_as: failedNotes,
          rag_sections_as: failedRag,
          qa_as: failedQa,
        }),
      }));

      expect(patchRes.status).toBe(500);
      const failureBody = await patchRes.json() as {
        ok?: boolean;
        notes_as?: string;
        rag_sections_as?: unknown[];
        qa_as?: unknown[];
      };
      expect(failureBody.ok).not.toBe(true);
      expect(failureBody.notes_as).toBeUndefined();
      expect(failureBody.rag_sections_as).toBeUndefined();
      expect(failureBody.qa_as).toBeUndefined();

      const getRes = await workerFetch(new Request(`http://worker/api/v1/staff/content/chapter/${chapterId}`, {
        headers: authHeaders(token),
      }));

      expect(getRes.status).toBe(200);
      const body = await getRes.json() as {
        notes_as: string;
        rag_sections_as: Array<{ title: string; content: string }>;
        qa_as: Array<{ question: string; answer: string }>;
      };

      expect(body.notes_as).toBe(beforeFailure.notes_as);
      expect(body.rag_sections_as).toEqual(beforeFailure.rag_sections_as);
      expect(body.qa_as).toEqual(beforeFailure.qa_as);
    } finally {
      await sharedEnv.DB.prepare(`DROP TRIGGER ${failureTrigger}`).run();
    }
  });
});

// ── Subject-level PYQ deletion ──────────────────────────────────────────────────
//
// Subject PYQ papers are indexed with the paper ID as the chapter namespace.
// Keep these checks next to the chapter round-trip fixture so they exercise the
// same Worker/D1 route stack and Vectorize binding replacement.

describe('Staff subject-level PYQ deletion', () => {
  let workerFetch: (req: Request) => Promise<Response>;
  let token: string;
  let subjectId: string;

  beforeAll(async () => {
    workerFetch = sharedWorkerFetch;
    token       = sharedToken;
    subjectId   = crypto.randomUUID();

    await sharedEnv.DB
      .prepare('INSERT INTO subjects (id, name, slug, is_published) VALUES (?, ?, ?, 1)')
      .bind(subjectId, 'Subject PYQ deletion test', `subject-pyq-delete-${subjectId}`)
      .run();
  });

  it('purges only the deleted paper namespace and its D1 chunk mappings before removing metadata', async () => {
    const paperId = 'subject-paper-to-delete';
    const retainedPaperId = 'subject-paper-to-retain';
    await sharedEnv.DB
      .prepare('UPDATE subjects SET pyq_papers = ? WHERE id = ?')
      .bind(JSON.stringify([
        { id: paperId, name: 'Paper to delete' },
        { id: retainedPaperId, name: 'Paper to retain' },
      ]), subjectId)
      .run();

    await sharedEnv.DB.batch([
      sharedEnv.DB.prepare(`
        INSERT INTO chunks
          (id, chapter_id, subject_id, source_type, medium, chunk_type, content, vector_id)
        VALUES (?, ?, ?, 'pyq', 'english', 'text', 'deleted paper chunk', ?)
      `).bind('subject-pyq-delete-chunk', paperId, subjectId, `${paperId}_english_pyq_0`),
      sharedEnv.DB.prepare(`
        INSERT INTO chunks
          (id, chapter_id, subject_id, source_type, medium, chunk_type, content, vector_id)
        VALUES (?, ?, ?, 'pyq', 'english', 'text', 'retained paper chunk', ?)
      `).bind('subject-pyq-retain-chunk', retainedPaperId, subjectId, `${retainedPaperId}_english_pyq_0`),
    ]);

    const originalVectorize = sharedEnv.VECTORIZE;
    const deletedIds: string[][] = [];
    sharedEnv.VECTORIZE = {
      deleteByIds: async (ids: string[]) => { deletedIds.push(ids); },
    } as unknown as typeof sharedEnv.VECTORIZE;

    try {
      const res = await workerFetch(new Request(
        `http://worker/api/v1/staff/content/subject/${subjectId}/pyq-papers/${paperId}`,
        { method: 'DELETE', headers: { Authorization: `Bearer ${token}` } },
      ));

      expect(res.status).toBe(200);
      const body = await res.json() as {
        ok: boolean;
        pyq_papers: Array<{ id: string }>;
      };
      expect(body.ok).toBe(true);
      expect(body.pyq_papers).not.toContainEqual(expect.objectContaining({ id: paperId }));
      expect(body.pyq_papers).toContainEqual(expect.objectContaining({ id: retainedPaperId }));

      expect(deletedIds).toHaveLength(1);
      expect(deletedIds[0]).toEqual(expect.arrayContaining([
        `${paperId}_english_pyq_0`,
        `${paperId}_assamese_pyq_499`,
      ]));
      expect(deletedIds[0]).not.toContain(`${retainedPaperId}_english_pyq_0`);

      const mappings = await sharedEnv.DB
        .prepare('SELECT chapter_id, vector_id FROM chunks WHERE source_type = ? ORDER BY chapter_id')
        .bind('pyq')
        .all<{ chapter_id: string; vector_id: string }>();
      expect(mappings.results).toEqual([
        { chapter_id: retainedPaperId, vector_id: `${retainedPaperId}_english_pyq_0` },
      ]);

      const row = await sharedEnv.DB
        .prepare('SELECT pyq_papers FROM subjects WHERE id = ?')
        .bind(subjectId)
        .first<{ pyq_papers: string }>();
      expect(JSON.parse(row?.pyq_papers ?? '[]')).not.toContainEqual(
        expect.objectContaining({ id: paperId }),
      );
    } finally {
      sharedEnv.VECTORIZE = originalVectorize;
    }
  });

  it('keeps the paper metadata when Vectorize cleanup fails', async () => {
    const paperId = 'subject-paper-purge-failure';
    const papers = [{ id: paperId, name: 'Paper whose purge fails' }];
    await sharedEnv.DB
      .prepare('UPDATE subjects SET pyq_papers = ? WHERE id = ?')
      .bind(JSON.stringify(papers), subjectId)
      .run();

    const originalVectorize = sharedEnv.VECTORIZE;
    sharedEnv.VECTORIZE = {
      deleteByIds: async () => { throw new Error('Vectorize unavailable'); },
    } as unknown as typeof sharedEnv.VECTORIZE;

    try {
      const res = await workerFetch(new Request(
        `http://worker/api/v1/staff/content/subject/${subjectId}/pyq-papers/${paperId}`,
        { method: 'DELETE', headers: { Authorization: `Bearer ${token}` } },
      ));

      expect(res.status).toBe(502);
      const body = await res.json() as { detail: string };
      expect(body.detail).toMatch(/not deleted/i);

      const row = await sharedEnv.DB
        .prepare('SELECT pyq_papers FROM subjects WHERE id = ?')
        .bind(subjectId)
        .first<{ pyq_papers: string }>();
      expect(JSON.parse(row?.pyq_papers ?? '[]')).toContainEqual(
        expect.objectContaining({ id: paperId }),
      );
    } finally {
      sharedEnv.VECTORIZE = originalVectorize;
    }
  });
});
