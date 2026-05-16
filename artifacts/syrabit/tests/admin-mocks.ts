import type { Page, Route } from '@playwright/test';

/**
 * Default JSON payload for any admin endpoint that the dashboard hits but
 * for which we don't want a tailored fixture. The dashboard uses defensive
 * `safeArr` / `safeObj` normalizers so an empty object is enough to render
 * the widget without crashing the global ErrorBoundary.
 */
const EMPTY = {};

/**
 * Per-endpoint fixtures. Keys are matched as substrings against the
 * request URL. The first match wins. Each handler returns the JSON body to
 * respond with (status 200) — return `null` to fall through to the
 * catch-all empty payload.
 */
type Fixture = (url: string) => unknown;

const FIXTURES: Array<[string, Fixture]> = [
  // --- Auth / session -----------------------------------------------------
  ['/api/admin/verify', () => ({
    name: 'E2E Admin',
    email: 'e2e-admin@syrabit.ai',
    access_token: 'e2e.admin.jwt',
  })],
  ['/api/admin/login', () => ({
    name: 'E2E Admin',
    email: 'e2e-admin@syrabit.ai',
    access_token: 'e2e.admin.jwt',
  })],

  // --- Top-level chrome ---------------------------------------------------
  // HealthOut (response_model on GET /api/health) requires: status, version,
  // service, workers, uptime_seconds, dependencies.  Keep all fields so the
  // global fixture-schema validator does not flag missing required fields.
  ['/api/health', () => ({
    status: 'ok',
    version: '0.0.0-e2e',
    service: 'syrabit-api',
    workers: 1,
    uptime_seconds: 0,
    dependencies: {},
  })],
  ['/api/admin/settings', () => ({ maintenance_mode: false })],
  ['/api/admin/alerts/unacknowledged-count', () => ({ count: 0 })],
  ['/api/admin/notification-prefs', () => ({
    sound_enabled: true,
    push_enabled: false,
    chime_tone: 'default',
    sound_severities: [],
    push_severities: [],
  })],
  ['/api/admin/push/delivery-stats', () => ({ totals: {}, daily: [] })],
  ['/api/admin/alert-settings', () => ({ channel_status: { push: null } })],
  ['/api/admin/seo/daily-summary-dispatches', () => ({ dispatches: [] })],
  ['/api/admin/kv-health', () => ({ configured: false })],
  ['/api/admin/ci-status', () => ({
    configured: true,
    github_token_set: true,
    workflows: [
      {
        name: 'azure-container-apps-deploy',
        status: 'completed',
        conclusion: 'success',
        run_number: 42,
        html_url: 'https://github.com/syrabit/syrabit/actions/runs/42',
        updated_at: '2026-05-15T08:00:00Z',
      },
      {
        name: 'edge-proxy-deploy',
        status: 'completed',
        conclusion: 'success',
        run_number: 38,
        html_url: 'https://github.com/syrabit/syrabit/actions/runs/38',
        updated_at: '2026-05-15T07:30:00Z',
      },
    ],
  })],

  // --- Dashboard data -----------------------------------------------------
  ['/api/admin/dashboard/metrics', () => ({})],
  ['/api/admin/dashboard', () => ({
    users: { total: 0, signups_today: 0 },
    conversations: { total: 0, today: 0 },
    content: { subjects: 0, chapters: 0 },
    activity: [],
    dependencies: {},
  })],
  ['/api/admin/rag/accuracy', () => ({ accuracy: 100, alert: 'green' })],
  ['/api/admin/chat/fallbacks', () => ({ daily: [], alert: 'green' })],
  ['/api/admin/vector/stats', () => ({ pages: {}, chapters: {} })],
  ['/api/admin/perf/latency', () => ({ daily: [], alert: 'green' })],
  ['/api/admin/chat/speedups', () => ({ daily: [], warm_runs: [], totals: {} })],
  ['/api/admin/analytics/queries', () => ({ top_queries: [] })],
  ['/api/admin/billing/tokens', () => ({ daily: [], totals: {} })],
  ['/api/admin/monetization/funnel', () => ({})],
  ['/api/admin/content/coverage', () => ({})],
  ['/api/admin/pwa/stats', () => ({})],
  ['/api/admin/analytics/bot-traffic', () => ({
    ok: true, period: '24h', total_requests: 48200, bot_requests: 1400,
    bot_pct: 2.9, status: 'green', threshold_amber: 15, threshold_red: 30,
    breakdown: { verified_bot: 900, automated: 300, likely_automated: 200 },
  })],
  ['/api/admin/indexnow/stats', () => ({ endpoints: [] })],
  ['/api/admin/indexnow/history', () => ({ history: [] })],
  ['/api/admin/alerts', () => ({ alerts: [] })],
  ['/api/admin/seo/health-history', () => ({ history: [] })],
  ['/api/admin/seo/deep-scan-history', () => ({})],
  // Corrected from /api/admin/cf/overview — the real backend path is
  // /api/admin/analytics/cf-overview (confirmed in server.py + api.jsx).
  ['/api/admin/analytics/cf-overview', () => ({
    ok: true, zone: 'syrabit.ai', requests_total: 48200,
    bandwidth_gb: 18.4, threats: 17, cached_pct: 82,
    cloudflare_token_set: true,
  })],
  // Corrected from /api/seo/pipeline/status — the real backend path is
  // /api/admin/seo/pipeline-status (confirmed in server.py + api.jsx).
  ['/api/admin/seo/pipeline-status', () => ({
    total_topics: 0, published: 0, has_content: 0,
    needs_schema: 0, needs_internal_links: 0,
    pages_total: 0, published_today: 0,
  })],
  ['/api/seo/health', () => ({ sitemaps: [] })],

  // Task #940 — Entity SEO admin panel.
  // Default fixture surfaces a "healthy / nothing missing" snapshot so
  // the dashboard renders the panel chrome without erroring. Drift
  // scenarios are wired via per-test `overrides`.
  ['/api/admin/seo/entity/status', () => ({
    configured: true,
    snapshot: {
      generated_at: '2026-04-26T04:30:00.000Z',
      iso_week: '2026-W17',
      aggregate_status: 'ok',
      summary: {
        wikidata_claims: 7, wikidata_missing: 0,
        sameas_total: 7, sameas_broken: 0,
        wikipedia_present: true, crunchbase_present: true, google_kg_present: true,
      },
      signals: {
        wikidata:   { name: 'wikidata',   status: 'ok',
          summary: 'Syrabit.ai (Q123) — 7 claims, 0 desired claims missing.',
          fields: { qid: 'Q123', claim_count: 7, present_claims: ['P31','P17'],
                    missing_claims: [], edit_url: 'https://www.wikidata.org/wiki/Q123' } },
        wikipedia:  { name: 'wikipedia',  status: 'ok',
          summary: 'Article live: Syrabit.ai',
          fields: { title: 'Syrabit.ai', page_url: 'https://en.wikipedia.org/wiki/Syrabit.ai' } },
        crunchbase: { name: 'crunchbase', status: 'ok',
          summary: 'Crunchbase profile reachable (100% of tracked fields detected).',
          fields: { permalink: 'syrabit-ai', completeness_pct: 100,
                    page_url: 'https://www.crunchbase.com/organization/syrabit-ai' } },
        sameas:     { name: 'sameas',     status: 'ok',
          summary: 'All 7 verified profiles live.',
          fields: { total: 7, broken: [] } },
        google_kg:  { name: 'google_kg',  status: 'ok',
          summary: 'Knowledge Panel present for all 2 tracked queries.',
          fields: { configured: true, name: 'Syrabit.ai',
                    queries: [
                      { query: 'Syrabit',    status: 'ok', kg_id: 'kg:/m/syrabit', name: 'Syrabit.ai',    score: 950 },
                      { query: 'Syrabit.ai', status: 'ok', kg_id: 'kg:/m/syrabit', name: 'Syrabit.ai',    score: 940 },
                    ] } },
        mentions:   { name: 'mentions',   status: 'ok',
          summary: 'All 3 mention targets cover us.',
          fields: { total: 3, missing: [], targets: [] } },
      },
      missing_claims: [],
      missing_mentions: [],
    },
    previous: null,
    drift: { hadBaseline: false, regressions: [], improvements: [],
             summaryDeltas: {
               wikidata_claims:  { current: 7, previous: 7, delta: 0 },
               wikidata_missing: { current: 0, previous: 0, delta: 0 },
               sameas_broken:    { current: 0, previous: 0, delta: 0 },
             } },
    missingClaims: [],
    missingMentions: [],
    alertState: null,
  })],
  ['/api/admin/seo/entity/history', () => ({ items: [] })],
  ['/api/admin/seo/entity/refresh', () => ({
    configured: true,
    snapshot: null, previous: null,
    drift: { hadBaseline: false, regressions: [], improvements: [], summaryDeltas: {} },
    missingClaims: [], alertState: null,
    refresh: { claimed: true, stored: true, regression_count: 0, paged: false },
  })],

  // --- AdminHealth cron endpoints (Task #894 / #919 / #956) ---------------
  // These are the four cron-pill data endpoints.  Returning a healthy
  // payload means every pill renders green and the waitForRequest()
  // assertions inside admin-health-cron-pills.spec.ts and
  // admin-health-alert-state-caption.spec.ts resolve quickly.
  ['/api/admin/health/edge-proxy-deploy/cron/alert-state', () => ({
    present: false,
    lastAlertAt: null,
    lastAlertAgeSeconds: null,
    inDebounce: false,
    debounceRemainingSeconds: null,
    realertIntervalSeconds: 21600,
  })],
  ['/api/admin/health/cf-waf-drift/cron/alert-state', () => ({
    present: false,
    lastAlertAt: null,
    lastAlertAgeSeconds: null,
    inDebounce: false,
    debounceRemainingSeconds: null,
    realertIntervalSeconds: 21600,
  })],
  ['/api/admin/health/trustpilot/refresh-cron/alert-state', () => ({
    present: false,
    lastAlertAt: null,
    lastAlertAgeSeconds: null,
    inDebounce: false,
    debounceRemainingSeconds: null,
    realertIntervalSeconds: 21600,
  })],
  ['/api/admin/health/unified-logs/cf-pull/cron/alert-state', () => ({
    present: false,
    lastAlertAt: null,
    lastAlertAgeSeconds: null,
    inDebounce: false,
    debounceRemainingSeconds: null,
    realertIntervalSeconds: 21600,
  })],
  // The cron-status endpoints must come AFTER their alert-state siblings
  // so the longer (more-specific) alert-state paths win when sorted by
  // descending length in installAdminApiMocks.
  ['/api/admin/health/edge-proxy-deploy/cron', () => ({
    configured: true,
    status: 'healthy',
    conclusion: 'success',
    html_url: 'https://github.com/syrabit/syrabit/actions/runs/777',
    lastRunUrl: 'https://github.com/syrabit/syrabit/actions/runs/777',
    updated_at: '2026-04-25T10:00:00Z',
    ageSeconds: 3600,
    runStatus: 'completed',
    workflowUrl: 'https://github.com/syrabit/syrabit/actions/workflows/edge-proxy-deploy.yml',
    staleThresholdSeconds: 604800,
    error: null,
  })],
  ['/api/admin/health/cf-waf-drift/cron', () => ({
    configured: true,
    status: 'healthy',
    lastHeartbeatAgeSeconds: 1800,
    lastSuccessHeartbeatAgeSeconds: 1800,
    lastRunUrl: 'https://github.com/syrabit/syrabit/actions/runs/555',
    workflowUrl: 'https://github.com/syrabit/syrabit/actions/workflows/cf-waf-drift-daily.yml',
    staleThresholdSeconds: 129600,
    error: null,
  })],
  ['/api/admin/health/trustpilot/refresh-cron', () => ({
    configured: true,
    status: 'healthy',
    lastHeartbeatAgeSeconds: 1800,
    lastSuccessHeartbeatAgeSeconds: 1800,
    workflowUrl: 'https://github.com/syrabit/syrabit/actions/workflows/trustpilot-aggregate-refresh.yml',
    staleThresholdSeconds: 129600,
    error: null,
  })],
  ['/api/admin/health/unified-logs/cf-pull/cron', () => ({
    configured: true,
    status: 'healthy',
    lastUpdatedTs: 1700000000,
    lastUpdatedAt: '2026-04-26T05:00:00Z',
    lastUpdatedAgeSeconds: 1800,
    leaseOwner: 'replica-A',
    leaseExpiresAt: '2026-04-26T05:30:00Z',
    cursor: 'cursor-xyz',
    silentThresholdSeconds: 900,
    statusUrl: '/api/admin/logs/status',
  })],

  // --- Analytics cf-ai-crawl-control (Task #xxx) --------------------------
  // Dashboard fetches /api/admin/analytics/cf-ai-crawl-control?days=7
  // on every load; without a fixture it falls through to the catch-all
  // EMPTY response which is fine but keeps it in failed[] for smoke test.
  ['/api/admin/analytics/cf-ai-crawl-control', () => ({
    ok: true, days: 7, zones: [],
  })],

  // --- AdminContentHub hierarchy (loaded on mount) ------------------------
  // AdminContentHub.reloadHierarchy() fires four GETs on mount.
  // Without fixtures the component stays in "loading" state and the
  // tab-bar buttons are never rendered, causing strict-mode failures.
  ['/api/admin/content/boards', () => []],
  ['/api/admin/content/classes', () => []],
  ['/api/admin/content/streams', () => []],
  ['/api/admin/content/subjects', () => []],

  // --- AdminLogs status endpoint (AdminLogsExplorer mount) ----------------
  ['/api/admin/logs/status', () => ({
    paused: false, ttl_days: 14, ingest_token_configured: true,
    backend_sample_rate: 0.05, edge_sample_rate: 0.05,
    max_ingest_batch: 500, cf_pull_interval_s: 60, cf_pull_24h: null,
  })],
  ['/api/admin/logs', () => ({
    logs: [], total: 0, total_capped: false, next_before: null,
  })],

  // --- AdminRateLimits panel (rate-policies GET on mount) -----------------
  ['/api/admin/rate-policies', () => ({
    free:       { req_per_min: 5,  credits_per_day: 30,   max_tokens: 10000,  req_per_min_ip: 20 },
    starter:    { req_per_min: 10, credits_per_day: 500,  max_tokens: 15000,  req_per_min_ip: 30 },
    pro:        { req_per_min: 15, credits_per_day: 4000, max_tokens: 20000,  req_per_min_ip: 40 },
    enterprise: { req_per_min: 60, credits_per_day: 99999, max_tokens: 200000, req_per_min_ip: 200 },
  })],
  ['/api/admin/rate-stats', () => ({ ok: true })],

  // --- AdminUsers & Plans panels ------------------------------------------
  ['/api/admin/users', () => ({
    users: [
      { id: 'user-001', email: 'alice@example.com', name: 'Alice', plan: 'starter', credits_used: 45, credits_limit: 1500 },
      { id: 'user-002', email: 'bob@example.com', name: 'Bob', plan: 'free', credits_used: 8, credits_limit: 30 },
    ],
    total: 2, page: 1, per_page: 20,
  })],
  ['/api/admin/plan-config', () => ({
    free:    { price: 0,   credits: 30,   validity: 'daily reset' },
    starter: { price: 99,  credits: 500,  validity: 'daily reset' },
    pro:     { price: 999, credits: 4000, validity: 'daily reset' },
  })],

  // --- AdminConversations panel -------------------------------------------
  ['/api/admin/conversations', () => ([
    {
      id: 'conv-001', title: 'Photosynthesis inquiry',
      user_id: 'user-a', user_name: 'Alice Barua', user_email: 'alice@example.com',
      is_anonymous: false, flagged: false,
      created_at: new Date(Date.now() - 3600_000).toISOString(),
      updated_at: new Date(Date.now() - 1800_000).toISOString(),
      messages: [],
    },
  ])],

  // --- AdminNotifications panel -------------------------------------------
  ['/api/admin/notifications', () => []],

  // --- AdminAnalytics panel (GA4) -----------------------------------------
  ['/api/admin/analytics', () => ({
    ok: true, pageviews: 0, sessions: 0, bounce_rate: 0,
    avg_session_duration_sec: 0, top_pages: [],
  })],

  // --- AdminSeoManager keyword / linker / topic-discovery ----------------
  ['/api/admin/extract-keywords', () => ({ suggestions: [] })],
  ['/api/admin/seo/internal-links/history', () => ({ items: [] })],
  ['/api/admin/seo/internal-links/pending', () => ({ items: [] })],
  ['/api/admin/seo/internal-links/status', () => ({
    enabled: true,
    budget: { auto_used: 0, auto_cap: 100 },
    pendingCount: 0,
    recentAutoApplied24h: 0,
    config: { autoApplyThreshold: 0.75, minLinksPerTarget: 3, maxLinksPerTarget: 5, candidatePoolSize: 30, nightlyTopN: 50 },
  })],
  ['/api/admin/seo/internal-links', () => ({ items: [], pendingCount: 0 })],
  ['/api/admin/seo/topic-discovery/runs', () => ({ runs: [] })],
  ['/api/admin/seo/topic-discovery/candidates', () => ({ candidates: [] })],
  ['/api/admin/seo/topic-discovery', () => ({ runs: [], candidates: [] })],
];

