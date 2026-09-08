import { describe, expect, it } from 'vitest';

import {
  chooseAssameseRetrievalLanguage,
  detectAuthoritativeIntent,
  semanticRetrievalFilters,
  shouldBypassSemanticRetrieval,
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
});