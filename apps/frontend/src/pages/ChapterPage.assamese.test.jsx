/**
 * ChapterPage — Assamese content availability tests.
 *
 * Verifies the reader tab behaves correctly when `has_assamese` is false
 * and `content_as` is empty:
 *
 *  1. A visible "Assamese not yet available" notice is shown (not a blank tab).
 *  2. The English content fallback is rendered, so the reader is never blank.
 *  3. Switching to the English tab shows full English content without any notice.
 *  4. When `has_assamese` is true and `content_as` is populated the notice
 *     is absent and the Assamese content is rendered.
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

let mockContentLang = 'en';
const mockSwitchLang = vi.fn();
vi.mock('@/context/LanguageContext', () => ({
  useContentLang: () => ({ contentLang: mockContentLang, switchLang: mockSwitchLang }),
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
vi.mock('@/components/chapter/RelatedTopicsNav',   () => ({ default: vi.fn(() => null) }));

// ─── import component (after all mocks are registered) ──────────────────────

import ChapterPage from './ChapterPage';

// ─── helpers ────────────────────────────────────────────────────────────────

/** A minimal chapter payload with English content but no Assamese. */
function makeChapter(overrides = {}) {
  return {
    chapter_id:       'ch-prose-1',
    chapter_title:    'The Portrait of a Lady',
    topic_title:      'The Portrait of a Lady',
    subject_name:     'English',
    board_name:       'AHSEC',
    class_name:       'Class 11',
    meta_description: 'Notes for The Portrait of a Lady.',
    content:          '## Introduction\n\nThis is the English chapter content.',
    word_count:       500,
    generated_at:     '2025-01-01T00:00:00Z',
    updated_at:       '2025-04-01T00:00:00Z',
    faq_entries:      [],
    published_topics: [],
    topics_related:   { siblings: [], cross_chapter: [] },
    // Assamese unavailable by default
    has_assamese:     false,
    content_as:       '',
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
  mockSwitchLang.mockReset();
  mockApiGet.mockResolvedValue({ data: {} });
  mockContentLang = 'en';
});

afterEach(() => {
  vi.restoreAllMocks();
  delete window.__CHAPTER_PRELOAD__;
});

// ─── tests ──────────────────────────────────────────────────────────────────

describe('ChapterPage — has_assamese=false reader tab', () => {
  it('shows the "Assamese unavailable" notice when has_assamese is false and contentLang is as', async () => {
    mockContentLang = 'as';
    const chapter = makeChapter({ has_assamese: false, content_as: '' });
    seedPreload(chapter);

    await act(async () => {
      render(<ChapterPage />);
    });

    // The bilingual "not yet available" notice must be visible — not a blank page.
    const notice = screen.getByTestId('assamese-unavailable-notice');
    expect(notice).toBeTruthy();
    expect(notice.textContent).toContain('Assamese version not yet available');
  });

  it('does NOT leave the reader blank when has_assamese is false — shows English fallback content', async () => {
    mockContentLang = 'as';
    const chapter = makeChapter({ has_assamese: false, content_as: '' });
    seedPreload(chapter);

    await act(async () => {
      render(<ChapterPage />);
    });

    // English content must be rendered as the fallback — the tab must not be blank.
    const content = screen.getByTestId('notes-content');
    expect(content).toBeTruthy();
    expect(content.textContent).toContain('Introduction');

    // The notes-empty-state sentinel must NOT appear (content IS available in English).
    expect(screen.queryByTestId('notes-empty-state')).toBeNull();
  });

  it('does NOT show the unavailable notice when contentLang is en, even if has_assamese is false', async () => {
    mockContentLang = 'en';
    const chapter = makeChapter({ has_assamese: false, content_as: '' });
    seedPreload(chapter);

    await act(async () => {
      render(<ChapterPage />);
    });

    // English tab: no notice, full content visible.
    expect(screen.queryByTestId('assamese-unavailable-notice')).toBeNull();
    expect(screen.getByTestId('notes-content')).toBeTruthy();
  });

  it('does NOT show the unavailable notice when has_assamese is true and content_as is populated', async () => {
    mockContentLang = 'as';
    const chapter = makeChapter({
      has_assamese: true,
      content_as:   '## পৰিচয়\n\nঅসমীয়া বিষয়বস্তু।',
    });
    seedPreload(chapter);

    await act(async () => {
      render(<ChapterPage />);
    });

    // Assamese content is present — notice must be absent.
    expect(screen.queryByTestId('assamese-unavailable-notice')).toBeNull();

    // Assamese content must be rendered.
    const content = screen.getByTestId('notes-content');
    expect(content.textContent).toContain('পৰিচয়');
  });

  it('English tab remains fully usable when has_assamese is false', async () => {
    // Start in Assamese mode then simulate switching to English by re-rendering
    // with contentLang='en' (the switchLang callback would update LanguageContext).
    mockContentLang = 'as';
    const chapter = makeChapter({ has_assamese: false, content_as: '' });
    seedPreload(chapter);

    const { rerender } = await act(async () => render(<ChapterPage />));

    // Assamese mode: notice visible, English fallback content shown.
    expect(screen.getByTestId('assamese-unavailable-notice')).toBeTruthy();

    // Simulate switching to English (LanguageContext updates contentLang).
    mockContentLang = 'en';
    await act(async () => { rerender(<ChapterPage />); });

    // English tab: notice gone, full content visible.
    expect(screen.queryByTestId('assamese-unavailable-notice')).toBeNull();
    const content = screen.getByTestId('notes-content');
    expect(content).toBeTruthy();
    expect(content.textContent).toContain('Introduction');
  });

  it('shows the notice and English fallback when has_assamese is false but content_as has a stale value', async () => {
    // Edge case: a previous seed run left content_as populated but has_assamese was later
    // reset to false (e.g. the content was detected as invalid and rejected).
    // The component must trust has_assamese, not content_as, to decide availability.
    mockContentLang = 'as';
    const chapter = makeChapter({
      has_assamese: false,
      content_as:   '## stale Assamese content that should not be shown',
    });
    seedPreload(chapter);

    await act(async () => {
      render(<ChapterPage />);
    });

    // Notice must be visible because has_assamese=false, regardless of content_as.
    const notice = screen.getByTestId('assamese-unavailable-notice');
    expect(notice).toBeTruthy();
    expect(notice.textContent).toContain('Assamese version not yet available');

    // English content must be the fallback — not the stale Assamese value.
    const content = screen.getByTestId('notes-content');
    expect(content.textContent).toContain('Introduction');
    expect(content.textContent).not.toContain('stale Assamese content');
  });

  it('shows notes-empty-state (not blank) when has_assamese is false and English content is also absent', async () => {
    // Edge case: chapter has no English notes yet AND no Assamese content.
    mockContentLang = 'as';
    const chapter = makeChapter({ has_assamese: false, content_as: '', content: '' });
    seedPreload(chapter);

    await act(async () => {
      render(<ChapterPage />);
    });

    // The reader must still show a human-readable empty state, not a blank area.
    const emptyState = screen.getByTestId('notes-empty-state');
    expect(emptyState).toBeTruthy();
    // In Assamese mode the label is in Assamese.
    expect(emptyState.textContent).toContain('নোট');
  });
});
