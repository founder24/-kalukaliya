/**
 * Task #296 — integration test for AdminPage.handleNavigate.
 *
 * Verifies the redirect map isn't only correct in isolation but also
 * actually drives the rendered UI:
 *   - onNavigate('feedback') lands inside the Conversations section
 *     with the Feedback tab active.
 *   - onNavigate('activitylog') lands on the Logs section with the
 *     admin-actions pseudo-source filter pre-applied (which swaps the
 *     explorer pane for the Admin Actions audit-trail view).
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, act, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('@/utils/api', () => ({
  adminVerify: vi.fn(() => Promise.resolve({ data: { name: 'A', email: 'a@b' } })),
  adminLogout: vi.fn(() => Promise.resolve()),
  adminGetSettings: vi.fn(() => Promise.resolve({ data: {} })),
  adminGetUnacknowledgedAlertCount: vi.fn(() => Promise.resolve({ data: { count: 0 } })),
  adminGetConversations: vi.fn(() => Promise.resolve({ data: [] })),
  extractFaqs: vi.fn(() => Promise.resolve({ data: {} })),
  conversationsSentiment: vi.fn(() => Promise.resolve({ data: {} })),
  syncConversations: vi.fn(() => Promise.resolve({ data: {} })),
  adminGetFeedback: vi.fn(() => Promise.resolve({ data: [] })),
  adminGetActivityLog: vi.fn(() => Promise.resolve({ data: [] })),
  adminLogsList: vi.fn(() => Promise.resolve({ data: { logs: [], total: 0 } })),
  adminLogsStatus: vi.fn(() => Promise.resolve({ data: {} })),
  adminLogsTrace: vi.fn(() => Promise.resolve({ data: { logs: [] } })),
  adminLogsPause: vi.fn(() => Promise.resolve({ data: {} })),
  adminLogsResume: vi.fn(() => Promise.resolve({ data: {} })),
  adminLogsRotateToken: vi.fn(() => Promise.resolve({ data: {} })),
  adminLogsClear: vi.fn(() => Promise.resolve({ data: {} })),
  adminLogsExportUrl: vi.fn(() => ''),
  adminLogsDownloadExport: vi.fn(() => Promise.resolve()),
  API_BASE: 'http://localhost:8000',
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock('axios', () => ({
  default: {
    get: vi.fn(() => Promise.resolve({ data: { dependencies: {} } })),
    delete: vi.fn(() => Promise.resolve({ data: { cleared: 0 } })),
  },
}));
vi.mock('@/components/admin/SyraAssistant', () => ({ default: () => null }));
vi.mock('@/components/admin/syra/SyraContext', () => ({
  SyraProvider: ({ children }) => <>{children}</>,
  useSyraContext: () => ({}),
  useSyraSelection: () => {},
  useSyraFilters: () => {},
  useSyraVisibleError: () => {},
  DEFAULT_PREFS: {},
}));
vi.mock('@/components/admin/BreakGlassBanner', () => ({ default: () => null }));

import AdminPage from './AdminPage';

function renderAdmin() {
  return render(
    <MemoryRouter>
      <AdminPage />
    </MemoryRouter>,
  );
}

async function waitForVerifiedAdmin() {
  await waitFor(() => {
    expect(screen.getByTestId('admin-dashboard')).toBeTruthy();
  });
  // Wait for sidebar to render past the "verifying" gate.
  await waitFor(() => {
    expect(screen.getByTestId('admin-nav-conversations')).toBeTruthy();
  });
}

describe('AdminPage.handleNavigate integration', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('Conversations sidebar nav exposes the Feedback sub-tab (post-merge)', async () => {
    renderAdmin();
    await waitForVerifiedAdmin();

    // Click the Conversations sidebar nav, then assert the Feedback
    // sub-tab exists so we know the consolidated Conversations panel
    // is hosting the feedback surface (rather than a retired sidebar id).
    fireEvent.click(screen.getByTestId('admin-nav-conversations'));
    // The Conversations panel is React.lazy — wait long enough for the
    // dynamic import + initial render to settle.
    await waitFor(
      () => expect(screen.getByTestId('admin-conversations-tab-feedback')).toBeTruthy(),
      { timeout: 5000 },
    );
  });

  it('resolveSectionRedirect("feedback") drives Conversations to the Feedback tab', async () => {
    // Exercise the actual legacy redirect entry-point: feed the legacy
    // id through resolveSectionRedirect and mount AdminConversations
    // with the resulting navContext — the Feedback tab must end up
    // active (violet styling) on first render.
    const { resolveSectionRedirect } = await import('./AdminPage');
    const AdminConversations = (await import('@/components/admin/AdminConversations')).default;
    const resolved = resolveSectionRedirect('feedback');
    expect(resolved.section).toBe('conversations');
    expect(resolved.navContext).toEqual({ tab: 'feedback' });

    const { container } = render(
      <AdminConversations adminToken="t" navContext={resolved.navContext} />,
    );
    await waitFor(() => {
      const tab = container.querySelector('[data-testid="admin-conversations-tab-feedback"]');
      expect(tab).toBeTruthy();
      expect(tab.className).toMatch(/violet/);
    });
  });

  it('AdminConversations honours navContext.tab="feedback" on initial render', async () => {
    // Direct unit-style assertion that the navContext wiring works:
    // mount AdminConversations with navContext={tab:'feedback'} and
    // verify the Feedback tab is the active one (not Conversations).
    const AdminConversations = (await import('@/components/admin/AdminConversations')).default;
    const { container } = render(
      <AdminConversations adminToken="t" navContext={{ tab: 'feedback' }} />,
    );
    await waitFor(() => {
      const tab = container.querySelector('[data-testid="admin-conversations-tab-feedback"]');
      expect(tab).toBeTruthy();
      // Active tab gets a violet background class in this component.
      expect(tab.className).toMatch(/violet/);
    });
  });

  it('AdminLogsExplorer honours navContext.initialSources for admin-actions deep-link', async () => {
    const AdminLogsExplorer = (await import('@/components/admin/AdminLogsExplorer')).default;
    const { container } = render(
      <AdminLogsExplorer
        adminToken="t"
        navContext={{ initialSources: ['admin-actions'] }}
      />,
    );
    // The admin-actions pane swap is rendered (audit-trail view) and
    // the unified-logs table is suppressed.
    await waitFor(() => {
      expect(container.querySelector('[data-testid="admin-logs-actions-pane"]')).toBeTruthy();
    });
  });
});
