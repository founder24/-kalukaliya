import { test, expect } from '@playwright/test';
import { STAFF_PORTAL_SECTIONS } from '../src/config/staffPortalSections.mjs';

/**
 * Staff panel smoke tests — Task #256
 *
 * Verifies that all four new sidebar sections (Dashboard, Analytics, Users,
 * Conversations) render without blank screens, error boundaries, or uncaught
 * console errors after the admin-component consolidation into StaffDashboard.
 *
 * Strategy: mock every backend call so the test runs in isolation without a
 * live API. A staff-role user is injected by:
 *   1. Setting `syrabit_token` in sessionStorage so AuthContext detects a
 *      token and calls fetchMe() immediately (fast path).
 *   2. Mocking /api/v1/users/me to return a staff-role profile so
 *      StaffGuard allows the route to render.
 *   3. Mocking all admin/* and staff/* API calls with minimal valid payloads
 *      so each component can move past its loading state without crashing.
 *
 * Route registration order matters in Playwright: the MOST RECENTLY registered
 * route is matched first. Catch-alls are registered first; specific routes are
 * registered last so they take priority. The CRUD scenario can opt into a
 * strict catch-all so an unlisted API request fails instead of returning an
 * empty success response.
 */

// Staff user profile returned by /users/me mock
const STAFF_USER = {
  id: 'staff-001',
  email: 'staff@syrabit.com',
  name: 'Staff User',
  role: 'staff',
  capabilities: ['referral:settle'],
  plan: 'pro',
  subscription_tier: 'pro',
};

/**
 * API requests owned by the application shell rather than by a staff section.
 *
 * Keep this list aligned with App.jsx startup effects and shared admin-shell
 * components such as BreakGlassBanner.jsx. When either adds a request, add its
 * isolated response here first so strict staff preview tests fail loudly until
 * the contract is updated. These routes are registered below the API catch-all
 * and always fulfill locally; they must never fall through to a live API.
 */
const STAFF_SHELL_API_ROUTES = [
  {
    pattern: '**/api/v1/content/library-bundle*',
    body: {
      boards: [],
      classes: [],
      streams: [],
      subjects: [],
      chapters: [],
    },
  },
  {
    pattern: '**/api/v1/admin/break-glass-status',
    body: { active: false },
  },
];

async function registerJsonRoutes(
  page: import('@playwright/test').Page,
  routes: Array<{ pattern: string; body: unknown }>,
) {
  for (const { pattern, body } of routes) {
    await page.route(pattern, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(body),
      }),
    );
  }
}

