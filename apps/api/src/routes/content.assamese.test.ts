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
    env.DB.prepare(`INSERT INTO subjects (id, stream_id, name, slug, is_published) VALUES ('related-subject', 'stream', 'Related Subject', 'related-subject', 1)`),
    env.DB.prepare(`UPDATE subjects SET pyq_papers = ? WHERE id = 'subject'`).bind(JSON.stringify([{
      id: 'paper-1',
      name: 'Physics Annual Final',
      class_name: 'Class 12',
      year: 2025,
      description: 'A sample paper',
      pages: [{ id: 'page-1', url: '/papers/physics-2025/page-1', text: 'Question text' }],
      rag_text: 'Long question paper transcription that must not be duplicated in the slim catalog.',
      rag_text_as: 'দীঘল প্ৰশ্নকাকতৰ পাঠ',
    }])),
    env.DB.prepare(`
      INSERT INTO chapters (
        id, subject_id, title, title_as, slug, slug_as, status,
        published_topics, qa_en, qa_as
      ) VALUES (?, 'related-subject', ?, ?, ?, ?, 'published', ?, ?, ?)
    `).bind(
      'related-current',
      'Current chapter',
      'বৰ্তমান অধ্যায়',
      'current-chapter',
      'bartaman-adhyay',
      JSON.stringify([{
        topic_id: 'current-topic',
        title: 'Current topic',
        topic_slug: 'current-topic',
        title_as: 'বৰ্তমান বিষয়',
        topic_slug_as: 'bartaman-bishoy',
        definition: 'A complete reviewed definition for the current topic.',
        definition_as: 'বৰ্তমান বিষয়টোৰ সম্পূৰ্ণ পৰীক্ষিত সংজ্ঞা।',
        notes_en: 'Approved English notes.',
        notes_as: 'অনুমোদিত অসমীয়া টোকা।',
        notes_status_en: 'approved',
        notes_status_as: 'approved',
        status: 'published',
      }]),
      JSON.stringify([{ question: 'English question?', answer: 'English answer.', topic_slugs: ['current-topic'] }]),
      JSON.stringify([{ question: 'অসমীয়া প্ৰশ্ন?', answer: 'অসমীয়া উত্তৰ।', topic_slugs: ['bartaman-bishoy'] }]),
    ),
    env.DB.prepare(`
      INSERT INTO chapters (
        id, subject_id, title, title_as, slug, slug_as, status,
        published_topics, qa_en, qa_as
      ) VALUES (?, 'related-subject', ?, ?, ?, ?, 'published', ?, ?, ?)
    `).bind(
      'related-sibling',
      'Sibling chapter',
      'সহোদৰ অধ্যায়',
      'sibling-chapter',
      'sohodor-adhyay',
      JSON.stringify([{
        topic_id: 'sibling-topic',
        title: 'Sibling topic',
        topic_slug: 'sibling-topic',
        title_as: 'সহোদৰ বিষয়',
        topic_slug_as: 'sohodor-bishoy',
        definition: 'A complete reviewed definition for this sibling topic.',
        definition_as: 'সহোদৰ বিষয়টোৰ সম্পূৰ্ণ পৰীক্ষিত সংজ্ঞা।',
        notes_en: 'Approved sibling English notes.',
        notes_as: 'Draft sibling Assamese notes.',
        notes_status_en: 'approved',
        notes_status_as: 'draft',
        status: 'published',
      }]),
      JSON.stringify([{ question: 'Sibling question?', answer: 'Sibling answer.', topic_slugs: ['sibling-topic'] }]),
      JSON.stringify([{ question: 'সহোদৰ প্ৰশ্ন?', answer: 'সহোদৰ উত্তৰ।', topic_slugs: ['sohodor-bishoy'] }]),
    ),
    env.DB.prepare(`
      INSERT INTO chapters (
        id, subject_id, title, title_as, slug, slug_as,
        meta_description, meta_description_as, keywords, keywords_as,
        status, notes_en, notes_as, published_topics
      ) VALUES (
        'translated', 'subject', 'Motion', 'গতি', 'motion', 'gati',
        'English description', 'অসমীয়া বিৱৰণ', 'motion, physics', 'গতি, পদাৰ্থবিজ্ঞান',
        'published', 'English notes', 'অসমীয়া টোকা',
        '[{"title":"Velocity","title_as":"বেগ","slug":"velocity","content":"Speed with direction.","content_as":"দিশসহ গতি।","status":"published","definition_status":"ok","definition":"Velocity is the rate of change of displacement with time. It describes both speed and direction of motion. In physics velocity is a vector quantity with magnitude and direction. Its SI unit is metres per second. The sign indicates movement along an axis. Average velocity differs from instantaneous velocity."},{"title":"Acceleration","title_as":"  ","slug":"acceleration"}]'
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
        '[{"title":"Newton laws","title_as":"  ","slug":"newton-laws","definition_status":"ok","definition":"Newton laws explain how forces determine motion. The first law describes inertia, the second relates force to mass and acceleration, and the third states that interactions occur in equal and opposite pairs. Together these laws help students analyze everyday motion and solve classical mechanics problems."}]'
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
        '[{"title":"Energy","slug":"energy","definition_status":"ok","definition":"Energy is the capacity to do work or cause change. It appears in kinetic, potential, thermal, chemical, and electrical forms. Energy can transfer between objects or transform from one form to another. In an isolated system the total energy remains conserved, helping explain physical processes."},{"title":"Missing slug"},{"name":"Named topic","slug":"named-topic","definition_status":"ok","definition":"A clear topic definition identifies the central concept and explains why it matters. It gives students enough context to connect the idea with its chapter, subject, and class. This reviewed example also demonstrates how canonical topic links are formed while navigation-only records remain outside the public index."}]'
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
    env.DB.prepare(`
      INSERT INTO chapters (id, subject_id, title, title_as, slug, slug_as, status, notes_en, notes_as, qa_en, qa_as)
      VALUES ('faq-complete', 'related-subject', 'FAQ fixture', 'FAQ উদাহৰণ',
        'faq-complete', 'faq-example', 'published', 'A short chapter summary.', 'এটা সৰু অধ্যায়ৰ সাৰাংশ।', ?, ?)
    `).bind(
      JSON.stringify([
        { question: 'A complete question one?', answer: 'A reviewed answer with enough detail.' },
        { question: 'A question with no answer?', answer: '' },
        { question: 'A complete question two?', solution: 'A reviewed solution with enough detail.' },
      ]),
      JSON.stringify([
        { question: 'সম্পূৰ্ণ প্ৰশ্ন এটা কি?', answer: 'পৰীক্ষিত উত্তৰটোত যথেষ্ট বিৱৰণ আছে।' },
        { question: 'উত্তৰ নথকা প্ৰশ্ন নেকি?', answer: '' },
        { question: 'সম্পূৰ্ণ প্ৰশ্ন দুটা কি?', solution: 'পৰীক্ষিত সমাধানটোত যথেষ্ট বিৱৰণ আছে।' },
      ]),
    ),
    env.DB.prepare(`
      INSERT INTO chapters (id, subject_id, title, slug, status, content_type, qa_en, qa_as)
      VALUES ('faq-question-paper', 'related-subject', 'FAQ question paper', 'faq-question-paper',
        'published', 'question_paper', '[{"question":"A question?","answer":"An answer that must not be exposed."}]',
        '[{"question":"এটা প্ৰশ্ন?","answer":"প্ৰদৰ্শন কৰিব নলগীয়া উত্তৰ।"}]')
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
  it('exposes only complete Q&A pairs to FAQ metadata', async () => {
    const response = await get('/api/v1/content/chapters/faq-complete/faq-jsonld');
    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toMatchObject({
      entries: [
        { question: 'A complete question one?', answer: 'A reviewed answer with enough detail.' },
        { question: 'A complete question two?', answer: 'A reviewed solution with enough detail.' },
      ],
      faq_jsonld: {
        '@type': 'FAQPage',
        mainEntity: [
          { name: 'A complete question one?' },
          { name: 'A complete question two?' },
        ],
      },
    });
  });

  it('keeps Assamese FAQ content localized and excludes question papers', async () => {
    const response = await get('/api/v1/content/chapters/faq-complete/faq-jsonld?lang=as');
    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toMatchObject({
      language: 'as',
      entries: [
        { question: 'সম্পূৰ্ণ প্ৰশ্ন এটা কি?', answer: 'পৰীক্ষিত উত্তৰটোত যথেষ্ট বিৱৰণ আছে।' },
        { question: 'সম্পূৰ্ণ প্ৰশ্ন দুটা কি?', answer: 'পৰীক্ষিত সমাধানটোত যথেষ্ট বিৱৰণ আছে।' },
      ],
      faq_jsonld: { '@type': 'FAQPage', mainEntity: [{ name: 'সম্পূৰ্ণ প্ৰশ্ন এটা কি?' }, { name: 'সম্পূৰ্ণ প্ৰশ্ন দুটা কি?' }] },
    });
    const chapter = await get('/api/v1/content/chapter-by-slug-as/ahsec/class-12/science/related-subject/faq-example');
    expect(chapter.status).toBe(200);
    await expect(chapter.json()).resolves.toMatchObject({
      faq_entries: [
        { question: 'সম্পূৰ্ণ প্ৰশ্ন এটা কি?', answer: 'পৰীক্ষিত উত্তৰটোত যথেষ্ট বিৱৰণ আছে।' },
        { question: 'সম্পূৰ্ণ প্ৰশ্ন দুটা কি?', answer: 'পৰীক্ষিত সমাধানটোত যথেষ্ট বিৱৰণ আছে।' },
      ],
      faq_jsonld: { '@type': 'FAQPage', mainEntity: [{ name: 'সম্পূৰ্ণ প্ৰশ্ন এটা কি?' }, { name: 'সম্পূৰ্ণ প্ৰশ্ন দুটা কি?' }] },
    });
    const paper = await get('/api/v1/content/chapters/faq-question-paper/faq-jsonld?lang=as');
    await expect(paper.json()).resolves.toMatchObject({ entries: [], faq_jsonld: null });
  });

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
      published_topics: [{
        title: 'বেগ',
        title_as: 'বেগ',
        slug: 'velocity',
        content: 'Speed with direction.',
        content_as: 'দিশসহ গতি।',
        status: 'published',
        definition: 'দিশসহ গতি।',
        topic_slug: 'velocity',
      }],
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

  it('rejects oversized search queries before database work', async () => {
    const response = await get(`/api/v1/content/search?q=${'A'.repeat(201)}`);
    expect(response.status).toBe(400);
    await expect(response.json()).resolves.toEqual({
      detail: 'Search query must be 200 characters or fewer',
    });
  });

  it('falls back to the default search limit for a non-numeric limit', async () => {
    const response = await get('/api/v1/content/search?q=physics&limit=not-a-number');
    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toMatchObject({
      query: 'physics',
      total: 2,
      available: true,
    });
  });

  it('localizes published answers and excludes drafts or navigation-only topics', async () => {
    const response = await get('/api/v1/content/chapters/translated/topics-published?lang=as');
    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({
      chapter_id: 'translated',
      topics: [{
        title: 'বেগ',
        title_as: 'বেগ',
        slug: 'velocity',
        content: 'Speed with direction.',
        content_as: 'দিশসহ গতি।',
        status: 'published',
        definition_status: 'ok',
        definition: 'দিশসহ গতি।',
        topic_slug: 'velocity',
        topic_page_available_en: false,
        topic_page_available_as: false,
      }],
      total: 1,
    });

    const chapter = await get('/api/v1/content/chapter-by-slug-as/ahsec/class-12/physics/gati');
    expect(chapter.status).toBe(200);
    await expect(chapter.json()).resolves.toMatchObject({
      published_topics: [{ topic_slug: 'velocity', definition: 'দিশসহ গতি।' }],
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
    expect(payload.total_topics).toBe(2);

    const motion = payload.chapters.find((chapter) => chapter.chapter_id === 'translated');
    expect(motion).toMatchObject({
      chapter_url: '/ahsec/class-12/physics/motion',
    });
    expect(motion?.topics[0]).toMatchObject({
      topic_slug: 'velocity',
      deep_link_path: '/ahsec/class-12/physics/motion/topic/velocity',
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
      chapter_url: '/ahsec/class-12/index-subject/indexed-topics',
      topics: [
        {
          topic_slug: 'energy',
          deep_link_path: '/ahsec/class-12/index-subject/indexed-topics/topic/energy',
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

  it('keeps slim library chapter counts aligned with the full bundle', async () => {
    const fullResponse = await get('/api/v1/content/library-bundle');
    const slimResponse = await get('/api/v1/content/library-bundle?slim=1');
    expect(fullResponse.status).toBe(200);
    expect(slimResponse.status).toBe(200);

    const full = await fullResponse.json() as {
      subjects: Array<{ id: string; chapter_count: number; pyq_papers: Array<Record<string, unknown>> }>;
    };
    const slim = await slimResponse.json() as {
      subjects: Array<{
        id: string;
        chapter_count: number;
        chapters?: unknown[];
        pyq_papers: Array<Record<string, unknown>>;
      }>;
    };
    const fullSubject = full.subjects.find(subject => subject.id === 'subject');
    const slimSubject = slim.subjects.find(subject => subject.id === 'subject');

    expect(fullSubject?.chapter_count).toBeGreaterThan(0);
    expect(slimSubject?.chapter_count).toBe(fullSubject?.chapter_count);
    expect(slimSubject).not.toHaveProperty('chapters');
    expect(fullSubject?.pyq_papers[0]).not.toHaveProperty('rag_text');
    expect(slimSubject?.pyq_papers[0]).not.toHaveProperty('rag_text');
    expect(slimSubject?.pyq_papers[0]?.pages).toEqual([
      { id: 'page-1', url: '/papers/physics-2025/page-1' },
    ]);
  });

  it('returns only complete approved topic pages in the requested language', async () => {
    const englishResponse = await get('/api/v1/content/chapters/related-current/topics-related?lang=en&limit=12');
    const assameseResponse = await get('/api/v1/content/chapters/related-current/topics-related?lang=as&limit=12');
    expect(englishResponse.status).toBe(200);
    expect(assameseResponse.status).toBe(200);

    const english = await englishResponse.json() as {
      language: string;
      siblings: Array<Record<string, unknown>>;
      cross_chapter: Array<Record<string, unknown>>;
    };
    const assamese = await assameseResponse.json() as {
      language: string;
      siblings: Array<Record<string, unknown>>;
      cross_chapter: Array<Record<string, unknown>>;
    };

    expect(english.language).toBe('en');
    expect(english.siblings.map(topic => topic.topic_slug)).toEqual(['current-topic']);
    expect(english.cross_chapter.map(topic => topic.topic_slug)).toEqual(['sibling-topic']);
    expect(english.cross_chapter[0]).toMatchObject({
      chapter_slug: 'sibling-chapter',
      chapter_slug_as: 'sohodor-adhyay',
      page_available: true,
      language: 'en',
    });
    expect(assamese.language).toBe('as');
    expect(assamese.siblings.map(topic => topic.topic_slug)).toEqual(['bartaman-bishoy']);
    expect(assamese.cross_chapter).toEqual([]);
  });
});

describe('MDC-01013 bilingual FAQ answer migration', () => {
  it('expands each published stream copy and marks its RAG index stale', async () => {
    const ids = [
      '6a90409b-3488-47ef-b443-e4c2f1bdf469',
      '9bf76bd3-b7c5-4a51-b66c-10d3a0b69d8d',
      'fe2d8e57-7d3a-4066-8d8c-9f6986b5c623',
    ];
    const shortQa = JSON.stringify(Array.from({ length: 5 }, (_, index) => ({
      question: `Question ${index + 1}?`,
      answer: 'Short answer.',
    })));
    await env.DB.batch(ids.map((id, index) => env.DB.prepare(`
      INSERT INTO chapters (
        id, subject_id, title, slug, status, content_type, qa_en, qa_as
      ) VALUES (?, ?, 'MDC-01013 fixture', 'course-syllabus-mdc-01013-makers-of-modern-assam-semester-1',
        'published', 'notes', ?, ?)
    `).bind(id, `fixture-subject-${index}`, shortQa, shortQa)));

    const migration = fs.readFileSync(
      path.join(API_ROOT, 'drizzle/migrations/0051_expand_mdc01013_bilingual_faq_answers.sql'),
      'utf8',
    );
    for (const statement of migration.split(';').map(fragment => fragment.split('\n')
      .filter(line => line.trim() && !line.trim().startsWith('--')).join('\n').trim()).filter(Boolean)) {
      await env.DB.prepare(statement).run();
    }

    const rows = await env.DB.prepare(`
      SELECT id, qa_en, qa_as, rag_updated_at
      FROM chapters
      WHERE id IN (${ids.map(() => '?').join(', ')})
      ORDER BY id
    `).bind(...ids).all<{ id: string; qa_en: string; qa_as: string; rag_updated_at: number | null }>();
    expect(rows.results).toHaveLength(3);
    for (const row of rows.results) {
      const answersEn = (JSON.parse(row.qa_en) as Array<{ answer: string }>).map(item => item.answer);
      const answersAs = (JSON.parse(row.qa_as) as Array<{ answer: string }>).map(item => item.answer);
      expect(answersEn).toHaveLength(5);
      expect(answersAs).toHaveLength(5);
      expect(answersEn.every(answer => answer.trim().split(/\s+/u).length >= 20)).toBe(true);
      expect(answersAs.every(answer => answer.trim().split(/\s+/u).length >= 20)).toBe(true);
      expect(row.rag_updated_at).toBeGreaterThan(0);
    }
  });
});