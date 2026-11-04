import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import MarkdownRenderer from './MarkdownRenderer';

describe('study Markdown rendering', () => {
  it('renders legacy inline and display formulas as math rather than literal TeX', () => {
    const { container } = render(<MarkdownRenderer>{String.raw`বল \(F = ma\).

\[F = 2 \times 3 = 6\,N\]`}</MarkdownRenderer>);
    expect(container.querySelectorAll('.katex')).toHaveLength(2);
    expect(container.querySelector('.katex-display')).not.toBeNull();
    expect(container.querySelector('math')).not.toBeNull();
  });

  it('preserves diagram spacing and keeps existing code literal', () => {
    const { container } = render(<MarkdownRenderer>{String.raw`┌──────┐
│  box │
└──────┘

` + '```text\n\\(literal\\)\n```'}</MarkdownRenderer>);
    expect(container.querySelector('pre code').textContent).toContain('│  box │');
    expect(container.querySelectorAll('pre')).toHaveLength(2);
    expect(container.querySelectorAll('pre')[1].textContent).toContain('\\(literal\\)');
  });

  it('keeps all labels and two-column explanations inside a legacy diagram', () => {
    const figure = '       CLASSICAL DYNAMICS\n               |\n   +-----------+-----------+\n   |                       |\n   ARISTOTELIAN VIEW        GALILEAN LAW\n   "External force"        "Uniform motion"';
    const { container } = render(<MarkdownRenderer>{`Introductory paragraph.\n${figure}\n\n### Next section`}</MarkdownRenderer>);
    expect(container.querySelector('pre code').textContent).toBe(`${figure}\n`);
    expect(screen.getByRole('heading', { name: 'Next section' })).toBeTruthy();
    expect(container.querySelector('p').textContent).toBe('Introductory paragraph.');
  });

  it('keeps tables, prose and inline code and does not allow unsafe HTML or links', () => {
    const { container } = render(<MarkdownRenderer>{'| Mass | Force |\n|---|---|\n| 2 | 6 |\n\n`\\(literal\\)`\n\n<script>alert(1)</script>\n\n[unsafe](javascript:alert%281%29)'}</MarkdownRenderer>);
    expect(container.querySelector('table')).not.toBeNull();
    expect(screen.getByText('\\(literal\\)')).toBeTruthy();
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('a[href^="javascript:"]')).toBeNull();
  });

  it('does not turn inline box symbols or table cells into diagram blocks', () => {
    const { container } = render(<MarkdownRenderer>{'Paragraph │ with inline symbol.\n\n| Symbol | Meaning |\n|---|---|\n| │ | vertical line |\n\n  Indented prose │ still prose.'}</MarkdownRenderer>);
    expect(container.querySelector('pre')).toBeNull();
    expect(container.querySelector('table')).not.toBeNull();
    expect(screen.getByText('Paragraph │ with inline symbol.')).toBeTruthy();
  });
});
