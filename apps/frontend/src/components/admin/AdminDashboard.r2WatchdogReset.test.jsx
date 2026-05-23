/**
 * Task #325 — AdminDashboard integration test for the R2 watchdog reset flow.
 *
 * The R2ColdStoragePanel unit tests cover the button render/click/disabled
 * states and the backend proxy tests cover the HTTP layer. This file
 * exercises the wiring layer in between: the inline `onResetWatchdog`
 * handler that AdminDashboard passes to <R2ColdStoragePanel/>.
 *
 * Flow under test (inline closure at AdminDashboard.jsx ~3163):
 *   click reset button
 *   → POST /admin/r2-storage-health/reset-watchdog with adminHdr(token)
 *   → r2Health.state is replaced from response (not merged top-level only)
 *   → watchdog-blind badge disappears (consecutive_query_failures=0)
 *   → toast.success fires with the operator-facing copy
 *
 * Catches wiring bugs the per-layer tests cannot:
 *   - wrong URL (e.g. missing /admin prefix, wrong route)
 *   - missing adminHdr (no Bearer token sent)
 *   - state-merge mistakes (e.g. spreading the response on top of state
 *     so stale watchdog counters survive)
 *   - missing toast on success / wrong copy
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

/** Default r2Health snapshot with the watchdog tripped (count >= threshold). */
function r2HealthTripped() {
  return {
    configured: true,
    disabled: false,
    buckets: ['syrabit-assets'],
    logpush_cap_gb: 5,
    rules_applied_at: '2026-01-01T00:00:00Z',
    rules_age_days: 120,
    query_fail_threshold: 2,
    state: {
      last_evaluated_at: new Date().toISOString(),
      ia_share_last_fired_at: null,
      logpush_last_fired_at: null,
      last_ia_share: 0.4,
      last_total_gb: 50,
      last_logpush_gb: 1.5,
      consecutive_query_failures: 2,
      query_fail_last_fired_at: '2026-04-15T12:00:00Z',
    },
  };
}

/** Reset-response state — counter cleared. */
function r2StateAfterReset() {
  return {
    last_evaluated_at: new Date().toISOString(),
    ia_share_last_fired_at: null,
    logpush_last_fired_at: null,
    last_ia_share: 0.4,
    last_total_gb: 50,
    last_logpush_gb: 1.5,
    consecutive_query_failures: 0,
    query_fail_last_fired_at: null,
  };
}

