/**
 * Public content routes — D1-backed, no auth required.
 *
 * Implements the exact API contract served by the Cloud Run / MongoDB backend
 * at /api/v1/content/*, preserving field names, response shapes, and URL
 * patterns so the frontend and CDN do not need changes after the cutover.
 *
 * Routes implemented here (Phase 6 — all public student-facing content):
 *   GET /boards
 *   GET /classes?board_id=
 *   GET /streams?class_id=
 *   GET /subjects?stream_id=&board_id=
 *   GET /subjects/:id
 *   GET /chapters/:subjectId        ← Cloud Run lists chapters by SUBJECT id
 *   GET /chapter-by-slug/:board/:classSlug/:subjectSlug/:chapterSlug
 *   GET /chapter-by-slug/:board/:classSlug/:streamSlug/:subjectSlug/:chapterSlug
 *   GET /chapter-by-slug-as/:board/:classSlug/:subjectSlug/:chapterSlug
 *   GET /chapter-by-slug-as/:board/:classSlug/:streamSlug/:subjectSlug/:chapterSlug
 *
 * Security: all chapter detail routes enforce is_published=1 on the parent
 * subject so unpublished chapters never reach unauthenticated callers.
 */

import { Hono, type Context } from 'hono';
import { eq, and } from 'drizzle-orm';
import { createDb } from '../db/client';
import { boards, classes, streams, subjects, chapters } from '../db/schema';
import type { Env } from '../types';

export const contentRouter = new Hono<{ Bindings: Env }>();

// ── Boards ─────────────────────────────────────────────────────────────────────
// GET /api/v1/content/boards → [{ id, name, slug, status }]

contentRouter.get('/boards', async (c) => {
  const db = createDb(c.env.DB);
  const rows = await db.select({
    id: boards.id,
    name: boards.name,
    slug: boards.slug,
    status: boards.status,
  }).from(boards);

  c.header('Cache-Control', 'public, max-age=300, s-maxage=600');
  return c.json(rows.map(b => ({
    id: b.id,
    name: b.name,
    slug: b.slug,
    status: b.status ?? 'published',
  })));
});

// ── Classes ────────────────────────────────────────────────────────────────────
// GET /api/v1/content/classes?board_id= → [{ id, name, board_id, status }]

contentRouter.get('/classes', async (c) => {
  const db = createDb(c.env.DB);
  const boardId = c.req.query('board_id');

  const rows = await db.select({
    id: classes.id,
    name: classes.name,
    boardId: classes.boardId,
    status: classes.status,
  }).from(classes)
    .where(boardId ? eq(classes.boardId, boardId) : undefined);

  c.header('Cache-Control', 'public, max-age=300, s-maxage=600');
  return c.json(rows.map(r => ({
    id: r.id,
    name: r.name,
    board_id: r.boardId,
    status: r.status ?? 'published',
  })));
});

// ── Streams ────────────────────────────────────────────────────────────────────
// GET /api/v1/content/streams?class_id= → [{ id, name, class_id, status }]

contentRouter.get('/streams', async (c) => {
  const db = createDb(c.env.DB);
  const classId = c.req.query('class_id');

  const rows = await db.select({
    id: streams.id,
    name: streams.name,
    classId: streams.classId,
    status: streams.status,
  }).from(streams)
    .where(classId ? eq(streams.classId, classId) : undefined);

  c.header('Cache-Control', 'public, max-age=300, s-maxage=600');
  return c.json(rows.map(r => ({
    id: r.id,
    name: r.name,
    class_id: r.classId,
    status: r.status ?? 'published',
  })));
});

// ── Subjects list ──────────────────────────────────────────────────────────────
// GET /api/v1/content/subjects?stream_id=&board_id=
// → [{ id, name, slug, stream_id, status, description, icon, thumbnail_url, tags }]

contentRouter.get('/subjects', async (c) => {
  const db = createDb(c.env.DB);
  const streamId = c.req.query('stream_id');
  const boardId  = c.req.query('board_id');

  // Build WHERE: always restrict to published subjects
  let condition;
  if (streamId) {
    condition = and(eq(subjects.isPublished, 1), eq(subjects.streamId, streamId));
  } else if (boardId) {
    // board_id filter: join through streams → classes to board
    // For now return published subjects in streams that belong to this board,
    // resolved via an inner select against the streams/classes hierarchy.
    const streamRows = await db.select({ id: streams.id })
      .from(streams)
      .innerJoin(classes, eq(streams.classId, classes.id))
      .where(eq(classes.boardId, boardId));
    const streamIds = streamRows.map(r => r.id);
    if (streamIds.length === 0) {
      c.header('Cache-Control', 'public, max-age=300, s-maxage=600');
      return c.json([]);
    }
    // SQLite doesn't support inArray nicely in drizzle; use raw SQL fallback
    const placeholders = streamIds.map(() => '?').join(',');
    const raw = await c.env.DB
      .prepare(`SELECT id, name, slug, stream_id, description, image_url, is_published
                FROM subjects WHERE is_published=1 AND stream_id IN (${placeholders})`)
      .bind(...streamIds)
      .all<{ id: string; name: string; slug: string; stream_id: string | null; description: string | null; image_url: string | null; is_published: number }>();
    c.header('Cache-Control', 'public, max-age=300, s-maxage=600');
    return c.json((raw.results ?? []).map(r => ({
      id: r.id,
      name: r.name,
      slug: r.slug,
      stream_id: r.stream_id ?? null,
      status: r.is_published ? 'published' : 'draft',
      description: r.description ?? null,
      icon: null,
      thumbnail_url: r.image_url ?? null,
      tags: [],
    })));
  } else {
    condition = eq(subjects.isPublished, 1);
  }

  const rows = await db.select({
    id: subjects.id,
    name: subjects.name,
    slug: subjects.slug,
    streamId: subjects.streamId,
    description: subjects.description,
    imageUrl: subjects.imageUrl,
    isPublished: subjects.isPublished,
  }).from(subjects).where(condition);

  c.header('Cache-Control', 'public, max-age=300, s-maxage=600');
  return c.json(rows.map(r => ({
    id: r.id,
    name: r.name,
    slug: r.slug,
    stream_id: r.streamId ?? null,
    status: r.isPublished ? 'published' : 'draft',
    description: r.description ?? null,
    icon: null,             // not migrated to D1 schema
    thumbnail_url: r.imageUrl ?? null,
    tags: [],
  })));
});

