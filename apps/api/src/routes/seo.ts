/**
 * D1-backed site-operation routes for crawlers and publishing tools.
 *
 * They replace the Cloud Run sitemap/feed implementation while retaining the
 * established public paths, media types, and no-auth access policy.
 */

import { Hono } from 'hono';
import type { Env } from '../types';

export const seoRouter = new Hono<{ Bindings: Env }>();

const SITE_URL = 'https://syrabit.ai';
const XML_CONTENT_TYPE = 'application/xml; charset=utf-8';
const RSS_CONTENT_TYPE = 'application/rss+xml; charset=utf-8';

type ContentUrl = {
  title: string;
  subjectName: string;
  subjectSlug: string;
  boardSlug: string;
  classSlug: string;
  chapterSlug: string;
  notesEn: string | null;
  publishedTopics: string | null;
  updatedAt: number | null;
  createdAt: number | null;
};

function xml(value: string): string {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&apos;');
}

function date(value: number | null): string {
  return value ? new Date(value * 1000).toISOString().slice(0, 10) : '';
}

function rfc822(value: number | null): string {
  return new Date((value ?? Math.floor(Date.now() / 1000)) * 1000).toUTCString();
}

function contentUrl(row: Pick<ContentUrl, 'boardSlug' | 'classSlug' | 'subjectSlug' | 'chapterSlug'>): string {
  return `${SITE_URL}/${row.boardSlug}/${row.classSlug}/${row.subjectSlug}/${row.chapterSlug}`;
}

function xmlResponse(content: string, contentType = XML_CONTENT_TYPE): Response {
  return new Response(content, {
    headers: {
      'Content-Type': contentType,
      'Cache-Control': 'public, max-age=600, s-maxage=600',
    },
  });
}

async function publishedContent(c: { env: Env }, subjectSlug?: string): Promise<ContentUrl[]> {
  const subjectFilter = subjectSlug ? 'AND s.slug = ?' : '';
  const statement = c.env.DB.prepare(`
    SELECT c.title, c.slug AS chapter_slug, c.notes_en, c.published_topics,
           c.updated_at, c.created_at, s.name AS subject_name, s.slug AS subject_slug,
           b.slug AS board_slug, cl.slug AS class_slug
    FROM chapters c
    JOIN subjects s ON s.id = c.subject_id
    JOIN streams str ON str.id = s.stream_id
    JOIN classes cl ON cl.id = str.class_id
    JOIN boards b ON b.id = cl.board_id
    WHERE c.status = 'published' AND s.is_published = 1 ${subjectFilter}
    ORDER BY c.updated_at DESC, c.chapter_number ASC
  `);
  const result = subjectSlug
    ? await statement.bind(subjectSlug).all<Record<string, string | number | null>>()
    : await statement.all<Record<string, string | number | null>>();
  return (result.results ?? []).map((row) => ({
    title: String(row.title ?? ''),
    subjectName: String(row.subject_name ?? ''),
    subjectSlug: String(row.subject_slug ?? ''),
    boardSlug: String(row.board_slug ?? ''),
    classSlug: String(row.class_slug ?? ''),
    chapterSlug: String(row.chapter_slug ?? ''),
    notesEn: typeof row.notes_en === 'string' ? row.notes_en : null,
    publishedTopics: typeof row.published_topics === 'string' ? row.published_topics : null,
    updatedAt: typeof row.updated_at === 'number' ? row.updated_at : null,
    createdAt: typeof row.created_at === 'number' ? row.created_at : null,
  }));
}

function urlSet(entries: string[]): string {
  return `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${entries.join('\n')}\n</urlset>`;
}

function urlEntry(location: string, lastModified: string, priority: string): string {
  return `  <url>\n    <loc>${xml(location)}</loc>${lastModified ? `\n    <lastmod>${lastModified}</lastmod>` : ''}\n    <changefreq>weekly</changefreq>\n    <priority>${priority}</priority>\n  </url>`;
}