async function setupMocks(page: import('@playwright/test').Page) {
  // ── Step 1: register broad catch-alls first (lowest priority) ──────────────
  let strictUnexpectedApiRequests = false;

  // Generic catch-all for any remaining /api/v1/* not covered below
  await page.route('**/api/v1/**', (route) => {
    if (strictUnexpectedApiRequests) {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      throw new Error(`Unexpected staff preview API request: ${request.method()} ${path}`);
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({}),
    });
  });

  // ── Step 2: register specific routes last (highest priority) ───────────────

  // Shared shell requests run on every route, including /staff. Keep their
  // contract separate from section-specific mocks so startup changes are easy
  // to review and strict CRUD mode remains fully offline.
  await registerJsonRoutes(page, STAFF_SHELL_API_ROUTES);

  // Staff content loaded on StaffDashboard mount
  const staffContentRoutes = [
    { pattern: '**/api/v1/staff/content/boards*',   body: [] },
    { pattern: '**/api/v1/staff/content/classes*',  body: [] },
    { pattern: '**/api/v1/staff/content/streams*',  body: [] },
    { pattern: '**/api/v1/staff/content/subjects*', body: [] },
  ];
  for (const { pattern, body } of staffContentRoutes) {
    await page.route(pattern, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) }),
    );
  }

  // AdminDashboard — all the parallel fetches in its load() call
  const adminDashboardRoutes = [
    { pattern: '**/api/v1/admin/dashboard', body: { total_users: 0, active_users: 0, total_subjects: 0, total_chapters: 0 } },
    { pattern: '**/api/v1/admin/dashboard/metrics', body: {} },
    { pattern: '**/api/v1/admin/rag/accuracy', body: {} },
    { pattern: '**/api/v1/admin/chat/fallbacks', body: { fallbacks: [] } },
    { pattern: '**/api/v1/admin/vector/stats', body: {} },
    { pattern: '**/api/v1/admin/perf/latency', body: {} },
    { pattern: '**/api/v1/admin/analytics/queries', body: { queries: [] } },
    { pattern: '**/api/v1/admin/billing/tokens', body: {} },
    { pattern: '**/api/v1/admin/monetization/funnel', body: { funnel: [] } },
    { pattern: '**/api/v1/admin/content/coverage', body: {} },
    { pattern: '**/api/v1/admin/pwa/stats', body: {} },
    { pattern: '**/api/v1/admin/analytics/bot-traffic*', body: {} },
    { pattern: '**/api/v1/admin/analytics/cf-overview*', body: { connected: false } },
    { pattern: '**/api/v1/admin/analytics/cf-ai-crawl-control*', body: { available: false } },
    { pattern: '**/api/v1/admin/chat/speedups*', body: { daily: [], warm_runs: [], totals: {} } },
    { pattern: '**/api/v1/admin/chat/anon-quota-exhausted*', body: { daily: [], by_hour: {}, by_day_of_week: {} } },
    { pattern: '**/api/v1/admin/indexnow/stats', body: {} },
    { pattern: '**/api/v1/admin/indexnow/history*', body: { history: [] } },
    { pattern: '**/api/v1/admin/seo/prewarm-coverage', body: {} },
    { pattern: '**/api/v1/admin/seo/deep-scan-history*', body: { history: [] } },
    { pattern: '**/api/v1/admin/seo/pipeline-status', body: {} },
    { pattern: '**/api/v1/admin/aws/workers/health', body: { composite: 'unknown', queues: [] } },
    { pattern: '**/api/v1/admin/alerts*', body: { alerts: [], total: 0 } },
    { pattern: '**/api/v1/admin/seo/health-history*', body: { history: [] } },
    { pattern: '**/api/v1/admin/seo/health/live*', body: {} },
    { pattern: '**/api/v1/admin/notification-prefs*', body: { sound_enabled: true, push_enabled: false, chime_tone: 'default', sound_severities: [], push_severities: [] } },
    { pattern: '**/api/v1/admin/push/delivery-stats*', body: {} },
    { pattern: '**/api/v1/admin/alert-settings*', body: { channel_status: {} } },
    { pattern: '**/api/v1/admin/seo/daily-summary-dispatches*', body: { dispatches: [] } },
    { pattern: '**/api/v1/admin/kv-health*', body: { configured: false } },
    { pattern: '**/api/v1/admin/r2-storage-health*', body: { configured: false } },
    { pattern: '**/api/v1/admin/ci-status*', body: { configured: false } },
    { pattern: '**/api/v1/admin/vertex/probe-status*', body: { status: 'unknown' } },
    { pattern: '**/api/v1/admin/alerts/cooldowns*', body: { active: [], total: 0 } },
    { pattern: '**/api/v1/admin/routing-config*', body: { pools: [] } },
    { pattern: '**/api/v1/admin/syra/prefs', body: {} },
    { pattern: '**/api/v1/admin/pyq/by-chapter/*', body: { pyqs: [] } },
  ];

  // AdminAnalytics endpoints
  const adminAnalyticsRoutes = [
    { pattern: '**/api/v1/admin/analytics', body: { visitor_stats: {}, cf_connected: false } },
    { pattern: '**/api/v1/admin/analytics/funnel*', body: { funnel: [] } },
    { pattern: '**/api/v1/admin/analytics/content-heatmap*', body: {} },
    { pattern: '**/api/v1/admin/analytics/revenue*', body: { cohorts: {}, daily_revenue: [] } },
    { pattern: '**/api/v1/admin/analytics/predictor*', body: {} },
    { pattern: '**/api/v1/admin/analytics/ga4*', body: { connected: false } },
    { pattern: '**/api/v1/admin/analytics/cf-status*', body: { auth_ok: true, configured: true } },
    { pattern: '**/api/v1/admin/analytics/daily*', body: {} },
  ];

  // AdminUsers / AdminConversations endpoints
  const adminCrudRoutes = [
    { pattern: '**/api/v1/admin/users*', body: { users: [], total: 0 } },
    { pattern: '**/api/v1/admin/conversations/sentiment*', body: { total: 0, positive: 0, negative: 0, neutral: 0, positive_pct: 0, negative_pct: 0 } },
    { pattern: '**/api/v1/admin/conversations*', body: { data: [], total: 0 } },
    { pattern: '**/api/v1/admin/content/draft-served-subjects*', body: { subjects: [] } },
    { pattern: '**/api/v1/staff/analytics/command-center*', body: {} },
    { pattern: '**/api/v1/admin/referrals/settlements*', body: { statements: [] } },
    { pattern: '**/api/v1/admin/referrals/beneficiaries*', body: { beneficiaries: [] } },
    {
      pattern: '**/api/v1/admin/referrals/roi/dashboard*',
      body: { inventory: [], controls: null, reports: [] },
    },
  ];

  // SEO live health (called directly, not via admin helper)
  const seoRoutes = [
    { pattern: '**/api/v1/seo/health*', body: {} },
  ];

  for (const { pattern, body } of [
    ...adminDashboardRoutes,
    ...adminAnalyticsRoutes,
    ...adminCrudRoutes,
    ...seoRoutes,
  ]) {
    await page.route(pattern, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) }),
    );
  }

  // Auth refresh — guard against token-expired retry loop
  await page.route('**/api/v1/auth/refresh*', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ access_token: 'mock-staff-token', refresh_token: 'mock-refresh' }),
    }),
  );

  // /users/me MUST be last (highest priority) — returns the staff profile that
  // lets StaffGuard pass. Must override the broad catch-all above.
  await page.route('**/api/v1/users/me', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(STAFF_USER),
    }),
  );

  return {
    enableStrictUnexpectedApiRequests() {
      strictUnexpectedApiRequests = true;
    },
  };
}

