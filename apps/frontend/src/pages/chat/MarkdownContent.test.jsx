import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { MarkdownContent } from './MarkdownContent';

vi.mock('@/hooks/useStrictMode', () => ({
  useStrictMode: () => ({ strict: true }),
  useEduAllowlist: () => (host) => host === 'syrabit.ai',
}));

describe('chat study content', () => {
  it('renders math while keeping strict-mode external link restrictions', () => {
    const { container } = render(
      <MemoryRouter>
        <MarkdownContent
          content={String.raw`মুঠ বল \(F = 2 \times 3 = 6\,N\).

[Unapproved source](https://unapproved.example/physics)`}
          streaming={false}
          sources={[]}
        />
      </MemoryRouter>,
    );
    expect(container.querySelector('.katex')).not.toBeNull();
    expect(container.querySelector('a[href*="unapproved.example"]')).toBeNull();
    expect(screen.getByText('Unapproved source')).toBeTruthy();
    expect(screen.getByText('(hidden · Strict Mode)')).toBeTruthy();
  });

  it('preserves diagrams during streaming without making raw HTML executable', () => {
    const { container } = render(
      <MemoryRouter>
        <MarkdownContent
          content={'┌──────┐\n│  box │\n└──────┘\n\n<img src=x onerror=alert(1)>'}
          streaming
          sources={[]}
        />
      </MemoryRouter>,
    );
    expect(container.querySelector('pre code').textContent).toContain('│  box │');
    expect(container.querySelector('img[onerror]')).toBeNull();
  });
});
