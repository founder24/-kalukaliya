import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { getPlatformProxy } from 'wrangler';
import { Hono } from 'hono';
import type { Env } from '../types';
import { contentRouter } from './content';

const API_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../');

function migrations(): string[] {
  return fs.readdirSync(path.join(API_ROOT, 'drizzle/migrations'))
    .filter(name => name.endsWith('.sql')).sort()
    .flatMap(name => fs.readFileSync(path.join(API_ROOT, 'drizzle/migrations', name), 'utf8')
      .split(';').map(fragment => fragment.split('\n')
        .filter(line => line.trim() && !line.trim().startsWith('--')).join('\n').trim())
      .filter(Boolean));
}

let env: Env;
let dispose: () => Promise<void>;
const app = new Hono<{ Bindings: Env }>();
app.route('/api/v1/content', contentRouter);

beforeAll(async () => {
  const proxy = await getPlatformProxy<Env>({
    configPath: path.join(API_ROOT, 'wrangler.toml'),
    remoteBindings: false,
    persist: false,
  });
  env = proxy.env;
  dispose = proxy.dispose;

  for (const statement of migrations()) await env.DB.prepare(statement).run();
  await env.DB.batch([
    env.DB.prepare(`INSERT INTO boards (id, name, slug) VALUES ('degree-board', 'DEGREE', 'degree')`),
    env.DB.prepare(`INSERT INTO boards (id, name, slug) VALUES ('other-board', 'DEGREE', 'other-degree')`),
    env.DB.prepare(`INSERT INTO boards (id, name, slug) VALUES ('empty-board', 'DEGREE', 'empty-degree')`),
    env.DB.prepare(`INSERT INTO classes (id, board_id, name, slug) VALUES ('degree-class', 'degree-board', 'Degree', 'degree')`),
    env.DB.prepare(`INSERT INTO classes (id, board_id, name, slug) VALUES ('other-class', 'other-board', 'Degree', 'other-degree')`),
    env.DB.prepare(`INSERT INTO streams (id, class_id, name, slug) VALUES ('major-stream', 'degree-class', 'Major', 'major')`),
    env.DB.prepare(`INSERT INTO streams (id, class_id, name, slug) VALUES ('minor-stream', 'degree-class', 'Minor', 'minor')`),
    env.DB.prepare(`INSERT INTO streams (id, class_id, name, slug) VALUES ('other-major-stream', 'other-class', 'Major', 'major')`),
    env.DB.prepare(`INSERT INTO subjects (id, stream_id, name, slug, is_published) VALUES ('major-math', 'major-stream', 'Mathematics', 'mathematics', 1)`),
    env.DB.prepare(`INSERT INTO subjects (id, stream_id, name, slug, is_published) VALUES ('major-chemistry', 'major-stream', 'Chemistry', 'chemistry', 1)`),
    env.DB.prepare(`INSERT INTO subjects (id, stream_id, name, slug, is_published) VALUES ('major-draft', 'major-stream', 'Draft Subject', 'draft-subject', 0)`),
    env.DB.prepare(`INSERT INTO subjects (id, stream_id, name, slug, is_published) VALUES ('minor-history', 'minor-stream', 'History', 'history', 1)`),
    env.DB.prepare(`INSERT INTO subjects (id, stream_id, name, slug, is_published) VALUES ('other-major-subject', 'other-major-stream', 'Other Board Subject', 'other-subject', 1)`),
  ]);
});

afterAll(async () => {
  await dispose?.();
});

async function get(pathname: string): Promise<Response> {
  return app.fetch(new Request(`http://worker${pathname}`), env);
}

describe('degree course type content contract', () => {
  it('groups published subjects by course type and filters by board', async () => {
    const response = await get(
      '/api/v1/content/subjects-by-course-type?board_id=degree-board',
    );

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual([
      {
        slug: 'major',
        name: 'Major',
        description: null,
        icon: 'target',
        subject_count: 2,
        subjects: [
          { id: 'major-chemistry', name: 'Chemistry' },
          { id: 'major-math', name: 'Mathematics' },
        ],
      },
      {
        slug: 'minor',
        name: 'Minor',
        description: null,
        icon: 'book',
        subject_count: 1,
        subjects: [{ id: 'minor-history', name: 'History' }],
      },
    ]);
  });

  it('returns an empty list for a board with no course type streams', async () => {
    const response = await get(
      '/api/v1/content/subjects-by-course-type?board_id=empty-board',
    );

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual([]);
  });

  it('requires board_id rather than returning a cross-board subject list', async () => {
    const response = await get('/api/v1/content/subjects-by-course-type');

    expect(response.status).toBe(400);
    await expect(response.json()).resolves.toEqual({
      detail: 'board_id is required',
    });
  });
});