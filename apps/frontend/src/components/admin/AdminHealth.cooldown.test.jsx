/**
 * Tasks #101, #104 & #111 — AdminHealth embed cooldown tests.
 *
 * Three complementary strategies in one file:
 *
 * ① Static-markup tests — fast, no jsdom boot.
 *   Mirror the exact className expressions in compact local components.
 *   Caught by: badge CSS logic across all urgency stages, banner palette split.
 *
 * ② Urgency-cue integration tests (Task #104) — mount AdminHealth with real
 *   timers + waitFor so CSS classes on the live component are verified.
 *   We do NOT use vi.useFakeTimers() here because it prevents waitFor (which
 *   relies on real setTimeout) from retrying.
 *   Covered: A. inactive, B. >10 s, C. 6–10 s, D. ≤5 s, D₂/D₃/C₂ cell+banner.
 *
 * ③ Ticker behavioral tests (Task #101) — mount AdminHealth with partial fake
 *   timers (setInterval/clearInterval only) to drive the live countdown and
 *   verify interval lifecycle precisely:
 *   (a) starts at correct backend value, (b) counts down to 0,
 *   (c) clears the specific handle when cooldown lifts,
 *   (d) no leaked interval handle after unmount.
 */
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

/* ── axios mock ──────────────────────────────────────────────────────────────
   vi.hoisted() so the spy reference is available inside the factory (which
   is hoisted to the top of the file by vitest).  Both test suites use
   axiosGet directly; the named `get` export lets HEAD-style
   `axios.get.mockImplementation(...)` patterns work too.               */
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

/* ── component import (after all vi.mock calls) ──────────────────────────── */
import AdminHealth from './AdminHealth';

/* ═══════════════════════════════════════════════════════════════════════════
   ① Static-markup mirror components
   ═══════════════════════════════════════════════════════════════════════════ */

/** Mirrors the "Cooldown clears in" stat cell from AdminHealth.jsx (~2257) */
function CooldownBadge({ cooldown, embedCooldownDisplay }) {
  const cellCls = [
    'rounded-lg p-2.5 border transition-colors',
    cooldown && embedCooldownDisplay <= 5
      ? 'bg-amber-50 border-amber-300'
      : 'bg-white/70 border-gray-100',
  ].join(' ');

  const labelCls = [
    'text-[10px] uppercase font-semibold mb-0.5',
    cooldown && embedCooldownDisplay <= 5 ? 'text-amber-600' : 'text-gray-400',
  ].join(' ');

  const valueCls = [
    'text-base font-bold tabular-nums',
    cooldown && embedCooldownDisplay <= 5
      ? 'text-orange-500 animate-pulse'
      : cooldown && embedCooldownDisplay <= 10
      ? 'text-red-600 animate-pulse'
      : cooldown
      ? 'text-red-600'
      : 'text-gray-400',
  ].join(' ');

  return (
    <div className={cellCls}>
      <div className={labelCls}>Cooldown clears in</div>
      <div data-testid="countdown-value" className={valueCls}>
        {cooldown ? `${embedCooldownDisplay} s` : '—'}
      </div>
    </div>
  );
}

/** Mirrors the alert banner from AdminHealth.jsx (~2222) */
function CooldownBanner({ embedCooldownDisplay, burst, threshold, durationS }) {
  const wrapCls = [
    'flex items-center gap-2 mb-3 px-3 py-2 rounded-lg border transition-colors',
    embedCooldownDisplay <= 5
      ? 'bg-amber-100 border-amber-300'
      : 'bg-red-100 border-red-200',
  ].join(' ');

  const iconCls = `shrink-0 transition-colors ${
    embedCooldownDisplay <= 5 ? 'text-amber-600' : 'text-red-600'
  }`;

  const textCls = `text-xs font-semibold transition-colors ${
    embedCooldownDisplay <= 5 ? 'text-amber-700' : 'text-red-700'
  }`;

  return (
    <div data-testid="banner" className={wrapCls}>
      <span data-testid="banner-icon" className={iconCls}>!</span>
      <span data-testid="banner-text" className={textCls}>
        Embed cooldown active — Workers AI embed skipped for {embedCooldownDisplay}s
        ({burst} of {threshold} hits in last {durationS}s)
      </span>
    </div>
  );
}

