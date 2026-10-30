/**
 * Canonical Worker-native RAG indexing implementation.
 *
 * Keeping vector deletion and the D1 mapping in one place is important: an
 * index is not considered successful until both Vectorize and D1 agree.
 */
import { and, eq } from 'drizzle-orm';
import { createDb } from '../db/client';
import { chapters, chunks } from '../db/schema';
import type { Env } from '../types';

export type RagScope = 'notes' | 'qa' | 'pyq';
type SourceType = 'notes' | 'important_questions' | 'pyq';
const sourceFor: Record<RagScope, SourceType> = { notes: 'notes', qa: 'important_questions', pyq: 'pyq' };
const VECTORIZE_DELETE_BATCH_SIZE = 100;
const VECTOR_ID_MAX_BYTES = 64;
const EMBEDDING_DIMENSIONS = 1024;
const now = () => Math.floor(Date.now() / 1000);

export function batchVectorIds(ids: string[]): string[][] {
  const batches: string[][] = [];
  for (let offset = 0; offset < ids.length; offset += VECTORIZE_DELETE_BATCH_SIZE) {
    batches.push(ids.slice(offset, offset + VECTORIZE_DELETE_BATCH_SIZE));
  }
  return batches;
}

function parse(value: string | null): Array<Record<string, string>> {
  try { return value ? JSON.parse(value) as Array<Record<string, string>> : []; } catch { return []; }
}
export function splitRagText(text: string): string[] {
  const trimmed = text.trim();
  if (!trimmed) return [];
  const all = trimmed.split(/\s+/);
  if (all.length <= 400) return [trimmed];
  const result: string[] = [];
  for (let start = 0; start < all.length; start += 350) result.push(all.slice(start, start + 400).join(' '));
  return result;
}
export function notesTextForRag(value: string | null, sections: string | null): string | null {
  const parsed = parse(sections);
  return parsed.length ? parsed.map(s => [s.title ? '## ' + s.title : '', s.content ?? ''].filter(Boolean).join('\n')).join('\n\n') : value;
}
export function publishedTopicTextForRag(value: string | null, medium: 'english' | 'assamese'): string {
  return parse(value).filter(topic => topic.status === 'published')
    .map(topic => [medium === 'english' ? topic.title : topic.title_as || topic.title, medium === 'english' ? topic.content : topic.content_as].filter(Boolean).join('\n'))
    .filter(Boolean).join('\n\n');
}
function vectorIdByteLength(id: string): number {
  return new TextEncoder().encode(id).byteLength;
}
async function chapterVectorKey(chapterId: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(chapterId));
  return [...new Uint8Array(digest).slice(0, 16)]
    .map(value => value.toString(16).padStart(2, '0')).join('');
}
function buildVectorId(chapterKey: string, medium: 'english' | 'assamese', scope: RagScope, index: number): string {
  const id = `c${chapterKey}_${medium === 'english' ? 'en' : 'as'}_${scope}_${index}`;
  if (vectorIdByteLength(id) > VECTOR_ID_MAX_BYTES) throw new Error('Generated Vectorize ID exceeds 64 bytes');
  return id;
}
function parseEmbeddingVectors(response: unknown, expectedCount: number): number[][] {
  const data = response && typeof response === 'object'
    ? (response as { data?: unknown }).data
    : undefined;
  if (!Array.isArray(data) || data.length !== expectedCount) {
    throw new Error('Embedding provider returned an unexpected vector count');
  }
  const vectors = data.map((item): number[] | null => {
    const values = Array.isArray(item)
      ? item
      : item && typeof item === 'object' && Array.isArray((item as { values?: unknown }).values)
        ? (item as { values: unknown[] }).values
        : null;
    if (!values || values.length !== EMBEDDING_DIMENSIONS
      || values.some(value => typeof value !== 'number' || !Number.isFinite(value))) return null;
    return values as number[];
  });
  if (vectors.some(vector => vector === null)) {
    throw new Error('Embedding provider returned a vector with an invalid dimension or value');
  }
  return vectors as number[][];
}
async function embedContent(env: Env, content: string[]): Promise<number[][]> {
  if (!content.length) return [];
  const response = await (env.AI as unknown as {
    run(model: string, input: { text: string[] }): Promise<unknown>;
  }).run('@cf/baai/bge-m3', { text: content });
  return parseEmbeddingVectors(response, content.length);
}
export function qaTextFromApprovedPairs(value: string | null, pyqOnly = false): string | null {
  const parsed = parse(value);
  const complete = parsed.flatMap((item) => {
    const question = typeof item.question === 'string' ? item.question.trim() : '';
    const answer = typeof item.answer === 'string' ? item.answer.trim() : '';
    const solution = typeof item.solution === 'string' ? item.solution.trim() : '';
    const content = typeof item.content === 'string' ? item.content.trim() : '';
    const source = typeof item.source === 'string' ? item.source.trim() : '';
    const hasPyqSource = /pyq|previous\s+year/i.test(source) || Boolean(item.year);

    if (pyqOnly && !hasPyqSource) return [];
    if (question && (answer || solution)) {
      return [[
        item.id && `Question ID: ${item.id}`,
        item.section && `Section: ${item.section}`,
        `Q: ${question}`,
        answer && `A: ${answer}`,
        solution && `Solution: ${solution}`,
        source && `Source: ${source}`,
        item.year && `Year: ${item.year}`,
        item.marks && `Marks: ${item.marks}`,
      ].filter(Boolean).join('\n')];
    }

    // Legacy freeform Q&A is accepted only when both sides are explicitly
    // present; bare question lists must not become answer-bearing RAG chunks.
    if (!question && content && /(?:^|\n)\s*(?:q|question)\s*:/i.test(content)
      && /(?:^|\n)\s*(?:a|answer|solution)\s*:/i.test(content)) {
      return [content];
    }
    return [];
  });
  return complete.join('\n\n') || null;
}

