import { describe, expect, it, vi } from 'vitest';

import {
  chooseAssameseRetrievalLanguage,
  detectCurriculumClass,
  fetchAuthoritativeIntentContext,
  fetchChapterContent,
  detectAuthoritativeIntent,
  semanticRetrievalFilters,
  shouldBypassSemanticRetrieval,
  shouldResolveCurriculumScopeForChat,
  shouldStartWebSearchForChat,
  terminalChatErrorEvent,
} from './chat';

describe('chapter-scoped chat retrieval', () => {
  it.each([
    ['Show the Physics syllabus', 'syllabus'],
    ['Give me the chapter list', 'syllabus'],
    ['Show previous year question papers', 'pyq'],
    ['PYQ for this subject', 'pyq'],
    ['Explain Newton’s first law', null],
  ] as const)('routes authoritative list intent: %s', (message, intent) => {
    expect(detectAuthoritativeIntent(message)).toBe(intent);
  });

  it('marks post-header provider failures as terminal SSE errors', () => {
    expect(terminalChatErrorEvent('Unavailable', 'provider_stream_failed', 'provider_stream', 'r1'))
      .toEqual({
        event: 'chat_error',
        content: '',
        done: true,
        error: 'Unavailable',
        error_code: 'provider_stream_failed',
        failure_stage: 'provider_stream',
        request_id: 'r1',
      });
  });

  it('bypasses embedding and Vectorize only for usable explicit chapter content', () => {
    expect(shouldBypassSemanticRetrieval('chapter-1', 'Chapter notes')).toBe(true);
  });

  it('does not scan the curriculum hierarchy before a direct chapter turn', () => {
    expect(shouldResolveCurriculumScopeForChat('chapter-1', null)).toBe(false);
    expect(shouldResolveCurriculumScopeForChat('chapter-1', 'syllabus')).toBe(true);
    expect(shouldResolveCurriculumScopeForChat(undefined, null)).toBe(true);
  });

  it('skips bounded web work for direct chapter turns unless freshness is explicit', () => {
    expect(shouldStartWebSearchForChat(true, 'chapter-1', null, false)).toBe(false);
    expect(shouldStartWebSearchForChat(true, 'chapter-1', null, true)).toBe(true);
    expect(shouldStartWebSearchForChat(true, undefined, null, false)).toBe(true);
    expect(shouldStartWebSearchForChat(true, undefined, 'syllabus', false)).toBe(false);
    expect(shouldStartWebSearchForChat(false, undefined, null, true)).toBe(false);
  });

  it.each([
    [undefined, 'Chapter notes'],
    ['chapter-1', null],
    ['chapter-1', '   '],
  ])('retains semantic retrieval when the direct path is incomplete', (chapterId, content) => {
    expect(shouldBypassSemanticRetrieval(chapterId, content)).toBe(false);
  });

  it('drops a stale direct chapter filter but keeps the valid subject scope', () => {
    expect(semanticRetrievalFilters('missing-chapter', 'physics', true)).toEqual({
      subjectId: 'physics',
    });
  });

  it('keeps an explicit chapter filter when no direct lookup was attempted', () => {
    expect(semanticRetrievalFilters('chapter-1', 'physics', false)).toEqual({
      chapterId: 'chapter-1',
      subjectId: 'physics',
    });
  });

  it.each([
    [0.80, 0.98, true, 'en'],
    [0.87, 0.89, true, 'as'],
    [0.00, 0.76, false, 'en'],
    [0.83, 0.00, true, 'as'],
  ] as const)(
    'chooses the strongest bilingual evidence (%s vs %s)',
    (assameseTop, englishTop, hasAssamese, expected) => {
      expect(chooseAssameseRetrievalLanguage(assameseTop, englishTop, hasAssamese))
        .toBe(expected);
    },
  );

  it.each([
    ['Class 11 Physics', '11'],
    ['Explain this for Class XI', '11'],
    ['HS 1st year chemistry', '11'],
    ['Class 12 Biology', '12'],
    ['HS 2nd Year Assamese', '12'],
    ['third semester economics', 'semester-3'],
    ['Explain Newton’s first law', null],
  ] as const)('detects only explicit curriculum classes: %s', (message, expected) => {
    expect(detectCurriculumClass(message)).toBe(expected);
  });

  it('constrains authoritative chapter reads by subject and published hierarchy', async () => {
    let query = '';
    const bind = vi.fn(() => ({ all: vi.fn(async () => ({ results: [] })) }));
    const d1 = {
      prepare: vi.fn((sql: string) => {
        query = sql;
        return { bind };
      }),
    };

    await fetchAuthoritativeIntentContext(
      d1 as unknown as D1Database,
      'syllabus',
      'class-11-physics',
      'page-chapter',
      'en',
    );

    expect(query).toContain('JOIN subjects');
    expect(query).toContain('LEFT JOIN streams');
    expect(query).toContain("boards.status = 'published'");
    expect(query).toContain('chapters.id = ?');
    expect(query).toContain('chapters.subject_id = ?');
    expect(bind).toHaveBeenCalledWith('page-chapter', 'class-11-physics', 30);
  });

  it('selects only the required language fields for direct English chapter RAG', async () => {
    let query = '';
    const first = vi.fn(async () => ({
      ragSectionsEn: JSON.stringify([{ content: 'Newton notes' }]),
      ragText: null,
      notesEn: null,
    }));
    const d1 = {
      prepare: vi.fn((sql: string) => {
        query = sql;
        return { bind: vi.fn(() => ({ first })) };
      }),
    };

    await fetchChapterContent(d1 as unknown as D1Database, 'chapter-1', 'en', 'physics');

    expect(query).toContain('rag_sections_en');
    expect(query).not.toContain('rag_sections_as');
    expect(query).not.toContain('notes_as');
  });
});