/**
 * Flat list of every URL substring key registered in FIXTURES.
 *
 * Imported by tests/global-setup.ts so the fixture-schema drift
 * validator can check each key against the committed OpenAPI snapshot
 * (tests/api-schema.json) at test startup without duplicating the list
 * here and in the setup file.
 */
export const FIXTURE_KEYS: readonly string[] = FIXTURES.map(([key]) => key);

/**
 * Representative fixture body for every URL key in FIXTURES.
 *
 * Each entry is the JSON object that the mock will actually return for
 * a request whose URL contains the corresponding key.  Calling the
 * fixture function with the key as the URL is correct for all fixtures
 * that ignore the URL argument (the vast majority); the few that do
 * use the URL (e.g. history paginators) return a type-stable shape at
 * any URL, so the key itself is a safe stand-in.
 *
 * Imported by tests/global-setup.ts so the drift validator can validate
 * these payloads against the OpenAPI response schemas for their
 * matching backend paths — catching field renames, type changes, and
 * removed required fields automatically at test startup.
 */
export const FIXTURE_SAMPLES: ReadonlyMap<string, unknown> = new Map(
  FIXTURES.map(([key, fn]) => [key, fn(key)] as const),
);

interface InstallOptions {
  /**
   * URL substrings to force-fail with a 500 status. Used to assert that
   * the dashboard degrades gracefully (inline "failed to load" card)
   * instead of falling through to the global ErrorBoundary.
   */
  failPatterns?: string[];
  /**
   * Per-endpoint fixture overrides keyed by URL substring. Checked
   * before the default FIXTURES list so a single test can replace
   * the catch-all empty payload for one endpoint (e.g. the
   * AdminHealth cron pills) without disturbing the rest of the
   * dashboard mocks. The value is the JSON body to respond with
   * (status 200), or a function returning that body.
   */
  overrides?: Record<string, unknown | ((url: string) => unknown)>;
}

