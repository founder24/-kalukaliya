import { describe, expect, it } from 'vitest';
import { batchVectorIds, qaTextFromApprovedPairs } from './rag-indexing';

describe('batchVectorIds', () => {
  it('keeps every Vectorize delete request within the 100-ID limit', () => {
    const ids = Array.from({ length: 1_004 }, (_, index) => `vector-${index}`);
    const batches = batchVectorIds(ids);

    expect(batches.map(batch => batch.length)).toEqual([100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 4]);
    expect(batches.flat()).toEqual(ids);
  });

  it('does not create an empty request when there are no IDs', () => {
    expect(batchVectorIds([])).toEqual([]);
  });
});

describe('RAG question-answer eligibility', () => {
  it('does not index question-only records, and retains stable source context for complete pairs', () => {
    const records = JSON.stringify([
      { id: 'stable-q1', question: 'Question without a solution?', answer: '', source: 'PYQ', year: 2024 },
      { id: 'stable-q2', question: 'Question with an approved answer?', answer: 'Approved answer.', source: 'PYQ', year: 2024, marks: 2 },
      { id: 'stable-q3', question: 'Question with a solution field?', solution: 'Approved solution.', source: 'Expected' },
    ]);

    const all = qaTextFromApprovedPairs(records);
    const pyqs = qaTextFromApprovedPairs(records, true);

    expect(all).toContain('Question ID: stable-q2');
    expect(all).toContain('A: Approved answer.');
    expect(all).toContain('Solution: Approved solution.');
    expect(all).not.toContain('stable-q1');
    expect(pyqs).toContain('stable-q2');
    expect(pyqs).not.toContain('stable-q3');
    expect(pyqs).not.toContain('stable-q1');
  });

  it('does not index incomplete or unstructured freeform question content', () => {
    expect(qaTextFromApprovedPairs(JSON.stringify([
      { question: 'Question only?', answer: '' },
      { content: 'Question: not answered' },
    ]))).toBeNull();
  });
});
