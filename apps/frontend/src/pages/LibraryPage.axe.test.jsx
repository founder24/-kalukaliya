/**
 * Task #197 — LibraryPage: axe accessibility audit.
 *
 * Covers three key render states:
 *  1. Loading state (bundleLoading=true) — shows LibrarySkeleton
 *  2. Error state (no bundle, not loading) — shows "Failed to load library" UI
 *  3. Loaded state — subject catalog rendered with VirtualSubjectGrid
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { axe, toHaveNoViolations } from 'jest-axe';
import { render, act, fireEvent } from '@testing-library/react';
import React from 'react';

expect.extend(toHaveNoViolations);

vi.mock('react-router-dom', () => ({
  useNavigate: () => vi.fn(),
  Link: ({ children, to }) => <a href={to}>{children}</a>,
}));

vi.mock('@/context/AuthContext', () => ({
  useAuth: () => ({ user: null }),
}));

vi.mock('@/context/LanguageContext', () => ({
  useContentLang: () => ({ contentLang: 'en', switchLang: vi.fn(), toggleLang: vi.fn() }),
}));

vi.mock('@/components/layout/AppLayout', () => ({
  AppLayout: ({ children }) => <div data-testid="app-layout">{children}</div>,
}));

vi.mock('@/components/seo/PageMeta', () => ({
  default: () => null,
}));

vi.mock('@/utils/analytics', () => ({
  Analytics: { page: vi.fn(), event: vi.fn(), searchUsed: vi.fn() },
}));

vi.mock('@/hooks/useContent', () => ({
  useLibraryBundle:      vi.fn(),
  useLibraryBundleSlim:  vi.fn(),
  useLibraryBundleBoot:  vi.fn(),
  useQuestionPapers:     vi.fn(() => ({ data: [], isLoading: false })),
  useSavedSubjects:      vi.fn(),
}));

vi.mock('@/hooks/useUser', () => ({
  useToggleSavedSubject: vi.fn(),
}));

vi.mock('./library/SubjectCard', () => ({
  default: ({ sub }) => <div data-testid="library-subject-card" data-subject-id={sub.id} />,
}));

vi.mock('./library/VirtualSubjectGrid', () => ({
  default: () => <div />,
}));

vi.mock('./library/LibrarySkeleton', () => ({
  default: () => <div data-testid="library-skeleton" role="status" aria-label="Loading library" />,
}));

vi.mock('./library/FilterChip', () => ({
  default: ({ chip, onClick }) => <button type="button" onClick={onClick}>{chip?.label}</button>,
}));

vi.mock('./library/ScrollableFilterRow', () => ({
  default: ({ children }) => <div>{children}</div>,
}));

vi.mock('./library/icons', () => ({
  Search:   () => null,
  Bookmark: () => null,
  BookOpen: () => null,
}));

vi.mock('./library/CmsDocsSection', () => ({ default: () => null }));
vi.mock('./library/CmsPostsGrid',   () => ({ default: () => null }));

vi.mock('@/components/content/TrustpilotReviewsSection', () => ({
  default: () => null,
}));

vi.mock('@/utils/recentChapters', () => ({
  getRecentChapters:   vi.fn(() => []),
  clearRecentChapters: vi.fn(),
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import LibraryPage from './LibraryPage';
import {
  useLibraryBundle,
  useLibraryBundleSlim,
  useLibraryBundleBoot,
  useSavedSubjects,
} from '@/hooks/useContent';
import { useToggleSavedSubject } from '@/hooks/useUser';
import { getRecentChapters } from '@/utils/recentChapters';
import { Analytics } from '@/utils/analytics';

const SAMPLE_BUNDLE = {
  boards:   [{ id: 'b1', name: 'AHSEC' }],
  classes:  [{ id: 'cl1', name: 'Class 11', board_id: 'b1' }],
  streams:  [{ id: 's1', name: 'Arts', class_id: 'cl1' }],
  subjects: [
    { id: 'sub1', name: 'English', class_id: 'cl1', stream_id: 's1', board_id: 'b1', icon: '📖', chapter_count: 5 },
    { id: 'sub2', name: 'Political Science', class_id: 'cl1', stream_id: 's1', board_id: 'b1', icon: '🏛️', chapter_count: 8 },
  ],
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
  vi.mocked(useLibraryBundleSlim).mockReturnValue({ data: undefined, isLoading: true });
  vi.mocked(useLibraryBundleBoot).mockReturnValue({ data: undefined });
  vi.mocked(useLibraryBundle).mockReturnValue({ data: undefined, isFetching: false, refetch: vi.fn() });
  vi.mocked(useSavedSubjects).mockReturnValue({ data: [] });
  vi.mocked(useToggleSavedSubject).mockReturnValue({ mutate: vi.fn() });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('LibraryPage — axe accessibility audit', () => {
  it('has no axe violations while the subject bundle is loading (skeleton state)', async () => {
    let container;
    await act(async () => {
      ({ container } = render(<LibraryPage />));
    });
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  it('has no axe violations when the bundle fails to load (error state)', async () => {
    vi.mocked(useLibraryBundleSlim).mockReturnValue({ data: undefined, isLoading: false });

    let container;
    await act(async () => {
      ({ container } = render(<LibraryPage />));
    });
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  it('has no axe violations when the subject catalog has loaded (content state)', async () => {
    vi.mocked(useLibraryBundleSlim).mockReturnValue({ data: SAMPLE_BUNDLE, isLoading: false });
    vi.mocked(useLibraryBundleBoot).mockReturnValue({ data: SAMPLE_BUNDLE });
    vi.mocked(useLibraryBundle).mockReturnValue({
      data: SAMPLE_BUNDLE, isFetching: false, refetch: vi.fn(),
    });

    let container;
    await act(async () => {
      ({ container } = render(<LibraryPage />));
    });
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  it('keeps the first twenty SSR-visible cards stable after personalized ranking hydrates', async () => {
    const subjects = Array.from({ length: 22 }, (_, index) => ({
      id: `sub${index + 1}`,
      slug: `subject-${index + 1}`,
      name: `Subject ${index + 1}`,
      class_id: index < 11 ? 'cl1' : 'cl2',
      stream_id: index < 11 ? 's1' : 's2',
      board_id: 'b1',
      chapter_count: 1,
    }));
    const bundle = {
      ...SAMPLE_BUNDLE,
      classes: [
        { id: 'cl1', name: 'Class 11', board_id: 'b1' },
        { id: 'cl2', name: 'Class 12', board_id: 'b1' },
      ],
      streams: [
        { id: 's1', name: 'Arts', class_id: 'cl1' },
        { id: 's2', name: 'Science', class_id: 'cl2' },
      ],
      subjects,
    };
    vi.mocked(getRecentChapters).mockReturnValue([
      { subject: 'Subject 22', path: '/ahsec/class-11/subject-22/chapter-1' },
    ]);
    vi.mocked(useLibraryBundleSlim).mockReturnValue({ data: bundle, isLoading: false });
    vi.mocked(useLibraryBundleBoot).mockReturnValue({ data: bundle });
    vi.mocked(useLibraryBundle).mockReturnValue({
      data: bundle, isFetching: false, refetch: vi.fn(),
    });

    let container;
    await act(async () => {
      ({ container } = render(<LibraryPage />));
    });

    const firstChunkIds = [...container.querySelectorAll('[data-testid="library-subject-card"]')]
      .map((card) => card.getAttribute('data-subject-id'));
    expect(firstChunkIds).toEqual([
      ...subjects.slice(0, 10),
      ...subjects.slice(11, 21),
    ].map((subject) => subject.id));
  });

  it('cancels pending search analytics when the page unmounts', async () => {
    vi.useFakeTimers();
    vi.mocked(useLibraryBundleSlim).mockReturnValue({ data: SAMPLE_BUNDLE, isLoading: false });
    vi.mocked(useLibraryBundleBoot).mockReturnValue({ data: SAMPLE_BUNDLE });
    vi.mocked(useLibraryBundle).mockReturnValue({
      data: SAMPLE_BUNDLE, isFetching: false, refetch: vi.fn(),
    });

    let view;
    await act(async () => {
      view = render(<LibraryPage />);
    });
    fireEvent.change(view.getAllByTestId('library-search-input')[0], {
      target: { value: 'English' },
    });
    view.unmount();
    await act(async () => {
      vi.runAllTimers();
    });

    expect(Analytics.searchUsed).not.toHaveBeenCalled();
    vi.useRealTimers();
  });
});
