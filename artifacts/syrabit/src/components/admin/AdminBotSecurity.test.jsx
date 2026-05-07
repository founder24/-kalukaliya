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
import { ALERT_THRESHOLD_FIELDS, parseAlertSettingsBackendError } from './AdminBotSecurity';

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

  // Task #447 / Task #484 — the Alert Settings panel iterates
  // ALERT_THRESHOLD_FIELDS to render every threshold row. Task #484
  // migrated the four originally hand-coded thresholds (spoof_rpm,
  // auto_block_threshold, auto_block_expiry_hours,
  // collection_growth_per_day) onto this same config so they share
  // the iterator's default-text + range validation behaviour. These
  // tests pin the config so a regression that drops any key from the
  // panel surfaces immediately.
  describe('ALERT_THRESHOLD_FIELDS config', () => {
    it('exposes both memory_brain failure-rate tunables in the iterated panel config', () => {
      const keys = ALERT_THRESHOLD_FIELDS.map(f => f.key);
      expect(keys).toContain('memory_brain_failure_rate_pct');
      expect(keys).toContain('memory_brain_failure_min_sample');
    });

    // Task #484 — mirrors the memory_brain assertion above for the
    // four legacy thresholds that were migrated onto the iterator.
    it('exposes the four legacy thresholds in the iterated panel config', () => {
      const keys = ALERT_THRESHOLD_FIELDS.map(f => f.key);
      expect(keys).toContain('spoof_rpm');
      expect(keys).toContain('auto_block_threshold');
      expect(keys).toContain('auto_block_expiry_hours');
      expect(keys).toContain('collection_growth_per_day');
    });

    // Task #531 — parseAlertSettingsBackendError must derive its
    // field-name allowlist from ALERT_THRESHOLD_FIELDS so newly added
    // thresholds (memory_brain_*) surface as per-field errors instead
    // of falling through to the generic banner.
    describe('parseAlertSettingsBackendError (Task #531)', () => {
      it('maps a 422 detail keyed on a legacy threshold (spoof_rpm) to a per-field error', () => {
        const err = {
          response: {
            status: 422,
            data: { detail: [{ loc: ['body', 'thresholds', 'spoof_rpm'], msg: 'must be ≥ 1' }] },
          },
        };
        const out = parseAlertSettingsBackendError(err);
        expect(out).toEqual({ spoof_rpm: 'must be ≥ 1' });
        expect(out.general).toBeUndefined();
      });

      it('maps a 422 detail keyed on a memory_brain threshold to a per-field error', () => {
        const err = {
          response: {
            status: 422,
            data: { detail: [{ loc: ['body', 'thresholds', 'memory_brain_failure_rate_pct'], msg: 'must be ≤ 100' }] },
          },
        };
        const out = parseAlertSettingsBackendError(err);
        expect(out).toEqual({ memory_brain_failure_rate_pct: 'must be ≤ 100' });
        expect(out.general).toBeUndefined();
      });

      it('maps a string-detail mentioning a memory_brain key to that per-field error', () => {
        const err = {
          response: {
            status: 400,
            data: { detail: 'memory_brain_failure_min_sample must be > 0' },
          },
        };
        const out = parseAlertSettingsBackendError(err);
        expect(out).toEqual({ memory_brain_failure_min_sample: 'memory_brain_failure_min_sample must be > 0' });
      });

      it('falls through to general for an unknown 422 field', () => {
        const err = {
          response: {
            status: 422,
            data: { detail: [{ loc: ['body', 'mystery_field'], msg: 'nope' }] },
          },
        };
        const out = parseAlertSettingsBackendError(err);
        expect(out).toEqual({ general: 'nope' });
      });
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
