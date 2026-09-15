import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
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
});
