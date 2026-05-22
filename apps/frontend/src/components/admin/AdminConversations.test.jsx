/**
 * Task #1 — AdminConversations component Vitest tests.
 *
 * Uses renderToStaticMarkup (synchronous, no effects). The component starts
 * in a loading state (loading=true), so initial markup shows a spinner.
 * Tests assert the component renders cleanly without errors.
 */
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, it, expect, vi } from 'vitest';

vi.mock('@/utils/api', () => ({
  adminGetConversations:  vi.fn(() => Promise.resolve({ data: [] })),
  extractFaqs:            vi.fn(() => Promise.resolve({ data: {} })),
  conversationsSentiment: vi.fn(() => Promise.resolve({ data: {} })),
  syncConversations:      vi.fn(() => Promise.resolve({ data: {} })),
  API_BASE: 'http://localhost:8000',
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

vi.mock('@/components/ErrorBoundary', () => ({
  SectionErrorBoundary: ({ children }) => <div>{children}</div>,
}));

import AdminConversations from './AdminConversations';

describe('AdminConversations', () => {
  it('renders without throwing — initial state is a loading spinner', () => {
    // renderToStaticMarkup is synchronous; useEffect does not run, so
    // loading=true and only the spinner div is rendered.
    const html = renderToStaticMarkup(
      <AdminConversations adminToken="test-token" />,
    );
    expect(html.length).toBeGreaterThan(0);
    // The loading spinner is the only thing rendered initially.
    expect(html).toContain('animate-spin');
  });

  it('renders the loading spinner with the violet color class', () => {
    const html = renderToStaticMarkup(
      <AdminConversations adminToken="test-token" />,
    );
    // Spinner is styled violet in this component.
    expect(html).toContain('text-violet-500');
  });

  it('renders with an empty adminToken without throwing', () => {
    expect(() =>
      renderToStaticMarkup(<AdminConversations adminToken="" />),
    ).not.toThrow();
  });
});
