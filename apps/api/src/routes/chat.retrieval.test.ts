import { describe, expect, it, vi } from 'vitest';

import {
  chooseAssameseRetrievalLanguage,
  detectCurriculumClass,
  fetchAuthoritativeIntentContext,
  fetchChapterContent,
  fetchMatchedChunkContext,
  firstUsableContext,
  normalizeChatSourceType,
  detectAuthoritativeIntent,
  resolveCurriculumScope,
  semanticRetrievalFilters,
  shouldBypassSemanticRetrieval,
  shouldSkipSemanticRetrievalForWebIntent,
  shouldResolveCurriculumScopeForChat,
  shouldStartWebSearchForChat,
  terminalChatErrorEvent,
} from './chat';

describe('chapter-scoped chat retrieval', () => {
  it.each([
    ['notes', 'notes'],
    ['qa', 'qa'],
    ['question_paper', 'pyq'],
    ['PYQ', 'pyq'],
    [' unexpected ', null],
  ] as const)('normalizes the selected content section: %s', (input, expected) => {
    expect(normalizeChatSourceType(input)).toBe(expected);
  });

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

  it('includes numeric phase timings on terminal SSE errors when available', () => {
    expect(terminalChatErrorEvent(
      'Unavailable',
      'provider_stream_failed',
      'provider_stream',
      'r1',
      { quota_ms: 40, retrieval_ms: 120, source_card_ms: 180, generation_ms: 3_000 },
    )).toMatchObject({
      event: 'chat_error',
      error_code: 'provider_stream_failed',
      timings_ms: {
        quota_ms: 40,
        retrieval_ms: 120,
        source_card_ms: 180,
        generation_ms: 3_000,
      },
    });
  });

  it('bypasses embedding and Vectorize only for usable explicit chapter content', () => {
    expect(shouldBypassSemanticRetrieval('chapter-1', 'Chapter notes')).toBe(true);
  });

  it('does not scan the curriculum hierarchy before a direct chapter turn', () => {
    expect(shouldResolveCurriculumScopeForChat('chapter-1', null)).toBe(false);
    expect(shouldResolveCurriculumScopeForChat('chapter-1', null, true)).toBe(true);
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

  it('skips semantic retrieval for unscoped freshness turns but preserves explicit chapter grounding', () => {
    expect(shouldSkipSemanticRetrievalForWebIntent(undefined, true)).toBe(true);
    expect(shouldSkipSemanticRetrievalForWebIntent('chapter-1', true)).toBe(false);
    expect(shouldSkipSemanticRetrievalForWebIntent(undefined, false)).toBe(false);
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
    ['Class 10 Physics', 'unsupported'],
    ['Class 11 Physics', '11'],
    ['Explain this for Class XI', '11'],
    ['HS 1st year chemistry', '11'],
    ['Class 12 Biology', '12'],
    ['HS 2nd Year Assamese', '12'],
    ['third semester economics', 'semester-3'],
    ['Explain the chapter for Class 9', 'unsupported'],
    ['Explain Newton’s first law', null],
  ] as const)('detects only explicit curriculum classes: %s', (message, expected) => {
    expect(detectCurriculumClass(message)).toBe(expected);
  });

  it('resolves an explicit class only against its matching published subject row', async () => {
    const rows = [
      {
        subject_id: 'physics-ahsec-11',
        subject_name: 'Physics',
        subject_slug: 'physics',
        class_name: 'Class 11',
        class_level: '11',
        class_slug: 'class-11',
        board_name: 'AHSEC',
        board_slug: 'ahsec',
      },
      {
        subject_id: 'physics-ahsec-12',
        subject_name: 'Physics',
        subject_slug: 'physics',
        class_name: 'Class 12',
        class_level: '12',
        class_slug: 'class-12',
        board_name: 'AHSEC',
        board_slug: 'ahsec',
      },
    ];
    const d1 = {
      prepare: vi.fn(() => ({
        all: vi.fn(async () => ({ results: rows })),
      })),
    };

    await expect(resolveCurriculumScope(
      d1 as unknown as D1Database,
      'Explain Class 11 Physics',
    )).resolves.toEqual({
      subjectId: 'physics-ahsec-11',
      subjectName: 'Physics',
      className: 'Class 11',
      boardName: 'AHSEC',
      explicit: true,
      unresolved: false,
    });
  });

  it('disambiguates page subjects, preserves conflicting subjects, and ignores output-language mentions', async () => {
    const rows = [
      {
        subject_id: 'physics-ahsec-11',
        subject_name: 'Physics',
        subject_slug: 'physics',
        class_name: 'Class 11',
        class_level: '11',
        class_slug: 'class-11',
        board_name: 'AHSEC',
        board_slug: 'ahsec',
      },
      {
        subject_id: 'physics-ahsec-12',
        subject_name: 'Physics',
        subject_slug: 'physics',
        class_name: 'Class 12',
        class_level: '12',
        class_slug: 'class-12',
        board_name: 'AHSEC',
        board_slug: 'ahsec',
      },
    ];
    const d1 = {
      prepare: vi.fn(() => ({
        all: vi.fn(async () => ({ results: rows })),
      })),
    };

    await expect(resolveCurriculumScope(
      d1 as unknown as D1Database,
      'Explain this Physics concept',
      'physics-ahsec-11',
    )).resolves.toMatchObject({
      subjectId: 'physics-ahsec-11',
      className: 'Class 11',
      explicit: true,
      unresolved: false,
    });

    await expect(resolveCurriculumScope(
      d1 as unknown as D1Database,
      'Explain Biology',
      'physics-ahsec-11',
    )).resolves.toMatchObject({
      explicit: true,
      unresolved: true,
    });

    await expect(resolveCurriculumScope(
      d1 as unknown as D1Database,
      'Explain this in English',
      'physics-ahsec-11',
    )).resolves.toMatchObject({
      subjectId: 'physics-ahsec-11',
      className: 'Class 11',
      explicit: false,
      unresolved: false,
    });

    await expect(resolveCurriculumScope(
      d1 as unknown as D1Database,
      'For AHSEC physics, explain when total internal reflection occurs.',
    )).resolves.toMatchObject({
      explicit: false,
      unresolved: false,
    });
  });

  it.each(['Explain Class 9 Physics', 'Explain Class 10 Physics'])(
    'fails closed for an explicit unsupported class: %s',
    async (message) => {
      const d1 = { prepare: vi.fn() };

      await expect(resolveCurriculumScope(
        d1 as unknown as D1Database,
        message,
      )).resolves.toMatchObject({
        explicit: true,
        unresolved: true,
        unsupportedClass: true,
      });
      expect(d1.prepare).not.toHaveBeenCalled();
    });

  it('tries lower-ranked chapter candidates until one has validated evidence', async () => {
    const attempts: string[] = [];
    const selected = await firstUsableContext(
      ['stale-top-match', 'valid-second-match', 'unused-third-match'],
      async (candidate) => {
        attempts.push(candidate);
        return candidate === 'valid-second-match' ? [{ chapterId: candidate }] : [];
      },
    );

    expect(attempts).toEqual(['stale-top-match', 'valid-second-match']);
    expect(selected).toEqual({
      candidate: 'valid-second-match',
      context: [{ chapterId: 'valid-second-match' }],
    });
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

  it('uses chapter Q&A rather than notes for an explicit Q&A request', async () => {
    let query = '';
    const first = vi.fn(async () => ({
      qaAs: '[]',
      qaEn: JSON.stringify([{ question: 'Question from Q&A', answer: 'Answer from Q&A' }]),
    }));
    const d1 = {
      prepare: vi.fn((sql: string) => {
        query = sql;
        return { bind: vi.fn(() => ({ first })) };
      }),
    };

    const result = await fetchChapterContent(
      d1 as unknown as D1Database,
      'chapter-qa',
      'as',
      'physics',
      'qa',
    );

    expect(query).toContain('qa_as');
    expect(query).toContain('qa_en');
    expect(query).not.toContain('notes_en');
    expect(result).toEqual({
      content: 'Q: Question from Q&A\nA: Answer from Q&A',
      language: 'english',
    });
  });

  it('does not substitute chapter notes for image-only PYQ content', async () => {
    const d1 = { prepare: vi.fn() };

    await expect(fetchChapterContent(
      d1 as unknown as D1Database,
      'chapter-pyq',
      'en',
      'physics',
      'pyq',
    )).resolves.toBeNull();
    expect(d1.prepare).not.toHaveBeenCalled();
  });

  it('rejects vector matches from another content section', async () => {
    const d1 = { prepare: vi.fn() };
    const matches = [{
      id: 'notes-vector',
      score: 0.99,
      metadata: {
        chapterId: 'chapter-1',
        subjectId: 'physics',
        sourceType: 'notes',
        medium: 'english',
        content: 'Notes must not satisfy a Q&A request.',
      },
    }];

    await expect(fetchMatchedChunkContext(
      d1 as unknown as D1Database,
      matches as unknown as Parameters<typeof fetchMatchedChunkContext>[1],
      'chapter-1',
      'en',
      'physics',
      'qa',
    )).resolves.toEqual([]);
    expect(d1.prepare).not.toHaveBeenCalled();
  });
});
