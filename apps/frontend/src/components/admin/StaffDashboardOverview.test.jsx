import React from 'react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

const get = vi.hoisted(() => vi.fn());

vi.mock('axios', () => ({
  default: { get },
}));

vi.mock('@/utils/api', () => ({
  API_BASE: '/api/v1',
}));

import StaffDashboardOverview from './StaffDashboardOverview';

describe('StaffDashboardOverview', () => {
  beforeEach(() => {
    get.mockReset();
  });

  it('loads the staff-safe command center and renders the returned metrics', async () => {
    get.mockResolvedValue({
      data: {
        users: { active_accounts: 12, new_users: 3 },
        content: { published: 8, unpublished: 2 },
        rag: { indexed: 7, stale: 1, unindexed: 1 },
        chat: { completions: 15, failures: 1, average_latency_ms: 420 },
        incidents: { failed_publish_jobs: 0 },
        audit: { actions: 4 },
      },
    });

    render(<StaffDashboardOverview adminToken="header.payload.signature" />);

    await waitFor(() => expect(screen.getByText('Published chapters')).toBeTruthy());
    expect(screen.getByText('12')).toBeTruthy();
    expect(screen.getByText('8')).toBeTruthy();
    expect(get).toHaveBeenCalledWith(
      '/api/v1/staff/analytics/command-center?days=7',
      expect.objectContaining({
        headers: { Authorization: 'Bearer header.payload.signature' },
        withCredentials: true,
      }),
    );
  });

  it('shows an actionable retry state when the command center is unavailable', async () => {
    get.mockRejectedValue({ response: { data: { detail: 'Command center unavailable' } } });

    render(<StaffDashboardOverview />);

    await waitFor(() => expect(screen.getByTestId('staff-dashboard-error')).toBeTruthy());
    expect(screen.getByText('Retry')).toBeTruthy();
  });
});