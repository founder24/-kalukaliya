/**
 * Task #266 — AdminHealth startup credit burn panel tests.
 *
 * Covers the four startup credit panels added in Task #263
 * (AdminHealth.jsx lines 3316–3738):
 *
 *   - AWS Activate     (/admin/billing/aws-activate)
 *   - Azure Startups   (/admin/billing/azure-startups)
 *   - Axiom Log Explorer (/admin/billing/axiom)
 *   - Sentry           (/admin/billing/sentry)
 *   - Startup Credits summary row (static — always rendered)
 *
 * Strategy: integration tests that mount AdminHealth with a controlled
 * axiosGet mock.  The credit panels live in the default 'infra' tab so
 * no tab-click navigation is required — flushEffects() is sufficient.
 * All other endpoints (GCP, CF addons, cron health, etc.) resolve with
 * { data: {} } so the component renders without errors.
 */
import React from 'react';
import { render, screen, waitFor, act, within } from '@testing-library/react';
import { describe, it, expect, vi, afterEach } from 'vitest';

/* ── axios mock (same vi.hoisted pattern as AdminHealth.cooldown.test.jsx) ── */
const { axiosGet } = vi.hoisted(() => ({ axiosGet: vi.fn() }));

vi.mock('axios', () => ({
  default: {
    get:    axiosGet,
    post:   vi.fn().mockResolvedValue({ data: {} }),
    create: vi.fn(),
    delete: vi.fn().mockResolvedValue({ data: {} }),
  },
  get: axiosGet,
}));

