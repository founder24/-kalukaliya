/**
 * Task #326 — AdminDashboard integration test for the R2 watchdog
 * re-evaluate flow.
 *
 * Sibling to AdminDashboard.r2WatchdogReset.test.jsx (Task #325).
 * Per-layer tests cover R2ColdStoragePanel's button render/click
 * and the backend route. This file exercises the wiring layer in
 * between: the inline `onReevaluate` closure AdminDashboard passes
 * to <R2ColdStoragePanel/> at AdminDashboard.jsx ~3192.
 *
 * Flow under test:
 *   click "Re-evaluate now"
 *   → POST /admin/r2-storage-health/run with adminHdr(token)
 *   → r2Health.state replaced from response
 *   → top-level metadata (buckets / logpush_cap_gb / rules_age_days)
 *     falls back to the prior r2Health when the run response omits
 *     them (the worker only echoes them inside `result`)
 *   → toast dispatches the correct copy across four branches:
 *       - success                    : toast.success
 *       - result.ok === false        : toast.error  ("skipped: …")
 *       - result.skipped (truthy)    : toast.message ("skipped: …")
 *       - 429 with retry_after_seconds: toast.error ("Cooldown — try again in Xs")
 *       - generic rejection          : toast.error  ("Re-evaluate failed")
 *
 * Catches wiring bugs the per-layer tests cannot:
 *   - wrong URL (e.g. /admin/r2-health/run, missing /admin prefix)
 *   - missing adminHdr (no Bearer header sent)
 *   - state-merge mistakes that overwrite top-level metadata when
 *     the run response only includes `state` + `result`
 *   - swapped/missing toast branches (e.g. firing success on a
 *     skipped response, or the cooldown toast missing the seconds)
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';

/* ── axios mock ────────────────────────────────────────────────────────── */
const { axiosGet, axiosPost, axiosPatch, axiosPut, axiosDelete } = vi.hoisted(() => ({
  axiosGet:    vi.fn(),
  axiosPost:   vi.fn(),
  axiosPatch:  vi.fn(),
  axiosPut:    vi.fn(),
  axiosDelete: vi.fn(),
}));

vi.mock('axios', () => ({
  default: {
    get:    axiosGet,
    post:   axiosPost,
    patch:  axiosPatch,
    put:    axiosPut,
    delete: axiosDelete,
    create: vi.fn(),
  },
  get: axiosGet,
  post: axiosPost,
}));

/* ── toast mock ────────────────────────────────────────────────────────── */
const { toastSuccess, toastError, toastMessage, toastInfo } = vi.hoisted(() => ({
  toastSuccess: vi.fn(),
  toastError:   vi.fn(),
  toastMessage: vi.fn(),
  toastInfo:    vi.fn(),
}));

vi.mock('sonner', () => ({
  toast: {
    success: toastSuccess,
    error:   toastError,
    message: toastMessage,
    info:    toastInfo,
  },
}));

/* ── library + sub-component stubs ─────────────────────────────────────── */
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

vi.mock('@/components/ErrorBoundary', () => ({
  SectionErrorBoundary: ({ children }) => <>{children}</>,
}));

vi.mock('./AdminQuickLinks',          () => ({ default: () => null }));
vi.mock('./AdminDraftServedSubjects', () => ({ default: () => null }));
vi.mock('./AlertReasonsRow',          () => ({ default: () => null }));
vi.mock('./BotCachePanel',            () => ({ default: () => null }));
vi.mock('./AudioTrimPreview',         () => ({ default: () => null }));
vi.mock('./analytics/CloudflareAnalyticsBanner', () => ({ default: () => null }));

vi.mock('@/hooks/usePushNotifications', () => ({
  usePushNotifications: () => ({
    permission: 'default',
    subscribed: false,
    isSupported: false,
    loading: false,
    error: null,
    subscribe: vi.fn(),
    unsubscribe: vi.fn(),
  }),
}));

vi.mock('@/utils/logger', () => ({
  log: { error: vi.fn(), warn: vi.fn(), info: vi.fn(), debug: vi.fn() },
}));