function badgeHtml(props)  { return renderToStaticMarkup(<CooldownBadge  {...props} />); }
function bannerHtml(props) { return renderToStaticMarkup(<CooldownBanner {...props} />); }

/* ═══════════════════════════════════════════════════════════════════════════
   Static badge tests
   ═══════════════════════════════════════════════════════════════════════════ */
describe('AdminHealth — embed cooldown badge', () => {
  it('shows a dash and gray text when cooldown is inactive', () => {
    const html = badgeHtml({ cooldown: false, embedCooldownDisplay: 0 });
    expect(html).toContain('—');
    expect(html).toContain('text-gray-400');
    expect(html).not.toContain('animate-pulse');
    expect(html).not.toContain('text-red-600');
    expect(html).not.toContain('text-orange-500');
  });

  it('shows red text but NO pulse when cooldown has more than 10 s remaining', () => {
    const html = badgeHtml({ cooldown: true, embedCooldownDisplay: 45 });
    expect(html).toContain('45 s');
    expect(html).toContain('text-red-600');
    expect(html).not.toContain('animate-pulse');
    expect(html).not.toContain('bg-amber-50');
  });

  it('applies animate-pulse with red text when cooldown is in the 6–10 s window', () => {
    for (const s of [10, 9, 8, 7, 6]) {
      const html = badgeHtml({ cooldown: true, embedCooldownDisplay: s });
      expect(html, `at ${s}s`).toContain('animate-pulse');
      expect(html, `at ${s}s`).toContain('text-red-600');
      expect(html, `at ${s}s`).not.toContain('text-orange-500');
      expect(html, `at ${s}s`).not.toContain('bg-amber-50');
    }
  });

  it('switches to orange text + amber cell background at exactly 5 s (stage-2 boundary)', () => {
    const html = badgeHtml({ cooldown: true, embedCooldownDisplay: 5 });
    expect(html).toContain('animate-pulse');
    expect(html).toContain('text-orange-500');
    expect(html).toContain('bg-amber-50');
    expect(html).toContain('border-amber-300');
    expect(html).not.toContain('text-red-600');
  });

  it('keeps stage-2 styling for remaining values 4, 3, 2, 1, 0', () => {
    for (const s of [4, 3, 2, 1, 0]) {
      const html = badgeHtml({ cooldown: true, embedCooldownDisplay: s });
      expect(html, `at ${s}s`).toContain('animate-pulse');
      expect(html, `at ${s}s`).toContain('text-orange-500');
      expect(html, `at ${s}s`).toContain('bg-amber-50');
      expect(html, `at ${s}s`).not.toContain('text-red-600');
    }
  });

  it('label text color follows the same stage split', () => {
    const stage1Html = badgeHtml({ cooldown: true,  embedCooldownDisplay: 8 });
    const stage2Html = badgeHtml({ cooldown: true,  embedCooldownDisplay: 3 });
    const idleHtml   = badgeHtml({ cooldown: false, embedCooldownDisplay: 0 });
    expect(stage1Html).toContain('text-gray-400');
    expect(stage2Html).toContain('text-amber-600');
    expect(idleHtml).toContain('text-gray-400');
  });
});

/* ═══════════════════════════════════════════════════════════════════════════
   Static banner tests
   ═══════════════════════════════════════════════════════════════════════════ */