seoRouter.get('/sitemap.xml', sitemapIndex);
seoRouter.get('/sitemap-index.xml', sitemapIndex);
async function sitemapIndex(c: { text: (body: string, status?: number, headers?: Record<string, string>) => Response }): Promise<Response> {
  const today = new Date().toISOString().slice(0, 10);
  return c.text(`<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>${SITE_URL}/sitemap-static.xml</loc><lastmod>${today}</lastmod></sitemap>
  <sitemap><loc>${SITE_URL}/sitemap-subjects.xml</loc><lastmod>${today}</lastmod></sitemap>
  <sitemap><loc>${SITE_URL}/sitemap-chapters.xml</loc><lastmod>${today}</lastmod></sitemap>
  <sitemap><loc>${SITE_URL}/sitemap-topics.xml</loc><lastmod>${today}</lastmod></sitemap>
</sitemapindex>`, 200, { 'Content-Type': XML_CONTENT_TYPE, 'Cache-Control': 'public, max-age=600, s-maxage=600' });
}

seoRouter.get('/sitemap-static.xml', (c) => xmlResponse(urlSet([
  urlEntry(SITE_URL, new Date().toISOString().slice(0, 10), '1.0'),
  urlEntry(`${SITE_URL}/library`, '', '0.9'),
  urlEntry(`${SITE_URL}/chat`, '', '0.8'),
  urlEntry(`${SITE_URL}/pricing`, '', '0.6'),
  urlEntry(`${SITE_URL}/about`, '', '0.5'),
  urlEntry(`${SITE_URL}/technology`, '', '0.6'),
  urlEntry(`${SITE_URL}/exam-routine`, '', '0.7'),
])));

seoRouter.get('/sitemap-subjects.xml', async (c) => {
  const rows = await publishedContent(c);
  const subjects = new Map<string, ContentUrl>();
  for (const row of rows) {
    subjects.set(`${row.boardSlug}/${row.classSlug}/${row.subjectSlug}`, row);
  }
  return xmlResponse(urlSet([...subjects.values()].map((row) =>
    urlEntry(`${SITE_URL}/${row.boardSlug}/${row.classSlug}/${row.subjectSlug}`, date(row.updatedAt), '0.8'),
  )));
});

seoRouter.get('/sitemap-chapters.xml', async (c) => {
  const rows = await publishedContent(c);
  return xmlResponse(urlSet(rows
    .filter((row) => Boolean(row.notesEn?.trim()))
    .map((row) => urlEntry(contentUrl(row), date(row.updatedAt), '0.7'))));
});

seoRouter.get('/sitemap-topics.xml', async (c) => {
  const rows = await publishedContent(c);
  const entries: string[] = [];
  for (const row of rows) {
    try {
      const topics = JSON.parse(row.publishedTopics ?? '[]') as Array<{ slug?: string; topic_slug?: string }>;
      for (const topic of topics) {
        const slug = topic.topic_slug ?? topic.slug;
        if (slug) entries.push(urlEntry(`${contentUrl(row)}/topic/${encodeURIComponent(slug)}`, date(row.updatedAt), '0.6'));
      }
    } catch {
      // Bad legacy topic JSON must not make the complete sitemap unavailable.
    }
  }
  return xmlResponse(urlSet(entries));
});

seoRouter.get('/robots.txt', (c) => c.text(`User-agent: *
Allow: /

User-agent: CCBot
Disallow: /

User-agent: Bytespider
Disallow: /

Sitemap: ${SITE_URL}/sitemap-index.xml
Sitemap: ${SITE_URL}/sitemap-static.xml
Sitemap: ${SITE_URL}/sitemap-subjects.xml
Sitemap: ${SITE_URL}/sitemap-chapters.xml
Sitemap: ${SITE_URL}/sitemap-topics.xml
`, 200, { 'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': 'public, max-age=86400' }));

function rss(items: ContentUrl[], title = 'Syrabit.ai - Study Notes & Exam Prep', selfUrl = `${SITE_URL}/feed.xml`): string {
  const entries = items.slice(0, 50).map((row) => {
    const url = contentUrl(row);
    const description = (row.notesEn ?? '').replace(/\s+/g, ' ').slice(0, 500);
    return `    <item>
      <title>${xml(row.title || 'Untitled')}</title>
      <link>${xml(url)}</link>
      <description>${xml(description)}</description>
      <pubDate>${rfc822(row.updatedAt ?? row.createdAt)}</pubDate>
      <guid isPermaLink="true">${xml(url)}</guid>
    </item>`;
  });
  return `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>${xml(title)}</title>
    <link>${SITE_URL}</link>
    <description>Latest study notes, definitions, and exam prep for Assam Board students</description>
    <language>en-in</language>
    <lastBuildDate>${rfc822(null)}</lastBuildDate>
    <atom:link href="${xml(selfUrl)}" rel="self" type="application/rss+xml" />
${entries.join('\n')}
  </channel>
</rss>`;
}

