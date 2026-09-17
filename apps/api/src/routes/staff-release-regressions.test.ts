/** Focused release regressions for durable RAG jobs and capability boundaries. */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { getPlatformProxy } from 'wrangler';
import { SignJWT } from 'jose';
import type { Env } from '../types';
import { resumeRagReindexJobs, runRagJob } from './staff';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../');
let env: Env; let dispose: () => Promise<void>; let fetchWorker: (r: Request) => Promise<Response>;
let fetchWorkerWithWaitUntil: (r: Request) => Promise<Response>;
const waitUntilPromises: Promise<unknown>[] = [];
const secret = 'staff-release-test-secret';
async function token(sub: string, role = 'staff') {
  return new SignJWT({ role, type: 'access' }).setProtectedHeader({ alg: 'HS256' }).setSubject(sub)
    .setIssuedAt().setExpirationTime('1h').sign(new TextEncoder().encode(secret));
}
async function adminSession(sub: string) {
  return new SignJWT({ role: 'admin', type: 'admin' }).setProtectedHeader({ alg: 'HS256' }).setSubject(sub)
    .setIssuedAt().setExpirationTime('8h').sign(new TextEncoder().encode('admin'));
}
function sql() {
  return fs.readdirSync(path.join(root, 'drizzle/migrations')).filter(file => file.endsWith('.sql')).sort()
    .flatMap(file => fs.readFileSync(path.join(root, 'drizzle/migrations', file), 'utf8').split(';'))
    .map(part => part.split('\n').filter(line => line.trim() && !line.trim().startsWith('--')).join('\n').trim()).filter(Boolean);
}
function request(pathname: string, jwt: string, method = 'GET', body?: unknown) {
  return new Request(`http://worker${pathname}`, { method, headers: { Authorization: `Bearer ${jwt}`, ...(body ? { 'Content-Type': 'application/json' } : {}) }, ...(body ? { body: JSON.stringify(body) } : {}) });
}
function cookieRequest(pathname: string, session: string) {
  return new Request(`http://worker${pathname}`, {
    headers: { Cookie: `syrabit_admin_session=${session}` },
  });
}
beforeAll(async () => {
  const proxy = await getPlatformProxy<Env>({ configPath: path.join(root, 'wrangler.toml'), remoteBindings: false, persist: false });
  dispose = proxy.dispose;
  env = { ...proxy.env, JWT_SECRET: secret, ADMIN_JWT_SECRET: 'admin', RESET_TOKEN_SECRET: 'reset', EDGE_SHARED_SECRET: 'edge', RESEND_API_KEY: 'x', ALLOWED_ORIGINS: '*', APP_ENV: 'test', REFERRAL_PROGRAM_RUNTIME_ENABLED: 'true',
    AI: { run: async (_m: string, input: { text?: string[] }) => ({ data: (input.text ?? []).map(() => ({ values: [1, 2] })) }) } as unknown as Ai };
  for (const statement of sql()) await env.DB.prepare(statement).run();
  await env.DB.batch([
    env.DB.prepare(`INSERT INTO users (id, role, capabilities) VALUES ('limited','staff','[]'),('editor','staff','["content:edit"]'),('reviewer','staff','["referral:review"]'),('settler','staff','["referral:settle"]'),('cookie-settler','admin','["referral:settle"]'),('legacy','staff',NULL),('admin','admin','[]')`),
    env.DB.prepare(`INSERT INTO boards (id,name,slug) VALUES ('b','B','b')`),
    env.DB.prepare(`INSERT INTO classes (id,board_id,name,slug) VALUES ('c','b','C','c')`),
    env.DB.prepare(`INSERT INTO streams (id,class_id,name,slug) VALUES ('s','c','S','s')`),
    env.DB.prepare(`INSERT INTO subjects (id,stream_id,name,slug) VALUES ('subject','s','Subject','subject')`),
    env.DB.prepare(`INSERT INTO chapters (id,subject_id,title,slug,notes_en,qa_en,rag_text) VALUES ('chapter','subject','Chapter','chapter','note text','[{"content":"legacy QA"}]','must not be pyq')`),
  ]);
  const { default: worker } = await import('../index.js');
  const workerFetch = worker.fetch as (r: Request, e: Env, c: ExecutionContext) => Promise<Response>;
  fetchWorker = request => workerFetch(request, env, {
    waitUntil: () => {},
    passThroughOnException: () => {},
  } as unknown as ExecutionContext);
  fetchWorkerWithWaitUntil = request => workerFetch(request, env, {
    waitUntil: (promise: Promise<unknown>) => waitUntilPromises.push(Promise.resolve(promise)),
    passThroughOnException: () => {},
  } as unknown as ExecutionContext);
});
afterAll(async () => { await dispose(); });

