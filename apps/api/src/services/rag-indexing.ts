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
const now = () => Math.floor(Date.now() / 1000);

function parse(value: string | null): Array<Record<string, string>> {
  try { return value ? JSON.parse(value) as Array<Record<string, string>> : []; } catch { return []; }
}
function words(text: string): string[] {
  const all = text.trim().split(/\s+/);
  if (all.length <= 400) return all.length ? [text.trim()] : [];
  const result: string[] = [];
  for (let start = 0; start < all.length; start += 350) result.push(all.slice(start, start + 400).join(' '));
  return result;
}
function notes(value: string | null, sections: string | null): string | null {
  const parsed = parse(sections);
  return parsed.length ? parsed.map(s => [s.title ? `## ${s.title}` : '', s.content ?? ''].filter(Boolean).join('\n')).join('\n\n') : value;
}
function qa(value: string | null): string | null {
  const parsed = parse(value);
  return parsed.length ? parsed.map(s => [s.section && `Section: ${s.section}`, s.question && `Q: ${s.question}`, s.answer && `A: ${s.answer}`, s.solution && `Solution: ${s.solution}`, s.content].filter(Boolean).join('\n')).filter(Boolean).join('\n\n') || null : null;
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
  const unique = [...new Set(ids)];
  for (let offset = 0; offset < unique.length; offset += 1000) await env.VECTORIZE.deleteByIds(unique.slice(offset, offset + 1000));
  await db.delete(chunks).where(and(eq(chunks.chapterId, chapterId), eq(chunks.sourceType, sourceType)));
}

export async function purgeChapterRag(env: Env, chapterId: string): Promise<void> {
  for (const scope of ['notes', 'qa', 'pyq'] as RagScope[]) await purgeRagScope(env, chapterId, scope);
}

async function ingest(env: Env, chapterId: string, subjectId: string, text: string, medium: 'english' | 'assamese', scope: RagScope): Promise<number> {
  const content = words(text);
  if (!content.length) return 0;
  const response = await (env.AI as unknown as { run(model: string, input: { text: string[] }): Promise<{ data: Array<{ values: number[] }> }> })
    .run('@cf/baai/bge-m3', { text: content });
  const entries = content.map((item, index) => ({ content: item, values: response.data[index]?.values, id: `${chapterId}_${medium}_${sourceFor[scope]}_${index}` }))
    .filter((entry): entry is { content: string; values: number[]; id: string } => Boolean(entry.values?.length));
  if (!entries.length) throw new Error('Embedding provider returned no vectors');
  await env.VECTORIZE.upsert(entries.map(entry => ({ id: entry.id, values: entry.values, metadata: { chapterId, subjectId, medium, sourceType: sourceFor[scope], chunkType: 'text', content: entry.content.slice(0, 512) } })));
  try {
    const db = createDb(env.DB);
    await Promise.all(entries.map(entry => db.insert(chunks).values({ id: crypto.randomUUID(), chapterId, subjectId, sourceType: sourceFor[scope], medium, chunkType: 'text', content: entry.content, vectorId: entry.id, metadata: JSON.stringify({ chapterId, subjectId, medium, sourceType: sourceFor[scope] }), createdAt: now() }).run()));
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
  const topicText = parse(chapter.publishedTopics).filter(topic => topic.status === 'published')
    .map(topic => [topic.title, topic.content].filter(Boolean).join('\n')).filter(Boolean).join('\n\n');
  const topicTextAs = parse(chapter.publishedTopics).filter(topic => topic.status === 'published')
    .map(topic => [topic.title_as || topic.title, topic.content_as].filter(Boolean).join('\n')).filter(Boolean).join('\n\n');
  const append = (base: string | null, topics: string) => [base, topics].filter(Boolean).join('\n\n') || null;
  const text: Record<RagScope, [string | null, string | null]> = {
    notes: [append(notes(chapter.ragText ?? chapter.notesEn, chapter.ragSectionsEn), topicText), append(notes(chapter.ragTextAs ?? chapter.notesAs, chapter.ragSectionsAs), topicTextAs)],
    qa: [qa(chapter.qaEn), qa(chapter.qaAs)],
    // Chapter rag_text belongs to notes, never PYQ. File-only PYQs have no
    // extractable text and are deliberately not embedded until OCR text exists.
    pyq: [null, null],
  };
  const result = {} as Record<RagScope, { chunks: number; error?: string; skipped?: string }>;
  for (const scope of requested) {
    try {
      await purgeRagScope(env, chapterId, scope);
      const [en, as] = text[scope];
      if (!en?.trim() && !as?.trim()) { result[scope] = { chunks: 0, skipped: 'no content' }; continue; }
      result[scope] = { chunks: (en ? await ingest(env, chapterId, chapter.subjectId, en, 'english', scope) : 0) + (as ? await ingest(env, chapterId, chapter.subjectId, as, 'assamese', scope) : 0) };
    } catch (error) { result[scope] = { chunks: 0, error: error instanceof Error ? error.message : String(error) }; }
  }
  if (requested.includes('notes') && !result.notes.error) await db.update(chapters).set({ ragIndexedAt: now(), updatedAt: now() }).where(eq(chapters.id, chapterId));
  return result;
}