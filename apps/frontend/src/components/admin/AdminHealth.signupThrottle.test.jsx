/**
 * Task #463 — AdminHealth signup-throttle tile.
 *
 * Task #430 added a new "Signup throttle (do_chat)" tile to the admin health
 * panel that surfaces the per-prefix breakdown the backend exposes via
 * `do_chat.rate_check_blocked_by_prefix.signup` and
 * `do_chat.rate_check_total_by_prefix.signup`. The backend half of that
 * contract is pinned in `tests/test_cf_tier2_helpers.py`, but the tile
 * itself had no React coverage — a regression in either:
 *
 *   • the prefix-key plumbing (e.g. flipping `.signup` to `.chat` or
 *     reading from the wrong dict), or
 *   • the tone-switch logic (`signupBlocked > 0 ? 'amber' : 'emerald'`)
 *
 * would silently ship.
 *
 * These tests mount the real <AdminHealth/> with a fixture cf-health
 * payload and assert against the production tile's data-testids and
 * Tailwind palette tokens.
 */
import React from 'react';
import { render, screen, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

/* ── axios mock (vi.hoisted so factory can see the spy) ─────────────────── */
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

/* ── library / sub-component stubs ──────────────────────────────────────── */
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
vi.mock('./EmbedBackfillPill',         () => ({ default: () => null }));
vi.mock('./EmbedStackHealthPill',      () => ({ default: () => null }));
vi.mock('./CfAuditCard',               () => ({ default: () => null }));
vi.mock('./AiGatewayCacheByModelTile', () => ({ default: () => null }));
vi.mock('./AiGatewayGuardrailByModelTile', () => ({ default: () => null }));

/* ── component import (after all vi.mock calls) ──────────────────────────── */
import AdminHealth from './AdminHealth';

/* ── fixture cf-health payload builder ──────────────────────────────────── */
function buildCfHealth({ signupBlocked = 0, signupTotal = 0 } = {}) {
  return {
    do_chat: {
      rate_check_blocked_by_prefix: { signup: signupBlocked, chat: 999 },
      rate_check_total_by_prefix:   { signup: signupTotal,   chat: 4321 },
    },
  };
}

function setCfHealthMock(payload) {
  axiosGet.mockImplementation((url) => {
    if (url.includes('/admin/cf-health')) return Promise.resolve({ data: payload });
    // Everything else AdminHealth polls — return empty objects so its
    // useEffects resolve without throwing. The signup tile only consumes
    // cf-health, so the contents of these other endpoints don't matter.
    return Promise.resolve({ data: {} });
  });
}

/** Flush a few microtask rounds so all useEffect-driven setStates settle. */
async function flushEffects() {
  for (let i = 0; i < 6; i++) {
    await act(async () => { await Promise.resolve(); });
  }
}

async function renderAdminHealth(payload) {
  setCfHealthMock(payload);
  render(<AdminHealth adminToken="test-token" onNavigate={vi.fn()} />);
  await flushEffects();
}

/* ═══════════════════════════════════════════════════════════════════════════
   Tests
   ═══════════════════════════════════════════════════════════════════════════ */
describe('Task #463 — AdminHealth signup throttle tile', () => {
  beforeEach(() => { axiosGet.mockReset(); });
  afterEach(()  => { vi.clearAllMocks(); });

  it('renders the signup-scoped numbers — not the chat slice — from rate_check_*_by_prefix', async () => {
    await renderAdminHealth(buildCfHealth({ signupBlocked: 7, signupTotal: 28 }));

    expect(screen.getByTestId('signup-throttle-blocked').textContent).toBe('7');
    expect(screen.getByTestId('signup-throttle-total').textContent).toBe('28');
    // 7 / 28 = 25%, rounded the same way the production IIFE does it.
    expect(screen.getByTestId('signup-throttle-ratio').textContent).toBe('25%');

    // Sanity: the tile must not accidentally surface the chat-prefix counts
    // (a regression in the prefix-key plumbing would render 999 / 4321 here).
    const tile = screen.getByTestId('signup-throttle-tile');
    expect(tile.textContent).not.toContain('999');
    expect(tile.textContent).not.toContain('4321');
  });

  it('shows zeroes and an em-dash ratio when signup has not been throttled', async () => {
    await renderAdminHealth(buildCfHealth({ signupBlocked: 0, signupTotal: 0 }));

    expect(screen.getByTestId('signup-throttle-blocked').textContent).toBe('0');
    expect(screen.getByTestId('signup-throttle-total').textContent).toBe('0');
    // signupTotal === 0 ⇒ avoid division-by-zero, render '—' not '0%'.
    expect(screen.getByTestId('signup-throttle-ratio').textContent).toBe('—');
  });

  it('paints the tile emerald when signupBlocked === 0 (calm state)', async () => {
    await renderAdminHealth(buildCfHealth({ signupBlocked: 0, signupTotal: 12 }));

    const tile = screen.getByTestId('signup-throttle-tile');
    expect(tile.className).toContain('bg-emerald-50');
    expect(tile.className).toContain('border-emerald-200');
    expect(tile.className).not.toContain('bg-amber-50');
    expect(tile.className).not.toContain('border-amber-200');
  });

  it('flips to amber the moment signupBlocked > 0 (active throttling)', async () => {
    await renderAdminHealth(buildCfHealth({ signupBlocked: 1, signupTotal: 4 }));

    const tile = screen.getByTestId('signup-throttle-tile');
    expect(tile.className).toContain('bg-amber-50');
    expect(tile.className).toContain('border-amber-200');
    expect(tile.className).not.toContain('bg-emerald-50');
    expect(tile.className).not.toContain('border-emerald-200');
  });

  it('rounds the block ratio with Math.round (1/3 → 33%, not 33.33%)', async () => {
    await renderAdminHealth(buildCfHealth({ signupBlocked: 1, signupTotal: 3 }));

    expect(screen.getByTestId('signup-throttle-ratio').textContent).toBe('33%');
  });
});