/* ── library / sub-component stubs (identical to cooldown test) ─────────── */
vi.mock('recharts', () => ({
  AreaChart:           ({ children }) => <div>{children}</div>,
  BarChart:            ({ children }) => <div>{children}</div>,
  LineChart:           ({ children }) => <div>{children}</div>,
  Area:                () => null,
  Bar:                 () => null,
  Line:                () => null,
  XAxis:               () => null,
  YAxis:               () => null,
  CartesianGrid:       () => null,
  Tooltip:             () => null,
  Legend:              () => null,
  ResponsiveContainer: ({ children }) => <div>{children}</div>,
  ReferenceLine:       () => null,
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

vi.mock('@/components/ErrorBoundary', () => ({
  SectionErrorBoundary: ({ children }) => <>{children}</>,
}));

vi.mock('@/utils/api', () => ({
  API_BASE: 'http://test.local',
  llmCosts: vi.fn(() => Promise.resolve({ data: {} })),
}));

vi.mock('@/utils/highlightSegments', () => ({
  buildHighlightedSegments: vi.fn(() => []),
}));

vi.mock('./CronHealthPill',            () => ({ default: () => null, SlackConfigBadge: () => null }));
vi.mock('./CfWafDriftCronPill',        () => ({ default: () => null }));
vi.mock('./TrustpilotRefreshCronPill', () => ({ default: () => null }));
vi.mock('./EdgeProxyDeployCronPill',   () => ({ default: () => null }));
vi.mock('./UnifiedLogsCfPullCronPill', () => ({ default: () => null }));
vi.mock('./AdminQuickLinks',           () => ({ default: () => null }));

import AdminHealth from './AdminHealth';

/* ── helpers ────────────────────────────────────────────────────────────── */

/**
 * Build an axiosGet implementation that serves `overrides[urlSegment]`
 * for matching URLs, and `{ data: {} }` for everything else — so the
 * many other endpoints the component calls resolve silently.
 */
function makeMock(overrides = {}) {
  return (url) => {
    for (const [pattern, data] of Object.entries(overrides)) {
      if (url.includes(pattern)) return Promise.resolve({ data });
    }
    return Promise.resolve({ data: {} });
  };
}

async function flushEffects() {
  await act(async () => { await Promise.resolve(); });
  await act(async () => { await Promise.resolve(); });
}

async function renderAdmin() {
  render(<AdminHealth adminToken="test-token" onNavigate={vi.fn()} />);
  await flushEffects();
}

/* ── AWS Activate tests ─────────────────────────────────────────────────── */
describe('AdminHealth — AWS Activate credit panel', () => {
  afterEach(() => vi.clearAllMocks());

  it('shows "not configured" text and setup instructions when API returns configured: false', async () => {
    axiosGet.mockImplementation(makeMock({
      'billing/aws-activate': { configured: false },
    }));
    await renderAdmin();

    const panel = await screen.findByTestId('aws-credit-panel');
    expect(panel).toBeInTheDocument();
    expect(screen.getByTestId('aws-credit-heading')).toHaveTextContent('AWS Activate');
    expect(screen.getByText('AWS cost explorer not configured')).toBeInTheDocument();
    expect(screen.getByText(/Set AWS_ACCESS_KEY_ID/)).toBeInTheDocument();
  });

  it('shows "not configured" state (not error banner) when API responds with 404', async () => {
    axiosGet.mockImplementation((url) => {
      if (url.includes('billing/aws-activate')) {
        const err = new Error('Not Found');
        err.response = { status: 404 };
        return Promise.reject(err);
      }
      return Promise.resolve({ data: {} });
    });
    await renderAdmin();

    await screen.findByTestId('aws-credit-panel');
    // Should show "not configured" UI — NOT the generic error banner
    expect(screen.getByText('AWS cost explorer not configured')).toBeInTheDocument();
    expect(screen.getByText(/Set AWS_ACCESS_KEY_ID/)).toBeInTheDocument();
    expect(screen.queryByText(/Failed to load AWS credit data/)).not.toBeInTheDocument();
  });

  it('renders grant total, spend MTD, remaining, and runway values when configured', async () => {
    axiosGet.mockImplementation(makeMock({
      'billing/aws-activate': {
        configured:             true,
        grant_usd:              5000,
        spend_mtd_usd:          123.45,
        estimated_remaining_usd: 4200,
        months_runway:          10.5,
        credits_low:            false,
        days_until_expiry:      180,
        expiry_date:            '2027-01-31',
        services:               ['Lambda'],
      },
    }));
    await renderAdmin();

    await screen.findByTestId('aws-grant-usd');
    expect(screen.getByTestId('aws-grant-usd')).toHaveTextContent('$5,000');
    expect(screen.getByTestId('aws-spend-mtd')).toHaveTextContent('$123.45');
    expect(screen.getByTestId('aws-remaining')).toHaveTextContent('$4,200.00');
    expect(screen.getByTestId('aws-runway')).toHaveTextContent('10.5 mo');
  });

  it('shows "Credits Low" badge and applies red text when credits_low is true', async () => {
    axiosGet.mockImplementation(makeMock({
      'billing/aws-activate': {
        configured:             true,
        grant_usd:              100000,
        spend_mtd_usd:          98000,
        estimated_remaining_usd: 500,
        months_runway:          0.1,
        credits_low:            true,
        days_until_expiry:      180,
        expiry_date:            '2027-01-31',
      },
    }));
    await renderAdmin();

    await screen.findByTestId('aws-credit-panel');
    expect(screen.getByText('Credits Low')).toBeInTheDocument();
    expect(screen.getByTestId('aws-remaining')).toHaveClass('text-red-600');
    expect(screen.getByTestId('aws-spend-mtd')).toHaveClass('text-red-600');
  });

  it('shows red days-remaining text when expiry is within 60 days', async () => {
    axiosGet.mockImplementation(makeMock({
      'billing/aws-activate': {
        configured:             true,
        grant_usd:              100000,
        spend_mtd_usd:          200,
        estimated_remaining_usd: 90000,
        months_runway:          30,
        credits_low:            false,
        days_until_expiry:      45,
        expiry_date:            '2025-06-15',
      },
    }));
    await renderAdmin();

    await waitFor(() => expect(screen.getByText(/45d remaining/)).toBeInTheDocument());
    expect(screen.getByText(/45d remaining/)).toHaveClass('text-red-500');
  });

  it('uses non-red styling for days-remaining when >= 60 days remain', async () => {
    axiosGet.mockImplementation(makeMock({
      'billing/aws-activate': {
        configured:             true,
        grant_usd:              100000,
        spend_mtd_usd:          200,
        estimated_remaining_usd: 90000,
        months_runway:          30,
        credits_low:            false,
        days_until_expiry:      90,
        expiry_date:            '2025-08-15',
      },
    }));
    await renderAdmin();

    await waitFor(() => expect(screen.getByText(/90d remaining/)).toBeInTheDocument());
    expect(screen.getByText(/90d remaining/)).not.toHaveClass('text-red-500');
    expect(screen.getByText(/90d remaining/)).toHaveClass('text-gray-600');
  });
});

/* ── Azure for Startups tests ───────────────────────────────────────────── */
describe('AdminHealth — Azure for Startups credit panel', () => {
  afterEach(() => vi.clearAllMocks());

  it('shows "not configured" text and setup instructions when API returns configured: false', async () => {
    axiosGet.mockImplementation(makeMock({
      'billing/azure-startups': { configured: false },
    }));
    await renderAdmin();

    const panel = await screen.findByTestId('azure-credit-panel');
    expect(panel).toBeInTheDocument();
    expect(screen.getByTestId('azure-credit-heading')).toHaveTextContent('Azure for Startups');
    expect(screen.getByText('Azure Cost Management not configured')).toBeInTheDocument();
    expect(screen.getByText(/Create an Azure service principal/)).toBeInTheDocument();
  });

  it('shows "not configured" state (not error banner) when API responds with 404', async () => {
    axiosGet.mockImplementation((url) => {
      if (url.includes('billing/azure-startups')) {
        const err = new Error('Not Found');
        err.response = { status: 404 };
        return Promise.reject(err);
      }
      return Promise.resolve({ data: {} });
    });
    await renderAdmin();

    await screen.findByTestId('azure-credit-panel');
    // Should show "not configured" UI — NOT the generic error banner
    expect(screen.getByText('Azure Cost Management not configured')).toBeInTheDocument();
    expect(screen.getByText(/Create an Azure service principal/)).toBeInTheDocument();
    expect(screen.queryByText(/Failed to load Azure credit data/)).not.toBeInTheDocument();
  });

  it('renders grant total, spend MTD, remaining, and runway values when configured', async () => {
    axiosGet.mockImplementation(makeMock({
      'billing/azure-startups': {
        configured:             true,
        grant_usd:              5000,
        spend_mtd_usd:          87.60,
        estimated_remaining_usd: 4500,
        months_runway:          8.2,
        credits_low:            false,
        days_until_expiry:      200,
        expiry_date:            '2027-01-31',
        subscription_name:      'Syrabit-Startups',
      },
    }));
    await renderAdmin();

    await screen.findByTestId('azure-grant-usd');
    expect(screen.getByTestId('azure-grant-usd')).toHaveTextContent('$5,000');
    expect(screen.getByTestId('azure-spend-mtd')).toHaveTextContent('$87.60');
    expect(screen.getByTestId('azure-remaining')).toHaveTextContent('$4,500.00');
    expect(screen.getByTestId('azure-runway')).toHaveTextContent('8.2 mo');
  });

  it('shows "Credits Low" badge and applies red text when credits_low is true', async () => {
    axiosGet.mockImplementation(makeMock({
      'billing/azure-startups': {
        configured:             true,
        grant_usd:              5000,
        spend_mtd_usd:          4900,
        estimated_remaining_usd: 100,
        months_runway:          0.1,
        credits_low:            true,
        days_until_expiry:      200,
        expiry_date:            '2027-01-31',
      },
    }));
    await renderAdmin();

    await screen.findByTestId('azure-credit-panel');
    expect(screen.getByText('Credits Low')).toBeInTheDocument();
    expect(screen.getByTestId('azure-remaining')).toHaveClass('text-red-600');
    expect(screen.getByTestId('azure-spend-mtd')).toHaveClass('text-red-600');
  });

  it('shows red days-remaining text when expiry is within 60 days', async () => {
    axiosGet.mockImplementation(makeMock({
      'billing/azure-startups': {
        configured:             true,
        grant_usd:              5000,
        spend_mtd_usd:          100,
        estimated_remaining_usd: 4500,
        months_runway:          10,
        credits_low:            false,
        days_until_expiry:      30,
        expiry_date:            '2025-06-01',
      },
    }));
    await renderAdmin();

    await waitFor(() => expect(screen.getByText(/30d remaining/)).toBeInTheDocument());
    expect(screen.getByText(/30d remaining/)).toHaveClass('text-red-500');
  });
});

/* ── Axiom Log Explorer tests ───────────────────────────────────────────── */
describe('AdminHealth — Axiom Log Explorer credit panel', () => {
  afterEach(() => vi.clearAllMocks());

  it('shows "not configured" text and setup instructions when API returns configured: false', async () => {
    axiosGet.mockImplementation(makeMock({
      'billing/axiom': { configured: false },
    }));
    await renderAdmin();

    const panel = await screen.findByTestId('axiom-credit-panel');
    expect(panel).toBeInTheDocument();
    expect(screen.getByText('Axiom API token not configured')).toBeInTheDocument();
    expect(screen.getByText(/Create an Axiom API token/)).toBeInTheDocument();
  });

  it('renders ingest GB and the correct usage percentage', async () => {
    axiosGet.mockImplementation(makeMock({
      'billing/axiom': {
        configured:      true,
        ingest_gb:       250,
        ingest_limit_gb: 500,
        retention_days:  30,
        over_limit:      false,
      },
    }));
    await renderAdmin();

    await screen.findByTestId('axiom-ingest-gb');
    expect(screen.getByTestId('axiom-ingest-gb')).toHaveTextContent('250.0 GB');
    // The percentage span next to the progress bar
    expect(screen.getByText('50%')).toBeInTheDocument();
  });

  it('renders the ingest progress bar at the correct percentage width', async () => {
    axiosGet.mockImplementation(makeMock({
      'billing/axiom': {
        configured:      true,
        ingest_gb:       100,
        ingest_limit_gb: 500,
        retention_days:  30,
        over_limit:      false,
      },
    }));
    await renderAdmin();

    await screen.findByTestId('axiom-ingest-gb');
    // 100 / 500 = 20 %
    const panel = screen.getByTestId('axiom-credit-panel');
    const progressFill = panel.querySelector('[style*="width: 20%"]');
    expect(progressFill).not.toBeNull();
  });

  it('shows "Over Limit" badge when over_limit is true', async () => {
    axiosGet.mockImplementation(makeMock({
      'billing/axiom': {
        configured:      true,
        ingest_gb:       510,
        ingest_limit_gb: 500,
        retention_days:  30,
        over_limit:      true,
      },
    }));
    await renderAdmin();

    await screen.findByTestId('axiom-credit-panel');
    expect(screen.getByText('Over Limit')).toBeInTheDocument();
  });
});

/* ── Sentry tests ───────────────────────────────────────────────────────── */
describe('AdminHealth — Sentry credit panel', () => {
  afterEach(() => vi.clearAllMocks());

  it('shows "not configured" text and setup instructions when API returns configured: false', async () => {
    axiosGet.mockImplementation(makeMock({
      'billing/sentry': { configured: false },
    }));
    await renderAdmin();

    const panel = await screen.findByTestId('sentry-credit-panel');
    expect(panel).toBeInTheDocument();
    expect(screen.getByText('Sentry auth token not configured')).toBeInTheDocument();
    expect(screen.getByText(/Generate a Sentry auth token/)).toBeInTheDocument();
  });

  it('renders errors MTD and the correct quota usage percentage', async () => {
    axiosGet.mockImplementation(makeMock({
      'billing/sentry': {
        configured:    true,
        errors_used:   50000,
        errors_limit:  100000,
        over_limit:    false,
        plan:          'Team',
        expiry_date:   '2026-12-31',
        days_until_expiry: 200,
      },
    }));
    await renderAdmin();

    await screen.findByTestId('sentry-errors-used');
    expect(screen.getByTestId('sentry-errors-used')).toHaveTextContent('50,000');
    expect(screen.getByText('50%')).toBeInTheDocument();
  });

  it('renders the error quota progress bar at the correct percentage width', async () => {
    axiosGet.mockImplementation(makeMock({
      'billing/sentry': {
        configured:    true,
        errors_used:   75000,
        errors_limit:  100000,
        over_limit:    false,
        plan:          'Team',
        expiry_date:   '2026-12-31',
        days_until_expiry: 200,
      },
    }));
    await renderAdmin();

    await screen.findByTestId('sentry-errors-used');
    // 75 000 / 100 000 = 75 %
    const panel = screen.getByTestId('sentry-credit-panel');
    const progressFill = panel.querySelector('[style*="width: 75%"]');
    expect(progressFill).not.toBeNull();
  });

  it('shows plan expiry date with red styling when < 60 days remain', async () => {
    axiosGet.mockImplementation(makeMock({
      'billing/sentry': {
        configured:    true,
        errors_used:   1000,
        errors_limit:  100000,
        over_limit:    false,
        plan:          'Team',
        expiry_date:   '2025-06-01',
        days_until_expiry: 45,
      },
    }));
    await renderAdmin();

    await screen.findByTestId('sentry-credit-panel');
    const expiryEl = screen.getByText('2025-06-01');
    expect(expiryEl).toHaveClass('text-red-500');
  });

  it('shows "Quota Exceeded" badge when over_limit is true', async () => {
    axiosGet.mockImplementation(makeMock({
      'billing/sentry': {
        configured:    true,
        errors_used:   105000,
        errors_limit:  100000,
        over_limit:    true,
        plan:          'Team',
        expiry_date:   '2026-12-31',
        days_until_expiry: 200,
      },
    }));
    await renderAdmin();

    await screen.findByTestId('sentry-credit-panel');
    expect(screen.getByText('Quota Exceeded')).toBeInTheDocument();
  });
});

/* ── Startup Credits summary row ────────────────────────────────────────── */
describe('AdminHealth — Startup Credits summary row', () => {
  afterEach(() => vi.clearAllMocks());

  it('renders all four provider boxes with correct amounts and labels', async () => {
    axiosGet.mockImplementation(makeMock({}));
    await renderAdmin();

    // Summary row is static — always rendered regardless of API responses.
    const heading = await screen.findByText(/Startup Credit Programmes/);
    expect(heading).toBeInTheDocument();

    // Scope assertions to the summary card to avoid conflicts with panel headings.
    const card = heading.closest('[class*="rounded-2xl"]');
    const sum = within(card);

    // Provider labels in the summary row
    expect(sum.getByText('GCP Activate')).toBeInTheDocument();
    expect(sum.getByText('AWS Activate')).toBeInTheDocument();
    expect(sum.getByText('Azure Startups')).toBeInTheDocument();
    expect(sum.getByText('Axiom + Sentry')).toBeInTheDocument();

    // Amounts (summary row uses '$100 000' with a space, not locale-formatted)
    expect(sum.getAllByText('$100 000')).toHaveLength(2); // GCP + AWS
    expect(sum.getByText('$5 000')).toBeInTheDocument();
    expect(sum.getByText('Free tiers')).toBeInTheDocument();
  });
});