describe('release regressions', () => {
  it('exposes only staff-authorized Cloudflare Access bypass status', async () => {
    const originalEnv = env.APP_ENV;
    env.APP_ENV = 'production';
    try {
      const jwt = await token('legacy');
      const bypassed = await fetchWorker(request('/api/v1/admin/break-glass-status', jwt));
      expect(bypassed.status).toBe(200);
      expect(await bypassed.json()).toEqual({ active: true });

      const protectedRequest = request('/api/v1/admin/break-glass-status', jwt);
      protectedRequest.headers.set('Cf-Access-Jwt-Assertion', 'signed-access-assertion');
      const protectedResponse = await fetchWorker(protectedRequest);
      expect(protectedResponse.status).toBe(200);
      expect(await protectedResponse.json()).toEqual({ active: false });

      const anonymous = await fetchWorker(new Request('http://worker/api/v1/admin/break-glass-status'));
      expect(anonymous.status).toBe(401);
    } finally {
      env.APP_ENV = originalEnv;
    }
  });

  it('grants every staff account the unified control-center capabilities', async () => {
    const limited = await token('limited');
    const legacy = await token('legacy');
    const admin = await token('admin', 'admin');
    for (const [path, method, body] of [
      ['/api/v1/staff/content/subjects', 'POST', { name: 'x' }],
      ['/api/v1/staff/content/kv-prewarm/subject', 'POST', {}],
      ['/api/v1/staff/content/reindex-jobs', 'POST', { chapter_ids: ['chapter'] }],
      ['/api/v1/staff/content/chapter/chapter', 'DELETE', undefined],
      ['/api/v1/staff/analytics/command-center', 'GET', undefined],
      ['/api/v1/staff/content/reindex-jobs', 'GET', undefined],
    ] as const) expect((await fetchWorker(request(path, limited, method, body))).status).not.toBe(403);
    expect((await fetchWorker(request('/api/v1/staff/content/subjects', legacy))).status).toBe(200);
    expect((await fetchWorker(request('/api/v1/staff/content/subjects', admin))).status).toBe(200);
  });

  it('enforces referral capabilities and preserves the Staff Panel response envelopes', async () => {
    const reviewer = await token('reviewer');
    const settler = await token('settler');
    const limited = await token('limited');

    const applicationsDenied = await fetchWorker(request('/api/v1/admin/referrals/applications', limited));
    expect(applicationsDenied.status).toBe(403);
    expect(await applicationsDenied.json()).toMatchObject({
      capability: 'referral:review',
    });

    const applications = await fetchWorker(request('/api/v1/admin/referrals/applications', reviewer));
    expect(applications.status).toBe(200);
    expect(await applications.json()).toEqual({ applications: [] });

    const settlementsDenied = await fetchWorker(request('/api/v1/admin/referrals/settlements', reviewer));
    expect(settlementsDenied.status).toBe(403);
    expect(await settlementsDenied.json()).toMatchObject({
      capability: 'referral:settle',
    });

    const settlements = await fetchWorker(request('/api/v1/admin/referrals/settlements', settler));
    expect(settlements.status).toBe(200);
    expect(await settlements.json()).toEqual({ statements: [] });

    const roiDenied = await fetchWorker(request('/api/v1/admin/referrals/roi/dashboard', limited));
    expect(roiDenied.status).toBe(403);
    expect(await roiDenied.json()).toMatchObject({
      capability: 'referral:settle',
    });

    const roi = await fetchWorker(request('/api/v1/admin/referrals/roi/dashboard', settler));
    expect(roi.status).toBe(200);
    const roiBody = await roi.json() as {
      inventory: Array<{ network: string; status: string; configured: boolean }>;
      controls: { evidence_id: string; warnings: string[] } | null;
      reports: unknown[];
      reconciliation: unknown;
    };
    expect(Array.isArray(roiBody.inventory)).toBe(true);
    expect(roiBody.inventory).toEqual(expect.arrayContaining([
      expect.objectContaining({ network: 'adsense', status: 'enabled', configured: true }),
      expect.objectContaining({ network: 'adsterra', status: 'disabled', configured: false }),
      expect.objectContaining({ network: 'propellerads', status: 'disabled', configured: false }),
    ]));
    expect(roiBody.controls).toMatchObject({
      evidence_id: 'missing',
      warnings: [],
    });
    expect(roiBody.reports).toEqual([]);

    const exportDenied = await fetchWorker(request('/api/v1/admin/referrals/roi/dashboard/export', limited));
    expect(exportDenied.status).toBe(403);
    expect(await exportDenied.json()).toMatchObject({
      capability: 'referral:settle',
    });

    const roiExport = await fetchWorkerWithWaitUntil(request('/api/v1/admin/referrals/roi/dashboard/export', settler));
    expect(roiExport.status).toBe(200);
    expect(roiExport.headers.get('content-disposition')).toContain('attachment');
    expect(roiExport.headers.get('content-type')).toContain('application/json');
    expect(await roiExport.json()).toEqual(roiBody);
    await Promise.all(waitUntilPromises.splice(0));
    const exportAudit = await env.DB.prepare(`
      SELECT user_id, action, target_type, target_id, diff, created_at
      FROM content_audit_log
      WHERE action = 'download_referral_roi_evidence' AND user_id = 'settler'
      ORDER BY created_at DESC LIMIT 1
    `).first<{
      user_id: string;
      action: string;
      target_type: string;
      target_id: string;
      diff: string;
      created_at: number;
    }>();
    expect(exportAudit).toMatchObject({
      user_id: 'settler',
      action: 'download_referral_roi_evidence',
      target_type: 'referral_roi',
      target_id: 'dashboard',
    });
    expect(JSON.parse(exportAudit?.diff ?? '{}')).toEqual({ report_limit: 12 });
    expect(exportAudit?.created_at).toEqual(expect.any(Number));
  });

  it('records the admin-cookie actor for a read-only ROI export without private metadata', async () => {
    const session = await adminSession('cookie-settler');
    const response = await fetchWorkerWithWaitUntil(
      cookieRequest('/api/v1/admin/referrals/roi/dashboard/export?limit=5', session),
    );
    expect(response.status).toBe(200);
    expect(response.headers.get('content-disposition')).toContain('attachment');
    expect(response.headers.get('content-type')).toContain('application/json');
    await response.json();
    await Promise.all(waitUntilPromises.splice(0));

    const exportAudit = await env.DB.prepare(`
      SELECT user_id, action, target_type, target_id, diff, created_at
      FROM content_audit_log
      WHERE action = 'download_referral_roi_evidence' AND user_id = 'cookie-settler'
      ORDER BY created_at DESC LIMIT 1
    `).first<{
      user_id: string;
      action: string;
      target_type: string;
      target_id: string;
      diff: string;
      created_at: number;
    }>();
    expect(exportAudit).toMatchObject({
      user_id: 'cookie-settler',
      action: 'download_referral_roi_evidence',
      target_type: 'referral_roi',
      target_id: 'dashboard',
    });
    const metadata = JSON.parse(exportAudit?.diff ?? '{}') as Record<string, unknown>;
    expect(metadata).toEqual({ report_limit: 5 });
    expect(Object.keys(metadata)).toEqual(['report_limit']);
    expect(exportAudit?.created_at).toEqual(expect.any(Number));
  });

  it('lists bounded ROI download audits without exposing raw audit details', async () => {
    const limited = await token('limited');
    const settler = await token('settler');
    const capturedAt = Math.floor(Date.now() / 1000) + 10;
    for (const audit of [
      {
        id: 'roi-audit-listing-001',
        actor: 'audit-operator-001',
        reportLimit: 7,
        diff: {
          report_limit: 7,
          provider_secret: 'must-not-leak',
          beneficiary_account: 'private-beneficiary-data',
        },
      },
      {
        id: 'roi-audit-listing-002',
        actor: 'audit-operator-002',
        reportLimit: 8,
        diff: { report_limit: 8 },
      },
      {
        id: 'roi-audit-listing-003',
        actor: 'audit-operator-003',
        reportLimit: 9,
        diff: { report_limit: 9 },
      },
    ]) {
      await env.DB.prepare(`
        INSERT INTO content_audit_log
          (id, user_id, action, target_type, target_id, diff, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
      `).bind(
        audit.id,
        audit.actor,
        'download_referral_roi_evidence',
        'referral_roi',
        'dashboard',
        JSON.stringify(audit.diff),
        capturedAt,
      ).run();
    }

    const queryPlan = await env.DB.prepare(`
      EXPLAIN QUERY PLAN
      SELECT id, user_id, action, diff, created_at
      FROM content_audit_log
      WHERE action = 'download_referral_roi_evidence'
        AND target_type = 'referral_roi'
        AND target_id = 'dashboard'
      ORDER BY created_at DESC, id DESC
      LIMIT 2
    `).all<{ detail: string }>();
    expect(queryPlan.results.some(row => row.detail.includes('cal_roi_download_history_idx'))).toBe(true);

    const denied = await fetchWorker(
      request('/api/v1/admin/referrals/roi/dashboard/export-audits?limit=1&cursor=invalid', limited),
    );
    expect(denied.status).toBe(403);
    expect(await denied.json()).toMatchObject({
      detail: 'Explicit referral:settle capability required',
      capability: 'referral:settle',
    });

    const malformedCursors = [
      'invalid',
      btoa(JSON.stringify({ c: 'not-a-timestamp', i: 'audit-id' })),
      btoa(JSON.stringify({ c: capturedAt, i: '' })),
    ];
    for (const cursor of malformedCursors) {
      const invalidCursor = await fetchWorker(
        request(
          `/api/v1/admin/referrals/roi/dashboard/export-audits?cursor=${encodeURIComponent(cursor)}`,
          settler,
        ),
      );
      expect(invalidCursor.status).toBe(422);
      expect(await invalidCursor.json()).toEqual({ detail: 'Invalid ROI audit cursor' });
    }

    const response = await fetchWorker(
      request('/api/v1/admin/referrals/roi/dashboard/export-audits?limit=2', settler),
    );
    expect(response.status).toBe(200);
    const body = await response.json() as {
      audits: Array<Record<string, unknown>>;
      next_cursor: string | null;
    };
    expect(body.audits).toEqual([
      {
        actor_id: 'audit-operator-003',
        captured_at: capturedAt,
        action: 'download_referral_roi_evidence',
        report_limit: 9,
      },
      {
        actor_id: 'audit-operator-002',
        captured_at: capturedAt,
        action: 'download_referral_roi_evidence',
        report_limit: 8,
      },
    ]);
    expect(body.next_cursor).toEqual(expect.any(String));
    for (const audit of body.audits) {
      expect(Object.keys(audit).sort()).toEqual(['action', 'actor_id', 'captured_at', 'report_limit']);
    }
    expect(JSON.stringify(body)).not.toContain('must-not-leak');
    expect(JSON.stringify(body)).not.toContain('private-beneficiary-data');

    // created_at has second-level precision. A later insert in this same
    // second must stay outside the snapshot captured by the cursor.
    await env.DB.prepare(`
      INSERT INTO content_audit_log
        (id, user_id, action, target_type, target_id, diff, created_at)
      VALUES (?, ?, ?, ?, ?, ?, ?)
    `).bind(
      'roi-audit-listing-000',
      'audit-operator-newer',
      'download_referral_roi_evidence',
      'referral_roi',
      'dashboard',
      JSON.stringify({ report_limit: 10 }),
      capturedAt,
    ).run();

    await env.DB.prepare(`
      DELETE FROM content_audit_log WHERE id = ?
    `).bind('roi-audit-listing-002').run();

    const nextPage = await fetchWorker(
      request(
        `/api/v1/admin/referrals/roi/dashboard/export-audits?limit=2&cursor=${encodeURIComponent(body.next_cursor ?? '')}`,
        settler,
      ),
    );
    expect(nextPage.status).toBe(200);
    const nextBody = await nextPage.json() as {
      audits: Array<Record<string, unknown>>;
      next_cursor: string | null;
    };
    const firstNextAudit = nextBody.audits[0];
    expect(firstNextAudit).toEqual({
      actor_id: 'audit-operator-001',
      captured_at: capturedAt,
      action: 'download_referral_roi_evidence',
      report_limit: 7,
    });
    expect(Object.keys(firstNextAudit ?? {}).sort()).toEqual(['action', 'actor_id', 'captured_at', 'report_limit']);
    expect(
      [...body.audits, ...nextBody.audits]
        .map(audit => audit.actor_id)
        .filter(actor => typeof actor === 'string' && actor.startsWith('audit-operator-')),
    ).toEqual([
      'audit-operator-003',
      'audit-operator-002',
      'audit-operator-001',
    ]);
    expect(JSON.stringify(nextBody)).not.toContain('must-not-leak');
    expect(JSON.stringify(nextBody)).not.toContain('private-beneficiary-data');

    const replayedPage = await fetchWorker(
      request(
        `/api/v1/admin/referrals/roi/dashboard/export-audits?limit=2&cursor=${encodeURIComponent(body.next_cursor ?? '')}`,
        settler,
      ),
    );
    expect(replayedPage.status).toBe(200);
    expect(await replayedPage.json()).toEqual(nextBody);
    expect(JSON.stringify(nextBody)).not.toContain('audit-operator-newer');
  });

  it('fences an active RAG lease and recovers an expired running lease', async () => {
    const items = JSON.stringify([{ chapter_id: 'chapter', scopes: ['notes'], status: 'pending' }]);
    await env.DB.prepare(`INSERT INTO rag_reindex_jobs (id,status,requested_scopes,items,lease_token,lease_expires_at,created_at,updated_at) VALUES ('active','running','["notes"]',?,'owner',?,1,1)`).bind(items, Math.floor(Date.now() / 1000) + 600).run();
    await runRagJob(env, 'active');
    expect(await env.DB.prepare(`SELECT lease_token FROM rag_reindex_jobs WHERE id='active'`).first<{ lease_token: string }>()).toMatchObject({ lease_token: 'owner' });
    await env.DB.prepare(`INSERT INTO rag_reindex_jobs (id,status,requested_scopes,items,lease_token,lease_expires_at,created_at,updated_at) VALUES ('stale','running','["notes"]',?,'dead',1,1,1)`).bind(items).run();
    await resumeRagReindexJobs(env);
    // Local Vectorize is intentionally unavailable; terminal failed proves the
    // expired lease was reclaimed and executed rather than remaining running.
    expect(await env.DB.prepare(`SELECT status FROM rag_reindex_jobs WHERE id='stale'`).first<{ status: string }>()).toMatchObject({ status: 'failed' });
  });

  it('uses estimated vectors in destructive impact preview', async () => {
    const legacy = await token('legacy');
    const response = await fetchWorker(request('/api/v1/staff/content/bulk/impact-preview', legacy, 'POST', { chapter_ids: ['chapter'] }));
    expect(response.status).toBe(200);
    const body = await response.json() as Record<string, unknown>;
    expect(body).toHaveProperty('vectors_estimated'); expect(body).not.toHaveProperty('vectors');
  });

  it('lets ordinary staff publish and unpublish topics', async () => {
    const legacy = await token('legacy');
    const created = await fetchWorker(request('/api/v1/staff/content/chapter/chapter/topics', legacy, 'POST', { title: 'Topic' }));
    const topic = (await created.json() as { topic: { id: string } }).topic;
    const limited = await token('limited');
    expect((await fetchWorker(request(`/api/v1/staff/content/chapter/chapter/topics/${topic.id}/publish`, limited, 'POST', {}))).status).toBe(200);
    expect((await fetchWorker(request(`/api/v1/staff/content/chapter/chapter/topics/${topic.id}/unpublish`, limited, 'POST', {}))).status).toBe(200);
  });

  it('binds destructive preview tokens to IDs and consumes them once', async () => {
    await env.DB.prepare(`INSERT INTO chapters (id,subject_id,title,slug) VALUES ('delete-me','subject','Delete','delete-me')`).run();
    const legacy = await token('legacy');
    const preview = await fetchWorker(request('/api/v1/staff/content/bulk/impact-preview', legacy, 'POST', { chapter_ids: ['chapter'] }));
    const previewToken = (await preview.json() as { preview_token: string }).preview_token;
    const changed = await fetchWorker(request('/api/v1/staff/content/bulk/delete', legacy, 'POST', { chapter_ids: ['delete-me'], preview_token: previewToken }));
    expect(changed.status).toBe(409);
    const first = await fetchWorker(request('/api/v1/staff/content/bulk/delete', legacy, 'POST', { chapter_ids: ['chapter'], preview_token: previewToken }));
    // Local Vectorize may make cleanup partial, but token is consumed before execution.
    expect([200, 502]).toContain(first.status);
    expect((await fetchWorker(request('/api/v1/staff/content/bulk/delete', legacy, 'POST', { chapter_ids: ['chapter'], preview_token: previewToken }))).status).toBe(409);
  });

  it('rejects bulk translation without applying a shared translation', async () => {
    const legacy = await token('legacy');
    const response = await fetchWorker(request('/api/v1/staff/content/bulk/translate', legacy, 'POST', { chapter_ids: ['chapter'] }));
    expect(response.status).toBe(400);
    expect(await response.json()).toMatchObject({ detail: expect.stringContaining('Bulk translate is unsupported') });
  });

  it('lets ordinary staff perform privileged chapter changes while retaining URL safety', async () => {
    const editor = await token('editor');
    expect((await fetchWorker(request('/api/v1/staff/content/chapter/chapter', editor, 'PATCH', { notes_en: 'safe edit' }))).status).toBe(200);
    expect((await fetchWorker(request('/api/v1/staff/content/chapter/chapter', editor, 'PATCH', { status: 'published' }))).status).toBe(200);
    expect((await fetchWorker(request('/api/v1/staff/content/chapter/chapter', editor, 'PATCH', { published_topics: [] }))).status).toBe(400);
    expect((await fetchWorker(request('/api/v1/staff/content/chapter/chapter', editor, 'PATCH', { pyq_pdf_url: 'https://example.test/x' }))).status).toBe(403);
  });

  it('invalidates a destructive preview after topic impact changes', async () => {
    const legacy = await token('legacy');
    const preview = await fetchWorker(request('/api/v1/staff/content/bulk/impact-preview', legacy, 'POST', { chapter_ids: ['chapter'] }));
    const previewToken = (await preview.json() as { preview_token: string }).preview_token;
    await fetchWorker(request('/api/v1/staff/content/chapter/chapter/topics', legacy, 'POST', { title: 'Changed impact' }));
    const deletion = await fetchWorker(request('/api/v1/staff/content/bulk/delete', legacy, 'POST', { chapter_ids: ['chapter'], preview_token: previewToken }));
    expect(deletion.status).toBe(409);
  });

  it('persists the authenticated actor and action for awaited content audits', async () => {
    const legacy = await token('legacy');
    const response = await fetchWorker(request('/api/v1/staff/content/chapter/chapter', legacy, 'PATCH', { notes_en: 'audited edit' }));
    expect(response.status).toBe(200);
    const audit = await env.DB.prepare(`SELECT user_id,action FROM content_audit_log WHERE target_id='chapter' AND action='update_chapter' AND user_id='legacy' ORDER BY created_at DESC LIMIT 1`).first<{ user_id: string; action: string }>();
    expect(audit).toMatchObject({ user_id: 'legacy', action: 'update_chapter' });
  });

  it('keeps the ROI download successful when its audit sink is unavailable', async () => {
    const settler = await token('settler');
    await env.DB.prepare('DROP TABLE content_audit_log').run();
    try {
      const response = await fetchWorkerWithWaitUntil(request('/api/v1/admin/referrals/roi/dashboard/export?limit=4', settler));
      expect(response.status).toBe(200);
      const body = await response.json() as { inventory: unknown[]; reports: unknown[] };
      expect(Array.isArray(body.inventory)).toBe(true);
      expect(Array.isArray(body.reports)).toBe(true);
      await Promise.all(waitUntilPromises.splice(0));
    } finally {
      await env.DB.prepare(`
        CREATE TABLE IF NOT EXISTS content_audit_log (
          id TEXT PRIMARY KEY,
          user_id TEXT,
          action TEXT NOT NULL,
          target_type TEXT,
          target_id TEXT,
          diff TEXT,
          expires_at INTEGER,
          created_at INTEGER DEFAULT (unixepoch())
        )
      `).run();
      await env.DB.prepare(`
        CREATE INDEX IF NOT EXISTS cal_target_idx
        ON content_audit_log(target_type, target_id)
      `).run();
      await env.DB.prepare(`
        CREATE INDEX IF NOT EXISTS cal_expires_idx
        ON content_audit_log(expires_at)
      `).run();
      await env.DB.prepare(`
        CREATE INDEX IF NOT EXISTS cal_roi_download_history_idx
        ON content_audit_log(action, target_type, target_id, created_at, id)
      `).run();
    }
  });
});
