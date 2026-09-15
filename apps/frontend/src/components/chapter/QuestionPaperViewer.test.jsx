import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import QuestionPaperViewer, { normalizeQuestionPaperPages } from './QuestionPaperViewer';

vi.mock('@/components/ads/AdSlot', () => ({
  default: ({ placement }) => <div data-testid={`ad-${placement}`} />,
}));

describe('QuestionPaperViewer', () => {
  it('normalizes the chapter API page shape without changing order', () => {
    const pages = normalizeQuestionPaperPages([
      { id: 'first', url: '/first.jpg', uploaded_at: '2026-09-15T00:00:00Z' },
      { id: 'second', url: '/second.jpg', uploaded_at: '2026-09-15T00:00:01Z' },
    ]);

    expect(pages.map(page => page.url)).toEqual(['/first.jpg', '/second.jpg']);
  });

  it('flattens legacy grouped file URLs and places an ad between pages', () => {
    render(
      <QuestionPaperViewer
        papers={[{ id: 'paper', exam_year: 2024, file_urls: ['/one.jpg', '/two.jpg', '/three.jpg'] }]}
      />,
    );

    expect(screen.getAllByRole('img').map(image => image.getAttribute('src'))).toEqual([
      '/one.jpg', '/two.jpg', '/three.jpg',
    ]);
    expect(screen.getAllByTestId('ad-chapter.pyq.betweenImages')).toHaveLength(2);
  });
});