// ── Subject detail ─────────────────────────────────────────────────────────────
// GET /api/v1/content/subjects/:id
// → { id, name, slug, description, tags, icon, gradient, thumbnailUrl,
//     has_document, status, pyq_papers }

contentRouter.get('/subjects/:id', async (c) => {
  const db = createDb(c.env.DB);
  const id = c.req.param('id');

  const row = await db.select({
    id: subjects.id,
    name: subjects.name,
    slug: subjects.slug,
    description: subjects.description,
    imageUrl: subjects.imageUrl,
    pyqPapers: subjects.pyqPapers,
    isPublished: subjects.isPublished,
  }).from(subjects)
    .where(and(eq(subjects.id, id), eq(subjects.isPublished, 1)))
    .get();

  if (!row) return c.json({ detail: 'Subject not found' }, 404);

  let pyqPapersArr: unknown[] = [];
  try { pyqPapersArr = JSON.parse(row.pyqPapers ?? '[]') as unknown[]; } catch { /* leave empty */ }

  c.header('Cache-Control', 'public, max-age=60, s-maxage=300');
  return c.json({
    id: row.id,
    name: row.name,
    slug: row.slug,
    description: row.description ?? null,
    tags: [],
    icon: null,
    gradient: null,
    thumbnailUrl: row.imageUrl ?? null,
    has_document: false,
    status: row.isPublished ? 'published' : 'draft',
    pyq_papers: pyqPapersArr,
  });
});

// ── Chapters list ──────────────────────────────────────────────────────────────
// GET /api/v1/content/chapters/:subjectId
// → [{ chapter_id, title, title_as, slug, chapter_number,
//      notes_generated, has_assamese }]
//
// Cloud Run returns ALL chapters for a (published) subject sorted by
// chapter_number. Security: we validate the subject is published first so
// draft subjects cannot be enumerated via this endpoint.

contentRouter.get('/chapters/:subjectId', async (c) => {
  const db = createDb(c.env.DB);
  const subjectId = c.req.param('subjectId');

  // ── Enforce published-subject gate FIRST (before any cache) ───────────────
  // Must run on every request so unpublishing a subject immediately stops
  // access, regardless of what is stored in KV.
  const subjectRow = await db.select({ id: subjects.id })
    .from(subjects)
    .where(and(eq(subjects.id, subjectId), eq(subjects.isPublished, 1)))
    .get();

  if (!subjectRow) return c.json({ detail: 'Subject not found' }, 404);

  // ── KV cache-aside: populated by staff kvPrewarm on publish ───────────────
  // Key written by apps/api/src/routes/staff.ts kvPrewarm().
  // TTL = 7 days; invalidated on subject unpublish and chapter publish/delete.
  const kvKey = `subject:${subjectId}:chapters`;
  const cached = await c.env.CONTENT_KV.get(kvKey);
  if (cached) {
    c.header('Cache-Control', 'public, max-age=60, s-maxage=300');
    c.header('X-Cache', 'HIT');
    return c.json(JSON.parse(cached));
  }

  const rows = await db.select({
    id: chapters.id,
    title: chapters.title,
    slug: chapters.slug,
    slugAs: chapters.slugAs,
    chapterNumber: chapters.chapterNumber,
    notesEn: chapters.notesEn,
    notesAs: chapters.notesAs,
  }).from(chapters)
    .where(eq(chapters.subjectId, subjectId))
    .orderBy(chapters.chapterNumber);

  const payload = rows.map(r => ({
    chapter_id:      r.id,
    title:           r.title,
    title_as:        null,           // not in D1 schema (field not migrated)
    slug:            r.slug,
    chapter_number:  r.chapterNumber ?? null,
    notes_generated: Boolean(r.notesEn),
    has_assamese:    Boolean(r.notesAs),
  }));

  // Populate KV so the next request is served from cache
  c.env.CONTENT_KV.put(kvKey, JSON.stringify(payload), { expirationTtl: 86400 * 7 })
    .catch(() => { /* best-effort */ });

  c.header('Cache-Control', 'public, max-age=60, s-maxage=300');
  c.header('X-Cache', 'MISS');
  return c.json(payload);
});

