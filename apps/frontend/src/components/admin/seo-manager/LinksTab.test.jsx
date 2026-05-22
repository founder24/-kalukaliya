/**
 * Task #939 — unit tests for the LinkerAgentPanel embedded inside
 * LinksTab. We focus on the three pieces that have real product
 * impact and that backend-only tests can't catch:
 *
 *   1. The empty pending-queue state renders cleanly when the API
 *      returns no items (so admins know it loaded vs. crashed).
 *   2. A pending suggestion renders its anchor + diff and exposes
 *      the Approve / Reject buttons.
 *   3. Clicking Approve calls the approve API helper with the right
 *      record id and refreshes the queue afterwards.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

vi.mock('../../../utils/api.jsx', () => ({
  adminSeoInternalLinksStatus: vi.fn(),
  adminSeoInternalLinksPending: vi.fn(),
  adminSeoInternalLinksHistory: vi.fn(),
  adminSeoInternalLinksApprove: vi.fn(),
  adminSeoInternalLinksReject:  vi.fn(),
  adminSeoInternalLinksRevert:  vi.fn(),
  adminSeoInternalLinksTrigger: vi.fn(),
  // The rest of LinksTab references these two helpers; stub safely.
  adminSeoInternalLinks: vi.fn(() => Promise.resolve({ data: { items: [] } })),
  adminSeoInternalLinksRebuild: vi.fn(() => Promise.resolve({ data: {} })),
}));

import * as api from '../../../utils/api.jsx';
import LinksTab from './LinksTab';

const noop = () => {};

const baseProps = {
  adminToken: 'tok',
  linksData: null, linksLoading: false, handleLinksAnalyze: noop,
  injectSlug: '', setInjectSlug: noop, injecting: false, handleLinksInject: noop,
};

function setupApi({ pending = [], history = [], status = null }) {
  // Status payload mirrors the real backend contract from
  // routes/admin_seo_internal_linker.py (nested ``budget`` + ``config``
  // sub-objects). Keeping the test fixture aligned to the real shape
  // protects us from silent regressions where the UI only renders
  // because the test mock happens to use a flatter shape.
  api.adminSeoInternalLinksStatus.mockResolvedValue({ data: status || {
    enabled: true,
    budget: { auto_used: 0, auto_cap: 100 },
    pendingCount: 0,
    recentAutoApplied24h: 0,
    config: {
      autoApplyThreshold: 0.75,
      minLinksPerTarget: 3,
      maxLinksPerTarget: 5,
      candidatePoolSize: 30,
      nightlyTopN: 50,
    },
  }});
  api.adminSeoInternalLinksPending.mockResolvedValue({ data: { items: pending }});
  api.adminSeoInternalLinksHistory.mockResolvedValue({ data: { items: history }});
}

describe('LinkerAgentPanel (LinksTab)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the empty state when no pending suggestions exist', async () => {
    setupApi({ pending: [], history: [] });
    render(<LinksTab {...baseProps} />);
    expect(await screen.findByTestId('linker-pending-empty')).toBeInTheDocument();
    expect(api.adminSeoInternalLinksStatus).toHaveBeenCalledWith('tok');
    expect(api.adminSeoInternalLinksPending).toHaveBeenCalled();
  });

  it('renders a pending suggestion with anchor + diff and Approve/Reject buttons', async () => {
    setupApi({
      pending: [{
        id: 'rec-1',
        sourcePageId: 'p-src',
        sourceTopicTitle: 'Inertia',
        targetPageId: 'p-tgt',
        targetTopicTitle: "Newton's First Law",
        anchorText: 'Newton',
        confidence: 0.62,
        reason: 'natural mention',
        diff: { beforeExcerpt: 'Newton wrote', afterExcerpt: '<a>Newton</a> wrote' },
      }],
    });
    render(<LinksTab {...baseProps} />);
    const row = await screen.findByTestId('linker-pending-rec-1');
    expect(row).toBeInTheDocument();
    expect(row.textContent).toMatch(/Inertia/);
    expect(row.textContent).toMatch(/Newton's First Law/);
    expect(row.textContent).toMatch(/"Newton"/);
    expect(row.textContent).toMatch(/62%/);
    expect(screen.getByTestId('linker-approve-rec-1')).toBeInTheDocument();
    expect(screen.getByTestId('linker-reject-rec-1')).toBeInTheDocument();
  });

  it('Reject button calls the reject helper with the row id', async () => {
    setupApi({
      pending: [{
        id: 'rec-2',
        sourcePageId: 'p-src', sourceTopicTitle: 'Inertia',
        targetPageId: 'p-tgt', targetTopicTitle: "Newton's First Law",
        anchorText: 'Newton', confidence: 0.40, reason: 'low-conf draft',
        diff: { beforeExcerpt: 'a', afterExcerpt: 'b' },
      }],
    });
    api.adminSeoInternalLinksReject.mockResolvedValue({ data: { ok: true }});

    render(<LinksTab {...baseProps} />);
    const btn = await screen.findByTestId('linker-reject-rec-2');
    fireEvent.click(btn);
    await waitFor(() => {
      expect(api.adminSeoInternalLinksReject).toHaveBeenCalledWith('tok', 'rec-2');
    });
  });

  it('Approve button calls the approve helper with the row id and refreshes', async () => {
    setupApi({
      pending: [{
        id: 'rec-1',
        sourcePageId: 'p-src', sourceTopicTitle: 'Inertia',
        targetPageId: 'p-tgt', targetTopicTitle: "Newton's First Law",
        anchorText: 'Newton', confidence: 0.62, reason: '—',
        diff: { beforeExcerpt: 'a', afterExcerpt: 'b' },
      }],
    });
    api.adminSeoInternalLinksApprove.mockResolvedValue({ data: { ok: true }});

    render(<LinksTab {...baseProps} />);
    const btn = await screen.findByTestId('linker-approve-rec-1');
    fireEvent.click(btn);
    await waitFor(() => {
      expect(api.adminSeoInternalLinksApprove).toHaveBeenCalledWith('tok', 'rec-1');
    });
    // Refresh fan-out: pending + history + status reload after approve.
    await waitFor(() => {
      expect(api.adminSeoInternalLinksPending).toHaveBeenCalledTimes(2);
    });
  });
});
