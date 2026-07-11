/**
 * ChapterPage — tab-sharing graceful fallback tests.
 *
 * Verifies that shared URLs with a ?tab= param work correctly even
 * when the chapter lacks the corresponding content type:
 *
 *  1. ?tab=pyq on a chapter with no pyq_pdf_url → Notes tab active,
 *     content area is NOT blank, URL param is NOT rewritten automatically.
 *  2. ?tab=qa on any chapter → Q&A tab is active.
 *  3. ?tab=pyq on a chapter WITH pyq_pdf_url → PYQ viewer is shown.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import React from 'react';

// ─── router mock (must be defined before component import) ──────────────────

const mockSetSearchParams = vi.fn();
let mockSearchParams = new URLSearchParams();

vi.mock('react-router-dom', () => ({
  useParams: () => ({
    board:       'ahsec',
    classSlug:   'class-11',
    subjectSlug: 'english',
    chapterSlug: 'prose',
  }),
  useSearchParams: () => [mockSearchParams, mockSetSearchParams],
  Link: ({ children, to }) => <a href={to}>{children}</a>,
}));

// ─── lightweight stubs for every import ChapterPage touches ─────────────────

vi.mock('@/components/seo/PageMeta', () => ({ default: () => null }));

vi.mock('@/components/MarkdownRenderer', () => ({
  default: ({ children }) => <div data-testid="notes-content">{children}</div>,
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

vi.mock('@/hooks/useHashScroll', () => ({ useHashScroll: vi.fn() }));

vi.mock('@/components/ui/badge', () => ({
  Badge: ({ children }) => <span>{children}</span>,
}));

vi.mock('@/components/ui/skeleton', () => ({
  Skeleton: ({ className }) => <div className={className} aria-hidden="true" />,
}));

const mockApiGet = vi.fn().mockResolvedValue({ data: {} });
vi.mock('@/utils/api', () => ({
  apiClient:           () => ({ get: mockApiGet }),
  seoRelatedByChapter: vi.fn().mockResolvedValue([]),
}));

vi.mock('@/hooks/useShare', () => ({
  useShare:         () => ({ sharing: false, share: vi.fn(), serpPreview: null, confirmShare: vi.fn(), dismissPreview: vi.fn() }),
  SerpPreviewModal: () => null,
}));

vi.mock('@/utils/analytics', () => ({
  default: {
    page:         vi.fn(),
    event:        vi.fn(),
    chapterView:  vi.fn(),
    chapterRetry: vi.fn(),
    chapterShare: vi.fn(),
    chapterAskAi: vi.fn(),
    scrollDepth:  vi.fn(),
    tocClick:     vi.fn(),
  },
}));

vi.mock('@/context/LanguageContext', () => ({
  useContentLang: () => ({ contentLang: 'en', switchLang: vi.fn() }),
}));

vi.mock('@/components/ui/StickyToc',            () => ({ default: () => null }));
vi.mock('@/components/content/ContinueLearning', () => ({ default: () => null }));
vi.mock('@/components/layout/MobileNavSwitch',   () => ({ MobileNavSwitch: () => null }));

vi.mock('@/hooks/useContent', () => ({
  useLibraryBundle:     vi.fn().mockReturnValue({ data: undefined }),
  useLibraryBundleSlim: vi.fn().mockReturnValue({ data: undefined }),
}));

vi.mock('@/utils/siblingChapter', () => ({
  findSiblingChapters: vi.fn(() => ({ prev: null, next: null })),
  siblingsAsRelated:   vi.fn(() => []),
}));

vi.mock('@/utils/recentChapters',           () => ({ pushRecentChapter: vi.fn() }));
vi.mock('@/components/study/HighlightSavePopover', () => ({ HighlightSavePopover: () => null }));
vi.mock('@/components/study/ReadAloudButton',      () => ({ ReadAloudButton: () => null }));
vi.mock('@/components/study/QuizModal',            () => ({ QuizModal: () => null }));
vi.mock('@/components/ReviewPrompt',               () => ({ requestReviewPrompt: vi.fn() }));
vi.mock('@/components/chapter/RelatedTopicsNav',   () => ({ default: () => null }));

// ─── import component (after all mocks are registered) ──────────────────────

import ChapterPage from './ChapterPage';

// ─── helpers ────────────────────────────────────────────────────────────────

/** A minimal chapter payload — no pyq_pdf_url by default. */
function makeChapter(overrides = {}) {
  return {
    chapter_id:       'ch-prose-1',
    chapter_title:    'The Portrait of a Lady',
    topic_title:      'The Portrait of a Lady',
    subject_name:     'English',
    board_name:       'AHSEC',
    class_name:       'Class 11',
    meta_description: 'Notes for The Portrait of a Lady.',
    content:          '## Introduction\n\nThis is the chapter content.',
    word_count:       500,
    generated_at:     '2025-01-01T00:00:00Z',
    updated_at:       '2025-04-01T00:00:00Z',
    faq_entries:      [],
    published_topics: [],
    topics_related:   { siblings: [], cross_chapter: [] },
    ...overrides,
  };
}