/**
 * Stateful content-editor fixture. Every request is fulfilled locally and
 * mutating requests update this in-memory copy, so the browser test cannot
 * read or write production curriculum data.
 */
async function setupContentEditorFixture(page: import('@playwright/test').Page) {
  const requests: Array<{ method: string; path: string }> = [];
  const boards = [{ id: 'board-1', name: 'AHSEC', slug: 'ahsec', status: 'published' }];
  const classes = [{ id: 'class-1', name: 'Class 12', board_id: 'board-1', status: 'published' }];
  const streams = [{ id: 'stream-1', name: 'Science', class_id: 'class-1', board_id: 'board-1', status: 'published' }];
  let subjects = [
    {
      id: 'subject-1',
      name: 'Physics',
      description: 'Physics for Class 12',
      stream_id: 'stream-1',
      class_id: 'class-1',
      board_id: 'board-1',
      status: 'published',
      chapter_count: 0,
    },
  ];
  let chapters = [{
    id: 'chapter-1',
    subject_id: 'subject-1',
    title: 'Motion',
    slug: 'motion',
    description: 'Motion fundamentals',
    status: 'published',
    content_type: 'notes',
    content: '',
    notes_en: '## Existing English\n\n• first point',
    notes_as: '## বিদ্যমান\n\n• প্ৰথম বিষয়',
    qa_text_en: '## Q1\n\n**Answer:** Existing answer',
    qa_text_as: '## প্ৰশ্ন ১\n\n**উত্তৰ:** বৰ্তমানৰ উত্তৰ',
    rag_sections_en: [],
    rag_sections_as: [],
    qa_rag_sections_en: [],
    qa_rag_sections_as: [],
    notes_rag_stale: true,
    qa_rag_stale: true,
    notes_rag_updated_at: '2026-09-15T09:00:00.000Z',
    qa_rag_updated_at: '2026-09-15T09:00:00.000Z',
    notes_rag_indexed_at: null,
    qa_rag_indexed_at: null,
    notes_generated: false,
    chapter_number: 1,
    version: 0,
  }];

  const json = (route: import('@playwright/test').Route, body: unknown, status = 200) =>
    route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  const pathOf = (route: import('@playwright/test').Route) => new URL(route.request().url()).pathname;
  const record = (route: import('@playwright/test').Route) => {
    requests.push({ method: route.request().method(), path: pathOf(route) });
  };
  const bodyOf = (route: import('@playwright/test').Route) => {
    try { return route.request().postDataJSON() as Record<string, unknown>; }
    catch { return {}; }
  };

  await page.route('**/api/v1/staff/content/boards*', route => {
    record(route);
    return json(route, boards);
  });
  await page.route('**/api/v1/staff/content/classes*', route => {
    record(route);
    return json(route, classes);
  });
  await page.route('**/api/v1/staff/content/streams*', route => {
    record(route);
    return json(route, streams);
  });
  const handleSubjects = (route: import('@playwright/test').Route) => {
    record(route);
    const path = pathOf(route);
    if (route.request().method() === 'GET') return json(route, subjects);
    const subjectId = path.split('/').pop();
    if (route.request().method() === 'POST') {
      const input = bodyOf(route);
      const created = {
        id: 'subject-2',
        name: String(input.name || 'Chemistry'),
        description: String(input.description || ''),
        stream_id: String(input.stream_id || 'stream-1'),
        class_id: 'class-1',
        board_id: 'board-1',
        status: String(input.status || 'published'),
        chapter_count: 0,
      };
      subjects = [...subjects, created];
      return json(route, created, 201);
    }
    if (route.request().method() === 'PATCH') {
      const input = bodyOf(route);
      subjects = subjects.map(subject => subject.id === subjectId ? { ...subject, ...input } : subject);
      return json(route, subjects.find(subject => subject.id === subjectId) || {});
    }
    if (route.request().method() === 'DELETE') {
      subjects = subjects.filter(subject => subject.id !== subjectId);
      return json(route, { ok: true });
    }
    return json(route, {});
  };
  await page.route('**/api/v1/staff/content/subjects*', handleSubjects);
  await page.route('**/api/v1/staff/content/subjects/**', handleSubjects);
  const handleChapters = (route: import('@playwright/test').Route) => {
    record(route);
    const path = pathOf(route);
    if (route.request().method() === 'GET') {
      const subjectId = path.split('/').pop();
      return json(route, chapters.filter(chapter => chapter.subject_id === subjectId));
    }
    if (route.request().method() === 'POST') {
      const input = bodyOf(route);
      const created = {
        id: 'chapter-1',
        subject_id: String(input.subject_id || 'subject-1'),
        title: String(input.title || 'New Chapter'),
        slug: String(input.slug || 'new-chapter'),
        description: String(input.description || ''),
        status: String(input.status || 'published'),
        content_type: String(input.content_type || 'notes'),
        content: String(input.content || ''),
        notes_en: String(input.notes_en || ''),
        notes_generated: false,
        chapter_number: Number(input.chapter_number || 1),
        version: 0,
      };
      chapters = [created];
      return json(route, created, 201);
    }
    return json(route, {});
  };
  await page.route('**/api/v1/staff/content/chapters*', handleChapters);
  await page.route('**/api/v1/staff/content/chapters/**', handleChapters);
  await page.route('**/api/v1/staff/content/chapter/**', route => {
    record(route);
    const path = pathOf(route);
    const reindexMatch = path.match(/\/chapter\/([^/]+)\/reindex$/);
    const chapterId = reindexMatch ? reindexMatch[1] : path.split('/').pop();
    const chapter = chapters.find(item => item.id === chapterId);
    if (route.request().method() === 'GET') return json(route, chapter || {}, chapter ? 200 : 404);
    if (route.request().method() === 'POST' && reindexMatch) {
      const scope = new URL(route.request().url()).searchParams.get('scope');
      chapters = chapters.map(item => {
        if (item.id !== chapterId) return item;
        const indexed = {
          ...item,
          notes_rag_stale: scope === 'notes' || scope === 'all' ? false : item.notes_rag_stale,
          qa_rag_stale: scope === 'qa' || scope === 'all' ? false : item.qa_rag_stale,
          notes_rag_indexed_at: scope === 'notes' || scope === 'all' ? '2026-09-15T09:01:00.000Z' : item.notes_rag_indexed_at,
          qa_rag_indexed_at: scope === 'qa' || scope === 'all' ? '2026-09-15T09:01:00.000Z' : item.qa_rag_indexed_at,
        };
        return indexed;
      });
      return json(route, { ok: true, chapter: chapters.find(item => item.id === chapterId) });
    }
    if (route.request().method() === 'PATCH') {
      chapters = chapters.map(item => item.id === chapterId ? { ...item, ...bodyOf(route), version: (item.version || 0) + 1 } : item);
      return json(route, chapters.find(item => item.id === chapterId) || {});
    }
    if (route.request().method() === 'DELETE') {
      chapters = chapters.filter(item => item.id !== chapterId);
      return json(route, { ok: true });
    }
    return json(route, {});
  });
  await page.route('**/api/v1/admin/content/subject/*/chapter-cards', route => {
    record(route);
    return json(route, {
      cards: chapters.map(chapter => ({
        chapter_id: chapter.id,
        notes_generated: Boolean(chapter.notes_generated),
        word_count: chapter.content?.split(/\s+/).filter(Boolean).length || 0,
      })),
    });
  });
  await page.route('**/api/v1/admin/content/subject/*/coverage', route => {
    record(route);
    return json(route, { chapters: [] });
  });
  await page.route('**/api/v1/admin/content/chapters/*/stats', route => {
    record(route);
    return json(route, { notes_generated: false, pyq_count: 0, mark_wise_counts: {} });
  });
  await page.route('**/api/v1/admin/content/chapter/*', route => {
    record(route);
    const chapterId = pathOf(route).split('/').pop();
    return json(route, chapters.find(chapter => chapter.id === chapterId) || {});
  });
  await page.route('**/api/v1/admin/content/chapters/*/generate-notes', route => {
    record(route);
    const chapterId = pathOf(route).split('/').slice(-2)[0];
    const generated = 'Generated notes for this isolated staff preview fixture with enough content to render the saved Notes state.';
    chapters = chapters.map(chapter => chapter.id === chapterId
      ? { ...chapter, content: generated, notes_en: generated, notes_generated: true }
      : chapter);
    return json(route, { content: generated, notes_en: generated, word_count: generated.split(/\s+/).length });
  });

  return {
    requests,
    hasRequest(method: string, path: string) {
      return requests.some(request => request.method === method && request.path === path);
    },
  };
}