export async function installAdminApiMocks(page: Page, opts: InstallOptions = {}) {
  const failPatterns = opts.failPatterns ?? [];
  // Match the more-specific override first so a key like
  // `/api/admin/health/edge-proxy-deploy/cron` doesn't shadow
  // `/api/admin/health/edge-proxy-deploy/cron/alert-state` (the
  // first is a prefix of the second, and `Array.find` returns
  // the first hit). Sorting by descending key length means
  // longer/more-specific patterns always win — author order
  // inside the `overrides` object then doesn't matter.
  const overrides = Object.entries(opts.overrides ?? {})
    .sort(([a], [b]) => b.length - a.length);

  await page.route('**/api/**', async (route: Route) => {
    const req = route.request();
    const url = req.url();
    const method = req.method();

    if (failPatterns.some((p) => url.includes(p))) {
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'forced failure (e2e)' }),
      });
      return;
    }

    if (method === 'OPTIONS') {
      await route.fulfill({ status: 204, body: '' });
      return;
    }

    const override = overrides.find(([key]) => url.includes(key));
    if (override) {
      const [, value] = override;
      const body = typeof value === 'function' ? (value as (u: string) => unknown)(url) : value;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(body ?? EMPTY),
      });
      return;
    }

    const fixture = FIXTURES.find(([key]) => url.includes(key));
    const body = fixture ? fixture[1](url) : EMPTY;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(body ?? EMPTY),
    });
  });
}

/**
 * Seed the admin token in localStorage so AdminPage's useEffect skips the
 * /admin/login redirect on first render. Must be called before navigating
 * to /admin.
 */
export async function seedAdminSession(page: Page) {
  await page.addInitScript(() => {
    try {
      window.localStorage.setItem('admin_token', 'e2e.admin.jwt');
    } catch {}
  });
}
