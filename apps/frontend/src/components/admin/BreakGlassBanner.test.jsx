import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import React from 'react';

vi.mock('@/utils/api', () => ({
  adminGetDiagnostics: vi.fn(),
  adminDisableBreakGlass: vi.fn(),
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn() },
}));

import { fireEvent } from '@testing-library/react';
import BreakGlassBanner from './BreakGlassBanner.jsx';
import { adminGetDiagnostics, adminDisableBreakGlass } from '@/utils/api';
import { toast } from 'sonner';

const BANNER = 'break-glass-banner';

describe('BreakGlassBanner', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders nothing when adminToken is null (no fetch fired)', async () => {
    const { container } = render(<BreakGlassBanner adminToken={null} />);
    expect(container).toBeEmptyDOMElement();
    expect(adminGetDiagnostics).not.toHaveBeenCalled();
  });

  it('renders nothing when diagnostics report break_glass_active=false', async () => {
    adminGetDiagnostics.mockResolvedValueOnce({
      data: { cf_access: { break_glass_active: false, break_glass_source: null } },
    });

    render(<BreakGlassBanner adminToken="admin.jwt" />);

    // Wait for the initial fetch to settle, then assert the banner is hidden.
    await waitFor(() => expect(adminGetDiagnostics).toHaveBeenCalledWith('admin.jwt'));
    expect(screen.queryByTestId(BANNER)).toBeNull();
  });

  it('renders the red banner with source copy when break_glass_active=true', async () => {
    adminGetDiagnostics.mockResolvedValueOnce({
      data: { cf_access: { break_glass_active: true, break_glass_source: 'env' } },
    });

    render(<BreakGlassBanner adminToken="admin.jwt" />);

    const banner = await screen.findByTestId(BANNER);
    expect(banner).toBeInTheDocument();
    expect(banner).toHaveTextContent(/Cloudflare Access is bypassed/i);
    expect(banner).toHaveTextContent(/via env/);
    // Action affordances are present.
    expect(screen.getByTestId('break-glass-banner-disable')).toBeInTheDocument();
    expect(screen.getByTestId('break-glass-banner-recheck')).toBeInTheDocument();
    expect(screen.getByTestId('break-glass-banner-runbook')).toBeInTheDocument();
    // No stale badge on the happy path.
    expect(screen.queryByTestId('break-glass-banner-stale')).toBeNull();
  });

  it('stays hidden when the diagnostics fetch errors before any successful poll', async () => {
    adminGetDiagnostics.mockRejectedValueOnce(new Error('network down'));

    render(<BreakGlassBanner adminToken="admin.jwt" />);

    // Drive the promise rejection to settle.
    await waitFor(() => expect(adminGetDiagnostics).toHaveBeenCalled());
    // Without a prior successful poll, a transient error must NOT pop the
    // banner — that would be a false-positive paging incident.
    expect(screen.queryByTestId(BANNER)).toBeNull();
  });

  describe('Disable now button', () => {
    const activeDiagnostics = {
      data: { cf_access: { break_glass_active: true, break_glass_source: 'env' } },
    };

    async function renderActiveBanner() {
      adminGetDiagnostics.mockResolvedValueOnce(activeDiagnostics);
      const utils = render(<BreakGlassBanner adminToken="admin.jwt" />);
      const button = await screen.findByTestId('break-glass-banner-disable');
      return { ...utils, button };
    }

    it('declined confirm dialog → no API call, banner unchanged', async () => {
      const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
      const { button } = await renderActiveBanner();

      fireEvent.click(button);

      expect(confirmSpy).toHaveBeenCalledTimes(1);
      expect(adminDisableBreakGlass).not.toHaveBeenCalled();
      expect(screen.getByTestId(BANNER)).toBeInTheDocument();
      expect(toast.success).not.toHaveBeenCalled();
      expect(toast.warning).not.toHaveBeenCalled();
      expect(toast.error).not.toHaveBeenCalled();

      confirmSpy.mockRestore();
    });

    it('accepted confirm + success response → calls API once with admin token, success toast, banner hidden', async () => {
      const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
      adminDisableBreakGlass.mockResolvedValueOnce({
        data: { cf_access: { break_glass_active: false, break_glass_source: null }, redis_persisted: true },
      });
      const { button } = await renderActiveBanner();

      fireEvent.click(button);

      await waitFor(() => expect(adminDisableBreakGlass).toHaveBeenCalledTimes(1));
      expect(adminDisableBreakGlass).toHaveBeenCalledWith('admin.jwt');
      await waitFor(() => expect(screen.queryByTestId(BANNER)).toBeNull());
      expect(toast.success).toHaveBeenCalledWith('Cloudflare Access break-glass disabled.');
      expect(toast.warning).not.toHaveBeenCalled();
      expect(toast.error).not.toHaveBeenCalled();

      confirmSpy.mockRestore();
    });

    it('accepted confirm + redis_persisted=false → warning toast shown', async () => {
      const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
      adminDisableBreakGlass.mockResolvedValueOnce({
        data: {
          cf_access: { break_glass_active: false, break_glass_source: null },
          redis_persisted: false,
        },
      });
      const { button } = await renderActiveBanner();

      fireEvent.click(button);

      await waitFor(() => expect(adminDisableBreakGlass).toHaveBeenCalledTimes(1));
      await waitFor(() => expect(toast.warning).toHaveBeenCalledTimes(1));
      expect(toast.warning.mock.calls[0][0]).toMatch(/cluster-wide flag failed/i);
      expect(toast.success).not.toHaveBeenCalled();
      expect(toast.error).not.toHaveBeenCalled();

      confirmSpy.mockRestore();
    });

    it('API rejection → error toast shown with the server detail string', async () => {
      const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
      const apiError = Object.assign(new Error('Request failed'), {
        response: { data: { detail: 'redis offline' } },
      });
      adminDisableBreakGlass.mockRejectedValueOnce(apiError);
      const { button } = await renderActiveBanner();

      fireEvent.click(button);

      await waitFor(() => expect(adminDisableBreakGlass).toHaveBeenCalledTimes(1));
      await waitFor(() => expect(toast.error).toHaveBeenCalledTimes(1));
      expect(toast.error).toHaveBeenCalledWith('Failed to disable break-glass: redis offline');
      // Banner stays visible since the active state was not cleared.
      expect(screen.getByTestId(BANNER)).toBeInTheDocument();
      expect(toast.success).not.toHaveBeenCalled();
      expect(toast.warning).not.toHaveBeenCalled();

      confirmSpy.mockRestore();
    });
  });
});