// ── Chapter-by-slug resolution ─────────────────────────────────────────────────
// Cloud Run URL patterns:
//   GET /chapter-by-slug/{board}/{class_slug}/{subject_slug}/{chapter_slug}
//   GET /chapter-by-slug/{board}/{class_slug}/{stream_slug}/{subject_slug}/{chapter_slug}
//   GET /chapter-by-slug-as/{board}/{class_slug}/{subject_slug}/{chapter_slug}
//   GET /chapter-by-slug-as/{board}/{class_slug}/{stream_slug}/{subject_slug}/{chapter_slug}
//
// The 4-segment variant tries all streams in the class; the 5-segment variant
// pins a specific stream segment. Cloud Run originally used _slugify(name) for
// class/stream/subject matching; D1 stores the slug column computed the same way.

async function resolveChapterBySlug(
  c: Context<{ Bindings: Env }>,
  useSlugAs: boolean,
  hasStreamSegment = false,
): Promise<Response> {
  const db = createDb(c.env.DB);
  // Hono guarantees route params are always present strings; cast to string
  const params = c.req.param() as Record<string, string>;
  const boardSlug    = params.board      as string;
  const classSlug    = params.classSlug  as string;
  const streamSlug   = hasStreamSegment ? (params.streamSlug  as string) : null;
  const subjectSlug  = hasStreamSegment ? (params.subjectSlug as string) : ((params.streamSlug ?? params.subjectSlug) as string);
  const chapterSlug  = hasStreamSegment ? (params.chapterSlug as string) : ((params.chapterSlug ?? params.subjectSlug) as string);

  // 1. Board
  const boardRow = await db.select({ id: boards.id, name: boards.name, slug: boards.slug })
    .from(boards).where(eq(boards.slug, boardSlug)).get();
  if (!boardRow) return c.json({ detail: `Board '${boardSlug}' not found` }, 404);

  // 2. Class within board
  const classRow = await db.select({ id: classes.id, name: classes.name, slug: classes.slug })
    .from(classes)
    .where(and(eq(classes.boardId, boardRow.id), eq(classes.slug, classSlug)))
    .get();
  if (!classRow) return c.json({ detail: `Class '${classSlug}' not found` }, 404);

  // 3. Stream(s) within class
  const allStreams = await db.select({ id: streams.id, name: streams.name, slug: streams.slug })
    .from(streams)
    .where(eq(streams.classId, classRow.id));

  if (allStreams.length === 0) return c.json({ detail: 'No streams found for class' }, 404);

  const targetStreams = streamSlug
    ? allStreams.filter(s => s.slug === streamSlug)
    : allStreams;

  if (streamSlug && targetStreams.length === 0) {
    return c.json({ detail: `Stream '${streamSlug}' not found` }, 404);
  }

  // 4. Subject by slug within resolved streams
  const streamIds = targetStreams.map(s => s.id);
  const placeholders = streamIds.map(() => '?').join(',');
  const subjectRows = await c.env.DB
    .prepare(`SELECT id, name, slug, stream_id FROM subjects
              WHERE is_published=1 AND stream_id IN (${placeholders})`)
    .bind(...streamIds)
    .all<{ id: string; name: string; slug: string; stream_id: string | null }>();

  const subjectRow = (subjectRows.results ?? []).find(
    s => s.slug === subjectSlug,
  );
  if (!subjectRow) {
    return c.json({ detail: `Subject '${subjectSlug}' not found` }, 404);
  }

  // 5. All chapters for this subject sorted by chapter_number
  const chapterRows = await db.select({
    id: chapters.id,
    title: chapters.title,
    slug: chapters.slug,
    slugAs: chapters.slugAs,
    chapterNumber: chapters.chapterNumber,
    status: chapters.status,
    notesEn: chapters.notesEn,
    notesAs: chapters.notesAs,
    publishedTopics: chapters.publishedTopics,
    qaEn: chapters.qaEn,
    qaAs: chapters.qaAs,
    wordCountEn: chapters.wordCountEn,
    pyqPdfUrl: chapters.pyqPdfUrl,
    pyqPapers: chapters.pyqPapers,
    createdAt: chapters.createdAt,
    updatedAt: chapters.updatedAt,
  }).from(chapters)
    .where(eq(chapters.subjectId, subjectRow.id))
    .orderBy(chapters.chapterNumber);

  // Match chapter by slug or slug_as
  const chapterRow = chapterRows.find(ch => {
    if (useSlugAs) return ch.slugAs === chapterSlug || ch.slug === chapterSlug;
    return ch.slug === chapterSlug;
  });
  if (!chapterRow) {
    return c.json({ detail: `Chapter '${chapterSlug}' not found` }, 404);
  }

  // Resolve which stream owns this subject
  const owningStream = targetStreams.find(s => s.id === subjectRow.stream_id);

  // Build prev/next
  const chIdx = chapterRows.findIndex(ch => ch.id === chapterRow.id);
  const prevCh = chIdx > 0 ? chapterRows[chIdx - 1] : null;
  const nextCh  = chIdx < chapterRows.length - 1 ? chapterRows[chIdx + 1] : null;

  // Content fields: notesEn is the primary source; fall back to empty string
  const contentEn = chapterRow.notesEn ?? '';
  const contentAs = chapterRow.notesAs ?? '';
  const hasAssamese = Boolean(contentAs);

  // For Assamese chapter with no content: signal CDN to not cache/index
  if (useSlugAs && !hasAssamese) {
    c.header('Cache-Control', 'no-store');
    c.header('X-Robots-Tag', 'noindex');
  } else {
    c.header('Cache-Control', 'public, max-age=60, s-maxage=300');
  }

  // Parse JSON fields
  let topicsArr: unknown[] = [];
  let faqArr: unknown[] = [];
  try { topicsArr = JSON.parse(chapterRow.publishedTopics ?? '[]') as unknown[]; } catch { /* leave empty */ }

  // topic_title: first published topic title or chapter title (matches Cloud Run logic)
  const topicTitle = (topicsArr.length > 0 && typeof (topicsArr[0] as Record<string, unknown>)?.title === 'string')
    ? (topicsArr[0] as Record<string, unknown>).title as string
    : chapterRow.title;

  return c.json({
    chapter_id:     chapterRow.id,
    title:          chapterRow.title,
    chapter_title:  chapterRow.title,
    chapter_slug:   chapterRow.slug,
    slug_as:        chapterRow.slugAs ?? null,
    topic_title:    topicTitle,
    subject_name:   subjectRow.name,
    subject_slug:   subjectRow.slug,
    board_name:     boardRow.name,
    board_slug:     boardRow.slug,
    class_name:     classRow.name,
    class_slug:     classRow.slug,
    stream_name:    owningStream?.name ?? '',
    stream_slug:    owningStream?.slug ?? '',
    content:        contentEn,
    content_as:     contentAs,
    content_type:   'chapter',
    has_assamese:   hasAssamese,
    pyq_pdf_url:    chapterRow.pyqPdfUrl ?? null,
    pyq_papers:     safeParse(chapterRow.pyqPapers) ?? [],
    meta_description: null,
    word_count:     chapterRow.wordCountEn ?? (contentEn ? contentEn.split(' ').length : 0),
    notes_generated: Boolean(contentEn || contentAs),
    chapter_number:  chapterRow.chapterNumber ?? null,
    topics:          topicsArr,
    faq_jsonld:      faqArr,
    faq_entries:     faqArr,
    prev_chapter:    prevCh ? {
      chapter_id:    prevCh.id,
      title:         prevCh.title,
      slug:          prevCh.slug,
      chapter_number: prevCh.chapterNumber ?? null,
    } : null,
    next_chapter:    nextCh ? {
      chapter_id:    nextCh.id,
      title:         nextCh.title,
      slug:          nextCh.slug,
      chapter_number: nextCh.chapterNumber ?? null,
    } : null,
    generated_at:    chapterRow.createdAt
      ? new Date(chapterRow.createdAt * 1000).toISOString()
      : null,
    updated_at:      chapterRow.updatedAt
      ? new Date(chapterRow.updatedAt * 1000).toISOString()
      : null,
  });
}