/** Delete mappings only after Vectorize deletion succeeds, including legacy IDs. */
export async function purgeRagScope(env: Env, chapterId: string, scope: RagScope): Promise<void> {
  const sourceType = sourceFor[scope];
  const db = createDb(env.DB);
  const mapped = await db.select({ vectorId: chunks.vectorId }).from(chunks)
    .where(and(eq(chunks.chapterId, chapterId), eq(chunks.sourceType, sourceType)));
  const ids = mapped.map(row => row.vectorId).filter((id): id is string => Boolean(id));
  // Deterministic legacy IDs must be swept to prevent stale tails after older deployments.
  for (const medium of ['english', 'assamese']) for (let index = 0; index < 500; index++) ids.push(`${chapterId}_${medium}_${sourceType}_${index}`);
  // Vectorize cannot contain IDs over 64 UTF-8 bytes. Older question IDs could
  // exceed that limit; skip those impossible IDs while still removing their D1 mappings.
  const unique = [...new Set(ids)].filter(id => vectorIdByteLength(id) <= VECTOR_ID_MAX_BYTES);
  for (const batch of batchVectorIds(unique)) await env.VECTORIZE.deleteByIds(batch);
  await db.delete(chunks).where(and(eq(chunks.chapterId, chapterId), eq(chunks.sourceType, sourceType)));
}

export async function purgeChapterRag(env: Env, chapterId: string): Promise<void> {
  for (const scope of ['notes', 'qa', 'pyq'] as RagScope[]) await purgeRagScope(env, chapterId, scope);
}

