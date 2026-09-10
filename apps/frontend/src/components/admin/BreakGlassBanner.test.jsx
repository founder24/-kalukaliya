import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import React from 'react';

vi.mock('@/utils/api', () => ({
  adminGetBreakGlassStatus: vi.fn(),
}));

import BreakGlassBanner from './BreakGlassBanner.jsx';
import { adminGetBreakGlassStatus } from '@/utils/api';

const BANNER = 'break-glass-banner';

describe('BreakGlassBanner', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders nothing when adminToken is null (no fetch fired)', async () => {
    const { container } = render(<BreakGlassBanner adminToken={null} />);
    expect(container).toBeEmptyDOMElement();
    expect(adminGetBreakGlassStatus).not.toHaveBeenCalled();
  });

  it('renders nothing when the staff status route reports active=false', async () => {
    adminGetBreakGlassStatus.mockResolvedValueOnce({ data: { active: false } });

    render(<BreakGlassBanner adminToken="admin.jwt" />);

    // Wait for the initial fetch to settle, then assert the banner is hidden.
    await waitFor(() => expect(adminGetBreakGlassStatus).toHaveBeenCalledWith('admin.jwt'));
    expect(screen.queryByTestId(BANNER)).toBeNull();
  });

  it('renders the persistent red warning when the staff status route reports active=true', async () => {
    adminGetBreakGlassStatus.mockResolvedValueOnce({ data: { active: true } });

    render(<BreakGlassBanner adminToken="admin.jwt" />);

    const banner = await screen.findByTestId(BANNER);
    expect(banner).toBeInTheDocument();
    expect(banner).toHaveTextContent(/Cloudflare Access is bypassed/i);
    expect(screen.getByTestId('break-glass-banner-recheck')).toBeInTheDocument();
    expect(screen.getByTestId('break-glass-banner-runbook')).toBeInTheDocument();
    // No stale badge on the happy path.
    expect(screen.queryByTestId('break-glass-banner-stale')).toBeNull();
  });

  it('shows an unavailable warning when the status fetch fails before any successful poll', async () => {
    adminGetBreakGlassStatus.mockRejectedValueOnce(new Error('network down'));

    render(<BreakGlassBanner adminToken="admin.jwt" />);

    // Drive the promise rejection to settle.
    await waitFor(() => expect(adminGetBreakGlassStatus).toHaveBeenCalled());
    expect(await screen.findByTestId(BANNER)).toHaveTextContent(/status is unavailable/i);
    expect(screen.getByTestId('break-glass-banner-stale')).toBeInTheDocument();
  });

  it('does not call the retired diagnostics route', async () => {
    adminGetBreakGlassStatus.mockResolvedValueOnce({ data: { active: false } });
    render(<BreakGlassBanner adminToken="admin.jwt" />);
    await waitFor(() => expect(adminGetBreakGlassStatus).toHaveBeenCalledTimes(1));
    expect(adminGetBreakGlassStatus).toHaveBeenCalledWith('admin.jwt');
  });
});
