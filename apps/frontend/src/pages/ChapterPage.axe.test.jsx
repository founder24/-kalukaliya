/**
 * Task #202 — ChapterPage: axe accessibility audit.
 *
 * Covers two key render states:
 *  1. Loading state (loading=true) — shows skeleton while chapter data is fetched
 *  2. Loaded state  — full chapter content rendered (seeded via window.__CHAPTER_PRELOAD__)
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { axe, toHaveNoViolations } from 'jest-axe';
import { render, act } from '@testing-library/react';
import React from 'react';

expect.extend(toHaveNoViolations);

vi.mock('react-router-dom', () => ({
  useParams:       () => ({
    board:       'ahsec',
    classSlug:   'class-11',
    subjectSlug: 'english',
    chapterSlug: 'prose',
  }),
  useSearchParams: () => [new URLSearchParams(), vi.fn()],
  Link:            ({ children, to }) => <a href={to}>{children}</a>,
}));

vi.mock('@/components/seo/PageMeta', () => ({
  default: () => null,
}));

vi.mock('@/components/MarkdownRenderer', () => ({
  default: ({ content }) => <div data-testid="markdown">{content}</div>,
}));

vi.mock('@/components/chapter/TopicAnswerCard', () => ({
  default: () => <div data-testid="topic-answer-card" />,
}));

vi.mock('@/components/chapter/ChapterTopicGraph', () => ({
  default: () => <div data-testid="chapter-topic-graph" />,
}));

vi.mock('@/utils/slugifyHeading', () => ({
  slugifyHeading: (t) => t.toLowerCase().replace(/\s+/g, '-'),
}));

vi.mock('@/hooks/useHashScroll', () => ({
  useHashScroll: vi.fn(),
}));

vi.mock('@/components/ui/badge', () => ({
  Badge: ({ children, className }) => <span className={className}>{children}</span>,
}));

vi.mock('@/components/ui/skeleton', () => ({
  Skeleton: ({ className }) => <div className={className} aria-hidden="true" />,
}));

vi.mock('@/utils/api', () => ({
  apiClient:           () => ({ get: vi.fn().mockResolvedValue({ data: {} }) }),
  seoRelatedByChapter: vi.fn().mockResolvedValue([]),
}));

vi.mock('@/hooks/useShare', () => ({
  useShare:        () => ({ sharing: false, share: vi.fn(), serpPreview: null, confirmShare: vi.fn(), dismissPreview: vi.fn() }),
  SerpPreviewModal: () => null,
}));

vi.mock('@/utils/analytics', () => ({
  default: {
    page:          vi.fn(),
    event:         vi.fn(),
    chapterView:   vi.fn(),
    chapterRetry:  vi.fn(),
    chapterShare:  vi.fn(),
    chapterAskAi:  vi.fn(),
    scrollDepth:   vi.fn(),
    tocClick:      vi.fn(),
  },
}));

vi.mock('@/context/LanguageContext', () => ({
  useContentLang: () => ({ contentLang: 'en', switchLang: vi.fn() }),
}));

vi.mock('@/components/ui/StickyToc', () => ({
  default: () => null,
}));

vi.mock('@/components/content/ContinueLearning', () => ({
  default: () => null,
}));

vi.mock('@/components/content/TrustpilotReviewsSection', () => ({
  default: () => null,
}));

vi.mock('@/components/layout/MobileNavSwitch', () => ({
  MobileNavSwitch: () => null,
}));

vi.mock('@/hooks/useContent', () => ({
  useLibraryBundle:     vi.fn(),
  useLibraryBundleSlim: vi.fn(),
}));

vi.mock('@/utils/siblingChapter', () => ({
  findSiblingChapters: vi.fn(() => ({ prev: null, next: null })),
  siblingsAsRelated:   vi.fn(() => []),
}));

vi.mock('@/utils/recentChapters', () => ({
  pushRecentChapter: vi.fn(),
}));

vi.mock('@/components/study/HighlightSavePopover', () => ({
  HighlightSavePopover: () => null,
}));

vi.mock('@/components/study/ReadAloudButton', () => ({
  ReadAloudButton: () => null,
}));

vi.mock('@/components/study/QuizModal', () => ({
  QuizModal: () => null,
}));

vi.mock('@/components/ReviewPrompt', () => ({
  requestReviewPrompt: vi.fn(),
}));

import ChapterPage from './ChapterPage';
import { useLibraryBundle, useLibraryBundleSlim } from '@/hooks/useContent';

const SAMPLE_CHAPTER = {
  chapter_id:      'ch-prose-1',
  chapter_title:   'The Portrait of a Lady',
  topic_title:     'The Portrait of a Lady',
  subject_name:    'English',
  board_name:      'AHSEC',
  class_name:      'Class 11',
  stream_name:     'Arts',
  meta_description: 'Notes for The Portrait of a Lady.',
  content:         '## Introduction\n\nThis is the chapter content.',
  word_count:      500,
  generated_at:    '2025-01-01T00:00:00Z',
  updated_at:      '2025-04-01T00:00:00Z',
  faq_entries:     [],
  published_topics: [],
  topics_related:  { siblings: [], cross_chapter: [] },
};

class MockIntersectionObserver {
  observe    = vi.fn();
  unobserve  = vi.fn();
  disconnect = vi.fn();
  constructor(_cb, _opts) {}
}

beforeEach(() => {
  vi.useRealTimers();
  window.IntersectionObserver = MockIntersectionObserver;
  vi.mocked(useLibraryBundleSlim).mockReturnValue({ data: undefined });
  vi.mocked(useLibraryBundle).mockReturnValue({ data: undefined });
  if (typeof window !== 'undefined') {
    delete window.__CHAPTER_PRELOAD__;
  }
  window.scrollTo = vi.fn();
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  vi.restoreAllMocks();
  if (typeof window !== 'undefined') {
    delete window.__CHAPTER_PRELOAD__;
  }
});

describe('ChapterPage — axe accessibility audit', () => {
  it('has no axe violations while the chapter is loading (skeleton state)', async () => {
    let container;
    await act(async () => {
      ({ container } = render(<ChapterPage />));
    });
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  it('has no axe violations when chapter content has loaded (content state)', async () => {
    window.__CHAPTER_PRELOAD__ = {
      board:       'ahsec',
      classSlug:   'class-11',
      subjectSlug: 'english',
      chapterSlug: 'prose',
      data:        SAMPLE_CHAPTER,
    };

    let container;
    await act(async () => {
      ({ container } = render(<ChapterPage />));
    });
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});
