import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { getPlatformProxy } from 'wrangler';

import { fetchMatchedChunkContext } from '../routes/chat';
import type { Env } from '../types';
import { reindexChapterRag } from './rag-indexing';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const API_ROOT = path.resolve(__dirname, '../../');
const CHAPTER_ID = 'worker-atomic-chapter';
const SUBJECT_ID = 'worker-atomic-subject';

function migrations(): string[] {
  return fs.readdirSync(path.join(API_ROOT, 'drizzle/migrations'))
    .filter(name => name.endsWith('.sql')).sort()
    .flatMap(name => fs.readFileSync(path.join(API_ROOT, 'drizzle/migrations', name), 'utf8')
      .split(';')
      .map(fragment => fragment.split('\n')
        .filter(line => line.trim() && !line.trim().startsWith('--'))
        .join('\n').trim())
      .filter(Boolean));
}

type StoredVector = {
  id: string;
  values: number[];
  metadata: Record<string, string>;
};

class CredentialFreeVectorize {
  readonly records = new Map<string, StoredVector>();

  constructor() {
    this.records.set(`${CHAPTER_ID}_english_notes_0`, {
      id: `${CHAPTER_ID}_english_notes_0`,
      values: [0.1, 0.2],
      metadata: {
        chapterId: CHAPTER_ID,
        subjectId: SUBJECT_ID,
        medium: 'english',
        sourceType: 'notes',
        content: 'stale pre-repair chunk',
      },
    });
  }

  async deleteByIds(ids: string[]): Promise<{ count: number }> {
    for (const id of ids) this.records.delete(id);
    return { count: ids.length };
  }

  async upsert(entries: StoredVector[]): Promise<{ count: number }> {
    for (const entry of entries) this.records.set(entry.id, entry);
    return { count: entries.length };
  }

  matches(): Array<{ id: string; score: number; metadata: Record<string, string> }> {
    return [...this.records.values()]
      .filter(vector => vector.metadata.chapterId === CHAPTER_ID)
      .map(vector => ({ id: vector.id, score: 0.99, metadata: vector.metadata }));
  }
}

function failSecondChunkInsertOnce(
  database: D1Database,
  onInjectedFailure: () => void,
): D1Database {
  let insertRuns = 0;
  let failureArmed = true;

  return new Proxy(database, {
    get(target, property, receiver) {
      if (property !== 'prepare') return Reflect.get(target, property, receiver);

      return (sql: string) => {
        const prepared = target.prepare(sql);
        if (!sql.toLowerCase().includes('insert into "chunks"')) return prepared;

        return new Proxy(prepared, {
          get(statement, statementProperty, statementReceiver) {
            if (statementProperty !== 'bind') {
              return Reflect.get(statement, statementProperty, statementReceiver);
            }

            return (...params: unknown[]) => {
              const bound = prepared.bind(...params);
              return new Proxy(bound, {
                get(boundStatement, boundProperty, boundReceiver) {
                  if (boundProperty !== 'run') {
                    return Reflect.get(boundStatement, boundProperty, boundReceiver);
                  }

                  return async (...runArgs: unknown[]) => {
                    insertRuns += 1;
                    if (failureArmed && insertRuns === 2) {
                      failureArmed = false;
                      onInjectedFailure();
                      throw new Error('simulated partial chunk mapping write');
                    }
                    return bound.run();
                  };
                },
              });
            };
          },
        });
      };
    },
  }) as D1Database;
}

function freshNotes(): string {
  const first = Array.from(
    { length: 340 },
    (_, index) => `fresh0-${index}`,
  ).join(' ');
  const second = Array.from(
    { length: 340 },
    (_, index) => `fresh1-${index}`,
  ).join(' ');
  return `## Repaired chapter\nfresh chunk zero marker ${first} fresh chunk one marker ${second}`;
}

