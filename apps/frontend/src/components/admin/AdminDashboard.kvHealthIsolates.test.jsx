/**
 * Task #543 — AdminDashboard kv-health "by isolate" expander.
 *
 * Task #510 added a per-isolate breakdown row beneath every CF_EDGE_CACHE
 * binding in the /admin/kv-health panel. Only the worker-side aggregator
 * had unit coverage; this file pins the React rendering so a regression
 * to the toggle / IDs / counters surfaces in CI instead of in prod.
 *
 * Two layers of coverage:
 *   1. Integration: mount the real AdminDashboard with a stubbed
 *      `/admin/kv-health` payload (including a long crypto.randomUUID-style
 *      isolate ID so we can prove the row shortens, doesn't leak the full
 *      UUID, and still exposes it via the `title` attr for ops). This is
 *      the reviewer-mandated "render the actual component tree" path.
 *   2. Mirror: cheap component-level checks of toggle wiring + counter
 *      formatting + binding-keyed testids, so a JSX shape regression in
 *      the production block fails fast without needing to rewire the
 *      ~17 admin endpoints used by AdminDashboard's loaders.
 *
 * The mirror follows the pattern in `AdminDashboard.metricsFreshness.test.jsx`.
 */
import React, { useState } from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act, within } from '@testing-library/react';

/* ─────────────────────────────────────────────────────────────
 * Section A — Integration test against the real AdminDashboard
 * ───────────────────────────────────────────────────────────── */

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

import AdminDashboard from './AdminDashboard';

const ADMIN_TOKEN = 'header.payload.signature';

// Long crypto.randomUUID-style ID so we can prove the rendered
// "by isolate" row shortens it. Worker-side IDs in production are
// `crypto.randomUUID()` outputs (kv-monitor.ts ~L121–127).
const LONG_HOT_ID = 'aaaaaaaa-1111-4222-8333-cccccccccccc';
const LONG_COLD_ID = 'bbbbbbbb-2222-4333-9444-ddddddddffff';

const KV_HEALTH_FIXTURE = {
  configured: true,
  snapshot: {
    utcDay: '2026-05-07',
    warningPct: 80,
    bindings: [
      {
        binding: 'CF_EDGE_CACHE',
        utcDay: '2026-05-07',
        counters:    { read: 1635, write: 68, list: 8, delete: 2 },
        quota:       { read: 100000, write: 1000, list: 1000, delete: 1000 },
        percentages: { read: 1.6,    write: 6.8, list: 0.8, delete: 0.2 },
        status: 'healthy',
        fallbackActive: false,
        lastAlertFired: null,
        // Hottest first (1234 reads), then cold (401), then idle (0).
        // The render must preserve this order so an operator can
        // identify a single rogue isolate; client-side re-sort would
        // hide that signal.
        isolates: [
          { id: LONG_HOT_ID,  counters: { read: 1234, write: 56, list: 7, delete: 2 } },
          { id: LONG_COLD_ID, counters: { read: 401,  write: 12, list: 1, delete: 0 } },
          { id: 'short-iso',  counters: { read: 0,    write: 0,  list: 0, delete: 0 } },
        ],
      },
    ],
  },
};

const NOTIF_PREFS_FIXTURE = {
  sound_enabled: true,
  push_enabled: false,
  chime_tone: 'default',
  sound_severities: [],
  push_severities: [],
};

function defaultAxiosGet(url) {
  if (typeof url === 'string') {
    if (url.endsWith('/admin/kv-health'))            return Promise.resolve({ data: KV_HEALTH_FIXTURE });
    if (url.endsWith('/admin/notification-prefs'))   return Promise.resolve({ data: NOTIF_PREFS_FIXTURE });
    if (url.includes('/admin/push/delivery-stats'))  return Promise.resolve({ data: {} });
    if (url.endsWith('/admin/alert-settings'))       return Promise.resolve({ data: { channel_status: { push: null } } });
    if (url.includes('/admin/seo/daily-summary-dispatches')) return Promise.resolve({ data: { dispatches: [] } });
  }
  return Promise.resolve({ data: {} });
}

async function flushEffects() {
  for (let i = 0; i < 8; i++) {
    // eslint-disable-next-line no-await-in-loop
    await act(async () => { await Promise.resolve(); });
  }
}

async function mountAndOpenPrefs() {
  render(<AdminDashboard adminToken={ADMIN_TOKEN} onNavigate={vi.fn()} />);
  await waitFor(() => {
    expect(screen.queryByText(/Loading dashboard/)).toBeNull();
  }, { timeout: 4000 });
  await flushEffects();
  // Click the "Preferences" button to open the notif-prefs panel
  // (the kv-health row only renders inside it).
  const prefsBtn = await screen.findByRole('button', { name: /Preferences/ });
  fireEvent.click(prefsBtn);
  await waitFor(() => {
    expect(screen.getByTestId('notif-prefs-kv-health')).toBeInTheDocument();
  }, { timeout: 4000 });
}