// ---------------------------------------------------------------------------
// Test suite
// ---------------------------------------------------------------------------

test.describe('Staff panel — sidebar sections', () => {
  let consoleErrors: string[] = [];
  let staffMocks: Awaited<ReturnType<typeof setupMocks>>;

  test.beforeEach(async ({ page }) => {
    consoleErrors = [];

    // Collect uncaught console errors (filter known non-critical noise)
    page.on('console', (msg) => {
      if (msg.type() === 'error') {
        const text = msg.text();
        const ignored = [
          'favicon',
          'net::ERR_',
          'Failed to load resource',
          'ResizeObserver loop',
          'Non-Error promise rejection',
          '[vite]',
          'VITE_BACKEND_URL',
          'API requests will use relative paths',
        ];
        if (!ignored.some((s) => text.includes(s))) {
          consoleErrors.push(text);
        }
      }
    });

    staffMocks = await setupMocks(page);

    // Inject the token into sessionStorage BEFORE the page loads so that
    // AuthContext.hydrateTokensFromStorage() finds it and calls fetchMe()
    // immediately (hasToken=true fast path). Key: 'syrabit_token' per
    // useTokenManager.ts (safeSessionSet / safeSessionGet).
    await page.addInitScript(() => {
      sessionStorage.setItem('syrabit_token', 'mock-staff-token');
    });
  });

  // ──────────────────────────────────────────────────────────────────────────
  // Helpers
  // ──────────────────────────────────────────────────────────────────────────

  /** Navigate to /staff and wait until the sidebar appears (guard passed). */
  async function gotoStaff(p: import('@playwright/test').Page) {
    await p.goto('/staff');
    // <aside> is only rendered when StaffGuard is satisfied (user.role===staff)
    await p.waitForSelector('aside', { timeout: 20_000 });
  }

  /** Click a sidebar nav button by its visible label. */
  async function clickSidebar(p: import('@playwright/test').Page, label: string) {
    await p.click(`aside button:has-text("${label}")`);
    // Allow async useEffect calls to fire and state to flush
    await p.waitForTimeout(400);
  }

  /** Assert the selected section has visible content and no error boundary. */
  async function assertSectionAlive(
    p: import('@playwright/test').Page,
    label: string,
  ) {
    const main = p.locator('main');

    // Must not show a React error boundary
    const errorBoundaryCount = await main
      .locator('text=/Something went wrong|failed to load|could not be loaded/i')
      .count();
    expect(
      errorBoundaryCount,
      `"${label}" must not show an error boundary message`,
    ).toBe(0);

    await expect(
      p.getByTestId(`admin-nav-${sectionIdForLabel(label)}`),
      `"${label}" navigation item must be selected`,
    ).toHaveClass(/bg-violet-50/);
    await expect(main, `"${label}" must not render a blank page`).not.toBeEmpty();
  }

  const sectionIdForLabel = (label: string) => {
    const match = ALL_SECTIONS.find((section) => section.label === label);
    if (!match) throw new Error(`Unknown staff section: ${label}`);
    return match.id;
  };

  const ALL_SECTIONS = STAFF_PORTAL_SECTIONS;

  // ──────────────────────────────────────────────────────────────────────────
  // Per-section tests
  // ──────────────────────────────────────────────────────────────────────────

  test('active break-glass status shows the operator warning and recovery controls', async ({ page }) => {
    // Playwright uses the most recently registered matching route first. This
    // intentionally overrides the shared inactive shell fixture without
    // contacting the real admin status endpoint.
    await page.route('**/api/v1/admin/break-glass-status', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ active: true }),
      }),
    );

    await gotoStaff(page);

    const banner = page.getByTestId('break-glass-banner');
    await expect(banner).toBeVisible();
    await expect(banner).toContainText(/Cloudflare Access is bypassed/i);
    await expect(banner).toContainText(/Break-glass mode is active/i);
    await expect(page.getByTestId('break-glass-banner-recheck')).toBeVisible();

    const runbook = page.getByTestId('break-glass-banner-runbook');
    await expect(runbook).toBeVisible();
    await expect(runbook).toHaveAttribute('href', /cloudflare-access-break-glass\.md/);
    await expect(page.getByTestId('break-glass-banner-stale')).toHaveCount(0);
    expect(consoleErrors, 'No uncaught console errors in the active break-glass preview').toHaveLength(0);
  });

  test('Dashboard section renders without blank page or uncaught errors', async ({ page }) => {
    staffMocks.enableStrictUnexpectedApiRequests();
    await gotoStaff(page);
    await clickSidebar(page, 'Dashboard');
    await assertSectionAlive(page, 'Dashboard');
    expect(consoleErrors, 'No uncaught console errors on Dashboard').toHaveLength(0);
  });

  test('Analytics section renders without blank page or uncaught errors', async ({ page }) => {
    staffMocks.enableStrictUnexpectedApiRequests();
    await gotoStaff(page);
    await clickSidebar(page, 'Analytics');
    await assertSectionAlive(page, 'Analytics');
    expect(consoleErrors, 'No uncaught console errors on Analytics').toHaveLength(0);
  });

  test('Users section renders without blank page or uncaught errors', async ({ page }) => {
    staffMocks.enableStrictUnexpectedApiRequests();
    await gotoStaff(page);
    await clickSidebar(page, 'Users');
    await assertSectionAlive(page, 'Users');
    expect(consoleErrors, 'No uncaught console errors on Users').toHaveLength(0);
  });

  test('Conversations section renders without blank page or uncaught errors', async ({ page }) => {
    staffMocks.enableStrictUnexpectedApiRequests();
    await gotoStaff(page);
    await clickSidebar(page, 'Conversations');
    await assertSectionAlive(page, 'Conversations');
    expect(consoleErrors, 'No uncaught console errors on Conversations').toHaveLength(0);
  });

  test('Referral ROI dashboard covers the protected healthy fixture and endpoint', async ({ page }) => {
    const roiRequests: string[] = [];
    await page.route('**/api/v1/admin/referrals/roi/dashboard*', (route) => {
      roiRequests.push(new URL(route.request().url()).pathname);
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          inventory: [{
            network: 'adsense',
            status: 'enabled',
            configured: true,
            policy_notes: 'Only provider-finalized AdSense reports fund rewards.',
            contributes_to_revenue: true,
          }],
          controls: {
            evidence_id: 'roi-release-control',
            reserve_healthy: true,
            revenue_fresh: true,
            invalid_traffic_healthy: true,
            ad_account_healthy: true,
            contribution_margin_healthy: true,
            identity_resets_healthy: true,
            fraud_healthy: true,
            exposure_healthy: true,
            warnings: [],
            expires_at: Math.floor(Date.now() / 1000) + 3600,
          },
          reports: [{
            id: 'roi-release-week',
            week_key: '2026-W36',
            data_quality: 'healthy',
            pause_recommended: false,
            referral_clicks: 12,
            unique_browser_identities: 9,
            authenticated_accounts: 6,
            mature_verified_visitors: 4,
            repeat_week_visitors: 2,
            payable_statements: 3,
            cash_paid_inr: 120,
            actual_monetized_impressions: 432,
            finalized_net_ad_revenue_paise: 22000,
            true_program_cost_inr: 120,
            contribution_margin_paise: 10000,
            payback_ratio_milli: 1833,
            warnings_json: '[]',
          }],
        }),
      });
    });

    await gotoStaff(page);
    await clickSidebar(page, 'Referral ROI');

    await expect(page.getByRole('heading', { name: 'Ad-funded referral ROI' })).toBeVisible();
    await expect(page.getByText('Production ad inventory')).toBeVisible();
    await expect(page.getByText('adsense', { exact: true })).toBeVisible();
    await expect(page.getByText('adsterra', { exact: true })).toHaveCount(0);
    await expect(page.getByText('Safety controls')).toBeVisible();
    await expect(page.getByText('Weekly unit economics')).toBeVisible();
    for (const metric of ['Week', 'Quality', 'Clicks', 'Browsers', 'Accounts', 'Mature', 'Repeat', 'Payable', 'Paid', 'Impressions', 'Net ad revenue', 'Cost', 'Margin', 'Payback', 'Warnings']) {
      await expect(page.getByRole('columnheader', { name: metric, exact: true })).toBeVisible();
    }
    await expect(page.getByTestId('referral-roi-pause-recommendation')).toHaveCount(0);
    expect(roiRequests.length).toBeGreaterThan(0);
    expect(roiRequests.every(path => path === '/api/v1/admin/referrals/roi/dashboard')).toBeTruthy();
    expect(consoleErrors, 'No uncaught console errors on Referral ROI').toHaveLength(0);
  });

  test('Referral ROI denies provider data without referral:settle', async ({ page }) => {
    const roiRequests: string[] = [];
    await page.route('**/api/v1/users/me', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ...STAFF_USER, capabilities: [] }),
      }),
    );
    await page.route('**/api/v1/admin/referrals/roi/dashboard*', (route) => {
      roiRequests.push(new URL(route.request().url()).pathname);
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          inventory: [{
            network: 'adsense',
            status: 'enabled',
            configured: true,
            policy_notes: 'Provider-finalized only.',
            contributes_to_revenue: true,
          }],
          controls: null,
          reports: [],
        }),
      });
    });

    await gotoStaff(page);
    await clickSidebar(page, 'Referral ROI');
    await expect(page.getByTestId('referral-roi-forbidden')).toBeVisible();
    await expect(page.getByText('Referral ROI is restricted.')).toBeVisible();
    await expect(page.getByText('Production ad inventory')).toHaveCount(0);
    expect(roiRequests).toHaveLength(0);
    expect(consoleErrors, 'No uncaught console errors in the restricted ROI preview').toHaveLength(0);
  });

  test('Referral ROI shows a pause recommendation for missing, stale, and negative-margin evidence', async ({ page }) => {
    const roiRequests: string[] = [];
    await page.route('**/api/v1/admin/referrals/roi/dashboard*', (route) => {
      roiRequests.push(new URL(route.request().url()).pathname);
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          inventory: [],
          controls: null,
          reports: [
            {
              id: 'stale-provider-revenue',
              week_key: '2026-W36',
              data_quality: 'blocked',
              pause_recommended: true,
              warnings_json: '["provider-revenue-stale"]',
            },
            {
              id: 'negative-margin',
              week_key: '2026-W35',
              data_quality: 'blocked',
              pause_recommended: true,
              contribution_margin_paise: -100,
              warnings_json: '["negative-contribution-margin"]',
            },
          ],
        }),
      });
    });

    await gotoStaff(page);
    await clickSidebar(page, 'Referral ROI');
    await expect(page.getByRole('heading', { name: 'Ad-funded referral ROI' })).toBeVisible();
    await expect(page.getByTestId('referral-roi-pause-recommendation')).toContainText('roi-control-evidence-missing');
    await expect(page.getByTestId('referral-roi-pause-recommendation')).toContainText('provider-revenue-stale');
    await expect(page.getByTestId('referral-roi-pause-recommendation')).toContainText('negative-contribution-margin');
    await expect(page.getByText('2026-W35')).toBeVisible();
    expect(roiRequests.length).toBeGreaterThan(0);
    expect(consoleErrors, 'No uncaught console errors for risky ROI states').toHaveLength(0);
  });

  test('Referral ROI clears stale warnings after refreshing with healthy evidence', async ({ page }) => {
    let roiRequestCount = 0;
    await page.route('**/api/v1/admin/referrals/roi/dashboard*', (route) => {
      roiRequestCount += 1;
      const blocked = roiRequestCount <= 2;
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(blocked
          ? {
              inventory: [],
              controls: null,
              reports: [{
                id: 'blocked-roi-week',
                week_key: '2026-W36',
                data_quality: 'blocked',
                pause_recommended: true,
                warnings_json: '["provider-revenue-stale","negative-contribution-margin"]',
                contribution_margin_paise: -100,
              }],
            }
          : {
              inventory: [{
                network: 'adsense',
                status: 'enabled',
                configured: true,
                policy_notes: 'Only provider-finalized AdSense reports fund rewards.',
                contributes_to_revenue: true,
              }],
              controls: {
                evidence_id: 'roi-fresh-control',
                reserve_healthy: true,
                revenue_fresh: true,
                invalid_traffic_healthy: true,
                ad_account_healthy: true,
                contribution_margin_healthy: true,
                identity_resets_healthy: true,
                fraud_healthy: true,
                exposure_healthy: true,
                warnings: [],
                expires_at: Math.floor(Date.now() / 1000) + 3600,
              },
              reports: [{
                id: 'healthy-roi-week',
                week_key: '2026-W37',
                data_quality: 'healthy',
                pause_recommended: false,
                referral_clicks: 24,
                unique_browser_identities: 18,
                authenticated_accounts: 11,
                mature_verified_visitors: 8,
                repeat_week_visitors: 5,
                payable_statements: 6,
                cash_paid_inr: 180,
                actual_monetized_impressions: 864,
                finalized_net_ad_revenue_paise: 39000,
                true_program_cost_inr: 180,
                contribution_margin_paise: 21000,
                payback_ratio_milli: 2167,
                warnings_json: '[]',
              }],
            }),
      });
    });

    await gotoStaff(page);
    await clickSidebar(page, 'Referral ROI');
    const pauseRecommendation = page.getByTestId('referral-roi-pause-recommendation');
    await expect(pauseRecommendation).toContainText('roi-control-evidence-missing');
    await expect(pauseRecommendation).toContainText('provider-revenue-stale');
    await expect(pauseRecommendation).toContainText('negative-contribution-margin');

    await page.getByTestId('button-refresh-referral-roi').click();

    await expect(pauseRecommendation).toHaveCount(0);
    await expect(page.getByText('roi-control-evidence-missing')).toHaveCount(0);
    await expect(page.getByText('provider-revenue-stale')).toHaveCount(0);
    await expect(page.getByText('negative-contribution-margin')).toHaveCount(0);
    await expect(page.getByTestId('referral-roi-inventory')).toContainText('adsense');
    await expect(page.getByTestId('referral-roi-unit-economics')).toContainText('2026-W37');
    await expect(page.getByTestId('referral-roi-unit-economics')).toContainText('₹210.00');
    expect(roiRequestCount).toBe(3);
    expect(consoleErrors, 'No uncaught console errors after healthy ROI refresh').toHaveLength(0);
  });

  // ──────────────────────────────────────────────────────────────────────────
  // Interactive controls test — exercises state-setter props (setSeoLive,
  // setR2Health) that were missing from ctx before the widget prop-scoping fix.
  // Clicking these controls would throw "X is not a function" if the setters
  // were not supplied via props.
  // ──────────────────────────────────────────────────────────────────────────

  test('Dashboard interactive controls (Probe now, R2 re-evaluate) complete without crashing', async ({ page }) => {
    staffMocks.enableStrictUnexpectedApiRequests();
    // Override r2-storage-health to return a configured state so the
    // "Re-evaluate now" button is enabled (it's disabled when configured===false)
    await page.route('**/api/v1/admin/r2-storage-health*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          configured: true,
          state: { watchdog_blind: false, last_evaluated_at: null },
          buckets: [],
        }),
      }),
    );
    // POST endpoints triggered by the interactive controls
    await page.route('**/api/v1/admin/r2-storage-health/run', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ state: { watchdog_blind: false, last_evaluated_at: new Date().toISOString() } }),
      }),
    );
    await page.route('**/api/v1/admin/seo/health/live*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'ok', sitemaps: [], checked_at: new Date().toISOString() }),
      }),
    );

    await gotoStaff(page);
    await clickSidebar(page, 'Dashboard');
    await assertSectionAlive(page, 'Dashboard');

    // ── "Probe now" — exercises setSeoLive / setSeoLiveLoading / setSeoLiveError
    const probeBtn = page.locator('[data-testid="seo-live-refresh"]');
    await probeBtn.waitFor({ state: 'visible', timeout: 5000 }).catch(() => {});
    if (await probeBtn.isVisible()) {
      await probeBtn.click();
      await page.waitForTimeout(500);
      // Probe now changes to "Probing…" then back — no crash means setters worked
      const hasErrorBoundary = await page.locator('text=Something went wrong').count();
      expect(hasErrorBoundary, '"Probe now" must not trigger an error boundary').toBe(0);
    }

    // ── "Re-evaluate now" R2 — exercises setR2Health via onReevaluate callback
    const r2Btn = page.locator('[data-testid="r2-cold-storage-reevaluate"]');
    await r2Btn.waitFor({ state: 'visible', timeout: 5000 }).catch(() => {});
    if (await r2Btn.isVisible() && !(await r2Btn.isDisabled())) {
      await r2Btn.click();
      await page.waitForTimeout(500);
      const hasErrorBoundary = await page.locator('text=Something went wrong').count();
      expect(hasErrorBoundary, '"Re-evaluate now" must not trigger an error boundary').toBe(0);
    }

    expect(consoleErrors, 'No uncaught console errors during interactive controls test').toHaveLength(0);
  });

  test('authenticated staff can edit bilingual notes, format each field, save, and reindex', async ({ page }) => {
    const fixture = await setupContentEditorFixture(page);
    staffMocks.enableStrictUnexpectedApiRequests();

    await gotoStaff(page);
    await expect(page.getByRole('heading', { name: 'Subjects', exact: true })).toBeVisible();
    const filters = page.locator('main select');
    await filters.nth(0).selectOption('board-1');
    await filters.nth(1).selectOption('class-1');
    await filters.nth(2).selectOption('stream-1');
    await page.getByRole('button', { name: /Physics/ }).click();
    await expect(page.getByRole('heading', { name: 'Chapters', exact: true })).toBeVisible();
    await expect(page.getByText('Motion', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Edit', exact: true }).click();
    await expect(page.getByText(/Ch\. 1 · Motion/)).toBeVisible();
    await expect(page.getByText(/Cloudflare Access is bypassed/i)).toHaveCount(0);
    const editor = page.locator('div.fixed.inset-0');
    await editor.getByRole('button', { name: /^Notes/ }).click();

    const notesEnglish = editor.getByPlaceholder(/Study notes in English/);
    const notesAssamese = editor.getByPlaceholder(/অসমীয়া ভাষাত টোকা/);
    await notesEnglish.fill('## English heading\r\n\r\n• English bullet');
    await notesAssamese.fill('## অসমীয়া শিৰোনাম\r\n\r\n• অসমীয়া বিন্দু');

    await editor.getByTestId('format-pasted-english-notes').click();
    await expect(notesEnglish).toHaveValue('## English heading\n\n- English bullet');
    await expect(notesAssamese).toHaveValue('## অসমীয়া শিৰোনাম\r\n\r\n• অসমীয়া বিন্দু');

    await editor.getByTestId('format-pasted-assamese-notes').click();
    await expect(notesEnglish).toHaveValue('## English heading\n\n- English bullet');
    await expect(notesAssamese).toHaveValue('## অসমীয়া শিৰোনাম\n\n- অসমীয়া বিন্দু');

    const saveRequest = page.waitForRequest(request =>
      request.method() === 'PATCH' &&
      request.url().includes('/staff/content/chapter/chapter-1'),
    );
    await editor.getByRole('button', { name: 'Save Chapter', exact: true }).click();
    expect((await saveRequest).postDataJSON()).toMatchObject({
      notes_en: '## English heading\n\n- English bullet',
      notes_as: '## অসমীয়া শিৰোনাম\n\n- অসমীয়া বিন্দু',
    });

    await page.getByRole('button', { name: 'Edit', exact: true }).click();
    await expect(page.getByText(/Ch\. 1 · Motion/)).toBeVisible();
    await editor.getByRole('button', { name: /^Notes/ }).click();
    await editor.getByRole('button', { name: /^RAG/ }).click();
    await expect(page.getByText('RAG updated but not reindexed.')).toBeVisible();
    await expect(editor.getByText(/latest notes until you reindex/)).toBeVisible();

    const reindexRequest = page.waitForRequest(request =>
      request.method() === 'POST' &&
      request.url().includes('/staff/content/chapter/chapter-1/reindex?scope=notes'),
    );
    await page.getByRole('button', { name: 'Reindex now', exact: true }).click();
    await reindexRequest;
    await expect(page.getByText('Reindex', { exact: true })).toBeVisible();

    await editor.getByRole('button', { name: /^Questions/ }).click();
    await editor.getByRole('button', { name: /^RAG/ }).click();
    await expect(editor.getByText(/latest Q&A until you reindex/)).toBeVisible();
    await expect(page.getByText('RAG updated but not reindexed.')).toBeVisible();

    expect(fixture.hasRequest('PATCH', '/api/v1/staff/content/chapter/chapter-1')).toBeTruthy();
    expect(fixture.hasRequest('POST', '/api/v1/staff/content/chapter/chapter-1/reindex')).toBeTruthy();
    expect(consoleErrors, 'No uncaught console errors during bilingual chapter editing').toHaveLength(0);
  });

  // ──────────────────────────────────────────────────────────────────────────
  // Combined navigation test
  // ──────────────────────────────────────────────────────────────────────────

  test('every control-center section renders correctly when cycled in sequence', async ({ page }) => {
    staffMocks.enableStrictUnexpectedApiRequests();
    await gotoStaff(page);

    for (const { label } of ALL_SECTIONS) {
      await clickSidebar(page, label);
      await assertSectionAlive(page, label);
    }

    expect(
      consoleErrors,
      'No uncaught console errors while cycling through every section',
    ).toHaveLength(0);
  });
});
