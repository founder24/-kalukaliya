import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, it, expect } from 'vitest';
import CronHealthPill, { ageLabel } from './CronHealthPill';

// Task #837 — Task #835 extracted the shared <CronHealthPill> used by
// the Trustpilot refresh and Cloudflare WAF drift admin tiles. The
// status -> colour/icon/pill-label mapping (healthy=emerald,
// silent=red, degraded=amber, never_observed/not_configured=gray,
// unknown=gray) lives in one place now. Lock the mapping down so a
// well-meant refactor of the colour ladder cannot silently flip the
// alarm states.

const STATUS_EXPECTATIONS = {
  healthy: {
    container: ['bg-emerald-50', 'border-emerald-200'],
    headerColor: 'text-emerald-600',
    pill: ['bg-emerald-100', 'text-emerald-700', 'border-emerald-200'],
    pillLabel: 'CRON HEALTHY',
    iconColor: 'text-emerald-500',
  },
  silent: {
    container: ['bg-red-50', 'border-red-200'],
    headerColor: 'text-red-600',
    pill: ['bg-red-100', 'text-red-700', 'border-red-200'],
    pillLabel: 'CRON SILENT',
    iconColor: 'text-red-500',
  },
  degraded: {
    container: ['bg-amber-50', 'border-amber-200'],
    headerColor: 'text-amber-600',
    pill: ['bg-amber-100', 'text-amber-700', 'border-amber-200'],
    pillLabel: 'CRON DEGRADED',
    iconColor: 'text-amber-500',
  },
  never_observed: {
    container: ['bg-gray-50', 'border-gray-200'],
    headerColor: 'text-gray-500',
    pill: ['bg-gray-100', 'text-gray-600', 'border-gray-200'],
    pillLabel: 'NEVER OBSERVED',
    iconColor: 'text-gray-400',
  },
  not_configured: {
    container: ['bg-gray-50', 'border-gray-200'],
    headerColor: 'text-gray-500',
    pill: ['bg-gray-100', 'text-gray-600', 'border-gray-200'],
    pillLabel: 'NOT CONFIGURED',
    iconColor: 'text-gray-400',
  },
};

const HEADER_TEXT_BY_STATUS = {
  healthy: 'Cron healthy',
  silent: 'Cron silent',
  degraded: 'Cron degraded',
  never_observed: 'Cron never observed',
  not_configured: 'Cron not configured',
  unknown: 'Cron — status unknown',
};

const renderPill = (overrides = {}) =>
  renderToStaticMarkup(
    <CronHealthPill
      data={overrides.data ?? null}
      loading={overrides.loading ?? false}
      onRefresh={overrides.onRefresh ?? (() => {})}
      testId={overrides.testId ?? 'foo'}
      headerTextByStatus={overrides.headerTextByStatus ?? HEADER_TEXT_BY_STATUS}
      pillLabelByStatus={overrides.pillLabelByStatus}
      defaultWorkflowUrl={overrides.defaultWorkflowUrl ?? 'https://example.com/workflow'}
      renderSubText={overrides.renderSubText}
      renderExtraActions={overrides.renderExtraActions}
      alertState={overrides.alertState}
      alertHistory={overrides.alertHistory}
      onLoadAlertHistory={overrides.onLoadAlertHistory}
      slackMissingAlertState={overrides.slackMissingAlertState}
      slackMissingAlertHistory={overrides.slackMissingAlertHistory}
      onSnoozeSlackMissing={overrides.onSnoozeSlackMissing}
    />
  );

const extractAttrValue = (html, testId, attr) => {
  const re = new RegExp(
    `<[^>]*data-testid="${testId}"[^>]*\\s${attr}="([^"]*)"|<[^>]*\\s${attr}="([^"]*)"[^>]*data-testid="${testId}"`
  );
  const m = html.match(re);
  return m ? (m[1] ?? m[2]) : null;
};