describe('AdminHealth — embed cooldown alert banner', () => {
  const defaultProps = { burst: 3, threshold: 3, durationS: 60 };

  it('uses red palette when more than 5 s remain', () => {
    for (const s of [60, 15, 11, 6]) {
      const html = bannerHtml({ embedCooldownDisplay: s, ...defaultProps });
      expect(html, `at ${s}s`).toContain('bg-red-100');
      expect(html, `at ${s}s`).toContain('border-red-200');
      expect(html, `at ${s}s`).toContain('text-red-600');
      expect(html, `at ${s}s`).toContain('text-red-700');
      expect(html, `at ${s}s`).not.toContain('bg-amber-100');
    }
  });

  it('switches to amber palette at exactly 5 s (stage-2 boundary)', () => {
    const html = bannerHtml({ embedCooldownDisplay: 5, ...defaultProps });
    expect(html).toContain('bg-amber-100');
    expect(html).toContain('border-amber-300');
    expect(html).toContain('text-amber-600');
    expect(html).toContain('text-amber-700');
    expect(html).not.toContain('bg-red-100');
    expect(html).not.toContain('text-red-700');
  });

  it('keeps amber palette for 4, 3, 2, 1, 0 s remaining', () => {
    for (const s of [4, 3, 2, 1, 0]) {
      const html = bannerHtml({ embedCooldownDisplay: s, ...defaultProps });
      expect(html, `at ${s}s`).toContain('bg-amber-100');
      expect(html, `at ${s}s`).not.toContain('bg-red-100');
    }
  });

  it('banner text always contains the countdown seconds', () => {
    const html = bannerHtml({ embedCooldownDisplay: 7, ...defaultProps });
    expect(html).toContain('skipped for 7s');
    expect(html).toContain('3 of 3');
    expect(html).toContain('last 60s');
  });
});

/* ═══════════════════════════════════════════════════════════════════════════
   ② Urgency-cue integration tests (Task #104)
   Uses real timers + waitFor; axes on the live component's className output.
   ═══════════════════════════════════════════════════════════════════════════ */

const basePoolStats = {
  embed_cooldown_active:      false,
  embed_cooldown_remaining_s: 0,
  embed_429_burst:            0,
  embed_429_threshold:        3,
  embed_cooldown_duration_s:  60,
};

function setPoolStatsMock(overrides = {}) {
  const data = { ...basePoolStats, ...overrides };
  axiosGet.mockImplementation((url) => {
    if (url.includes('/admin/workers-ai/status'))
      return Promise.resolve({ data: { ok: true, enabled_globally: true, secret_configured: true, edge_url: 'https://cf.example.com' } });
    if (url.includes('/admin/dashboard/metrics'))
      return Promise.resolve({ data: { workers_ai_throttle: null } });
    if (url.includes('/admin/llm/pool-stats'))
      return Promise.resolve({ data });
    return Promise.reject(new Error('mocked rejection'));
  });
}

/** Flush promise-based useEffects (two act passes). */
async function flushEffects() {
  await act(async () => { await Promise.resolve(); });
  await act(async () => { await Promise.resolve(); });
}

async function renderAndNavigate() {
  render(<AdminHealth adminToken="test-token" onNavigate={vi.fn()} />);
  await flushEffects();
  const tab = screen.getByRole('button', { name: /Workers AI Fallback/i });
  fireEvent.click(tab);
}