describe('Worker RAG repair consistency', () => {
  it('does not hydrate mixed mappings after partial D1 write and converges on retry', async () => {
    const proxy = await getPlatformProxy<Env>({
      configPath: path.join(API_ROOT, 'wrangler.toml'),
      remoteBindings: false,
      persist: false,
    });

    try {
      const vectorize = new CredentialFreeVectorize();
      let injectedFailure = false;
      const database = failSecondChunkInsertOnce(proxy.env.DB, () => {
        injectedFailure = true;
      });
      const env = {
        ...proxy.env,
        DB: database,
        AI: {
          run: async (model: string, input: { text?: string[] }) => (
            model === '@cf/baai/bge-m3'
              ? { data: (input.text ?? []).map(() => ({ values: [0.01, 0.02] })) }
              : { response: '' }
          ),
        } as unknown as Ai,
        VECTORIZE: vectorize,
      } as unknown as Env;

      for (const statement of migrations()) await env.DB.prepare(statement).run();
      await env.DB.batch([
        env.DB.prepare(`INSERT INTO boards (id, name, slug) VALUES ('worker-atomic-board', 'Board', 'worker-atomic-board')`),
        env.DB.prepare(`INSERT INTO classes (id, board_id, name, slug) VALUES ('worker-atomic-class', 'worker-atomic-board', 'Class', 'worker-atomic-class')`),
        env.DB.prepare(`INSERT INTO streams (id, class_id, name, slug) VALUES ('worker-atomic-stream', 'worker-atomic-class', 'Science', 'worker-atomic-stream')`),
        env.DB.prepare(`INSERT INTO subjects (id, stream_id, name, slug, is_published) VALUES (?, 'worker-atomic-stream', 'Physics', 'worker-atomic-subject', 1)`).bind(SUBJECT_ID),
        env.DB.prepare(`INSERT INTO chapters (id, subject_id, title, slug, status, rag_text) VALUES (?, ?, 'Atomic repair chapter', 'worker-atomic-chapter', 'published', ?)`)
          .bind(CHAPTER_ID, SUBJECT_ID, freshNotes()),
        env.DB.prepare(`
          INSERT INTO chunks
            (id, chapter_id, subject_id, source_type, medium, chunk_type, content, vector_id, metadata)
          VALUES ('old-chunk', ?, ?, 'notes', 'english', 'text', 'stale pre-repair chunk', ?, '{}')
        `).bind(CHAPTER_ID, SUBJECT_ID, `${CHAPTER_ID}_english_notes_0`),
      ]);

      const first = await reindexChapterRag(env, CHAPTER_ID, ['notes']);
      const firstMatches = await fetchMatchedChunkContext(
        env.DB,
        vectorize.matches() as Parameters<typeof fetchMatchedChunkContext>[1],
        CHAPTER_ID,
        'en',
        SUBJECT_ID,
      );
      const afterFailure = await env.DB.prepare(`
        SELECT vector_id, content FROM chunks
        WHERE chapter_id = ? AND source_type = 'notes' AND medium = 'english'
        ORDER BY vector_id
      `).bind(CHAPTER_ID).all<{ vector_id: string; content: string }>();

      expect(injectedFailure).toBe(true);
      expect(first.notes?.error).toContain('Failed query');
      expect(vectorize.matches()).toEqual([]);
      expect(firstMatches).toEqual([]);
      expect(afterFailure.results.length).toBeGreaterThan(0);
      expect(new Set(afterFailure.results.map(row => row.vector_id)).size)
        .toBe(afterFailure.results.length);
      expect(afterFailure.results.every(row => row.content.includes('fresh'))).toBe(true);
      expect(afterFailure.results.every(row => !row.content.includes('stale pre-repair'))).toBe(true);

      const second = await reindexChapterRag(env, CHAPTER_ID, ['notes']);
      const finalMatches = await fetchMatchedChunkContext(
        env.DB,
        vectorize.matches() as Parameters<typeof fetchMatchedChunkContext>[1],
        CHAPTER_ID,
        'en',
        SUBJECT_ID,
      );
      const finalRows = await env.DB.prepare(`
        SELECT vector_id, content FROM chunks
        WHERE chapter_id = ? AND source_type = 'notes' AND medium = 'english'
        ORDER BY vector_id
      `).bind(CHAPTER_ID).all<{ vector_id: string; content: string }>();

      const expectedChunkCount = second.notes?.chunks ?? 0;
      expect(expectedChunkCount).toBeGreaterThan(0);
      expect(second.notes).toEqual({ chunks: expectedChunkCount });
      expect(finalMatches).toHaveLength(expectedChunkCount);
      expect(finalMatches.every(chunk => chunk.content.includes('fresh'))).toBe(true);
      expect(finalMatches.every(chunk => !chunk.content.includes('stale pre-repair'))).toBe(true);
      expect(finalRows.results).toHaveLength(expectedChunkCount);
      expect(new Set(finalRows.results.map(row => row.vector_id)).size).toBe(expectedChunkCount);
      expect(new Set(finalRows.results.map(row => row.content)).size).toBe(expectedChunkCount);
      expect(new Set(finalRows.results.map(row => row.vector_id))).toEqual(
        new Set(vectorize.matches().map(vector => vector.id)),
      );
    } finally {
      await proxy.dispose();
    }
  });
});