describe('CronHealthPill — status to colour/icon/label mapping', () => {
  for (const [status, expected] of Object.entries(STATUS_EXPECTATIONS)) {
    it(`maps status="${status}" to the locked tile/header/pill/icon classes and label`, () => {
      const html = renderPill({ data: { status, workflowUrl: 'https://example.com/wf' } });

      const tileCls = extractAttrValue(html, 'foo-tile', 'class') || '';
      for (const cls of expected.container) {
        expect(tileCls).toContain(cls);
      }

      const headerCls = extractAttrValue(html, 'foo-status', 'class') || '';
      expect(headerCls).toContain(expected.headerColor);

      const pillCls = extractAttrValue(html, 'foo-pill', 'class') || '';
      for (const cls of expected.pill) {
        expect(pillCls).toContain(cls);
      }

      // Pill text is the locked label for this status.
      const pillTextRe = new RegExp(
        `data-testid="foo-pill"[^>]*>\\s*${expected.pillLabel}\\s*<`
      );
      expect(html).toMatch(pillTextRe);

      // Icon colour is locked alongside the colour family.
      expect(html).toContain(expected.iconColor);

      // Header text is sourced from headerTextByStatus[status].
      expect(html).toContain(HEADER_TEXT_BY_STATUS[status]);
    });
  }

  it('treats null/missing data as the gray "unknown" state with the unknown header text', () => {
    const html = renderPill({ data: null });

    const tileCls = extractAttrValue(html, 'foo-tile', 'class') || '';
    expect(tileCls).toContain('bg-gray-50');
    expect(tileCls).toContain('border-gray-200');

    const headerCls = extractAttrValue(html, 'foo-status', 'class') || '';
    expect(headerCls).toContain('text-gray-500');

    const pillCls = extractAttrValue(html, 'foo-pill', 'class') || '';
    expect(pillCls).toContain('bg-gray-100');
    expect(pillCls).toContain('text-gray-600');
    expect(pillCls).toContain('border-gray-200');

    // No mapping entry exists for "unknown" -> default pill label is "UNKNOWN".
    expect(html).toMatch(/data-testid="foo-pill"[^>]*>\s*UNKNOWN\s*</);

    // Header falls back to headerTextByStatus.unknown.
    expect(html).toContain('Cron — status unknown');

    // Gray clock icon is used for the unknown state.
    expect(html).toContain('text-gray-400');
  });

  // NOTE: An explicit `data.status === 'unknown'` is intentionally NOT
  // tested as "gray" because the current component only routes to the
  // gray branch via `!data` (or status in {never_observed,
  // not_configured}). A literal "unknown" status string with a present
  // data object falls through to the emerald default — that quirk is
  // out of scope for this colour-pinning suite and would be its own fix.

  it('treats data with _error as the unknown/gray state', () => {
    const html = renderPill({ data: { _error: true, status: 'healthy' } });

    const tileCls = extractAttrValue(html, 'foo-tile', 'class') || '';
    expect(tileCls).toContain('bg-gray-50');
    expect(tileCls).toContain('border-gray-200');

    const pillCls = extractAttrValue(html, 'foo-pill', 'class') || '';
    expect(pillCls).toContain('bg-gray-100');
  });

  it('lets pillLabelByStatus override the default pill label for a status', () => {
    const html = renderPill({
      data: { status: 'healthy' },
      pillLabelByStatus: { healthy: 'TRUSTPILOT OK' },
    });
    expect(html).toMatch(/data-testid="foo-pill"[^>]*>\s*TRUSTPILOT OK\s*</);
  });
});

describe('CronHealthPill — testId templating', () => {
  it('renders all interactive testIds using the `${testId}-…` template', () => {
    const html = renderPill({
      testId: 'foo',
      data: { status: 'healthy', workflowUrl: 'https://example.com/wf' },
    });

    expect(html).toContain('data-testid="foo-tile"');
    expect(html).toContain('data-testid="foo-status"');
    expect(html).toContain('data-testid="foo-pill"');
    expect(html).toContain('data-testid="foo-run-link"');
    // Task #882 — primary refresh testId follows the AdminHealth
    // cron-pill convention (replit.md § "AdminHealth cron-pill testId
    // convention"). The legacy `button-refresh-<prefix>` form is
    // preserved on `data-testid-legacy` for external selectors.
    expect(html).toContain('data-testid="foo-refresh"');
    expect(html).toContain('data-testid-legacy="button-refresh-foo"');
  });

  it('changes every templated testId when the testId prop changes', () => {
    const html = renderPill({
      testId: 'cf-waf',
      data: { status: 'silent', workflowUrl: 'https://example.com/wf' },
    });

    expect(html).toContain('data-testid="cf-waf-tile"');
    expect(html).toContain('data-testid="cf-waf-status"');
    expect(html).toContain('data-testid="cf-waf-pill"');
    expect(html).toContain('data-testid="cf-waf-run-link"');
    expect(html).toContain('data-testid="cf-waf-refresh"');
    expect(html).toContain('data-testid-legacy="button-refresh-cf-waf"');

    // No leftover ids from the other test's testId value.
    expect(html).not.toContain('data-testid="foo-tile"');
    expect(html).not.toContain('data-testid="foo-refresh"');
    expect(html).not.toContain('data-testid-legacy="button-refresh-foo"');
  });
});

