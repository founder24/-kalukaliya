import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import ChapterTopicGraph from './ChapterTopicGraph';

describe('ChapterTopicGraph', () => {
  it('shows only approved topic pages for the selected language', () => {
    render(
      <MemoryRouter>
        <ChapterTopicGraph
          language="as"
          siblings={[
            { topic_id: 'as-approved', topic_slug: 'gati', title: 'গতি', language: 'as', page_available: true },
            { topic_id: 'en-only', topic_slug: 'motion', title: 'Motion', language: 'en', page_available: true },
            { topic_id: 'as-incomplete', topic_slug: 'bal', title: 'বল', language: 'as', page_available: false },
          ]}
          crossChapter={[
            {
              topic_id: 'cross-approved',
              topic_slug: 'newtons-laws',
              title: 'নিউটনৰ সূত্ৰ',
              language: 'as',
              page_available: true,
              deep_link_path: '/as/degree/1st-semester/ba/physics/laws/topic/newtons-laws',
            },
            {
              topic_id: 'cross-incomplete',
              topic_slug: 'energy',
              title: 'শক্তি',
              language: 'as',
              page_available: true,
            },
          ]}
        />
      </MemoryRouter>,
    );

    expect(screen.getByRole('link', { name: 'গতি' })).toHaveAttribute('href', '#topic-gati');
    expect(screen.getByRole('link', { name: /নিউটনৰ সূত্ৰ/ })).toHaveAttribute(
      'href',
      '/as/degree/1st-semester/ba/physics/laws/topic/newtons-laws',
    );
    expect(screen.queryByText('Motion')).not.toBeInTheDocument();
    expect(screen.queryByText('বল')).not.toBeInTheDocument();
    expect(screen.queryByText('শক্তি')).not.toBeInTheDocument();
  });
});
