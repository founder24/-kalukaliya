import { describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';

const mockContentLanguage = vi.hoisted(() => ({ value: 'en' }));

vi.mock('react-router-dom', () => ({
  Link: ({ children, to, ...props }) => <a href={to} {...props}>{children}</a>,
}));

vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => ({ prefetchQuery: vi.fn() }),
}));

vi.mock('@/context/LanguageContext', () => ({
  useContentLang: () => ({ contentLang: mockContentLanguage.value }),
}));

vi.mock('@/hooks/useContent', () => ({
  prefetchSubjectData: vi.fn(),
}));

vi.mock('@/hooks/useShare', () => ({
  useShare: () => ({ sharing: false, share: vi.fn() }),
}));

vi.mock('@/utils/analytics', () => ({
  default: { subjectBookmarked: vi.fn() },
}));

import { getSubjectLandingPath } from './SubjectCard';
import SubjectCard from './SubjectCard';

describe('SubjectCard share destination', () => {
  it('uses the canonical subject page represented by normalized card data', () => {
    expect(getSubjectLandingPath({
      id: 'physics-id',
      boardSlug: 'ahsec',
      classSlug: 'hs-1st-year',
      slug: 'physics',
    })).toBe('/ahsec/hs-1st-year/physics');
  });

  it('uses the same canonical page for raw API snake_case data', () => {
    expect(getSubjectLandingPath({
      id: 'physics-id',
      board_slug: 'ahsec',
      class_slug: 'hs-1st-year',
      subject_slug: 'physics',
    })).toBe('/ahsec/hs-1st-year/physics');
  });

  it.each([
    ['English', 'en'],
    ['Assamese', 'as'],
  ])('keeps a notes-less chapter visible in %s mode', (language, contentLang) => {
    mockContentLanguage.value = contentLang;
    const subject = {
      id: 'physics-id',
      name: 'Physics',
      boardName: 'AHSEC',
      className: 'Class 11',
      boardSlug: 'ahsec',
      classSlug: 'hs-1st-year',
      slug: 'physics',
    };
    const notesLessChapter = {
      id: 'chapter-without-notes',
      title: 'Units and Measurement',
      title_as: 'একক আৰু পৰিমাপ',
      slug: 'units-and-measurement',
      slug_as: 'ekok-aru-porimap',
      notes_en: '',
      notes_as: '',
      notes_generated: false,
      content_type: 'notes',
    };

    render(
      <SubjectCard
        sub={subject}
        chapters={[notesLessChapter]}
        isSaved={false}
        onToggleSave={vi.fn()}
        onAskAI={vi.fn()}
        index={0}
      />,
    );

    expect(screen.getByRole('button', {
      name: new RegExp(`${contentLang === 'as' ? 'টোকা' : 'Notes'}\\s*1`),
    })).toBeInTheDocument();

    const chapterLink = screen.getByRole('link', {
      name: contentLang === 'as' ? 'একক আৰু পৰিমাপ' : 'Units and Measurement',
    });
    expect(chapterLink).toHaveAttribute(
      'href',
      contentLang === 'as'
        ? '/as/ahsec/hs-1st-year/physics/ekok-aru-porimap'
        : '/ahsec/hs-1st-year/physics/units-and-measurement',
    );
    expect(chapterLink).toHaveStyle({ opacity: '0.5' });
  });

  it('renders chapters in syllabus order even when the bundle is unsorted', () => {
    mockContentLanguage.value = 'en';
    const subject = {
      id: 'physics-id',
      name: 'Physics',
      boardName: 'AHSEC',
      className: 'Class 11',
      boardSlug: 'ahsec',
      classSlug: 'hs-1st-year',
      slug: 'physics',
    };
    const chapters = [
      { id: 'ch7', title: 'Gravitation', slug: 'gravitation', chapter_number: 7, content_type: 'notes' },
      { id: 'ch3', title: 'Motion in a Plane', slug: 'motion-in-a-plane', chapter_number: 3, content_type: 'notes' },
      { id: 'ch1', title: 'Units and Measurements', slug: 'units-and-measurements', chapter_number: 1, content_type: 'notes' },
      { id: 'ch2', title: 'Motion in a Straight Line', slug: 'motion-in-a-straight-line', chapter_number: 2, content_type: 'notes' },
    ];

    render(
      <SubjectCard
        sub={subject}
        chapters={chapters}
        isSaved={false}
        onToggleSave={vi.fn()}
        onAskAI={vi.fn()}
        index={0}
      />,
    );

    const chapterLinks = screen.getAllByRole('link').filter((link) =>
      chapters.some((chapter) => link.textContent.includes(chapter.title)),
    );
    expect(chapterLinks.map((link) => link.textContent.trim())).toEqual([
      'Units and Measurements',
      'Motion in a Straight Line',
      'Motion in a Plane',
    ]);
  });

  it('uses the Assamese subject destination and preserves the Questions tab', () => {
    mockContentLanguage.value = 'as';
    const subject = {
      id: 'physics-id',
      name: 'Physics',
      name_as: 'পদাৰ্থবিজ্ঞান',
      boardSlug: 'ahsec',
      classSlug: 'hs-1st-year',
      slug: 'physics',
    };

    render(
      <SubjectCard
        sub={subject}
        chapters={[{
          id: 'qa-chapter',
          title: 'Motion questions',
          title_as: 'গতিৰ প্ৰশ্ন',
          slug: 'motion-questions',
          slug_as: 'gotir-prashna',
          content_type: 'notes',
          has_qa: false,
          has_qa_as: true,
        }]}
        isSaved={false}
        onToggleSave={vi.fn()}
        onAskAI={vi.fn()}
        index={0}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /প্ৰশ্ন\s*1/ }));
    expect(screen.getByRole('link', { name: 'গতিৰ প্ৰশ্ন' })).toHaveAttribute(
      'href',
      '/as/ahsec/hs-1st-year/physics/gotir-prashna?tab=qa',
    );
    expect(screen.getByRole('link', { name: /View পদাৰ্থবিজ্ঞান/ })).toHaveAttribute(
      'href',
      '/as/ahsec/hs-1st-year/physics',
    );
  });
});
