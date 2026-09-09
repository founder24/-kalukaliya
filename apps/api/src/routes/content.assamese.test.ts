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
    env.DB.prepare(`INSERT INTO boards (id, name, slug) VALUES ('board', 'AHSEC', 'ahsec')`),
    env.DB.prepare(`INSERT INTO classes (id, board_id, name, slug) VALUES ('class', 'board', 'Class 12', 'class-12')`),
    env.DB.prepare(`INSERT INTO streams (id, class_id, name, slug) VALUES ('stream', 'class', 'Science', 'science')`),
    env.DB.prepare(`INSERT INTO subjects (id, stream_id, name, slug, is_published) VALUES ('subject', 'stream', 'Physics', 'physics', 1)`),
    env.DB.prepare(`
      INSERT INTO chapters (
        id, subject_id, title, title_as, slug, slug_as,
        meta_description, meta_description_as, keywords, keywords_as,
        status, notes_en, notes_as, published_topics
      ) VALUES (
        'translated', 'subject', 'Motion', 'গতি', 'motion', 'gati',
        'English description', 'অসমীয়া বিৱৰণ', 'motion, physics', 'গতি, পদাৰ্থবিজ্ঞান',
        'published', 'English notes', 'অসমীয়া টোকা',
        '[{"title":"Velocity","title_as":"বেগ","slug":"velocity"},{"title":"Acceleration","title_as":"  ","slug":"acceleration"}]'
      )
    `),
    env.DB.prepare(`
      INSERT INTO chapters (
        id, subject_id, title, title_as, slug, slug_as,
        meta_description, meta_description_as, keywords, keywords_as,
        status, notes_en, notes_as, published_topics
      ) VALUES (
        'fallback', 'subject', 'Force', '  ', 'force', '  ',
        'Force description', '  ', 'force, mechanics', '  ',
        'published', 'Force notes', 'বলৰ টোকা',
        '[{"title":"Newton laws","title_as":"  ","slug":"newton-laws"}]'
      )
    `),
  ]);
});

afterAll(async () => {
  await dispose?.();
});

async function get(pathname: string): Promise<Response> {
  return app.fetch(new Request(`http://worker${pathname}`), env);
}

describe('Assamese public content metadata', () => {
  it('prefers translated chapter metadata, slug, and topic titles', async () => {
    const response = await get('/api/v1/content/chapter-by-slug-as/ahsec/class-12/physics/gati');
    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toMatchObject({
      title: 'গতি',
      chapter_title: 'গতি',
      topic_title: 'গতি',
      chapter_slug: 'motion',
      slug_as: 'gati',
      meta_description: 'অসমীয়া বিৱৰণ',
      keywords: 'গতি, পদাৰ্থবিজ্ঞান',
      topics: [
        { title: 'বেগ', title_as: 'বেগ', slug: 'velocity' },
        { title: 'Acceleration', title_as: '  ', slug: 'acceleration' },
      ],
      published_topics: [
        { title: 'বেগ', title_as: 'বেগ', slug: 'velocity' },
        { title: 'Acceleration', title_as: '  ', slug: 'acceleration' },
      ],
    });
  });

  it('falls back to valid English values when Assamese metadata is blank', async () => {
    const response = await get('/api/v1/content/chapter-by-slug-as/ahsec/class-12/physics/force');
    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toMatchObject({
      title: 'Force',
      chapter_title: 'Force',
      topic_title: 'Force',
      chapter_slug: 'force',
      slug_as: '  ',
      meta_description: 'Force description',
      keywords: 'force, mechanics',
      topics: [{ title: 'Newton laws', slug: 'newton-laws' }],
    });
  });

  it('keeps the English resolver response unchanged', async () => {
    const response = await get('/api/v1/content/chapter-by-slug/ahsec/class-12/physics/motion');
    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toMatchObject({
      title: 'Motion',
      chapter_title: 'Motion',
      topic_title: 'Motion',
      chapter_slug: 'motion',
      meta_description: 'English description',
      keywords: 'motion, physics',
      topics: [
        { title: 'Velocity', title_as: 'বেগ', slug: 'velocity' },
        { title: 'Acceleration', title_as: '  ', slug: 'acceleration' },
      ],
    });
  });

  it('localizes the topics-published query and preserves English fallbacks', async () => {
    const response = await get('/api/v1/content/chapters/translated/topics-published?lang=as');
    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({
      chapter_id: 'translated',
      topics: [
        { title: 'বেগ', title_as: 'বেগ', slug: 'velocity' },
        { title: 'Acceleration', title_as: '  ', slug: 'acceleration' },
      ],
      total: 2,
    });
  });
});