describe('CronHealthPill — workflow URL fallback', () => {
  // Task #839 — every wrapper (CfWafDriftCronPill, TrustpilotCronPill,
  // …) passes a `defaultWorkflowUrl` so the GitHub Actions deep-link
  // still points somewhere sensible BEFORE the heartbeat row carries
  // a `workflowUrl`. The component prefers the per-heartbeat URL when
  // present, and falls back to `defaultWorkflowUrl` otherwise. Both
  // hyperlinks (the pill itself and the "Runs" link beside it) must
  // resolve to the same URL — they're the same link rendered twice.
  // Pin both behaviours so a future refactor of the fallback ladder
  // doesn't silently break either link.
  const DEFAULT_URL = 'https://github.com/example/actions/workflows/x.yml';
  const DATA_URL = 'https://github.com/example/actions/runs/9999';

  it('uses data.workflowUrl when the heartbeat carries one (overrides defaultWorkflowUrl)', () => {
    const html = renderPill({
      data: { status: 'healthy', workflowUrl: DATA_URL },
      defaultWorkflowUrl: DEFAULT_URL,
    });
    expect(extractAttrValue(html, 'foo-pill', 'href')).toBe(DATA_URL);
    expect(extractAttrValue(html, 'foo-run-link', 'href')).toBe(DATA_URL);
  });

  it('falls back to defaultWorkflowUrl when data has no workflowUrl', () => {
    const html = renderPill({
      data: { status: 'healthy' }, // no workflowUrl
      defaultWorkflowUrl: DEFAULT_URL,
    });
    expect(extractAttrValue(html, 'foo-pill', 'href')).toBe(DEFAULT_URL);
    expect(extractAttrValue(html, 'foo-run-link', 'href')).toBe(DEFAULT_URL);
  });

  it('falls back to defaultWorkflowUrl when data is null entirely', () => {
    const html = renderPill({
      data: null,
      defaultWorkflowUrl: DEFAULT_URL,
    });
    expect(extractAttrValue(html, 'foo-pill', 'href')).toBe(DEFAULT_URL);
    expect(extractAttrValue(html, 'foo-run-link', 'href')).toBe(DEFAULT_URL);
  });

  it('falls back to defaultWorkflowUrl when data is the _error sentinel (treated as no data)', () => {
    // The `_error` short-circuit replaces `data` with null inside the
    // component, so `data._error.workflowUrl` (if it ever existed)
    // must NOT be honoured — the URL must come from defaultWorkflowUrl.
    const html = renderPill({
      data: { _error: true, workflowUrl: DATA_URL, status: 'healthy' },
      defaultWorkflowUrl: DEFAULT_URL,
    });
    expect(extractAttrValue(html, 'foo-pill', 'href')).toBe(DEFAULT_URL);
    expect(extractAttrValue(html, 'foo-run-link', 'href')).toBe(DEFAULT_URL);
  });

  it('honours data.workflowUrl across every non-error status, not just healthy', () => {
    // Sanity: the data.workflowUrl preference is independent of the
    // status colour ladder. A silent or degraded heartbeat that
    // carries its own URL must still surface it.
    for (const status of ['silent', 'degraded', 'never_observed', 'not_configured']) {
      const html = renderPill({
        data: { status, workflowUrl: DATA_URL },
        defaultWorkflowUrl: DEFAULT_URL,
      });
      expect(extractAttrValue(html, 'foo-pill', 'href')).toBe(DATA_URL);
      expect(extractAttrValue(html, 'foo-run-link', 'href')).toBe(DATA_URL);
    }
  });
});