// Register all four chapter-by-slug variants
contentRouter.get('/chapter-by-slug/:board/:classSlug/:subjectSlug/:chapterSlug',
  (c) => resolveChapterBySlug(c, false, false));

contentRouter.get('/chapter-by-slug/:board/:classSlug/:streamSlug/:subjectSlug/:chapterSlug',
  (c) => resolveChapterBySlug(c, false, true));

contentRouter.get('/chapter-by-slug-as/:board/:classSlug/:subjectSlug/:chapterSlug',
  (c) => resolveChapterBySlug(c, true, false));

contentRouter.get('/chapter-by-slug-as/:board/:classSlug/:streamSlug/:subjectSlug/:chapterSlug',
  (c) => resolveChapterBySlug(c, true, true));

// ── Helper: safe JSON parse ───────────────────────────────────────────────────
function safeParse<T = unknown>(json: string | null | undefined): T | null {
  if (!json) return null;
  try { return JSON.parse(json) as T; } catch { return null; }
}

// ── GET /library-bundle ────────────────────────────────────────────────────────
// Critical pre-load endpoint — the frontend requests this at app start.
//   ?slim=1  → boards/classes/streams/subjects only (no chapters)
//   ?boot=<boardId> → slim metadata + chapters for the specified board only
//   (no params) → full hierarchy including all chapters
//
// Response shape: { boards, classes, streams, subjects, chapters? }
// boards entries carry nested classes → streams → subjects
// subjects carry chapter_count and pyq_papers from D1