function defaultAxiosGet(url) {
  if (url.includes('/admin/r2-storage-health')) {
    return Promise.resolve({ data: r2HealthTripped() });
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
  // Every other admin GET resolves with an empty payload so `loading`
  // flips to false (Promise.allSettled never fully rejects) and the
  // dashboard mounts the prefs modal subtree.
  return Promise.resolve({ data: {} });
}

async function flushEffects() {
  // Two passes drains the chain of useState → useEffect → axios .then
  // microtasks the dashboard does on mount.
  for (let i = 0; i < 6; i++) {
    await act(async () => { await Promise.resolve(); });
  }
}

async function mountAndOpenPrefs() {
  render(<AdminDashboard adminToken={ADMIN_TOKEN} onNavigate={vi.fn()} />);
  // Wait until the loading spinner disappears (data load resolved).
  await waitFor(() => {
    expect(screen.queryByText(/Loading dashboard/)).toBeNull();
  }, { timeout: 4000 });
  // Drain a few extra microtask cycles so loadNotifPrefs() finishes
  // populating notifPrefs + r2Health before we open the modal.
  await flushEffects();
  // Open the notification preferences modal — that subtree renders
  // <R2ColdStoragePanel/> with the inline reset handler.
  fireEvent.click(screen.getByRole('button', { name: /Preferences/i }));
  // The watchdog indicator only renders once notifPrefs + r2Health are
  // both populated; one more effect flush guarantees the panel is up.
  await waitFor(() => {
    expect(screen.getByTestId('r2-cold-storage-watchdog-indicator')).toBeInTheDocument();
  }, { timeout: 4000 });
}

/* ── tests ─────────────────────────────────────────────────────────────── */
describe('AdminDashboard — R2 watchdog reset wiring (Task #325)', () => {
  beforeEach(() => {
    axiosGet.mockImplementation(defaultAxiosGet);
    axiosPost.mockResolvedValue({ data: { ok: true, state: r2StateAfterReset() } });
    axiosPatch.mockResolvedValue({ data: {} });
    axiosPut.mockResolvedValue({ data: {} });
    axiosDelete.mockResolvedValue({ data: {} });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it('POSTs to the canonical reset URL with the admin Bearer header', async () => {
    await mountAndOpenPrefs();

    fireEvent.click(screen.getByTestId('r2-cold-storage-watchdog-reset'));

    await waitFor(() => {
      expect(axiosPost).toHaveBeenCalledWith(
        'http://test.local/api/admin/r2-storage-health/reset-watchdog',
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

  it('replaces r2Health.state from the response — watchdog badge disappears', async () => {
    await mountAndOpenPrefs();

    // Sanity check: indicator is initially red/tripped (count=2/2).
    const before = screen.getByTestId('r2-cold-storage-watchdog-indicator');
    expect(before.getAttribute('data-watchdog-state')).toBe('tripped');
    expect(before.getAttribute('data-watchdog-count')).toBe('2');

    fireEvent.click(screen.getByTestId('r2-cold-storage-watchdog-reset'));

    // Once the POST resolves and setR2Health re-renders the panel
    // the indicator (which is hidden when count===0) disappears.
    await waitFor(() => {
      expect(screen.queryByTestId('r2-cold-storage-watchdog-indicator')).toBeNull();
    });
    expect(screen.queryByTestId('r2-cold-storage-watchdog-reset')).toBeNull();
  });

  it('fires the operator-facing success toast', async () => {
    await mountAndOpenPrefs();

    fireEvent.click(screen.getByTestId('r2-cold-storage-watchdog-reset'));

    await waitFor(() => {
      expect(toastSuccess).toHaveBeenCalledWith('R2 watchdog-blind counter reset');
    });
    expect(toastError).not.toHaveBeenCalled();
  });

  it('fires the failure toast and keeps the badge visible when POST rejects', async () => {
    axiosPost.mockRejectedValueOnce(new Error('boom'));
    await mountAndOpenPrefs();

    fireEvent.click(screen.getByTestId('r2-cold-storage-watchdog-reset'));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith('Reset failed');
    });
    // Badge must still be rendered — we never replaced state.
    expect(screen.getByTestId('r2-cold-storage-watchdog-indicator')).toBeInTheDocument();
    expect(toastSuccess).not.toHaveBeenCalledWith('R2 watchdog-blind counter reset');
  });

  it('preserves prior top-level config metadata when replacing state (no overwrite)', async () => {
    // The handler does { ...prev, configured: true, state: res.data.state }
    // — i.e. it replaces only `state` and keeps the rest of the payload.
    // If a future refactor accidentally spreads the response on top of
    // `prev`, top-level fields the reset response does NOT echo back
    // (buckets, logpush_cap_gb, rules_age_days) silently disappear.
    //
    // Pin that contract by asserting the rendered bucket list — which
    // comes from `health.buckets` (top-level, not in `state`) and is
    // never present in the reset response payload — still shows after
    // the reset completes.
    await mountAndOpenPrefs();

    // Sanity: bucket list is rendered before the reset.
    expect(screen.getByText(/syrabit-assets/)).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('r2-cold-storage-watchdog-reset'));

    await waitFor(() => {
      expect(axiosPost).toHaveBeenCalled();
    });
    // Wait for the watchdog badge to clear so we know setR2Health
    // has fired with the response — then confirm the top-level
    // bucket list survived (would be gone if the handler had
    // overwritten the whole r2Health object with the response).
    await waitFor(() => {
      expect(screen.queryByTestId('r2-cold-storage-watchdog-indicator')).toBeNull();
    });
    expect(screen.getByText(/syrabit-assets/)).toBeInTheDocument();
  });
});