// Task #902 — alerter-state caption surfaced under subText.
// `formatAlertStateCaption` is itself unit-tested in
// `cronCaptionHelpers.test.js`; here we just verify the pill wires
// it up correctly: renders nothing without alertState, renders the
// caption testId when provided, and uses amber-vs-gray text colour
// based on `inDebounce`.
describe('alertState rendering', () => {
  it('renders no alert-state caption when no alertState prop is passed', () => {
    const html = renderToStaticMarkup(
      <CronHealthPill
        data={{ status: 'silent' }}
        testId="foo"
        defaultWorkflowUrl="https://example.test/workflow"
      />,
    );
    expect(html).not.toContain('foo-alert-state');
  });

  it('renders no alert-state caption when alertState.present is false', () => {
    // Brand-new deployment: alerter has never fired. The pill must
    // not render an orphan "last paged: never" line — the colour
    // already conveys "no page has been sent".
    const html = renderToStaticMarkup(
      <CronHealthPill
        data={{ status: 'healthy' }}
        testId="foo"
        defaultWorkflowUrl="https://example.test/workflow"
        alertState={{ present: false, lastAlertAt: null,
                      lastAlertAgeSeconds: null, inDebounce: false,
                      debounceRemainingSeconds: null }}
      />,
    );
    expect(html).not.toContain('foo-alert-state');
  });

  it('renders the caption in amber when inDebounce=true', () => {
    // Amber so admins glance at the pill and know "yes we paged,
    // and the next page is auto-suppressed for another ~22h".
    const html = renderToStaticMarkup(
      <CronHealthPill
        data={{ status: 'silent' }}
        testId="foo"
        defaultWorkflowUrl="https://example.test/workflow"
        alertState={{
          present: true,
          lastAlertAt: '2026-04-25T00:00:00+00:00',
          lastAlertAgeSeconds: 2 * 3600,
          inDebounce: true,
          debounceRemainingSeconds: 22 * 3600,
        }}
      />,
    );
    expect(html).toContain('foo-alert-state');
    expect(html).toContain('last paged 2h ago · in debounce ~22h remaining');
    // Amber colour class on the caption <p>.
    expect(html).toMatch(/text-amber-600[^"]*"[^>]*data-testid="foo-alert-state"/);
  });

  it('renders the caption in gray when inDebounce=false', () => {
    // Past the realert window: still informative ("we did page on
    // this") but neutral colour because the next poll can re-page.
    const html = renderToStaticMarkup(
      <CronHealthPill
        data={{ status: 'silent' }}
        testId="foo"
        defaultWorkflowUrl="https://example.test/workflow"
        alertState={{
          present: true,
          lastAlertAt: '2026-04-23T00:00:00+00:00',
          lastAlertAgeSeconds: 30 * 3600,
          inDebounce: false,
          debounceRemainingSeconds: null,
        }}
      />,
    );
    expect(html).toContain('foo-alert-state');
    expect(html).toContain('last paged 1d ago');
    expect(html).not.toContain('in debounce');
    expect(html).toMatch(/text-gray-500[^"]*"[^>]*data-testid="foo-alert-state"/);
  });
});

// Task #918 — paged-on-call audit-log disclosure. The pill renders
// a "Show paged history" toggle when the wrapper passes
// `onLoadAlertHistory`; clicking it lazy-fetches via the parent's
// loader and expands an inline panel listing alerter events. Locked
// down here so the testIds + visibility rules can't drift from the
// AdminHealth wiring (loadEdgeProxyDeployCronAlertHistory et al.).
describe('alertHistory panel', () => {
  it('does NOT render the toggle when onLoadAlertHistory is missing', () => {
    // Backwards-compat: callers (snapshot-style tests, the
    // mockup-sandbox) that never opted into the history feature
    // should see no extra DOM.
    const html = renderToStaticMarkup(
      <CronHealthPill
        data={{ status: 'healthy' }}
        testId="foo"
        defaultWorkflowUrl="https://example.test/workflow"
      />,
    );
    expect(html).not.toContain('foo-history-toggle');
    expect(html).not.toContain('foo-history-panel');
  });

  it('renders the collapsed toggle with event count when onLoadAlertHistory is provided', () => {
    // History pre-loaded by the parent (e.g. a polling cycle that
    // ran before the admin clicked) — count appears in the toggle
    // label so admins know there's something worth opening before
    // they expand the panel.
    const html = renderToStaticMarkup(
      <CronHealthPill
        data={{ status: 'silent' }}
        testId="foo"
        defaultWorkflowUrl="https://example.test/workflow"
        alertHistory={{ events: [
          { id: 'e1', kind: 'broken', subKind: 'failed',
            pagedAt: new Date(Date.now() - 3600 * 1000).toISOString(),
            lastConclusion: 'failure', lastRunId: 42,
            lastRunUrl: 'https://gh.test/runs/42' },
          { id: 'e2', kind: 'recovered', subKind: null,
            pagedAt: new Date(Date.now() - 600 * 1000).toISOString(),
            lastConclusion: 'success', lastRunId: 43 },
        ] }}
        onLoadAlertHistory={() => {}}
      />,
    );
    expect(html).toContain('foo-history-toggle');
    expect(html).toContain('Show paged history (2)');
    // Panel stays collapsed until the user clicks; static-render
    // sees only the toggle, not the panel itself.
    expect(html).not.toContain('foo-history-panel');
  });

  it('expands the panel and fires the loader on click', async () => {
    // Use @testing-library/react for the click + state-update path
    // since renderToStaticMarkup can't run effects. The static
    // colour-mapping tests above stay on the static renderer
    // because they don't need state — keeping that fast path
    // unchanged.
    const { render, fireEvent, cleanup } = await import('@testing-library/react');
    const calls = { n: 0 };
    const onLoad = () => { calls.n += 1; };
    const { getByTestId, queryByTestId, queryAllByTestId } = render(
      <CronHealthPill
        data={{ status: 'silent' }}
        testId="foo"
        defaultWorkflowUrl="https://example.test/workflow"
        alertHistory={{ events: [
          { id: 'e1', kind: 'broken', subKind: 'failed',
            pagedAt: new Date(Date.now() - 7200 * 1000).toISOString(),
            lastConclusion: 'failure', lastRunId: 42,
            lastRunUrl: 'https://gh.test/runs/42' },
        ] }}
        onLoadAlertHistory={onLoad}
      />,
    );
    const toggle = getByTestId('foo-history-toggle');
    expect(queryByTestId('foo-history-panel')).toBeNull();
    fireEvent.click(toggle);
    // First open: panel renders + loader fires exactly once.
    expect(queryByTestId('foo-history-panel')).not.toBeNull();
    expect(queryAllByTestId('foo-history-event').length).toBe(1);
    expect(calls.n).toBe(1);
    // Closing the panel does NOT re-fire the loader (no fetch on
    // the way down) but the panel is gone from the DOM.
    fireEvent.click(toggle);
    expect(queryByTestId('foo-history-panel')).toBeNull();
    expect(calls.n).toBe(1);
    // Re-opening DOES re-fire the loader — opening is the admin's
    // explicit "show me the latest" gesture and history is not
    // covered by AdminHealth's 60s polling, so without this
    // refresh-on-open the panel could go stale indefinitely.
    fireEvent.click(toggle);
    expect(queryByTestId('foo-history-panel')).not.toBeNull();
    expect(calls.n).toBe(2);
    cleanup();
  });

  it('renders the empty-state row when the alerter has never fired', async () => {
    const { render, fireEvent, cleanup } = await import('@testing-library/react');
    const { getByTestId, queryByTestId } = render(
      <CronHealthPill
        data={{ status: 'healthy' }}
        testId="foo"
        defaultWorkflowUrl="https://example.test/workflow"
        alertHistory={{ events: [] }}
        onLoadAlertHistory={() => {}}
      />,
    );
    fireEvent.click(getByTestId('foo-history-toggle'));
    expect(queryByTestId('foo-history-empty')).not.toBeNull();
    cleanup();
  });
});

// Task #964 — Slack-config badge surfaces whether the alerter has its
// webhook env var set on the backend. The badge sits next to the
// status pill in the header and:
//   * renders nothing when `data.slackConfigured` is missing (older
//     backend / pills that don't fan out to Slack at all);
//   * renders "Slack ✓" with emerald colors when configured;
//   * renders "Slack ✗" with neutral grey colors when not configured.
// In both rendered cases, `data.slackWebhookEnv` (when present) shows
// up in the title attribute so an admin can copy/paste the missing
// env var name. The webhook URL itself must NEVER appear — the
// backend only publishes the boolean.
describe('CronHealthPill — Slack-config badge (Task #964)', () => {
  it('renders nothing when slackConfigured is absent', () => {
    const html = renderPill({
      data: { status: 'healthy' },
      testId: 'foo',
    });
    expect(html).not.toMatch(/Slack/);
    expect(html).not.toMatch(/foo-slack-config/);
  });

  it('renders "Slack ✓" in emerald when slackConfigured is true', () => {
    const html = renderPill({
      data: {
        status: 'healthy',
        slackConfigured: true,
        slackWebhookEnv: 'CF_WAF_DRIFT_SLACK_WEBHOOK',
      },
      testId: 'foo',
    });
    expect(html).toMatch(/data-testid="foo-slack-config"/);
    expect(html).toMatch(/data-slack-configured="true"/);
    expect(html).toMatch(/Slack \u2713/);
    expect(html).toMatch(/bg-emerald-50/);
    expect(html).toMatch(/CF_WAF_DRIFT_SLACK_WEBHOOK/);
  });

  it('renders "Slack ✗" in neutral grey when slackConfigured is false', () => {
    const html = renderPill({
      data: {
        status: 'healthy',
        slackConfigured: false,
        slackWebhookEnv: 'UNIFIED_LOGS_CF_PULL_SLACK_WEBHOOK',
      },
      testId: 'foo',
    });
    expect(html).toMatch(/data-testid="foo-slack-config"/);
    expect(html).toMatch(/data-slack-configured="false"/);
    expect(html).toMatch(/Slack \u2717/);
    expect(html).toMatch(/bg-gray-100/);
    // The hint must mention the missing env var so admins know what
    // to set without grepping the codebase.
    expect(html).toMatch(/UNIFIED_LOGS_CF_PULL_SLACK_WEBHOOK/);
  });

  it('never leaks the webhook URL into the rendered markup', () => {
    // Defense-in-depth check: even if a future backend regression
    // accidentally returned the URL on the slackConfigured field,
    // the badge component should only ever read `slackConfigured`
    // and `slackWebhookEnv` — so a sentinel URL passed in those
    // fields-of-the-wrong-name must never appear.
    const html = renderPill({
      data: {
        status: 'healthy',
        slackConfigured: true,
        slackWebhookEnv: 'CF_WAF_DRIFT_SLACK_WEBHOOK',
        slackWebhookUrl: 'https://hooks.slack.example.com/SECRET-LEAK-XYZ',
      },
      testId: 'foo',
    });
    expect(html).not.toMatch(/SECRET-LEAK-XYZ/);
    expect(html).not.toMatch(/hooks\.slack\.example\.com/);
  });
});

describe('CronHealthPill — Snooze button wiring (Task #980)', () => {
  // The snooze button lives on `SlackConfigBadge`, but the only
  // way wrappers (CfWafDriftCronPill, EdgeProxyDeployCronPill,
  // UnifiedLogsCfPullCronPill) reach the badge is THROUGH the
  // `<CronHealthPill>` shell. So if `CronHealthPill` forgets to
  // forward `onSnoozeSlackMissing` down to the badge, the wrappers
  // ship a dead button. These tests pin that wiring contract end
  // to end (wrapper -> shell -> badge) so a refactor that drops
  // the prop name fails loudly here instead of silently in prod.

  const REDDISH_DATA = {
    status: 'silent',
    slackConfigured: false,
    slackWebhookEnv: 'CF_WAF_DRIFT_SLACK_WEBHOOK',
  };

  // The badge gates the "paged Nh ago" caption on
  // ``lastAlertAgeSeconds`` (server-computed seconds-since-last-page),
  // not on a raw timestamp. Pin the same shape these tests use so a
  // schema rename here fails loud instead of silently turning the
  // gate off.
  const PAGED_ALERT_STATE = {
    envName: 'CF_WAF_DRIFT_SLACK_WEBHOOK',
    present: false,
    lastAlertAgeSeconds: 3600,
    snoozeActive: false,
  };

  it('renders the "Snooze 7d" button when shell is red+paged+envName known and onSnoozeSlackMissing is provided', () => {
    const html = renderPill({
      data: REDDISH_DATA,
      testId: 'foo',
      slackMissingAlertState: PAGED_ALERT_STATE,
      onSnoozeSlackMissing: () => Promise.resolve({}),
    });
    expect(html).toMatch(/Snooze 7d/);
    expect(html).toMatch(/data-testid="foo-slack-config-snooze"/);
  });

  it('does NOT render the snooze button when onSnoozeSlackMissing is missing (button is dead without callback)', () => {
    const html = renderPill({
      data: REDDISH_DATA,
      testId: 'foo',
      slackMissingAlertState: PAGED_ALERT_STATE,
      // no onSnoozeSlackMissing — pins the contract that wrappers
      // which forget to forward the prop ship a no-op badge.
    });
    expect(html).not.toMatch(/Snooze 7d/);
    expect(html).not.toMatch(/foo-slack-config-snooze/);
  });

  it('does NOT render the snooze button when no page has been recorded yet (red but never paged)', () => {
    const html = renderPill({
      data: REDDISH_DATA,
      testId: 'foo',
      slackMissingAlertState: {
        envName: 'CF_WAF_DRIFT_SLACK_WEBHOOK',
        present: false,
        lastAlertAgeSeconds: null,
        snoozeActive: false,
      },
      onSnoozeSlackMissing: () => Promise.resolve({}),
    });
    expect(html).not.toMatch(/Snooze 7d/);
  });

  it('does NOT render the snooze button when a snooze is already active (no double-snooze)', () => {
    const html = renderPill({
      data: REDDISH_DATA,
      testId: 'foo',
      slackMissingAlertState: {
        ...PAGED_ALERT_STATE,
        snoozeActive: true,
        snoozeRemainingSeconds: 6 * 3600,
      },
      onSnoozeSlackMissing: () => Promise.resolve({}),
    });
    expect(html).not.toMatch(/Snooze 7d/);
  });

  it('does NOT render the snooze button when the badge is green (Slack configured)', () => {
    const html = renderPill({
      data: { ...REDDISH_DATA, slackConfigured: true },
      testId: 'foo',
      slackMissingAlertState: PAGED_ALERT_STATE,
      onSnoozeSlackMissing: () => Promise.resolve({}),
    });
    expect(html).not.toMatch(/Snooze 7d/);
  });

  it('replaces the "paged Nh ago" caption with a snoozed caption when snoozeActive is true', () => {
    const html = renderPill({
      data: REDDISH_DATA,
      testId: 'foo',
      slackMissingAlertState: {
        ...PAGED_ALERT_STATE,
        snoozeActive: true,
        snoozeRemainingSeconds: 12 * 3600,
      },
      onSnoozeSlackMissing: () => Promise.resolve({}),
    });
    // Snoozed caption present — exact wording is owned by the
    // badge but we pin the word + the dedicated test-id so a
    // refactor that accidentally drops the caption fails loudly.
    expect(html).toMatch(/snoozed/);
    expect(html).toMatch(/data-testid="foo-slack-config-snoozed"/);
    // And the page-age caption is suppressed.
    expect(html).not.toMatch(/foo-slack-config-paged/);
  });
});

describe('CronHealthPill — Slack-missing recent-pages disclosure (Task #979)', () => {
  // The disclosure lives on `SlackConfigBadge` and surfaces the
  // ~10 most-recent missing-Slack-webhook pages for the env
  // (sourced from the alert-history endpoint, polled in AdminHealth
  // on the same 60s cadence as alert-state). These tests pin the
  // gating contract so an empty-history env (bootstrap grace
  // window or audit-log retention trim) stays visually quiet, and
  // a green badge never carries the disclosure.

  const REDDISH_DATA = {
    status: 'silent',
    slackConfigured: false,
    slackWebhookEnv: 'CF_WAF_DRIFT_SLACK_WEBHOOK',
  };

  const SAMPLE_HISTORY = {
    lockId: 'slack_webhook_missing/CF_WAF_DRIFT_SLACK_WEBHOOK',
    limit: 10,
    events: [
      {
        id: 'evt-1',
        pagedAt: '2026-04-26T11:00:00.000Z',
        kind: 'broken',
        subKind: 'missing',
        lastConclusion: 'failure',
        lastRunUrl: 'https://github.example/run/1',
        lastRunId: 1,
      },
      {
        id: 'evt-2',
        pagedAt: '2026-04-26T09:00:00.000Z',
        kind: 'recovered',
      },
    ],
  };

  it('renders the "Recent pages (N)" toggle when red AND history has events', () => {
    const html = renderPill({
      data: REDDISH_DATA,
      testId: 'foo',
      slackMissingAlertHistory: SAMPLE_HISTORY,
    });
    expect(html).toMatch(/Recent pages \(2\)/);
    expect(html).toMatch(/data-testid="foo-slack-config-history-toggle"/);
    // Disclosure starts collapsed — panel must NOT be in the
    // server-rendered markup. Pinning this so the SSR snapshot
    // stays compact on first paint.
    expect(html).not.toMatch(/foo-slack-config-history-panel/);
  });

  it('does NOT render the toggle when history is empty (only "· paged Nh ago" stays)', () => {
    const html = renderPill({
      data: REDDISH_DATA,
      testId: 'foo',
      slackMissingAlertState: {
        envName: 'CF_WAF_DRIFT_SLACK_WEBHOOK',
        present: false,
        lastAlertAgeSeconds: 7200,
        snoozeActive: false,
      },
      slackMissingAlertHistory: { lockId: 'x', limit: 10, events: [] },
    });
    expect(html).not.toMatch(/Recent pages/);
    expect(html).not.toMatch(/foo-slack-config-history-toggle/);
    // The "· paged Nh ago" decoration survives on its own.
    expect(html).toMatch(/data-testid="foo-slack-config-paged"/);
  });

  it('does NOT render the toggle when history snapshot is missing/null', () => {
    const html = renderPill({
      data: REDDISH_DATA,
      testId: 'foo',
      // no slackMissingAlertHistory at all — pre-poll initial render.
    });
    expect(html).not.toMatch(/Recent pages/);
    expect(html).not.toMatch(/foo-slack-config-history-toggle/);
  });

  it('does NOT render the toggle when the badge is green (Slack configured)', () => {
    const html = renderPill({
      data: { ...REDDISH_DATA, slackConfigured: true },
      testId: 'foo',
      // Even with events, a green badge has nothing to disclose —
      // the env is wired up; any historical pages are stale noise.
      slackMissingAlertHistory: SAMPLE_HISTORY,
    });
    expect(html).not.toMatch(/Recent pages/);
    expect(html).not.toMatch(/foo-slack-config-history-toggle/);
  });

  it('caps the disclosure title at the event count it actually got (no fixed limit baked into the UI)', () => {
    // Backend caps at limit=10 (AdminHealth requests ?limit=10),
    // but the UI must render whatever count it received — never
    // hardcode a number that would fall out of sync if the cap
    // changes. Verify with a 5-event payload.
    const fiveEvents = {
      lockId: 'x',
      limit: 10,
      events: Array.from({ length: 5 }, (_, i) => ({
        id: `e-${i}`,
        pagedAt: '2026-04-26T10:00:00.000Z',
        kind: 'broken',
      })),
    };
    const html = renderPill({
      data: REDDISH_DATA,
      testId: 'foo',
      slackMissingAlertHistory: fiveEvents,
    });
    expect(html).toMatch(/Recent pages \(5\)/);
  });
});

describe('ageLabel helper', () => {
  const cases = [
    { input: null, expected: null, why: 'null input -> null' },
    { input: undefined, expected: null, why: 'undefined input -> null' },
    { input: 0, expected: '0s', why: '0 seconds -> "0s"' },
    { input: 1, expected: '1s', why: 'sub-minute -> seconds' },
    { input: 59, expected: '59s', why: 'just under 1m -> seconds' },
    { input: 60, expected: '1m', why: 'exactly 60s -> minutes' },
    { input: 119, expected: '1m', why: 'under 2m -> 1m (floor)' },
    { input: 3599, expected: '59m', why: 'just under 1h -> minutes' },
    { input: 3600, expected: '1h', why: 'exactly 1h -> hours' },
    { input: 86399, expected: '23h', why: 'just under 1d -> hours' },
    { input: 86400, expected: '1d', why: 'exactly 1d -> days' },
    { input: 172800, expected: '2d', why: '2d -> days' },
    { input: -5, expected: '0s', why: 'negative input clamped to 0s' },
  ];

  for (const { input, expected, why } of cases) {
    it(`${why} (ageLabel(${String(input)}) === ${JSON.stringify(expected)})`, () => {
      expect(ageLabel(input)).toBe(expected);
    });
  }
});
