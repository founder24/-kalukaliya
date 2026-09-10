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
const secret = 'staff-release-test-secret';
async function token(sub: string, role = 'staff') {
  return new SignJWT({ role, type: 'access' }).setProtectedHeader({ alg: 'HS256' }).setSubject(sub)
    .setIssuedAt().setExpirationTime('1h').sign(new TextEncoder().encode(secret));
}
function sql() {
  return fs.readdirSync(path.join(root, 'drizzle/migrations')).filter(file => file.endsWith('.sql')).sort()
    .flatMap(file => fs.readFileSync(path.join(root, 'drizzle/migrations', file), 'utf8').split(';'))
    .map(part => part.split('\n').filter(line => line.trim() && !line.trim().startsWith('--')).join('\n').trim()).filter(Boolean);
}
function request(pathname: string, jwt: string, method = 'GET', body?: unknown) {
  return new Request(`http://worker${pathname}`, { method, headers: { Authorization: `Bearer ${jwt}`, ...(body ? { 'Content-Type': 'application/json' } : {}) }, ...(body ? { body: JSON.stringify(body) } : {}) });
}
beforeAll(async () => {
  const proxy = await getPlatformProxy<Env>({ configPath: path.join(root, 'wrangler.toml'), remoteBindings: false, persist: false });
  dispose = proxy.dispose;
  env = { ...proxy.env, JWT_SECRET: secret, ADMIN_JWT_SECRET: 'admin', RESET_TOKEN_SECRET: 'reset', EDGE_SHARED_SECRET: 'edge', RESEND_API_KEY: 'x', ALLOWED_ORIGINS: '*', APP_ENV: 'test',
    AI: { run: async (_m: string, input: { text?: string[] }) => ({ data: (input.text ?? []).map(() => ({ values: [1, 2] })) }) } as unknown as Ai };
  for (const statement of sql()) await env.DB.prepare(statement).run();
  await env.DB.batch([
    env.DB.prepare(`INSERT INTO users (id, role, capabilities) VALUES ('limited','staff','[]'),('editor','staff','["content:edit"]'),('legacy','staff',NULL),('admin','admin','[]')`),
    env.DB.prepare(`INSERT INTO boards (id,name,slug) VALUES ('b','B','b')`),
    env.DB.prepare(`INSERT INTO classes (id,board_id,name,slug) VALUES ('c','b','C','c')`),
    env.DB.prepare(`INSERT INTO streams (id,class_id,name,slug) VALUES ('s','c','S','s')`),
    env.DB.prepare(`INSERT INTO subjects (id,stream_id,name,slug) VALUES ('subject','s','Subject','subject')`),
    env.DB.prepare(`INSERT INTO chapters (id,subject_id,title,slug,notes_en,qa_en,rag_text) VALUES ('chapter','subject','Chapter','chapter','note text','[{"content":"legacy QA"}]','must not be pyq')`),
  ]);
  const { default: worker } = await import('../index.js');
  fetchWorker = request => (worker.fetch as (r: Request, e: Env, c: ExecutionContext) => Promise<Response>)(request, env, { waitUntil: () => {}, passThroughOnException: () => {} } as unknown as ExecutionContext);
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
    const limited = await token('limited'); const legacy = await token('legacy'); const admin = await token('admin', 'admin');
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
    const response = await fetchWorker(request('/api/v1/staff/content/bulk/impact-preview', await token('legacy'), 'POST', { chapter_ids: ['chapter'] }));
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
    const preview = await fetchWorker(request('/api/v1/staff/content/bulk/impact-preview', legacy, 'POST', { chapter_ids: ['delete-me'] }));
    const previewToken = (await preview.json() as { preview_token: string }).preview_token;
    const changed = await fetchWorker(request('/api/v1/staff/content/bulk/delete', legacy, 'POST', { chapter_ids: ['chapter'], preview_token: previewToken }));
    expect(changed.status).toBe(409);
    const first = await fetchWorker(request('/api/v1/staff/content/bulk/delete', legacy, 'POST', { chapter_ids: ['delete-me'], preview_token: previewToken }));
    // Local Vectorize may make cleanup partial, but token is consumed before execution.
    expect([200, 502]).toContain(first.status);
    expect((await fetchWorker(request('/api/v1/staff/content/bulk/delete', legacy, 'POST', { chapter_ids: ['delete-me'], preview_token: previewToken }))).status).toBe(409);
  });

  it('rejects bulk translation without applying a shared translation', async () => {
    const response = await fetchWorker(request('/api/v1/staff/content/bulk/translate', await token('legacy'), 'POST', { chapter_ids: ['chapter'], translations: {} }));
    expect(response.status).toBe(400);
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
});