describe('AdminHealth — embed cooldown pulse animation & urgency cue', () => {
  beforeEach(() => { setPoolStatsMock(); });
  afterEach(() => { vi.clearAllMocks(); });

  /** Find the countdown value div by its position beneath the label. */
  async function getValueEl() {
    const label = await screen.findByText('Cooldown clears in', {}, { timeout: 3000 });
    return label.nextElementSibling;
  }

  it('A — no animate-pulse and gray text when cooldown is inactive', async () => {
    setPoolStatsMock({ embed_cooldown_active: false, embed_cooldown_remaining_s: 0 });
    await renderAndNavigate();

    const valueEl = await getValueEl();
    expect(valueEl.className).toContain('text-gray-400');
    expect(valueEl.className).not.toContain('animate-pulse');
    expect(valueEl.textContent).toBe('—');
  });

  it('B — red text, no animate-pulse when cooldown has > 10 s remaining', async () => {
    setPoolStatsMock({ embed_cooldown_active: true, embed_cooldown_remaining_s: 45 });
    await renderAndNavigate();

    const valueEl = await getValueEl();
    expect(valueEl.className).toContain('text-red-600');
    expect(valueEl.className).not.toContain('animate-pulse');
  });

  it('C — animate-pulse + red text in the 6–10 s stage-1 window', async () => {
    setPoolStatsMock({ embed_cooldown_active: true, embed_cooldown_remaining_s: 8 });
    await renderAndNavigate();

    const valueEl = await getValueEl();
    expect(valueEl.className).toContain('animate-pulse');
    expect(valueEl.className).toContain('text-red-600');
    expect(valueEl.className).not.toContain('text-orange-500');
  });

  it('D — orange text + animate-pulse in the ≤5 s stage-2 window', async () => {
    setPoolStatsMock({ embed_cooldown_active: true, embed_cooldown_remaining_s: 3 });
    await renderAndNavigate();

    const valueEl = await getValueEl();
    expect(valueEl.className).toContain('animate-pulse');
    expect(valueEl.className).toContain('text-orange-500');
    expect(valueEl.className).not.toContain('text-red-600');
  });

  it('D₂ — countdown cell wrapper has amber bg/border at ≤5 s', async () => {
    setPoolStatsMock({ embed_cooldown_active: true, embed_cooldown_remaining_s: 3 });
    await renderAndNavigate();

    const label = await screen.findByText('Cooldown clears in', {}, { timeout: 3000 });
    const cell = label.parentElement;
    expect(cell.className).toContain('bg-amber-50');
    expect(cell.className).toContain('border-amber-300');
  });

  it('D₃ — alert banner has amber bg at ≤5 s', async () => {
    setPoolStatsMock({ embed_cooldown_active: true, embed_cooldown_remaining_s: 4 });
    await renderAndNavigate();

    const bannerText = await screen.findByText(/Embed cooldown active/, {}, { timeout: 3000 });
    const banner = bannerText.closest('div.rounded-lg');
    expect(banner.className).toContain('bg-amber-100');
    expect(banner.className).not.toContain('bg-red-100');
  });

  it('C₂ — alert banner has red bg when > 5 s remain', async () => {
    setPoolStatsMock({ embed_cooldown_active: true, embed_cooldown_remaining_s: 8 });
    await renderAndNavigate();

    const bannerText = await screen.findByText(/Embed cooldown active/, {}, { timeout: 3000 });
    const banner = bannerText.closest('div.rounded-lg');
    expect(banner.className).toContain('bg-red-100');
    expect(banner.className).not.toContain('bg-amber-100');
  });
});

/* ═══════════════════════════════════════════════════════════════════════════
   ③ Ticker behavioral tests (Task #101)
   Uses partial fake timers (setInterval/clearInterval only) to drive the
   live countdown and assert interval lifecycle precisely.
   ═══════════════════════════════════════════════════════════════════════════ */

/** Build an axios.get implementation keyed on URL suffix for ticker tests. */
function makeAxiosGet({ cooldown = true, remainingS = 5 } = {}) {
  return vi.fn((url) => {
    if (url.includes('/admin/llm/pool-stats')) {
      return Promise.resolve({
        data: {
          embed_429_burst:            cooldown ? 4 : 0,
          embed_cooldown_active:      cooldown,
          embed_cooldown_remaining_s: remainingS,
          embed_429_threshold:        3,
          embed_cooldown_duration_s:  60,
        },
      });
    }
    if (url.includes('/admin/dashboard/metrics'))
      return Promise.resolve({ data: { workers_ai_throttle: null } });
    if (url.includes('/admin/workers-ai/status'))
      return Promise.resolve({
        data: { ok: true, enabled_globally: true, secret_configured: true, edge_url: '', capabilities: {} },
      });
    return Promise.resolve({ data: {} });
  });
}

/** Drain the microtask queue without touching fake timers. */
async function flushPromises() {
  for (let i = 0; i < 5; i++) await Promise.resolve();
}

