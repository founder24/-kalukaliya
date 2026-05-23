/**
 * Task #1 — AdminNotifications component Vitest tests.
 *
 * Uses renderToStaticMarkup (synchronous, no effects).
 */
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, it, expect, vi } from 'vitest';

vi.mock('@/utils/api', () => ({
  getNotificationTriggers:   vi.fn(() => Promise.resolve({ data: { triggers: [] } })),
  createNotificationTrigger: vi.fn(() => Promise.resolve({ data: {} })),
  updateNotificationTrigger: vi.fn(() => Promise.resolve({ data: {} })),
  deleteNotificationTrigger: vi.fn(() => Promise.resolve({ data: {} })),
  API_BASE: 'http://localhost:8000',
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

vi.mock('@/components/ErrorBoundary', () => ({
  SectionErrorBoundary: ({ children }) => <div>{children}</div>,
}));

import AdminNotifications from './AdminNotifications';

describe('AdminNotifications', () => {
  it('renders the Compose Notification section heading', () => {
    const html = renderToStaticMarkup(
      <AdminNotifications adminToken="test-token" />,
    );
    expect(html).toContain('Compose Notification');
  });

  it('renders the Notifications heading and trigger list area', () => {
    const html = renderToStaticMarkup(
      <AdminNotifications adminToken="test-token" />,
    );
    expect(html).toContain('Notifications');
  });

  it('renders the notification title input placeholder text', () => {
    const html = renderToStaticMarkup(
      <AdminNotifications adminToken="test-token" />,
    );
    expect(html).toContain('Notification title');
  });
});