contentRouter.get('/library-bundle', async (c) => {
  const db = createDb(c.env.DB);
  const slim = c.req.query('slim') === '1';
  const boot = c.req.query('boot') ?? null; // board DB id for boot mode

  // Load base hierarchy data in parallel
  const [allBoards, allClasses, allStreams, allSubjects] = await Promise.all([
    db.select({ id: boards.id, name: boards.name, slug: boards.slug }).from(boards),
    db.select({ id: classes.id, boardId: classes.boardId, name: classes.name, slug: classes.slug, level: classes.level }).from(classes),
    db.select({ id: streams.id, classId: streams.classId, name: streams.name, slug: streams.slug }).from(streams),
    db.select({
      id: subjects.id, streamId: subjects.streamId, name: subjects.name, slug: subjects.slug,
      description: subjects.description, imageUrl: subjects.imageUrl,
      pyqPapers: subjects.pyqPapers, isPublished: subjects.isPublished,
    }).from(subjects).where(eq(subjects.isPublished, 1)),
  ]);

  // Determine which subjects belong to the boot board (if boot mode)
  let bootSubjectIds: Set<string> | null = null;
  if (boot) {
    const bootClasses = allClasses.filter(c => c.boardId === boot);
    const bootClassIds = new Set(bootClasses.map(c => c.id));
    const bootStreams  = allStreams.filter(s => bootClassIds.has(s.classId));
    const bootStreamIds = new Set(bootStreams.map(s => s.id));
    bootSubjectIds = new Set(allSubjects.filter(s => s.streamId && bootStreamIds.has(s.streamId)).map(s => s.id));
  }

  // Load chapters when needed (full mode or boot mode)
  type ChapterRow = { id: string; subjectId: string; title: string; slug: string; slugAs: string | null;
    chapterNumber: number | null; status: string | null; contentType: string | null;
    notesEn: string | null; notesAs: string | null; qaEn: string | null; publishedTopics: string | null; };

  let allChapters: ChapterRow[] = [];
  if (!slim) {
    allChapters = await db.select({
      id: chapters.id, subjectId: chapters.subjectId,
      title: chapters.title, slug: chapters.slug, slugAs: chapters.slugAs,
      chapterNumber: chapters.chapterNumber, status: chapters.status, contentType: chapters.contentType,
      notesEn: chapters.notesEn, notesAs: chapters.notesAs,
      qaEn: chapters.qaEn, publishedTopics: chapters.publishedTopics,
    }).from(chapters).where(eq(chapters.status, 'published'));
  }

  // Build chapter maps: subjectId → chapter list
  const chaptersBySubject = new Map<string, unknown[]>();
  const allPublishedSubjectIds = new Set(allSubjects.map(s => s.id));

  for (const ch of allChapters) {
    if (!allPublishedSubjectIds.has(ch.subjectId)) continue;
    // In boot mode only include chapters for the boot board
    if (bootSubjectIds && !bootSubjectIds.has(ch.subjectId)) continue;

    const topicsArr = safeParse<unknown[]>(ch.publishedTopics) ?? [];
    const entry = {
      chapter_id: ch.id,
      title: ch.title,
      title_as: null,  // field not present in D1 schema (MongoDB had it optionally)
      slug: ch.slug,
      slug_as: ch.slugAs ?? null,
      chapter_number: ch.chapterNumber ?? null,
      subject_id: ch.subjectId,
      status: ch.status ?? 'published',
      content_type: ch.contentType ?? 'standard',
      notes_generated: !!(ch.notesEn && ch.notesEn.length > 10),
      has_assamese: !!(ch.notesAs && ch.notesAs.length > 10),
      has_qa: ch.qaEn !== '[]' && ch.qaEn != null,
      topic_count: topicsArr.length,
      pyq_papers: [],
    };
    const list = chaptersBySubject.get(ch.subjectId) ?? [];
    list.push(entry);
    chaptersBySubject.set(ch.subjectId, list);
  }

  // Build subject list (flat + with chapter_count)
  const buildSubject = (sub: typeof allSubjects[0], includeChapters: boolean) => {
    const chaps = chaptersBySubject.get(sub.id) ?? [];
    return {
      id: sub.id,
      name: sub.name,
      slug: sub.slug,
      stream_id: sub.streamId ?? null,
      status: sub.isPublished ? 'published' : 'draft',
      description: sub.description ?? null,
      icon: null,
      gradient: null,
      thumbnail_url: sub.imageUrl ?? null,
      tags: [],
      chapter_count: chaps.length,
      pyq_papers: safeParse(sub.pyqPapers) ?? [],
      ...(includeChapters ? { chapters: chaps } : {}),
    };
  };

  // Subjects indexed by stream for hierarchy build
  const subjectsByStream = new Map<string, ReturnType<typeof buildSubject>[]>();
  for (const sub of allSubjects) {
    const key = sub.streamId ?? '__no_stream__';
    const list = subjectsByStream.get(key) ?? [];
    list.push(buildSubject(sub, !slim));
    subjectsByStream.set(key, list);
  }

  // Streams with subjects
  const streamWithSubjects = allStreams.map(str => ({
    id: str.id,
    class_id: str.classId,
    name: str.name,
    slug: str.slug,
    status: 'published',
    subjects: subjectsByStream.get(str.id) ?? [],
  }));
  const streamsByClass = new Map<string, typeof streamWithSubjects[0][]>();
  for (const str of streamWithSubjects) {
    const list = streamsByClass.get(str.class_id) ?? [];
    list.push(str);
    streamsByClass.set(str.class_id, list);
  }

  // Classes with streams
  const classWithStreams = allClasses.map(cls => ({
    id: cls.id,
    board_id: cls.boardId,
    name: cls.name,
    slug: cls.slug,
    level: cls.level ?? null,
    status: 'published',
    streams: streamsByClass.get(cls.id) ?? [],
  }));
  const classesByBoard = new Map<string, typeof classWithStreams[0][]>();
  for (const cls of classWithStreams) {
    const list = classesByBoard.get(cls.board_id) ?? [];
    list.push(cls);
    classesByBoard.set(cls.board_id, list);
  }

  // Boards with nested hierarchy
  const boardHierarchy = allBoards.map(b => ({
    id: b.id,
    name: b.name,
    slug: b.slug,
    status: 'published',
    classes: classesByBoard.get(b.id) ?? [],
  }));

  // Flat subject list (always included in response)
  const flatSubjects = allSubjects.map(s => buildSubject(s, false));

  // Flat chapter list (full mode only — not returned in slim/boot)
  const flatChapters = slim ? undefined
    : [...chaptersBySubject.values()].flat();

  c.header('Cache-Control', 'public, max-age=120, s-maxage=300');
  return c.json({
    boards: boardHierarchy,
    classes: allClasses.map(cls => ({ id: cls.id, board_id: cls.boardId, name: cls.name, slug: cls.slug, level: cls.level ?? null, status: 'published' })),
    streams: allStreams.map(str => ({ id: str.id, class_id: str.classId, name: str.name, slug: str.slug, status: 'published' })),
    subjects: flatSubjects,
    ...(flatChapters !== undefined ? { chapters: flatChapters } : {}),
  });
});

