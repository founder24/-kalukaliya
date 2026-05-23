/**
 * Task #202 — SubjectLandingPage: axe accessibility audit.
 *
 * Covers two key render states:
 *  1. Loading state — skeleton while subject/chapters are fetched
 *  2. Loaded state  — subject header + full chapter list rendered
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { axe, toHaveNoViolations } from 'jest-axe';
import { render, act } from '@testing-library/react';
import React from 'react';

expect.extend(toHaveNoViolations);

vi.mock('react-router-dom', () => ({
  useParams: () => ({ board: 'ahsec', classSlug: 'class-11', subjectSlug: 'english' }),
  Link:      ({ children, to }) => <a href={to}>{children}</a>,
}));

vi.mock('@/components/seo/PageMeta', () => ({
  default: () => null,
}));

vi.mock('@/components/ui/badge', () => ({
  Badge: ({ children, className }) => <span className={className}>{children}</span>,
}));

vi.mock('@/components/ui/skeleton', () => ({
  Skeleton: ({ className }) => <div className={className} aria-hidden="true" />,
}));

vi.mock('@/hooks/useContent', () => ({
  useResolveSubject: vi.fn(),
  useChapters:       vi.fn(),
}));

vi.mock('@/components/content/ContinueLearning', () => ({
  default: () => null,
}));

vi.mock('@/components/content/TrustpilotReviewsSection', () => ({
  default: () => null,
}));

vi.mock('@/components/subject/SubjectTopicIndex', () => ({
  default: () => null,
}));

vi.mock('@/utils/api', () => ({
  apiClient:           () => ({ get: vi.fn().mockResolvedValue({ data: { chapters: [], total_topics: 0 } }) }),
  seoRelatedByChapter: vi.fn().mockResolvedValue([]),
}));

vi.mock('@/components/layout/MobileNavSwitch', () => ({
  MobileNavSwitch: () => null,
}));

vi.mock('@/context/LanguageContext', () => ({
  useContentLang: () => ({ contentLang: 'en', switchLang: vi.fn() }),
}));

vi.mock('@/utils/siblingChapter', () => ({
  siblingsAsRelated: vi.fn(() => []),
}));

import SubjectLandingPage from './SubjectLandingPage';
import { useResolveSubject, useChapters } from '@/hooks/useContent';

const SAMPLE_SUBJECT = {
  id:          'sub-english-11',
  name:        'English',
  description: 'Class 11 English notes for AHSEC students.',
  icon:        '📖',
  board_name:  'AHSEC',
  class_name:  'Class 11',
  stream_name: 'Arts',
  tags:        ['notes', 'grammar'],
};

const SAMPLE_CHAPTERS = [
  { id: 'ch1', title: 'The Portrait of a Lady', slug: 'the-portrait-of-a-lady', description: 'A grandmother and her grandson.', content_type: 'notes' },
  { id: 'ch2', title: "We're Not Afraid to Die",  slug: 'were-not-afraid-to-die',  description: 'A family at sea.',               content_type: 'notes' },
];

beforeEach(() => {
  vi.useRealTimers();
  vi.mocked(useResolveSubject).mockReturnValue({ data: null, isLoading: true, error: null });
  vi.mocked(useChapters).mockReturnValue({ data: [], isLoading: true });
  if (typeof window !== 'undefined') {
    delete window.__SUBJECT_PRELOAD__;
  }
});

afterEach(() => {
  vi.restoreAllMocks();
  if (typeof window !== 'undefined') {
    delete window.__SUBJECT_PRELOAD__;
  }
});

describe('SubjectLandingPage — axe accessibility audit', () => {
  it('has no axe violations while the subject is loading (skeleton state)', async () => {
    let container;
    await act(async () => {
      ({ container } = render(<SubjectLandingPage />));
    });
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  it('has no axe violations when subject and chapters have loaded (content state)', async () => {
    vi.mocked(useResolveSubject).mockReturnValue({ data: SAMPLE_SUBJECT, isLoading: false, error: null });
    vi.mocked(useChapters).mockReturnValue({ data: SAMPLE_CHAPTERS, isLoading: false });

    let container;
    await act(async () => {
      ({ container } = render(<SubjectLandingPage />));
    });
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});
