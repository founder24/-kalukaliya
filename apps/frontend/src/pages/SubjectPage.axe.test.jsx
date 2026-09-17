/**
 * Task #197 — SubjectPage: axe accessibility audit.
 *
 * Covers three render states for the subject/chapter browser:
 *  1. Loading state — animated skeleton while subject data is fetched
 *  2. Error state   — "Failed to load subject" UI when the request fails
 *  3. Loaded state  — subject header + chapter accordion (LegacyAccordion)
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { axe, toHaveNoViolations } from 'jest-axe';
import { render, act, fireEvent, screen, waitFor } from '@testing-library/react';
import React from 'react';

expect.extend(toHaveNoViolations);
const { mockApiGet } = vi.hoisted(() => ({ mockApiGet: vi.fn() }));
const { mockContentLang } = vi.hoisted(() => ({ mockContentLang: { value: 'en' } }));

vi.mock('react-router-dom', () => ({
  useParams: () => ({ subjectId: 'english-class-11' }),
  Link:      ({ children, to }) => <a href={to}>{children}</a>,
}));

vi.mock('@/components/layout/AppLayout', () => ({
  AppLayout: ({ children }) => <div data-testid="app-layout">{children}</div>,
}));

vi.mock('@/components/seo/PageMeta', () => ({
  default: () => null,
}));

vi.mock('@/components/ui/button', () => ({
  Button: ({ children, onClick, variant, className }) => (
    <button type="button" onClick={onClick} className={className}>{children}</button>
  ),
}));

vi.mock('@/components/ui/StickyToc', () => ({
  default: () => null,
}));

vi.mock('@/components/ui/skeleton', () => ({
  Skeleton: ({ className }) => <div className={className} aria-hidden="true" />,
}));

vi.mock('@/components/ui/accordion', () => ({
  Accordion:        ({ children }) => <div>{children}</div>,
  AccordionContent: ({ children }) => <div>{children}</div>,
  AccordionItem:    ({ children, value }) => <div data-value={value}>{children}</div>,
  AccordionTrigger: ({ children }) => <button type="button">{children}</button>,
}));

vi.mock('@/utils/api', () => ({
  getChunks:              vi.fn(),
  getChapterTopicSummary: vi.fn(),
  apiClient:              () => ({ get: mockApiGet }),
}));

vi.mock('@/hooks/useShare', () => ({
  useShare: () => ({ sharing: false, share: vi.fn() }),
}));

vi.mock('@/hooks/useContent', () => ({
  useSubject:  vi.fn(),
  useChapters: vi.fn(),
}));

vi.mock('@/context/LanguageContext', () => ({
  useContentLang: () => ({ contentLang: mockContentLang.value, switchLang: vi.fn() }),
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import SubjectPage from './SubjectPage';
import { useSubject, useChapters } from '@/hooks/useContent';

const SAMPLE_SUBJECT = {
  id:           'english-class-11',
  name:         'English',
  description:  'Class 11 English notes for AHSEC students.',
  is_cms_post:  false,
  board_name:   'AHSEC',
  class_name:   'Class 11',
  stream_name:  'Arts',
  icon:         '📖',
  tags:         ['notes', 'grammar'],
  board_slug:   'ahsec',
  class_slug:   'class-11',
  stream_slug:  'arts',
  slug:         'english',
};

const SAMPLE_CHAPTERS = [
  { id: 'ch1', name: 'Chapter 1: Prose',  title: 'Chapter 1: Prose',  chapter_number: 1, order: 1, slug: 'chapter-1-prose' },
  { id: 'ch2', name: 'Chapter 2: Poetry', title: 'Chapter 2: Poetry', chapter_number: 2, order: 2, slug: 'chapter-2-poetry' },
];

beforeEach(() => {
  vi.useRealTimers();
  mockContentLang.value = 'en';
  mockApiGet.mockReset();
  vi.mocked(useSubject).mockReturnValue({
    data:     undefined,
    isLoading: true,
    isError:  false,
    refetch:  vi.fn(),
  });
  vi.mocked(useChapters).mockReturnValue({ data: [], isLoading: true });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('SubjectPage — axe accessibility audit', () => {
  it('has no axe violations while the subject is loading (skeleton state)', async () => {
    let container;
    await act(async () => {
      ({ container } = render(<SubjectPage />));
    });
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  it('has no axe violations when the subject fails to load (error state)', async () => {
    vi.mocked(useSubject).mockReturnValue({
      data:      undefined,
      isLoading: false,
      isError:   true,
      refetch:   vi.fn(),
    });
    vi.mocked(useChapters).mockReturnValue({ data: [], isLoading: false });

    let container;
    await act(async () => {
      ({ container } = render(<SubjectPage />));
    });
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  it('has no axe violations when the subject and chapters have loaded (content state)', async () => {
    vi.mocked(useSubject).mockReturnValue({
      data:      SAMPLE_SUBJECT,
      isLoading: false,
      isError:   false,
      refetch:   vi.fn(),
    });
    vi.mocked(useChapters).mockReturnValue({ data: SAMPLE_CHAPTERS, isLoading: false });

    let container;
    await act(async () => {
      ({ container } = render(<SubjectPage />));
    });
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  it('loads and renders the Worker-native merged blog when Blog View is selected', async () => {
    vi.mocked(useSubject).mockReturnValue({
      data: SAMPLE_SUBJECT,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    });
    vi.mocked(useChapters).mockReturnValue({ data: SAMPLE_CHAPTERS, isLoading: false });
    mockApiGet.mockResolvedValue({
      data: {
        title: 'English Complete Study Guide',
        merged_md: '## Published chapter\n\nWorker-native study notes.',
        headings: JSON.stringify([{ level: 2, text: 'Published chapter', anchor: 'published-chapter' }]),
        word_count: 5,
      },
    });

    render(<SubjectPage />);
    fireEvent.click(screen.getByRole('button', { name: /Blog View/i }));

    await waitFor(() => expect(mockApiGet).toHaveBeenCalledWith('/cms/post/english-class-11'));
    expect(await screen.findByText('Worker-native study notes.')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Published chapter' })).toHaveAttribute('id', 'published-chapter');
  });

  it('uses Assamese chapter metadata and preserves the Questions tab in legacy links', async () => {
    mockContentLang.value = 'as';
    vi.mocked(useSubject).mockReturnValue({
      data: {
        ...SAMPLE_SUBJECT,
        name_as: 'পদাৰ্থবিজ্ঞান',
      },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    });
    vi.mocked(useChapters).mockReturnValue({
      data: [
        {
          ...SAMPLE_CHAPTERS[0],
          title: 'Motion',
          title_as: 'গতি',
          slug: 'motion',
          slug_as: 'goti',
          content_type: 'qa',
          has_qa: false,
          has_qa_as: true,
        },
        {
          ...SAMPLE_CHAPTERS[1],
          title: 'English Questions',
          title_as: 'ইংৰাজী প্ৰশ্ন',
          slug: 'english-questions',
          slug_as: 'english-questions-as',
          content_type: 'qa',
          has_qa: true,
          has_qa_as: false,
        },
      ],
      isLoading: false,
    });

    render(<SubjectPage />);
    expect(screen.getByRole('button', { name: /প্ৰশ্ন2/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /প্ৰশ্ন/ }));

    expect(screen.getAllByText('গতি').length).toBeGreaterThan(0);
    expect(screen.getByRole('link', { name: /ইংৰাজী প্ৰশ্ন — Questions/ })).toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: /Questions|প্ৰশ্ন|গতি/ }).some((link) =>
      link.getAttribute('href') === '/as/ahsec/class-11/english/goti?tab=qa'
    )).toBe(true);
  });
});