/* ── @/utils/api mock ──────────────────────────────────────────────────── */
vi.mock('@/utils/api', () => ({
  API_BASE: 'http://test.local/api',
  adminGetDashboard:        vi.fn(() => Promise.resolve({ data: {} })),
  adminGetCfOverview:       vi.fn(() => Promise.resolve({ data: {} })),
  seoPipelineStatus:        vi.fn(() => Promise.resolve({ data: {} })),
  adminSeoHealthHistory:    vi.fn(() => Promise.resolve({ data: {} })),
  adminSeoHealthSnapshotNow:vi.fn(() => Promise.resolve({ data: {} })),
  seoHealthLive:            vi.fn(() => Promise.resolve({ data: {} })),
  seoHealthDeepScan:        vi.fn(() => Promise.resolve({ data: {} })),
  adminSeoDeepScanHistory:  vi.fn(() => Promise.resolve({ data: {} })),
  adminGetAlertCooldowns:   vi.fn(() => Promise.resolve({ data: { active_count: 0 } })),
}));

/* ── component import (AFTER vi.mock calls) ────────────────────────────── */
import AdminDashboard from './AdminDashboard';

/* ── helpers ───────────────────────────────────────────────────────────── */
const ADMIN_TOKEN = 'header.payload.signature'; // 3-segment token → triggers Bearer header

/** Default r2Health snapshot — healthy, watchdog clean. The
 *  re-evaluate button renders regardless of watchdog state, so we
 *  do not need a tripped counter to exercise this flow. */
function r2HealthHealthy() {
  return {
    configured: true,
    disabled: false,
    buckets: ['syrabit-assets', 'syrabit-public'],
    logpush_cap_gb: 5,
    rules_applied_at: '2026-01-01T00:00:00Z',
    rules_age_days: 120,
    query_fail_threshold: 2,
    state: {
      last_evaluated_at: '2026-04-01T00:00:00Z',
      ia_share_last_fired_at: null,
      logpush_last_fired_at: null,
      last_ia_share: 0.4,
      last_total_gb: 50,
      last_logpush_gb: 1.5,
      consecutive_query_failures: 0,
      query_fail_last_fired_at: null,
    },
  };
}

/** Fresh state returned by /run on a successful evaluation. */
function r2StateAfterRun() {
  return {
    last_evaluated_at: '2026-05-03T12:00:00Z',
    ia_share_last_fired_at: null,
    logpush_last_fired_at: null,
    last_ia_share: 0.5,
    last_total_gb: 60,
    last_logpush_gb: 2.0,
    consecutive_query_failures: 0,
    query_fail_last_fired_at: null,
  };
}

function defaultAxiosGet(url) {
  if (url.includes('/admin/r2-storage-health')) {
    return Promise.resolve({ data: r2HealthHealthy() });
  }
  if (url.includes('/admin/notification-prefs')) {
    return Promise.resolve({
      data: {
        sound_enabled: true,
        push_enabled: false,
        chime_tone: 'default',
        sound_severities: [],
        push_severities: [],
      },
    });
  }
  return Promise.resolve({ data: {} });
}

async function flushEffects() {
  for (let i = 0; i < 6; i++) {
    await act(async () => { await Promise.resolve(); });
  }
}

async function mountAndOpenPrefs() {
  render(<AdminDashboard adminToken={ADMIN_TOKEN} onNavigate={vi.fn()} />);
  await waitFor(() => {
    expect(screen.queryByText(/Loading dashboard/)).toBeNull();
  }, { timeout: 4000 });
  await flushEffects();
  fireEvent.click(screen.getByRole('button', { name: /Preferences/i }));
  // The R2 panel renders synchronously inside the prefs modal once
  // r2Health is loaded; wait for the re-evaluate button itself.
  await waitFor(() => {
    expect(screen.getByTestId('r2-cold-storage-reevaluate')).toBeInTheDocument();
  }, { timeout: 4000 });
}

