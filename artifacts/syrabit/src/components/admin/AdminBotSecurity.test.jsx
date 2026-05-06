/**
 * Task #1 — AdminBotSecurity component Vitest tests.
 *
 * Uses renderToStaticMarkup (synchronous, no effects). The component's
 * outer panel renders the "Bot Security" heading, but sub-sections with
 * their own loading states emit their loading text first.
 */
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, it, expect, vi } from 'vitest';

vi.mock('@/utils/api', () => ({
  adminGetSpoofedBots:              vi.fn(() => Promise.resolve({ data: { bots: [] } })),
  adminGetBlockedIps:               vi.fn(() => Promise.resolve({ data: { blocked_ips: [] } })),
  adminGetBlockTrends:              vi.fn(() => Promise.resolve({ data: [] })),
  adminBlockIp:                     vi.fn(() => Promise.resolve({ data: {} })),
  adminUnblockIp:                   vi.fn(() => Promise.resolve({ data: {} })),
  adminGetAlertSettings:            vi.fn(() => Promise.resolve({ data: {} })),
  adminUpdateAlertSettings:         vi.fn(() => Promise.resolve({ data: {} })),
  adminTestAlertDelivery:           vi.fn(() => Promise.resolve({ data: {} })),
  adminGetTtlMonitor:               vi.fn(() => Promise.resolve({ data: {} })),
  adminGetCollectionSizeHistory:    vi.fn(() => Promise.resolve({ data: [] })),
  adminGetAlerts:                   vi.fn(() => Promise.resolve({ data: { alerts: [] } })),
  adminAcknowledgeAlert:            vi.fn(() => Promise.resolve({ data: {} })),
  adminAcknowledgeAllAlerts:        vi.fn(() => Promise.resolve({ data: {} })),
  adminBackfillThresholds:          vi.fn(() => Promise.resolve({ data: {} })),
  adminSendReviewPromptWeeklyDigest:vi.fn(() => Promise.resolve({ data: {} })),
  adminGetAlertCooldowns:           vi.fn(() => Promise.resolve({ data: {} })),
  adminReleaseAlertCooldown:        vi.fn(() => Promise.resolve({ data: {} })),
  API_BASE: 'http://localhost:8000',
}));

vi.mock('recharts', () => ({
  LineChart:           ({ children }) => <div>{children}</div>,
  AreaChart:           ({ children }) => <div>{children}</div>,
  BarChart:            ({ children }) => <div>{children}</div>,
  Line:                () => null,
  Area:                () => null,
  Bar:                 () => null,
  XAxis:               () => null,
  YAxis:               () => null,
  CartesianGrid:       () => null,
  Tooltip:             () => null,
  ResponsiveContainer: ({ children }) => <div>{children}</div>,
  ReferenceLine:       () => null,
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

vi.mock('@/components/ErrorBoundary', () => ({
  SectionErrorBoundary: ({ children }) => <div>{children}</div>,
}));

import AdminBotSecurity from './AdminBotSecurity';
import { ALERT_THRESHOLD_FIELDS } from './AdminBotSecurity';

describe('AdminBotSecurity', () => {
  it('renders without throwing — shows loading state initially', () => {
    // The component starts in loading state — initial render shows spinner text.
    const html = renderToStaticMarkup(
      <AdminBotSecurity adminToken="test-token" />,
    );
    expect(html.length).toBeGreaterThan(0);
    // The component emits some loading indicator or animate-spin class.
    expect(html.toLowerCase()).toMatch(/loading|animate-spin/i);
  });

  it('renders with an empty adminToken without throwing', () => {
    expect(() =>
      renderToStaticMarkup(<AdminBotSecurity adminToken="" />),
    ).not.toThrow();
  });

  // Task #447 — the Alert Settings panel now iterates
  // ALERT_THRESHOLD_FIELDS to render the memory_brain failure-rate
  // tunables (added to _ALERT_THRESHOLDS_DEFAULT in Task #417). This
  // test pins the iterated config so a regression that drops either
  // key from the panel surfaces immediately.
  describe('ALERT_THRESHOLD_FIELDS config', () => {
    it('exposes both memory_brain failure-rate tunables in the iterated panel config', () => {
      const keys = ALERT_THRESHOLD_FIELDS.map(f => f.key);
      expect(keys).toContain('memory_brain_failure_rate_pct');
      expect(keys).toContain('memory_brain_failure_min_sample');
    });

    it('every field has a label, help text and a default for the panel UI', () => {
      for (const f of ALERT_THRESHOLD_FIELDS) {
        expect(typeof f.key).toBe('string');
        expect(f.key.length).toBeGreaterThan(0);
        expect(typeof f.label).toBe('string');
        expect(f.label.length).toBeGreaterThan(0);
        expect(typeof f.help).toBe('string');
        expect(f.help.length).toBeGreaterThan(0);
        expect(typeof f.default).toBe('number');
        expect(typeof f.min).toBe('number');
        expect(typeof f.max).toBe('number');
        expect(f.max).toBeGreaterThan(f.min);
      }
    });
  });
});