// ── GET /resolve-subject/:board/:classSlug/:subjectSlug ────────────────────────
// Resolves a subject by board/class/subject slugs — used by navigation hooks.
// Returns full subject metadata + breadcrumb context (board_name, class_name, etc.)

contentRouter.get('/resolve-subject/:board/:classSlug/:subjectSlug', async (c) => {
  const db = createDb(c.env.DB);
  const boardSlug   = c.req.param('board')       as string;
  const classSlug   = c.req.param('classSlug')   as string;
  const subjectSlug = c.req.param('subjectSlug') as string;

  // Board
  const boardRow = await db.select({ id: boards.id, name: boards.name, slug: boards.slug })
    .from(boards).where(eq(boards.slug, boardSlug)).get();
  if (!boardRow) return c.json({ detail: `Board '${boardSlug}' not found` }, 404);

  // Class within board
  const classRow = await db.select({ id: classes.id, name: classes.name, slug: classes.slug })
    .from(classes).where(and(eq(classes.boardId, boardRow.id), eq(classes.slug, classSlug))).get();
  if (!classRow) return c.json({ detail: `Class '${classSlug}' not found` }, 404);

  // Streams within class
  const allStreams = await db.select({ id: streams.id, name: streams.name, slug: streams.slug })
    .from(streams).where(eq(streams.classId, classRow.id));

  // Find subject across all streams in this class
  let subjectRow: (typeof subjects.$inferSelect) | undefined;
  let owningStream: { id: string; name: string; slug: string } | undefined;

  for (const str of allStreams) {
    const found = await db.select().from(subjects)
      .where(and(eq(subjects.streamId, str.id), eq(subjects.slug, subjectSlug), eq(subjects.isPublished, 1)))
      .get();
    if (found) { subjectRow = found; owningStream = str; break; }
  }

  if (!subjectRow) return c.json({ detail: `Subject '${subjectSlug}' not found` }, 404);

  // Chapter count
  const chapterCountResult = await c.env.DB.prepare(
    `SELECT COUNT(*) as cnt FROM chapters WHERE subject_id = ? AND status = 'published'`
  ).bind(subjectRow.id).first<{ cnt: number }>();

  return c.json({
    id: subjectRow.id,
    name: subjectRow.name,
    slug: subjectRow.slug,
    description: subjectRow.description ?? null,
    tags: [],
    icon: null,
    gradient: null,
    thumbnailUrl: subjectRow.imageUrl ?? null,
    thumbnail_url: subjectRow.imageUrl ?? null,
    has_document: false,
    seo_stats: null,
    status: subjectRow.isPublished ? 'published' : 'draft',
    board_name: boardRow.name,
    board_slug: boardRow.slug,
    class_name: classRow.name,
    class_slug: classRow.slug,
    stream_name: owningStream?.name ?? '',
    stream_slug: owningStream?.slug ?? '',
    chapter_count: chapterCountResult?.cnt ?? 0,
    pyq_papers: safeParse(subjectRow.pyqPapers) ?? [],
  });
});

// ── Chapter sub-routes ─────────────────────────────────────────────────────────
// These read published topics / Q&A stored as JSON in the D1 chapters table.

contentRouter.get('/chapters/:chapterId/topics-published', async (c) => {
  const db = createDb(c.env.DB);
  const chapterId = c.req.param('chapterId') as string;

  const ch = await db.select({ id: chapters.id, publishedTopics: chapters.publishedTopics })
    .from(chapters).where(eq(chapters.id, chapterId)).get();
  if (!ch) return c.json({ detail: 'Chapter not found' }, 404);

  const topics = safeParse<unknown[]>(ch.publishedTopics) ?? [];
  return c.json({ chapter_id: ch.id, topics, total: topics.length });
});