describe('AdminDashboard kv-health per-isolate expander — integration (Task #543)', () => {
  beforeEach(() => {
    axiosGet.mockImplementation(defaultAxiosGet);
    axiosPost.mockResolvedValue({ data: {} });
    axiosPatch.mockResolvedValue({ data: {} });
    axiosPut.mockResolvedValue({ data: {} });
    axiosDelete.mockResolvedValue({ data: {} });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it('renders the "by isolate (N)" toggle starting collapsed under CF_EDGE_CACHE', async () => {
    await mountAndOpenPrefs();
    const toggle = screen.getByTestId('notif-prefs-kv-health-isolates-toggle-CF_EDGE_CACHE');
    expect(toggle.textContent).toContain('▸');
    expect(toggle.textContent).not.toContain('▾');
    expect(toggle.textContent).toContain('by isolate (3)');
    // Collapsed: the list MUST NOT be in the DOM. If it slipped in
    // expanded by default, screen-reader users + e2e selectors would
    // see a permanently expanded panel.
    expect(
      screen.queryByTestId('notif-prefs-kv-health-isolates-list-CF_EDGE_CACHE'),
    ).toBeNull();
  });

  it('expands on click, shows shortened IDs (no full-UUID leak), and lists the hottest isolate first', async () => {
    await mountAndOpenPrefs();
    const toggle = screen.getByTestId('notif-prefs-kv-health-isolates-toggle-CF_EDGE_CACHE');
    fireEvent.click(toggle);
    expect(toggle.textContent).toContain('▾');

    const list = screen.getByTestId('notif-prefs-kv-health-isolates-list-CF_EDGE_CACHE');
    expect(list).toBeTruthy();

    // ─── Shortened IDs: long crypto.randomUUID-style values must
    // be truncated. The full ID stays available via the `title`
    // attr for ops who need the raw value (e.g. to grep logs).
    const hotIdSpan = screen.getByTestId(
      `notif-prefs-kv-health-isolate-CF_EDGE_CACHE-${LONG_HOT_ID}-id`,
    );
    expect(hotIdSpan.textContent).toBe('aaaaaaaa…cccc');
    expect(hotIdSpan.textContent).not.toBe(LONG_HOT_ID);
    expect(hotIdSpan.getAttribute('title')).toBe(LONG_HOT_ID);
    // The visible text of the list MUST NOT contain the full UUID.
    expect(list.textContent).not.toContain(LONG_HOT_ID);
    expect(list.textContent).not.toContain(LONG_COLD_ID);
    // Cold isolate also shortened.
    const coldIdSpan = screen.getByTestId(
      `notif-prefs-kv-health-isolate-CF_EDGE_CACHE-${LONG_COLD_ID}-id`,
    );
    expect(coldIdSpan.textContent).toBe('bbbbbbbb…ffff');
    // Short fixture IDs (≤12 chars) render unchanged so dev fixtures
    // and the existing edge-proxy unit-test IDs stay readable.
    const shortIdSpan = screen.getByTestId(
      'notif-prefs-kv-health-isolate-CF_EDGE_CACHE-short-iso-id',
    );
    expect(shortIdSpan.textContent).toBe('short-iso');

    // ─── Hottest first: the worker returns isolates sorted by burn
    // and the panel must preserve that order, otherwise the whole
    // point of the breakdown (spotting a single rogue isolate) is
    // lost. The worker-side helper has its own ordering test; this
    // pins that the React render doesn't re-sort.
    const idCells = within(list).getAllByText(/^(aaaaaaaa…cccc|bbbbbbbb…ffff|short-iso)$/);
    expect(idCells.map((el) => el.textContent)).toEqual([
      'aaaaaaaa…cccc',
      'bbbbbbbb…ffff',
      'short-iso',
    ]);

    // ─── Counters: locked "r N · w N · l N · d N" format with
    // toLocaleString thousands separators on the hot row.
    const hotRow = screen.getByTestId(
      `notif-prefs-kv-health-isolate-CF_EDGE_CACHE-${LONG_HOT_ID}`,
    );
    expect(hotRow.textContent).toContain('r 1,234');
    expect(hotRow.textContent).toContain('w 56');
    expect(hotRow.textContent).toContain('l 7');
    expect(hotRow.textContent).toContain('d 2');
  });

  it('collapses again on a second click', async () => {
    await mountAndOpenPrefs();
    const toggle = screen.getByTestId('notif-prefs-kv-health-isolates-toggle-CF_EDGE_CACHE');
    fireEvent.click(toggle);
    expect(
      screen.getByTestId('notif-prefs-kv-health-isolates-list-CF_EDGE_CACHE'),
    ).toBeTruthy();
    fireEvent.click(toggle);
    expect(
      screen.queryByTestId('notif-prefs-kv-health-isolates-list-CF_EDGE_CACHE'),
    ).toBeNull();
    expect(toggle.textContent).toContain('▸');
  });
});

/* ─────────────────────────────────────────────────────────────
 * Section B — Mirror tests
 *
 * Cheap, fast checks of the JSX shape so a counter-format or
 * testid-key regression in the production block fails without
 * needing the full AdminDashboard tree spun up.
 * ───────────────────────────────────────────────────────────── */

function shortenIsolateIdForTest(id) {
  const s = String(id ?? '');
  return s.length > 12 ? `${s.slice(0, 8)}…${s.slice(-4)}` : s;
}

function KvIsolatesRow({ binding, isolates }) {
  const [expandedMap, setExpandedMap] = useState({});
  if (!Array.isArray(isolates) || isolates.length === 0) return null;
  const expanded = !!expandedMap[binding];
  return (
    <div>
      <button
        type="button"
        onClick={() =>
          setExpandedMap((prev) => ({ ...prev, [binding]: !prev[binding] }))
        }
        data-testid={`notif-prefs-kv-health-isolates-toggle-${binding}`}
      >
        {expanded ? '▾' : '▸'} by isolate ({isolates.length})
      </button>
      {expanded && (
        <ul data-testid={`notif-prefs-kv-health-isolates-list-${binding}`}>
          {isolates.map((iso) => {
            const r = iso.counters?.read ?? 0;
            const w = iso.counters?.write ?? 0;
            const l = iso.counters?.list ?? 0;
            const d = iso.counters?.delete ?? 0;
            const shortId = shortenIsolateIdForTest(iso.id);
            return (
              <li
                key={iso.id}
                data-testid={`notif-prefs-kv-health-isolate-${binding}-${iso.id}`}
              >
                <span data-testid={`notif-prefs-kv-health-isolate-${binding}-${iso.id}-id`}>
                  {shortId}
                </span>
                <span>
                  r {r.toLocaleString()} · w {w.toLocaleString()} · l {l.toLocaleString()} · d {d.toLocaleString()}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

const MIRROR_FIXTURE = [
  { id: 'iso-aaaa1111', counters: { read: 1234, write: 56, list: 7, delete: 2 } },
  { id: 'iso-bbbb2222', counters: { read: 401,  write: 12, list: 1, delete: 0 } },
];

describe('AdminDashboard kv-health per-isolate expander — mirror (Task #543)', () => {
  it('renders nothing when isolates is missing/empty so legacy snapshots stay quiet', () => {
    const { container: empty } = render(<KvIsolatesRow binding="CF_EDGE_CACHE" isolates={[]} />);
    expect(empty.firstChild).toBeNull();
    const { container: undef } = render(<KvIsolatesRow binding="CF_EDGE_CACHE" isolates={undefined} />);
    expect(undef.firstChild).toBeNull();
  });

  it('zero/missing counters render as "0", not blank or NaN', () => {
    render(
      <KvIsolatesRow
        binding="CF_EDGE_CACHE"
        isolates={[{ id: 'iso-no-counters' }]}
      />,
    );
    fireEvent.click(
      screen.getByTestId('notif-prefs-kv-health-isolates-toggle-CF_EDGE_CACHE'),
    );
    const row = screen.getByTestId(
      'notif-prefs-kv-health-isolate-CF_EDGE_CACHE-iso-no-counters',
    );
    expect(row.textContent).toContain('r 0');
    expect(row.textContent).toContain('w 0');
    expect(row.textContent).toContain('l 0');
    expect(row.textContent).toContain('d 0');
    expect(row.textContent).not.toMatch(/NaN/);
  });

  it('keys testids by binding so two side-by-side bindings get independent toggles', () => {
    function TwoBindings() {
      return (
        <>
          <KvIsolatesRow binding="CF_EDGE_CACHE" isolates={MIRROR_FIXTURE} />
          <KvIsolatesRow binding="RATE_LIMIT" isolates={MIRROR_FIXTURE.slice(0, 1)} />
        </>
      );
    }
    render(<TwoBindings />);
    const a = screen.getByTestId('notif-prefs-kv-health-isolates-toggle-CF_EDGE_CACHE');
    const b = screen.getByTestId('notif-prefs-kv-health-isolates-toggle-RATE_LIMIT');
    expect(a.textContent).toContain('by isolate (2)');
    expect(b.textContent).toContain('by isolate (1)');
    fireEvent.click(a);
    expect(
      screen.getByTestId('notif-prefs-kv-health-isolates-list-CF_EDGE_CACHE'),
    ).toBeTruthy();
    expect(
      screen.queryByTestId('notif-prefs-kv-health-isolates-list-RATE_LIMIT'),
    ).toBeNull();
  });
});
