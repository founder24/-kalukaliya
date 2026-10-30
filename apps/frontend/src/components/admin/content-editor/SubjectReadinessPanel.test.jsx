import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import SubjectReadinessPanel from './SubjectReadinessPanel';

describe('SubjectReadinessPanel', () => {
  it('shows server scores and specific remediation for failed checks', () => {
    render(
      <SubjectReadinessPanel
        readiness={{
          required: true,
          can_publish: false,
          scores: { seo: 85, geo: 80, aeo: 70 },
          rag_score: 0,
          rag_checks: [
            { id: 'rag-notes-en', label: 'Every English notes chunk has a vector mapping', passed: false, indexed_chunks: 0, expected_chunks: 4 },
          ],
          rag_evaluation: { status: 'not_scored', score: null, reason: 'No approved benchmark.' },
          blockers: ['Assamese meta description is localized'],
          improvements: [],
          checks: [
            {
              id: 'seo-meta-as',
              group: 'seo',
              label: 'Assamese meta description is localized and names the course and term',
              fix_hint: 'Add the Assamese-script description and course code.',
              passed: false,
            },
          ],
          topic_pages: { total: 0, ready: 0, topics: [] },
          rag_ready: false,
        }}
      />,
    );

    expect(screen.getByText('85/100')).toBeInTheDocument();
    expect(screen.getByText('80/100')).toBeInTheDocument();
    expect(screen.getByText('70/100')).toBeInTheDocument();
    expect(screen.getByText('0/100')).toBeInTheDocument();
    expect(screen.getByText(/Assamese meta description is localized/)).toBeInTheDocument();
    expect(screen.getByText('Add the Assamese-script description and course code.')).toBeInTheDocument();
    expect(screen.getByText(/Retrieval quality is not scored until reviewer-approved benchmark cases are available/)).toBeInTheDocument();
    expect(screen.getByText(/RAG index check needs attention/)).toBeInTheDocument();
  });

  it('does not render a gate for standard subjects', () => {
    const { container } = render(<SubjectReadinessPanel readiness={{ required: false }} />);
    expect(container.firstChild).toBeNull();
  });
});
