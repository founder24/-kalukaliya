import { describe, expect, it } from 'vitest';
import { groupPyqTextByMarks } from './staff';

describe('pasted PYQ mark grouping', () => {
  it('recognizes bracket, parenthesized, word, and Assamese mark formats', () => {
    const groups = groupPyqTextByMarks([
      '1. Define force. [1]',
      '2. State Newton’s law. (2 marks)',
      '3. Explain the graph. 5 marks',
      '৪. চিত্ৰটো ব্যাখ্যা কৰা। ৫ নম্বৰ',
    ].join('\n'));

    expect(groups.map(group => group.marks)).toEqual([1, 2, 5, 5]);
    expect(groups[0]?.text).toContain('Define force');
    expect(groups[3]?.text).toContain('চিত্ৰটো');
  });

  it('preserves question order and unmarked text', () => {
    const groups = groupPyqTextByMarks('Instructions\n1. Answer all questions.\n[3]\n2. Explain acceleration.');

    expect(groups[0]).toEqual({
      marks: null,
      label: 'Unmarked',
      text: 'Instructions\n1. Answer all questions.',
    });
    expect(groups[1]).toEqual({
      marks: 3,
      label: '3 marks',
      text: '[3]\n2. Explain acceleration.',
    });
  });
});