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
async function setupContentEditorFixture(
  page: import('@playwright/test').Page,
  { includeSecondChapter = false }: { includeSecondChapter?: boolean } = {},
) {
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
  let chapters = [
    {
      id: 'chapter-1',
      subject_id: 'subject-1',
      title: 'Motion',
      slug: 'motion',
      description: 'Motion fundamentals',
      status: 'published',
      content_type: 'notes',
      content: 'Raw Motion notes with a pasted table.',
      notes_en: 'Raw Motion notes with a pasted table.',
      notes_generated: false,
      chapter_number: 1,
      version: 0,
    },
    ...(includeSecondChapter ? [{
      id: 'chapter-2',
      subject_id: 'subject-1',
      title: 'Energy',
      slug: 'energy',
      description: 'Energy fundamentals',
      status: 'published',
      content_type: 'notes',
      content: 'Raw Energy notes with a pasted table.',
      notes_en: 'Raw Energy notes with a pasted table.',
      notes_generated: false,
      chapter_number: 2,
      version: 0,
    }] : []),
  ];
  let chapterPages: Array<{
    id: string;
    title: string;
    year: number;
    url: string;
    uploaded_at: string;
  }> = [];
  let nextChapterPageId = 1;
  const chapterPageImage =
    'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=';

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
      return json(route, { chapters: chapters.filter(chapter => chapter.subject_id === subjectId) });
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
    const chapterId = path.split('/').pop();
    const chapter = chapters.find(item => item.id === chapterId);
    if (route.request().method() === 'GET') return json(route, chapter || {}, chapter ? 200 : 404);
    if (route.request().method() === 'PATCH') {
      const input = bodyOf(route);
      chapters = chapters.map(item => item.id === chapterId
        ? {
            ...item,
            ...input,
            // The chapter list still renders the legacy content field, while
            // the formatter intentionally persists notes_en. Mirror the
            // backend's normalized response so reload tests read saved notes.
            ...(input.notes_en !== undefined ? { content: input.notes_en } : {}),
            version: (item.version || 0) + 1,
          }
        : item);
      return json(route, chapters.find(item => item.id === chapterId) || {});
    }
    if (route.request().method() === 'DELETE') {
      chapters = chapters.filter(item => item.id !== chapterId);
      return json(route, { ok: true });
    }
    return json(route, {});
  });
  await page.route('**/api/v1/admin/content/format-text', route => {
    record(route);
    const input = bodyOf(route);
    const source = String(input.text || '');
    const title = source.includes('Energy') ? 'Energy' : 'Motion';
    return json(route, {
      formatted_text: `## Formatted ${title}\n\nCleaned notes with a normalized table.`,
    });
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
  await page.route('**/api/v1/content/chapters/*/pyq-images', route => {
    record(route);
    return json(route, { papers: chapterPages });
  });
  await page.route('**/api/v1/staff/content/chapter/*/pyq-papers', route => {
    record(route);
    if (route.request().method() !== 'POST') return json(route, {}, 405);
    const paper = {
      id: `chapter-page-${nextChapterPageId++}`,
      title: 'HS 2025 Page 1',
      year: 2025,
      url: chapterPageImage,
      uploaded_at: new Date().toISOString(),
    };
    chapterPages = [...chapterPages, paper];
    return json(route, { ok: true, paper, pyq_papers: chapterPages }, 201);
  });
  await page.route('**/api/v1/staff/content/chapter/*/pyq-papers/*', route => {
    record(route);
    if (route.request().method() !== 'DELETE') return json(route, {}, 405);
    const pageId = pathOf(route).split('/').pop();
    chapterPages = chapterPages.filter(page => page.id !== pageId);
    return json(route, { ok: true, pyq_papers: chapterPages });
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

  /** Navigate to /staff and wait until the authenticated staff shell appears. */
  async function gotoStaff(p: import('@playwright/test').Page) {
    await p.goto('/staff');
    // The desktop sidebar is hidden at mobile widths, so use the shared shell
    // marker rather than requiring a visible desktop-only element.
    await p.waitForSelector('[data-testid="admin-dashboard"]', { timeout: 20_000 });
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

  test('authenticated Content Editor completes isolated subject and chapter CRUD', async ({ page }) => {
    const fixture = await setupContentEditorFixture(page);
    staffMocks.enableStrictUnexpectedApiRequests();

    await gotoStaff(page);
    await clickSidebar(page, 'Content Editor');
    await expect(page.getByTestId('content-hub-panel-editor')).toBeVisible();
    await expect(page.getByText('All-in-One Content Manager')).toBeVisible();

    // Navigate the seeded hierarchy and prove the chapter list is populated.
    await page.getByRole('button', { name: /AHSEC/ }).click();
    await page.getByRole('button', { name: /Class 12/ }).click();
    await page.getByRole('button', { name: /Science/ }).click();
    await expect(page.getByTestId('subject-card-subject-1')).toContainText('Physics');

    // Subject create, status, edit, and delete all use the isolated fixture.
    await page.getByTestId('add-subject').click();
    await page.getByPlaceholder('Subject name...').fill('Chemistry');
    await page.getByRole('button', { name: 'Create', exact: true }).click();
    await expect(page.getByTestId('subject-card-subject-2')).toContainText('Chemistry');

    await page.getByTestId('subject-status-toggle-subject-2-trigger').click();
    await page.getByTestId('subject-status-toggle-subject-2-option-draft').click();
    await expect(page.getByTestId('subject-status-toggle-subject-2-trigger')).toContainText('Draft');

    const chemistryCard = page.getByTestId('subject-card-subject-2');
    await page.getByTestId('edit-subject-subject-2').click();
    await chemistryCard.locator('input:not([type="checkbox"])').first().fill('Chemistry Updated');
    const subjectPatch = page.waitForRequest(request =>
      request.method() === 'PATCH' && request.url().includes('/staff/content/subjects/subject-2'),
    );
    await chemistryCard.getByRole('button', { name: 'Save', exact: true }).click();
    expect((await subjectPatch).postDataJSON()).toMatchObject({ name: 'Chemistry Updated' });
    await expect(page.getByTestId('subject-card-subject-2')).toContainText('Chemistry Updated');

    await page.getByTestId('delete-subject-subject-2').click();
    await page.getByRole('button', { name: 'Delete', exact: true }).click();
    await expect(page.getByTestId('subject-card-subject-2')).toHaveCount(0);

    // Select the seeded subject, create a chapter, and confirm it appears
    // after the editor refreshes its list.
    await page.getByTestId('subject-card-subject-1').click();
    await expect(page.getByText('Chapters (1)')).toBeVisible();
    await page.getByTestId('create-chapter').click();
    await page.getByPlaceholder('Chapter title').fill('Work and Energy');
    await page.getByRole('button', { name: 'Create', exact: true }).click();
    await expect(page.getByText('Work and Energy')).toBeVisible();

    // Edit and status changes update the visible card after successful requests.
    await page.getByTestId('edit-chapter-chapter-1').click();
    await expect(page.getByText('Edit Chapter')).toBeVisible();
    await page.getByPlaceholder('Chapter title').fill('Work and Energy — Updated');
    await page.getByRole('button', { name: 'Update', exact: true }).click();
    await expect(page.getByText('Work and Energy — Updated')).toBeVisible();

    await page.getByTestId('chapter-status-toggle-chapter-1-trigger').click();
    await page.getByTestId('chapter-status-toggle-chapter-1-option-draft').click();
    await expect(page.getByTestId('chapter-status-toggle-chapter-1-trigger')).toContainText('Draft');

    // Generated notes are fetched back into the chapter card instead of only
    // showing a successful toast.
    await page.getByTestId('generate-notes-chapter-1').click();
    await expect(page.getByText('Notes', { exact: true })).toBeVisible();

    await page.getByTestId('delete-chapter-chapter-1').click();
    await page.getByRole('button', { name: 'Delete', exact: true }).click();
    await expect(page.getByText('Work and Energy — Updated')).toHaveCount(0);

    expect(fixture.hasRequest('GET', '/api/v1/staff/content/chapters/subject-1')).toBeTruthy();
    expect(fixture.hasRequest('POST', '/api/v1/staff/content/subjects')).toBeTruthy();
    expect(fixture.hasRequest('PATCH', '/api/v1/staff/content/subjects/subject-2')).toBeTruthy();
    expect(fixture.hasRequest('DELETE', '/api/v1/staff/content/subjects/subject-2')).toBeTruthy();
    expect(fixture.hasRequest('POST', '/api/v1/staff/content/chapters')).toBeTruthy();
    expect(fixture.hasRequest('PATCH', '/api/v1/staff/content/chapter/chapter-1')).toBeTruthy();
    expect(fixture.hasRequest('DELETE', '/api/v1/staff/content/chapter/chapter-1')).toBeTruthy();
    expect(fixture.hasRequest('POST', '/api/v1/admin/content/chapters/chapter-1/generate-notes')).toBeTruthy();
    expect(consoleErrors, 'No uncaught console errors during content CRUD').toHaveLength(0);
  });

  test('authenticated Content Editor regression preserves AI formatting and chapter PYQ pages', async ({ page }) => {
    const fixture = await setupContentEditorFixture(page, { includeSecondChapter: true });
    staffMocks.enableStrictUnexpectedApiRequests();

    const openContentEditorSubject = async () => {
      await gotoStaff(page);
      await clickSidebar(page, 'Content Editor');
      await expect(page.getByTestId('content-hub-panel-editor')).toBeVisible();
      const subjectSearch = page.getByTestId('search-subjects');
      await subjectSearch.fill('Physics');
      await page.getByTestId('search-result-subject-1').click();
      await expect(page.getByText('Chapters (2)')).toBeVisible();
    };

    await openContentEditorSubject();

    const aiFormatButtons = page.locator('[data-testid^="ai-format-"]');
    await expect(aiFormatButtons).toHaveCount(2);
    await expect(page.getByTestId('ai-format-chapter-1')).toBeVisible();
    await expect(page.getByTestId('ai-format-chapter-2')).toBeVisible();

    await page.getByTestId('ai-format-chapter-1').click();
    await expect(page.getByText('Formatted Motion', { exact: false })).toBeVisible();
    await page.getByTestId('ai-format-chapter-2').click();
    await expect(page.getByText('Formatted Energy', { exact: false })).toBeVisible();

    // The fixture persists PATCH results outside the page instance. Reloading
    // proves the formatter result was saved, not only painted into React state.
    await openContentEditorSubject();
    await expect(page.getByText('Formatted Motion', { exact: false })).toBeVisible();
    await expect(page.getByText('Formatted Energy', { exact: false })).toBeVisible();

    await page.getByTestId('edit-chapter-chapter-1').click();
    await expect(page.getByRole('heading', { name: 'Edit Chapter' })).toBeVisible();

    const pagesPanel = page.getByTestId('chapter-pyq-pages-panel');
    await expect(pagesPanel).toContainText('0 pages');
    await pagesPanel.getByPlaceholder('Optional, e.g. HS 2025').fill('HS 2025');
    await pagesPanel.locator('input[type="file"]').setInputFiles({
      name: 'hs-2025-page-1.png',
      mimeType: 'image/png',
      buffer: Buffer.from('isolated-pyq-page'),
    });

    await expect(pagesPanel.getByAltText('HS 2025 Page 1')).toBeVisible();
    await expect(pagesPanel).toContainText('1 page');

    await pagesPanel.getByTitle('Delete image page').click();
    await expect(pagesPanel.getByAltText('HS 2025 Page 1')).toHaveCount(0);
    await expect(pagesPanel).toContainText('No image pages uploaded yet');

    // Re-opening the chapter confirms deletion was persisted by the staff
    // route, rather than only removing the card from local component state.
    await page.getByRole('button', { name: 'Cancel', exact: true }).click();
    await page.getByTestId('edit-chapter-chapter-1').click();
    await expect(page.getByTestId('chapter-pyq-pages-panel')).toContainText('0 pages');

    expect(fixture.hasRequest('POST', '/api/v1/admin/content/format-text')).toBeTruthy();
    expect(fixture.hasRequest('PATCH', '/api/v1/staff/content/chapter/chapter-1')).toBeTruthy();
    expect(fixture.hasRequest('PATCH', '/api/v1/staff/content/chapter/chapter-2')).toBeTruthy();
    expect(fixture.hasRequest('GET', '/api/v1/content/chapters/chapter-1/pyq-images')).toBeTruthy();
    expect(fixture.hasRequest('POST', '/api/v1/staff/content/chapter/chapter-1/pyq-papers')).toBeTruthy();
    expect(fixture.hasRequest('DELETE', '/api/v1/staff/content/chapter/chapter-1/pyq-papers/chapter-page-1')).toBeTruthy();
    expect(consoleErrors, 'No uncaught console errors during the Content Editor regression').toHaveLength(0);
  });

  test('mobile Content Editor keeps navigation, formatting, and image pages reachable', async ({ page }) => {
    const fixture = await setupContentEditorFixture(page, { includeSecondChapter: true });
    staffMocks.enableStrictUnexpectedApiRequests();
    await page.setViewportSize({ width: 390, height: 844 });

    await gotoStaff(page);
    await expect(page.getByTestId('admin-mobile-menu')).toBeVisible();
    await page.getByTestId('admin-mobile-menu').click();
    await expect(page.getByTestId('admin-mobile-nav-contenthub')).toBeVisible();
    await page.getByTestId('admin-mobile-nav-contenthub').click();

    await page.getByTestId('mobile-select-board').selectOption('board-1');
    await page.getByTestId('mobile-select-class').selectOption('class-1');
    await page.getByTestId('mobile-select-stream').selectOption('stream-1');
    await page.getByTestId('mobile-select-subject').selectOption('subject-1');
    await expect(page.getByText('Chapters (2)')).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();

    await page.getByTestId('edit-chapter-chapter-1').click();
    await expect(page.getByTestId('ai-format-button')).toBeVisible();
    await page.getByTestId('ai-format-button').click();
    await expect(page.getByText('Formatted Motion', { exact: false })).toBeVisible();

    const pagesPanel = page.getByTestId('chapter-pyq-pages-panel');
    await expect(pagesPanel.getByTestId('upload-pyq-pages')).toBeVisible();
    await pagesPanel.locator('input[type="file"]').setInputFiles({
      name: 'mobile-page.png',
      mimeType: 'image/png',
      buffer: Buffer.from('mobile-pyq-page'),
    });
    await expect(pagesPanel.getByAltText('HS 2025 Page 1')).toBeVisible();

    expect(fixture.hasRequest('POST', '/api/v1/admin/content/format-text')).toBeTruthy();
    expect(fixture.hasRequest('POST', '/api/v1/staff/content/chapter/chapter-1/pyq-papers')).toBeTruthy();
    expect(consoleErrors, 'No uncaught console errors in the mobile Content Editor').toHaveLength(0);
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