contentRouter.get('/chapters/:chapterId/topics-related', async (c) => {
  const db = createDb(c.env.DB);
  const chapterId = c.req.param('chapterId') as string;
  const limit = Math.min(50, parseInt(c.req.query('limit') ?? '12', 10));

  const ch = await db.select({ id: chapters.id, subjectId: chapters.subjectId, publishedTopics: chapters.publishedTopics })
    .from(chapters).where(eq(chapters.id, chapterId)).get();
  if (!ch) return c.json({ detail: 'Chapter not found' }, 404);

  // Related = topics from sibling chapters in the same subject
  const siblings = await db.select({ id: chapters.id, title: chapters.title, slug: chapters.slug, publishedTopics: chapters.publishedTopics })
    .from(chapters)
    .where(and(eq(chapters.subjectId, ch.subjectId), eq(chapters.status, 'published')))
    .limit(20);

  type TopicEntry = { title?: string; slug?: string; chapter_id?: string; chapter_title?: string };
  const relatedTopics: TopicEntry[] = [];
  for (const sib of siblings) {
    if (sib.id === chapterId) continue;
    const tops = safeParse<TopicEntry[]>(sib.publishedTopics) ?? [];
    for (const t of tops.slice(0, 3)) {
      relatedTopics.push({ ...t, chapter_id: sib.id, chapter_title: sib.title });
      if (relatedTopics.length >= limit) break;
    }
    if (relatedTopics.length >= limit) break;
  }

  return c.json({ chapter_id: ch.id, related_topics: relatedTopics, total: relatedTopics.length });
});

contentRouter.get('/chapters/:chapterId/topic-pyqs', async (c) => {
  const db = createDb(c.env.DB);
  const chapterId = c.req.param('chapterId') as string;
  const lang  = c.req.query('lang') ?? 'en';

  const ch = await db.select({ id: chapters.id, qaEn: chapters.qaEn, qaAs: chapters.qaAs })
    .from(chapters).where(eq(chapters.id, chapterId)).get();
  if (!ch) return c.json({ detail: 'Chapter not found' }, 404);

  type QAItem = { id?: string; question?: string; answer?: string; marks?: number; year?: number; source?: string };
  const rawQA = lang === 'as'
    ? (safeParse<QAItem[]>(ch.qaAs) ?? safeParse<QAItem[]>(ch.qaEn) ?? [])
    : (safeParse<QAItem[]>(ch.qaEn) ?? []);

  // Build mark_wise grouping
  const markWise: Record<string, QAItem[]> = {};
  for (const item of rawQA) {
    const key = String(item.marks ?? 'unknown');
    if (!markWise[key]) markWise[key] = [];
    markWise[key].push(item);
  }

  return c.json({ chapter_id: ch.id, total: rawQA.length, pyqs: rawQA, mark_wise: markWise });
});

contentRouter.get('/chapters/:chapterId/pyq-images', async (c) => {
  // Return chapter-level PYQ papers uploaded via POST /staff/content/chapter/:id/pyq-papers.
  // Falls back to empty array when the chapter has no uploads or the column is absent
  // (pre-migration rows or Cloud Run–only chapters).
  const db = createDb(c.env.DB);
  const chapterId = c.req.param('chapterId') as string;
  const ch = await db.select({ pyqPapers: chapters.pyqPapers })
    .from(chapters).where(eq(chapters.id, chapterId)).get();
  const papers = safeParse(ch?.pyqPapers) ?? [];
  return c.json({ chapter_id: chapterId, papers, total: (papers as unknown[]).length });
});

contentRouter.get('/chapters/:chapterId/faq-jsonld', async (c) => {
  const db = createDb(c.env.DB);
  const chapterId = c.req.param('chapterId') as string;

  const ch = await db.select({ id: chapters.id, title: chapters.title, qaEn: chapters.qaEn })
    .from(chapters).where(eq(chapters.id, chapterId)).get();
  if (!ch) return c.json({ detail: 'Chapter not found' }, 404);

  type QAItem = { question?: string; answer?: string };
  const qaArr = safeParse<QAItem[]>(ch.qaEn) ?? [];

  const faqJsonLd = qaArr.length > 0 ? {
    '@context': 'https://schema.org',
    '@type': 'FAQPage',
    mainEntity: qaArr.slice(0, 20).map(q => ({
      '@type': 'Question',
      name: q.question ?? '',
      acceptedAnswer: { '@type': 'Answer', text: q.answer ?? '' },
    })),
  } : null;

  return c.json({ chapter_id: ch.id, faq_jsonld: faqJsonLd });
});

// ── GET /question-papers ───────────────────────────────────────────────────────
// Returns all PYQ papers from subjects.pyq_papers JSON field (migrated from MongoDB).
// Cloud Run shape: array of { id, title, subject, board, class_level, year, image_url, is_pdf }

