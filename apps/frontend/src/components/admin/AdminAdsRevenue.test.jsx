import React from 'react';
import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/utils/api', () => ({
  adminGetAdsRevenue: vi.fn(),
}));

import { adminGetAdsRevenue } from '@/utils/api';
import AdminAdsRevenue from './AdminAdsRevenue';

describe('AdminAdsRevenue', () => {
  beforeEach(() => {
    vi.mocked(adminGetAdsRevenue).mockResolvedValue({
      data: {
        endpoint_configured: false,
        totals: {
          gross_revenue_paise: 25000,
          net_revenue_paise: 22000,
          monetized_impressions: 432,
        },
        reconciliation: {
          status: 'skipped',
          started_at: 1_800_000_000,
          completed_at: 1_800_000_000,
          weeks: 1,
          fetched: 0,
          imported: 0,
          idempotent: 0,
          failures: ['AdSense report endpoint is not configured'],
        },
        receipts: [{
          id: 'receipt-1',
          period_start: 1_799_000_000,
          period_end: 1_799_600_000,
          settlement_period: 'week-1',
          currency: 'INR',
          gross_revenue_paise: 25000,
          adjustments_paise: 1000,
          provider_fees_paise: 2000,
          net_revenue_paise: 22000,
          monetized_impressions: 432,
          stale: false,
        }],
      },
    });
  });

  it('labels provider receipts, shows reconciliation failures, and omits estimate-entry controls', async () => {
    render(<AdminAdsRevenue adminToken="admin-session" />);

    expect(await screen.findByText(/production report endpoint is not configured/i)).toBeInTheDocument();
    expect(screen.getByText('AdSense report endpoint is not configured')).toBeInTheDocument();
    expect(screen.getByText('Finalized provider receipts')).toBeInTheDocument();
    expect(screen.getAllByText('₹220.00').length).toBeGreaterThan(0);
    expect(screen.queryByText(/add single entry/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/csv upload/i)).not.toBeInTheDocument();
    expect(adminGetAdsRevenue).toHaveBeenCalledWith('admin-session', 30);
  });
});