async function ingest(
  env: Env,
  chapterId: string,
  subjectId: string,
  content: string[],
  values: number[][],
  medium: 'english' | 'assamese',
  scope: RagScope,
  chapterKey: string,
): Promise<number> {
  if (!content.length) return 0;
  if (content.length !== values.length) throw new Error('Embedding count does not match RAG chunk count');
  const hierarchy = await env.DB.prepare(`
    SELECT subjects.name AS subjectName, subjects.slug AS subjectSlug,
           streams.id AS streamId, streams.name AS streamName,
           classes.id AS classId, classes.name AS className,
           boards.id AS boardId, boards.name AS boardName
    FROM subjects
    LEFT JOIN streams ON streams.id = subjects.stream_id
    LEFT JOIN classes ON classes.id = streams.class_id
    LEFT JOIN boards ON boards.id = classes.board_id
    WHERE subjects.id = ?
  `).bind(subjectId).first<{
    subjectName: string | null;
    subjectSlug: string | null;
    streamId: string | null;
    streamName: string | null;
    classId: string | null;
    className: string | null;
    boardId: string | null;
    boardName: string | null;
  }>().catch(() => null);
  const hierarchyMetadata = {
    ...(hierarchy?.subjectName && { subjectName: hierarchy.subjectName }),
    ...(hierarchy?.subjectSlug && { subjectSlug: hierarchy.subjectSlug }),
    ...(hierarchy?.streamId && { streamId: hierarchy.streamId }),
    ...(hierarchy?.streamName && { streamName: hierarchy.streamName }),
    ...(hierarchy?.classId && { classId: hierarchy.classId }),
    ...(hierarchy?.className && { className: hierarchy.className }),
    ...(hierarchy?.boardId && { boardId: hierarchy.boardId }),
    ...(hierarchy?.boardName && { boardName: hierarchy.boardName }),
  };
  const entries = content.map((item, index) => ({
    content: item,
    values: values[index]!,
    id: buildVectorId(chapterKey, medium, scope, index),
  }));
  await env.VECTORIZE.upsert(entries.map(entry => ({ id: entry.id, values: entry.values, metadata: { chapterId, subjectId, medium, sourceType: sourceFor[scope], chunkType: 'text', content: entry.content.slice(0, 512), ...hierarchyMetadata } })));
  try {
    const db = createDb(env.DB);
    await Promise.all(entries.map(entry => db.insert(chunks).values({ id: crypto.randomUUID(), chapterId, subjectId, sourceType: sourceFor[scope], medium, chunkType: 'text', content: entry.content, vectorId: entry.id, metadata: JSON.stringify({ chapterId, subjectId, medium, sourceType: sourceFor[scope], ...hierarchyMetadata }), createdAt: now() }).run()));
  } catch (error) {
    await env.VECTORIZE.deleteByIds(entries.map(entry => entry.id)).catch(() => undefined);
    throw error;
  }
  return entries.length;
}

export async function reindexChapterRag(env: Env, chapterId: string, requested: RagScope[] = ['notes']): Promise<Record<RagScope, { chunks: number; error?: string; skipped?: string }>> {
  const db = createDb(env.DB);
  const chapter = await db.select().from(chapters).where(eq(chapters.id, chapterId)).get();
  if (!chapter) throw new Error('Chapter not found');
  const topicText = publishedTopicTextForRag(chapter.publishedTopics, 'english');
  const topicTextAs = publishedTopicTextForRag(chapter.publishedTopics, 'assamese');
  const append = (base: string | null, topics: string) => [base, topics].filter(Boolean).join('\n\n') || null;
  const text: Record<RagScope, [string | null, string | null]> = {
    notes: [append(notesTextForRag(chapter.ragText ?? chapter.notesEn, chapter.ragSectionsEn), topicText), append(notesTextForRag(chapter.ragTextAs ?? chapter.notesAs, chapter.ragSectionsAs), topicTextAs)],
    qa: [qaTextFromApprovedPairs(chapter.qaEn), qaTextFromApprovedPairs(chapter.qaAs)],
    // Chapter rag_text belongs to notes, never PYQ. File-only PYQs have no
    // extractable text and are deliberately not embedded until OCR text exists.
    pyq: [qaTextFromApprovedPairs(chapter.qaEn, true), qaTextFromApprovedPairs(chapter.qaAs, true)],
  };
  const result = {} as Record<RagScope, { chunks: number; error?: string; skipped?: string }>;
  for (const scope of requested) {
    try {
      const [en, as] = text[scope];
      const englishContent = en?.trim() ? splitRagText(en) : [];
      const assameseContent = as?.trim() ? splitRagText(as) : [];
      // Validate both language responses before removing the existing vectors.
      const [englishVectors, assameseVectors, chapterKey] = await Promise.all([
        embedContent(env, englishContent),
        embedContent(env, assameseContent),
        chapterVectorKey(chapterId),
      ]);
      await purgeRagScope(env, chapterId, scope);
      if (!englishContent.length && !assameseContent.length) {
        result[scope] = { chunks: 0, skipped: 'no content' };
        continue;
      }
      result[scope] = {
        chunks: (englishContent.length
          ? await ingest(env, chapterId, chapter.subjectId, englishContent, englishVectors, 'english', scope, chapterKey)
          : 0)
          + (assameseContent.length
            ? await ingest(env, chapterId, chapter.subjectId, assameseContent, assameseVectors, 'assamese', scope, chapterKey)
            : 0),
      };
    } catch (error) { result[scope] = { chunks: 0, error: error instanceof Error ? error.message : String(error) }; }
  }
  if (requested.includes('notes') && !result.notes.error) await db.update(chapters).set({ ragIndexedAt: now(), updatedAt: now() }).where(eq(chapters.id, chapterId));
  return result;
}