/** Seed window.__CHAPTER_PRELOAD__ so the component skips the API fetch. */
function seedPreload(chapter) {
  window.__CHAPTER_PRELOAD__ = {
    board:       'ahsec',
    classSlug:   'class-11',
    subjectSlug: 'english',
    chapterSlug: 'prose',
    data:        chapter,
  };
}

class MockIntersectionObserver {
  observe    = vi.fn();
  unobserve  = vi.fn();
  disconnect = vi.fn();
  constructor(_cb, _opts) {}
}

// ─── setup / teardown ───────────────────────────────────────────────────────

beforeEach(() => {
  vi.useRealTimers();
  window.IntersectionObserver = MockIntersectionObserver;
  window.scrollTo             = vi.fn();
  Element.prototype.scrollIntoView = vi.fn();
  delete window.__CHAPTER_PRELOAD__;
  mockSearchParams = new URLSearchParams();
  mockSetSearchParams.mockReset();
  mockApiGet.mockResolvedValue({ data: {} });
});

afterEach(() => {
  vi.restoreAllMocks();
  delete window.__CHAPTER_PRELOAD__;
});

// ─── tests ──────────────────────────────────────────────────────────────────

describe('ChapterPage — ?tab= URL sharing', () => {
  it('falls back to Notes when ?tab=pyq but the chapter has no pyq_pdf_url', async () => {
    // Chapter has NO pyq_pdf_url
    const chapter = makeChapter();
    seedPreload(chapter);
    mockSearchParams = new URLSearchParams('tab=pyq');

    await act(async () => {
      render(<ChapterPage />);
    });

    // Notes content must be visible — no blank area
    expect(screen.getByTestId('notes-content')).toBeTruthy();

    // PYQ viewer must NOT be present — it was never available
    expect(screen.queryByTestId('pyq-viewer')).toBeNull();

    // Q&A cards must NOT be present — we fell back to notes, not qa
    expect(screen.queryByTestId('topic-answer-cards')).toBeNull();
  });

  it('does NOT rewrite the URL param when falling back from ?tab=pyq to notes', async () => {
    const chapter = makeChapter();
    seedPreload(chapter);
    mockSearchParams = new URLSearchParams('tab=pyq');

    await act(async () => {
      render(<ChapterPage />);
    });

    // switchTab (which calls setSearchParams) must NOT have been called —
    // the stale ?tab=pyq stays in the URL until the user manually switches.
    expect(mockSetSearchParams).not.toHaveBeenCalled();
  });

  it('shows the Q&A tab when ?tab=qa is in the URL', async () => {
    const chapter = makeChapter();
    seedPreload(chapter);
    mockSearchParams = new URLSearchParams('tab=qa');

    await act(async () => {
      render(<ChapterPage />);
    });

    // Q&A cards panel must be present
    expect(screen.getByTestId('topic-answer-cards')).toBeTruthy();

    // Notes markdown must NOT be visible
    expect(screen.queryByTestId('notes-content')).toBeNull();
  });

  it('renders a non-blank empty state with a View Notes button when ?tab=qa but no Q&A content', async () => {
    // published_topics is empty (default in makeChapter)
    const chapter = makeChapter();
    seedPreload(chapter);
    mockSearchParams = new URLSearchParams('tab=qa');

    await act(async () => {
      render(<ChapterPage />);
    });

    // The Q&A container must render (not blank)
    expect(screen.getByTestId('topic-answer-cards')).toBeTruthy();

    // The empty state block must be visible
    expect(screen.getByTestId('qa-empty-state')).toBeTruthy();

    // A "View Notes" escape hatch must be present
    expect(screen.getByTestId('qa-empty-view-notes')).toBeTruthy();
  });

  it('shows the PYQ viewer when ?tab=pyq and pyq_pdf_url is present', async () => {
    const chapter = makeChapter({ pyq_pdf_url: 'https://example.com/paper.pdf' });
    seedPreload(chapter);
    mockSearchParams = new URLSearchParams('tab=pyq');

    await act(async () => {
      render(<ChapterPage />);
    });

    expect(screen.getByTestId('pyq-viewer')).toBeTruthy();
    expect(screen.queryByTestId('notes-content')).toBeNull();
  });

  it('defaults to Notes when no ?tab= param is present', async () => {
    const chapter = makeChapter();
    seedPreload(chapter);
    // mockSearchParams is an empty URLSearchParams by default

    await act(async () => {
      render(<ChapterPage />);
    });

    expect(screen.getByTestId('notes-content')).toBeTruthy();
    expect(screen.queryByTestId('topic-answer-cards')).toBeNull();
    expect(screen.queryByTestId('pyq-viewer')).toBeNull();
  });
});