contentRouter.get('/question-papers', async (c) => {
  const db = createDb(c.env.DB);

  const allSubjectRows = await db.select({
    id: subjects.id,
    name: subjects.name,
    pyqPapers: subjects.pyqPapers,
  }).from(subjects).where(eq(subjects.isPublished, 1));

  type PYQPaper = { id?: string; title?: string; subject?: string; board?: string | null;
    class_level?: string | null; year?: number | null; image_url?: string | null; is_pdf?: boolean; filename?: string };
  const papers: PYQPaper[] = [];

  for (const sub of allSubjectRows) {
    const subPapers = safeParse<PYQPaper[]>(sub.pyqPapers) ?? [];
    for (const p of subPapers) {
      papers.push({
        id: p.id ?? crypto.randomUUID(),
        title: p.title ?? `${sub.name} Question Paper`,
        subject: p.subject ?? sub.name,
        board: p.board ?? null,
        class_level: p.class_level ?? null,
        year: p.year ?? null,
        image_url: p.image_url ?? null,
        is_pdf: p.is_pdf ?? !!(p.filename),
      });
    }
  }

  c.header('Cache-Control', 'public, max-age=300, s-maxage=600');
  return c.json(papers);
});

// ── GET /cms-library, /cms/posts ───────────────────────────────────────────────
// CMS blog posts are not stored in D1 (authored in Cloud Run / MongoDB).
// Return empty response so the frontend shows "no articles" rather than erroring.

contentRouter.get('/cms-library',       (c) => c.json({ items: [], total: 0 }));
contentRouter.get('/cms/posts',         (c) => c.json({ items: [], total: 0 }));
contentRouter.get('/cms/personalize',   (c) => c.json({ recommendations: [], total: 0 }));

// Published CMS documents are stored as JSON records in D1 by the native admin
// CMS editor. Find by slug without exposing draft documents.
contentRouter.get('/cms-documents/:slug', async c => {
  const slug = c.req.param('slug') as string;
  const rows = await c.env.DB.prepare(
    `SELECT * FROM cms_documents WHERE status = 'published' ORDER BY updated_at DESC`,
  ).all<{ id: string; data: string; status: string; created_at: number; updated_at: number }>();
  const row = (rows.results ?? []).find(item => {
    try {
      const document = JSON.parse(item.data) as Record<string, unknown>;
      return document.seo_slug === slug || document.slug === slug;
    } catch { return false; }
  });
  if (!row) return c.json({ detail: `CMS document '${slug}' not found` }, 404);
  const data = JSON.parse(row.data) as Record<string, unknown>;
  return c.json({
    ...data, id: row.id, status: row.status,
    created_at: new Date(row.created_at * 1000).toISOString(),
    updated_at: new Date(row.updated_at * 1000).toISOString(),
  });
});

// Flashcards are a planned feature not yet seeded in D1 (no flashcard table/data).
// Return an empty response matching the expected shape so LearnPage renders
// "no flashcards available" rather than crashing on a 404.
contentRouter.get('/chapters/:chapterId/flashcards', (c) => {
  const chapterId = c.req.param('chapterId') as string;
  return c.json({ chapter_id: chapterId, flashcards: [], total: 0 });
});

// ── GET /search ────────────────────────────────────────────────────────────────
// Full-text search across chapter titles and subjects using SQLite LIKE.
// Cloud Run uses a Vertex AI Search engine; this D1 version uses title-matching
// as a lightweight fallback. The Vectorize semantic search still handles chat.

contentRouter.get('/search', async (c) => {
  const db = createDb(c.env.DB);
  const q     = (c.req.query('q') ?? '').trim().slice(0, 200);
  const board = c.req.query('board');
  const limit = Math.min(20, Math.max(1, parseInt(c.req.query('limit') ?? '10', 10)));

  if (q.length < 2) {
    return c.json({ query: q, results: [], total: 0, available: true });
  }

  // Simple LIKE search on chapter titles + subject names
  const pattern = `%${q}%`;

  // If board slug provided, resolve board ID first
  let boardId: string | null = null;
  if (board) {
    const boardRow = await db.select({ id: boards.id }).from(boards).where(eq(boards.slug, board)).get();
    boardId = boardRow?.id ?? null;
  }

  const chapterResults = await c.env.DB.prepare(
    `SELECT c.id, c.title, c.slug, c.subject_id, s.name as subject_name, s.slug as subject_slug,
            b.slug as board_slug, cl.slug as class_slug
     FROM chapters c
     JOIN subjects s ON s.id = c.subject_id
     JOIN streams str ON str.id = s.stream_id
     JOIN classes cl ON cl.id = str.class_id
     JOIN boards b ON b.id = cl.board_id
     WHERE c.status = 'published' AND c.title LIKE ?
     ${boardId ? 'AND b.id = ?' : ''}
     ORDER BY c.chapter_number ASC LIMIT ?`
  ).bind(...(boardId ? [pattern, boardId, limit] : [pattern, limit])).all<{
    id: string; title: string; slug: string; subject_id: string;
    subject_name: string; subject_slug: string; board_slug: string; class_slug: string;
  }>();

  const results = (chapterResults.results ?? []).map(row => ({
    id: row.id,
    title: row.title,
    snippet: `${row.subject_name} — ${row.title}`,
    url: `/learn/${row.board_slug}/${row.class_slug}/${row.subject_slug}/${row.slug}`,
    score: 1.0,
  }));

  return c.json({ query: q, results, total: results.length, available: true });
});