/** Click the "Workers AI Fallback" tab where the embed cooldown display lives. */
function switchToWorkersAiTab() {
  fireEvent.click(screen.getByText('Workers AI Fallback'));
}

describe('AdminHealth — embed cooldown ticker', () => {
  beforeEach(() => {
    // Only fake setInterval/clearInterval so Promise microtask resolution is never blocked.
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
    axiosGet.mockImplementation(makeAxiosGet({ cooldown: true, remainingS: 5 }));
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it('(a) starts the display at the value reported by the backend', async () => {
    render(<AdminHealth adminToken="test.jwt.token" />);
    await act(async () => { await flushPromises(); });
    await act(async () => { switchToWorkersAiTab(); });

    expect(screen.getByText('5 s')).toBeInTheDocument();
  });

  it('(b) decrements by 1 every second and reaches 0 after 5 ticks', async () => {
    render(<AdminHealth adminToken="test.jwt.token" />);
    await act(async () => { await flushPromises(); });
    await act(async () => { switchToWorkersAiTab(); });

    expect(screen.getByText('5 s')).toBeInTheDocument();

    for (let remaining = 4; remaining >= 0; remaining--) {
      await act(async () => {
        vi.advanceTimersByTime(1000);
        await flushPromises();
      });
      expect(screen.getByText(`${remaining} s`)).toBeInTheDocument();
    }
  });

  it('(c) clears the specific interval handle when the next poll reports cooldown === false', async () => {
    const setIntervalSpy   = vi.spyOn(globalThis, 'setInterval');
    const clearIntervalSpy = vi.spyOn(globalThis, 'clearInterval');

    render(<AdminHealth adminToken="test.jwt.token" />);
    await act(async () => { await flushPromises(); });
    await act(async () => { switchToWorkersAiTab(); });

    expect(screen.getByText('5 s')).toBeInTheDocument();

    // Identify the 1 s cooldown ticker handle (only setInterval with delay === 1000).
    const cooldownCall = setIntervalSpy.mock.results.find(
      (_, i) => setIntervalSpy.mock.calls[i]?.[1] === 1000,
    );
    expect(cooldownCall).toBeDefined();
    const cooldownHandle = cooldownCall.value;

    axiosGet.mockImplementation(makeAxiosGet({ cooldown: false, remainingS: 0 }));

    await act(async () => {
      vi.advanceTimersByTime(30_000);
      await flushPromises();
    });

    // UI: banner gone, cell reverts to "—".
    expect(screen.queryByText(/Embed cooldown active/)).not.toBeInTheDocument();
    const label = screen.getByText('Cooldown clears in');
    expect(label.parentElement.textContent).toContain('—');

    // The exact cooldown handle must have been passed to clearInterval.
    const clearedHandles = clearIntervalSpy.mock.calls.map((args) => args[0]);
    expect(clearedHandles).toContain(cooldownHandle);

    setIntervalSpy.mockRestore();
    clearIntervalSpy.mockRestore();
  });

  it('(d) clears the specific cooldown interval on unmount — no leaked ticker', async () => {
    const setIntervalSpy   = vi.spyOn(globalThis, 'setInterval');
    const clearIntervalSpy = vi.spyOn(globalThis, 'clearInterval');

    const { unmount } = render(<AdminHealth adminToken="test.jwt.token" />);
    await act(async () => { await flushPromises(); });

    const cooldownCall = setIntervalSpy.mock.results.find(
      (_, i) => setIntervalSpy.mock.calls[i]?.[1] === 1000,
    );
    expect(cooldownCall).toBeDefined();
    const cooldownHandle = cooldownCall.value;

    await act(async () => { unmount(); });

    const clearedHandles = clearIntervalSpy.mock.calls.map((args) => args[0]);
    expect(clearedHandles).toContain(cooldownHandle);
    expect(vi.getTimerCount()).toBe(0);

    setIntervalSpy.mockRestore();
    clearIntervalSpy.mockRestore();
  });
});