/* ── tests ─────────────────────────────────────────────────────────────── */
describe('AdminDashboard — R2 watchdog re-evaluate wiring (Task #326)', () => {
  beforeEach(() => {
    axiosGet.mockImplementation(defaultAxiosGet);
    axiosPost.mockResolvedValue({
      data: { ok: true, state: r2StateAfterRun(), result: { ok: true } },
    });
    axiosPatch.mockResolvedValue({ data: {} });
    axiosPut.mockResolvedValue({ data: {} });
    axiosDelete.mockResolvedValue({ data: {} });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it('POSTs to the canonical /run URL with the admin Bearer header', async () => {
    await mountAndOpenPrefs();

    fireEvent.click(screen.getByTestId('r2-cold-storage-reevaluate'));

    await waitFor(() => {
      expect(axiosPost).toHaveBeenCalledWith(
        'http://test.local/api/admin/r2-storage-health/run',
        null,
        expect.objectContaining({
          headers: expect.objectContaining({
            Authorization: `Bearer ${ADMIN_TOKEN}`,
          }),
          withCredentials: true,
        }),
      );
    });
  });

  it('replaces r2Health.state from the response (last-evaluated updates)', async () => {
    await mountAndOpenPrefs();

    fireEvent.click(screen.getByTestId('r2-cold-storage-reevaluate'));

    // After the POST resolves, setR2Health re-renders the panel
    // using the fresh `state.last_total_gb = 60` — the IA share
    // tile sub-line displays "of 60.00 GB total".
    await waitFor(() => {
      expect(screen.getByTestId('r2-cold-storage-ia-share')).toHaveTextContent(
        /of 60\.00 GB total/,
      );
    });
    // And the watchdog stays clean (counter still 0 → indicator hidden).
    expect(screen.queryByTestId('r2-cold-storage-watchdog-indicator')).toBeNull();
  });

  it('preserves prior buckets / logpush_cap_gb / rules_age_days when the response omits them', async () => {
    // The /run response only echoes back `state` (and sometimes a
    // `result` block). The handler must fall back to the prior
    // r2Health for top-level config metadata. If a future refactor
    // accidentally drops the fallback chain, the rendered bucket
    // list / cap / rules-age line would silently disappear.
    await mountAndOpenPrefs();

    // Sanity: prior metadata is rendered.
    expect(screen.getByText(/syrabit-assets, syrabit-public/)).toBeInTheDocument();
    expect(screen.getByTestId('r2-cold-storage-logpush')).toHaveTextContent(/cap 5 GB/);
    expect(screen.getByTestId('r2-cold-storage-rules-age')).toHaveTextContent(/120d/);

    fireEvent.click(screen.getByTestId('r2-cold-storage-reevaluate'));

    await waitFor(() => {
      expect(axiosPost).toHaveBeenCalled();
    });
    // Wait for the state replacement to land (last-evaluated tile
    // updates as a marker the new r2Health has been committed).
    await waitFor(() => {
      expect(screen.getByTestId('r2-cold-storage-ia-share')).toHaveTextContent(
        /of 60\.00 GB total/,
      );
    });

    // Top-level config metadata survived — the response contained
    // none of these and the prev fallbacks fired correctly.
    expect(screen.getByText(/syrabit-assets, syrabit-public/)).toBeInTheDocument();
    expect(screen.getByTestId('r2-cold-storage-logpush')).toHaveTextContent(/cap 5 GB/);
    expect(screen.getByTestId('r2-cold-storage-rules-age')).toHaveTextContent(/120d/);
  });

  it('uses fresh metadata from runRes.data.result when present (overrides prior)', async () => {
    // Mirror image of the previous test — when the worker DOES echo
    // metadata back inside `result`, the handler should adopt it.
    axiosPost.mockResolvedValueOnce({
      data: {
        ok: true,
        state: r2StateAfterRun(),
        result: {
          ok: true,
          buckets: ['syrabit-archive'],
          logpush_cap_gb: 7,
          rules_age_days: 200,
        },
      },
    });
    await mountAndOpenPrefs();

    fireEvent.click(screen.getByTestId('r2-cold-storage-reevaluate'));

    await waitFor(() => {
      expect(screen.getByText(/syrabit-archive/)).toBeInTheDocument();
    });
    expect(screen.getByTestId('r2-cold-storage-logpush')).toHaveTextContent(/cap 7 GB/);
    expect(screen.getByTestId('r2-cold-storage-rules-age')).toHaveTextContent(/200d/);
    // Prior buckets must NOT linger.
    expect(screen.queryByText(/syrabit-public/)).toBeNull();
  });

  it('fires the success toast on a clean run', async () => {
    await mountAndOpenPrefs();

    fireEvent.click(screen.getByTestId('r2-cold-storage-reevaluate'));

    await waitFor(() => {
      expect(toastSuccess).toHaveBeenCalledWith('R2 cold-storage watchdog re-evaluated');
    });
    expect(toastError).not.toHaveBeenCalled();
    expect(toastMessage).not.toHaveBeenCalled();
  });

  it('fires the error toast when result.ok === false (worker reported failure)', async () => {
    axiosPost.mockResolvedValueOnce({
      data: {
        ok: true,
        state: r2StateAfterRun(),
        result: { ok: false, reason: 'graphql_query_failed' },
      },
    });
    await mountAndOpenPrefs();

    fireEvent.click(screen.getByTestId('r2-cold-storage-reevaluate'));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith(
        'Re-evaluate skipped: graphql_query_failed',
      );
    });
    expect(toastSuccess).not.toHaveBeenCalledWith('R2 cold-storage watchdog re-evaluated');
    expect(toastMessage).not.toHaveBeenCalled();
  });

  it('falls back to "unknown reason" when result.ok===false omits a reason', async () => {
    axiosPost.mockResolvedValueOnce({
      data: { ok: true, state: r2StateAfterRun(), result: { ok: false } },
    });
    await mountAndOpenPrefs();

    fireEvent.click(screen.getByTestId('r2-cold-storage-reevaluate'));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith('Re-evaluate skipped: unknown reason');
    });
  });

  it('fires toast.message (neutral) when result.skipped is truthy', async () => {
    axiosPost.mockResolvedValueOnce({
      data: {
        ok: true,
        state: r2StateAfterRun(),
        result: { ok: true, skipped: true, reason: 'cooldown_anchor' },
      },
    });
    await mountAndOpenPrefs();

    fireEvent.click(screen.getByTestId('r2-cold-storage-reevaluate'));

    await waitFor(() => {
      expect(toastMessage).toHaveBeenCalledWith(
        'Re-evaluate skipped: cooldown_anchor',
      );
    });
    expect(toastSuccess).not.toHaveBeenCalledWith('R2 cold-storage watchdog re-evaluated');
    expect(toastError).not.toHaveBeenCalled();
  });

  it('falls back to "no work to do" when skipped omits a reason', async () => {
    axiosPost.mockResolvedValueOnce({
      data: {
        ok: true,
        state: r2StateAfterRun(),
        result: { ok: true, skipped: true },
      },
    });
    await mountAndOpenPrefs();

    fireEvent.click(screen.getByTestId('r2-cold-storage-reevaluate'));

    await waitFor(() => {
      expect(toastMessage).toHaveBeenCalledWith('Re-evaluate skipped: no work to do');
    });
  });

  it('fires the cooldown toast with retry seconds on 429', async () => {
    const err = new Error('Request failed with status code 429');
    err.response = {
      status: 429,
      data: { detail: { retry_after_seconds: 42 } },
    };
    axiosPost.mockRejectedValueOnce(err);
    await mountAndOpenPrefs();

    fireEvent.click(screen.getByTestId('r2-cold-storage-reevaluate'));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith('Cooldown — try again in 42s');
    });
    expect(toastError).not.toHaveBeenCalledWith('Re-evaluate failed');
  });

  it('falls back to "?" seconds when 429 detail omits retry_after_seconds', async () => {
    const err = new Error('429');
    err.response = { status: 429, data: { detail: 'too soon' } }; // string detail
    axiosPost.mockRejectedValueOnce(err);
    await mountAndOpenPrefs();

    fireEvent.click(screen.getByTestId('r2-cold-storage-reevaluate'));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith('Cooldown — try again in ?s');
    });
  });

  it('fires the generic failure toast on a non-429 rejection', async () => {
    const err = new Error('boom');
    err.response = { status: 500, data: {} };
    axiosPost.mockRejectedValueOnce(err);
    await mountAndOpenPrefs();

    fireEvent.click(screen.getByTestId('r2-cold-storage-reevaluate'));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith('Re-evaluate failed');
    });
    expect(toastSuccess).not.toHaveBeenCalled();
  });
});
