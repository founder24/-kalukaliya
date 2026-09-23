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
    env.DB.prepare(`
      INSERT INTO subjects (id, stream_id, name, slug, is_published)
      VALUES ('empty-subject', 'stream', 'Empty Subject', 'empty-subject', 1)
    `),
    env.DB.prepare(`
      INSERT INTO subjects (id, stream_id, name, slug, is_published)
      VALUES ('index-subject', 'stream', 'Index Subject', 'index-subject', 1)
    `),
    env.DB.prepare(`
      INSERT INTO subjects (id, stream_id, name, slug, is_published)
      VALUES ('hidden-subject', 'stream', 'Hidden Subject', 'hidden-subject', 0)
    `),
    env.DB.prepare(`
      INSERT INTO chapters (
        id, subject_id, title, slug, status, published_topics
      ) VALUES (
        'indexed', 'index-subject', 'Indexed Topics', 'indexed-topics', 'published',
        '[{"title":"Energy","slug":"energy"},{"title":"Missing slug"},{"name":"Named topic","slug":"named-topic"}]'
      )
    `),
    env.DB.prepare(`
      INSERT INTO chapters (
        id, subject_id, title, slug, status, published_topics
      ) VALUES (
        'draft-topics', 'subject', 'Draft Topics', 'draft-topics', 'draft',
        '[{"title":"Draft only","slug":"draft-only"}]'
      )
    `),
    env.DB.prepare(`
      INSERT INTO chapters (
        id, subject_id, title, slug, status, published_topics
      ) VALUES (
        'hidden-draft', 'hidden-subject', 'Hidden Draft', 'hidden-draft', 'draft',
        '[{"title":"Hidden only","slug":"hidden-only"}]'
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

  it('does not resolve draft chapters through any public slug variant', async () => {
    const paths = [
      '/api/v1/content/chapter-by-slug/ahsec/class-12/physics/draft-topics',
      '/api/v1/content/chapter-by-slug/ahsec/class-12/science/physics/draft-topics',
      '/api/v1/content/chapter-by-slug-as/ahsec/class-12/physics/draft-topics',
      '/api/v1/content/chapter-by-slug-as/ahsec/class-12/science/physics/draft-topics',
    ];

    for (const pathname of paths) {
      const response = await get(pathname);
      expect(response.status, pathname).toBe(404);
    }
  });

  it('searches published subjects as well as chapter titles', async () => {
    const response = await get('/api/v1/content/search?q=physics');
    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toMatchObject({
      query: 'physics',
      total: 2,
      results: [
        { title: 'Motion', snippet: 'Physics — Motion' },
        { title: 'Force', snippet: 'Physics — Force' },
      ],
      available: true,
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

  it('builds a hierarchy-aware topic index and filters non-citable topics', async () => {
    const response = await get('/api/v1/content/subjects/subject/topic-index');
    expect(response.status).toBe(200);

    const payload = await response.json() as {
      subject_id: string;
      total_topics: number;
      chapters: Array<{
        chapter_id: string;
        chapter_url: string;
        topics: Array<{
          topic_slug: string;
          deep_link_path: string;
        }>;
      }>;
    };
    expect(payload.subject_id).toBe('subject');
    expect(payload.total_topics).toBe(3);

    const motion = payload.chapters.find((chapter) => chapter.chapter_id === 'translated');
    expect(motion).toMatchObject({
      chapter_url: '/ahsec/class-12/science/physics/motion',
    });
    expect(motion?.topics[0]).toMatchObject({
      topic_slug: 'velocity',
      deep_link_path: '/ahsec/class-12/science/physics/motion/topic/velocity',
    });

    expect(payload.chapters.some((chapter) => chapter.chapter_id === 'draft-topics')).toBe(false);

    const indexedResponse = await get('/api/v1/content/subjects/index-subject/topic-index');
    expect(indexedResponse.status).toBe(200);
    const indexedPayload = await indexedResponse.json() as {
      chapters: Array<{
        chapter_url: string;
        topics: Array<{ topic_slug: string; deep_link_path: string }>;
      }>;
      total_topics: number;
    };
    expect(indexedPayload.total_topics).toBe(2);
    expect(indexedPayload.chapters[0]).toMatchObject({
      chapter_url: '/ahsec/class-12/science/index-subject/indexed-topics',
      topics: [
        {
          topic_slug: 'energy',
          deep_link_path: '/ahsec/class-12/science/index-subject/indexed-topics/topic/energy',
        },
        { topic_slug: 'named-topic' },
      ],
    });
  });

  it('returns stable empty indexes without exposing unpublished or unknown chapters', async () => {
    const empty = await get('/api/v1/content/subjects/empty-subject/topic-index');
    expect(empty.status).toBe(200);
    await expect(empty.json()).resolves.toEqual({
      subject_id: 'empty-subject',
      chapters: [],
      total_topics: 0,
    });

    const hidden = await get('/api/v1/content/subjects/hidden-subject/topic-index');
    expect(hidden.status).toBe(200);
    await expect(hidden.json()).resolves.toEqual({
      subject_id: 'hidden-subject',
      chapters: [],
      total_topics: 0,
    });

    const unknown = await get('/api/v1/content/subjects/does-not-exist/topic-index');
    expect(unknown.status).toBe(200);
    await expect(unknown.json()).resolves.toEqual({
      subject_id: 'does-not-exist',
      chapters: [],
      total_topics: 0,
    });

    const missingSubject = await get('/api/v1/content/subjects/does-not-exist');
    expect(missingSubject.status).toBe(404);
    expect(missingSubject.headers.get('Cache-Control')).toBe('public, max-age=60, s-maxage=300');
  });
});