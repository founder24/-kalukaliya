/**
 * Task #1 — AdminRateLimits component Vitest tests.
 *
 * Uses renderToStaticMarkup (synchronous, no effects) to assert the
 * initial markup structure. Mocks axios so the component can import
 * cleanly without network calls.
 */
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, it, expect, vi } from 'vitest';

vi.mock('axios', () => ({
  default: {
    get:  vi.fn(() => Promise.resolve({ data: {} })),
    put:  vi.fn(() => Promise.resolve({ data: {} })),
    post: vi.fn(() => Promise.resolve({ data: {} })),
  },
}));

vi.mock('@/utils/api', () => ({
  API_BASE: 'http://localhost:8000',
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

vi.mock('@/components/ErrorBoundary', () => ({
  SectionErrorBoundary: ({ children }) => <div>{children}</div>,
}));

vi.mock('./AdminQuickLinks', () => ({
  default: () => null,
}));

import AdminRateLimits from './AdminRateLimits';

describe('AdminRateLimits', () => {
  it('renders the "Rate Limits" heading', () => {
    const html = renderToStaticMarkup(
      <AdminRateLimits adminToken="test-token" />,
    );
    expect(html).toContain('Rate Limits');
  });

  it('renders the default tier names (Free, Starter) in initial state', () => {
    const html = renderToStaticMarkup(
      <AdminRateLimits adminToken="test-token" />,
    );
    // The TIERS const defines Free and Starter — these must appear in static markup.
    expect(html).toMatch(/Free|Starter/);
  });
});