seoRouter.get('/feed.xml', async (c) => xmlResponse(await publishedContent(c).then((rows) => rss(rows)), RSS_CONTENT_TYPE));
// This is the public "study notes" feed linked from the frontend. It is an
// all-subject feed, not a subject whose slug happens to be "notes".
seoRouter.get('/feed/notes.xml', async (c) => xmlResponse(
  await publishedContent(c).then((rows) =>
    rss(rows, 'Syrabit.ai - Study Notes & Exam Prep', `${SITE_URL}/feed/notes.xml`),
  ),
  RSS_CONTENT_TYPE,
));
seoRouter.get('/feed/:subjectSlug.xml', async (c) => {
  const subjectSlug = c.req.param('subjectSlug') ?? '';
  const rows = await publishedContent(c, subjectSlug);
  return xmlResponse(rss(rows, `Syrabit.ai - ${subjectSlug.replaceAll('-', ' ')}`, `${SITE_URL}/feed/${subjectSlug}.xml`), RSS_CONTENT_TYPE);
});

seoRouter.get('/feed.json', async (c) => {
  const rows = await publishedContent(c);
  return new Response(JSON.stringify({
    version: 'https://jsonfeed.org/version/1.1',
    title: 'Syrabit.ai - Study Notes & Exam Prep',
    home_page_url: SITE_URL,
    feed_url: `${SITE_URL}/api/v1/seo/feed.json`,
    description: 'Latest study notes, definitions, and exam prep for Assam Board students',
    language: 'en-IN',
    items: rows.slice(0, 50).map((row) => ({
      id: contentUrl(row),
      url: contentUrl(row),
      title: row.title,
      content_text: (row.notesEn ?? '').slice(0, 500),
      tags: [],
      date_published: new Date((row.createdAt ?? row.updatedAt ?? Math.floor(Date.now() / 1000)) * 1000).toISOString(),
      date_modified: new Date((row.updatedAt ?? row.createdAt ?? Math.floor(Date.now() / 1000)) * 1000).toISOString(),
    })),
  }), {
    headers: {
      'Content-Type': 'application/feed+json; charset=utf-8',
      'Cache-Control': 'public, max-age=600, s-maxage=600',
    },
  });
});

seoRouter.get('/llms.txt', async (c) => {
  const count = (await publishedContent(c)).length;
  return c.text(`# Syrabit.ai

> Syrabit.ai is the educational browser for AHSEC, SEBA, Degree, FYUGP, and NEP students in Assam.

Technology stack: Cloudflare Workers, D1, R2, KV, Vectorize, Workers AI, and Cloudflare Pages.

Total published chapters: ${count}
Full content index: ${SITE_URL}/llms-full.txt

Explore:
- ${SITE_URL}/library
- ${SITE_URL}/technology
- ${SITE_URL}/about

Contact: founder@syrabit.ai
`, 200, { 'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': 'public, max-age=600, s-maxage=600' });
});

seoRouter.get('/llms-full.txt', async (c) => {
  const rows = await publishedContent(c);
  const lines = rows.map((row) => `- [${row.title || 'Untitled'}](${contentUrl(row)})`);
  return c.text(`# Syrabit.ai — Full Content Index

Total indexed chapters: ${rows.length}

${lines.join('\n')}
`, 200, { 'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': 'public, max-age=600, s-maxage=600' });
});

seoRouter.get('/health', async (c) => {
  const rows = await publishedContent(c);
  const withNotes = rows.filter((row) => (row.notesEn?.trim().length ?? 0) > 50).length;
  const total = rows.length;
  return c.json({
    ok: true,
    score: total === 0 ? 100 : Math.round((withNotes / total) * 100),
    checked: total,
    failed_urls: [],
    banner: { severity: 'ok', message: total === 0 ? 'No published chapters yet.' : 'D1 sitemap data is healthy.' },
    breakdown: { total, with_notes: withNotes, with_assamese: 0, with_rag: 0 },
    probed_at: new Date().toISOString(),
  });
});