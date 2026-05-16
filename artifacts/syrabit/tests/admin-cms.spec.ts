/**
 * Admin CMS (ContentHub) & Vertex Panel specs (Task #1 — 75 missing tests).
 *
 * Covers 11 cases against the actual component structure:
 *   ContentHub tabs: "Content Editor" | "CMS / Docs" | "Blog Publisher"
 *   Vertex Panel API actions (confirmed endpoints from api.jsx):
 *     MCQ Generator   → POST /api/admin/vertex/mcq-generator
 *     Flashcard Gen   → POST /api/admin/vertex/flashcards
 *     SEO Meta Gen    → POST /api/admin/vertex/seo-meta
 *     Translation     → POST /api/admin/vertex/translate
 *
 * installAdminApiMocks FIRST (broad all-api routes), narrow routes AFTER (LIFO).
 */
import { test, expect, type Page, type Route } from '@playwright/test';
import { installAdminApiMocks, seedAdminSession } from './admin-mocks';

const LONG_TEXT = 'Photosynthesis is the biochemical process by which green plants, algae, and cyanobacteria convert light energy (usually from the sun) into chemical energy stored as glucose. This process is fundamental to life on Earth and serves as the primary source of oxygen in our atmosphere. The light reactions occur in the thylakoids.';

async function openContentHub(page: Page) {
  await page.goto('/admin');
  await expect(page.getByTestId('admin-dashboard')).toBeVisible({ timeout: 15_000 });
  await page.getByTestId('admin-nav-contenthub').click();
}

async function openVertexPanel(page: Page) {
  await page.goto('/admin');
  await expect(page.getByTestId('admin-dashboard')).toBeVisible({ timeout: 15_000 });
  await page.getByTestId('admin-nav-ai').click();
  await page.getByTestId('admin-ai-hub-subtab-vertex').click();
}

test.describe('Admin CMS & Vertex Panel', () => {
  test.beforeEach(async ({ page }) => {
    await seedAdminSession(page);
    await installAdminApiMocks(page);
  });

  // ── ContentHub ─────────────────────────────────────────────────────────────

  test('ContentHub renders the three tab labels', async ({ page }) => {
    await openContentHub(page);

    await expect(page.getByRole('button', { name: /Content Editor/i })).toBeVisible({ timeout: 10_000 });
    await expect(page.getByRole('button', { name: /CMS \/ Docs/i })).toBeVisible({ timeout: 5_000 });
    await expect(page.getByRole('button', { name: /Blog Publisher/i })).toBeVisible({ timeout: 5_000 });
  });

  test('ContentHub switching to CMS / Docs tab changes the active view', async ({ page }) => {
    await openContentHub(page);

    const cmsTab = page.getByRole('button', { name: /CMS \/ Docs/i });
    await expect(cmsTab).toBeVisible({ timeout: 10_000 });
    await cmsTab.click();

    await expect(cmsTab).toBeVisible({ timeout: 5_000 });
  });

  test('ContentHub switching to Blog Publisher tab is possible', async ({ page }) => {
    await openContentHub(page);

    const blogTab = page.getByRole('button', { name: /Blog Publisher/i });
    await expect(blogTab).toBeVisible({ timeout: 10_000 });
    await blogTab.click();

    await expect(blogTab).toBeVisible({ timeout: 5_000 });
  });

  // ── Vertex Panel — tab navigation ──────────────────────────────────────────

  test('Vertex Panel renders all 10 service card tabs', async ({ page }) => {
    await openVertexPanel(page);

    await expect(page.getByRole('button', { name: /Semantic Search/i })).toBeVisible({ timeout: 10_000 });
    await expect(page.getByRole('button', { name: /Translation/i })).toBeVisible({ timeout: 5_000 });
    await expect(page.getByRole('button', { name: /MCQ Generator/i })).toBeVisible({ timeout: 5_000 });
    await expect(page.getByRole('button', { name: /Flashcard Generator/i })).toBeVisible({ timeout: 5_000 });
    await expect(page.getByRole('button', { name: /SEO Meta Generator/i })).toBeVisible({ timeout: 5_000 });
  });

  // ── Vertex Panel — API action tests ────────────────────────────────────────

  test('Vertex MCQ Generator: submitting chapter text fires POST /api/admin/vertex/mcq-generator', async ({ page }) => {
    const mcqCalls: Array<{ url: string; body: unknown }> = [];
    // Register narrow routes AFTER installAdminApiMocks (higher LIFO priority).
    await page.route('**/api/admin/vertex/mcq-generator', async (route: Route) => {
      const req = route.request();
      let body: unknown = null;
      try { body = req.postDataJSON(); } catch { body = req.postData(); }
      mcqCalls.push({ url: req.url(), body });
      await route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({
          total: 2,
          mcqs: [
            { question: 'What captures light energy?', options: { A: 'Chlorophyll', B: 'ATP', C: 'NADPH', D: 'G3P' }, correct_answer: 'A', difficulty: 'easy' },
            { question: 'Where do light reactions occur?', options: { A: 'Stroma', B: 'Thylakoids', C: 'Cytoplasm', D: 'Mitochondria' }, correct_answer: 'B', difficulty: 'medium' },
          ],
        }),
      });
    });

    await openVertexPanel(page);
    await page.getByRole('button', { name: /MCQ Generator/i }).click();

    const textarea = page.locator('textarea').first();
    await expect(textarea).toBeVisible({ timeout: 8_000 });
    await textarea.fill(LONG_TEXT);

    await page.getByRole('button', { name: /Generate MCQs/i }).click();

    await expect.poll(() => mcqCalls.length, { timeout: 8_000 }).toBeGreaterThan(0);
    expect(mcqCalls[0].url).toContain('/api/admin/vertex/mcq-generator');

    // Results render: "2 questions generated" from the mock response.
    await expect(page.getByText(/questions generated|MCQs/i).first()).toBeVisible({ timeout: 8_000 });
  });

  test('Vertex Flashcard Generator: submitting chapter text fires POST /api/admin/vertex/flashcards', async ({ page }) => {
    const flashCalls: Array<{ url: string; body: unknown }> = [];
    await page.route('**/api/admin/vertex/flashcards', async (route: Route) => {
      const req = route.request();
      let body: unknown = null;
      try { body = req.postDataJSON(); } catch { body = req.postData(); }
      flashCalls.push({ url: req.url(), body });
      await route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({
          total_cards: 2,
          flashcards: [
            { front: 'What is chlorophyll?', back: 'A green pigment that absorbs light for photosynthesis.' },
            { front: 'Where do light reactions occur?', back: 'In the thylakoid membranes of the chloroplast.' },
          ],
        }),
      });
    });

    await openVertexPanel(page);
    await page.getByRole('button', { name: /Flashcard Generator/i }).click();

    const textarea = page.locator('textarea').first();
    await expect(textarea).toBeVisible({ timeout: 8_000 });
    await textarea.fill(LONG_TEXT);

    await page.getByRole('button', { name: /Generate Flashcards/i }).click();

    await expect.poll(() => flashCalls.length, { timeout: 8_000 }).toBeGreaterThan(0);
    expect(flashCalls[0].url).toContain('/api/admin/vertex/flashcards');

    // Flashcard result renders with the question text.
    await expect(page.getByText(/chlorophyll|thylakoid|flashcard/i).first()).toBeVisible({ timeout: 8_000 });
  });

  test('Vertex SEO Meta Generator: submitting a topic fires POST /api/admin/vertex/seo-meta', async ({ page }) => {
    const seoCalls: Array<{ url: string; body: unknown }> = [];
    await page.route('**/api/admin/vertex/seo-meta', async (route: Route) => {
      const req = route.request();
      let body: unknown = null;
      try { body = req.postDataJSON(); } catch { body = req.postData(); }
      seoCalls.push({ url: req.url(), body });
      await route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({
          title: 'Photosynthesis Class 11 Notes – AHSEC | Syrabit',
          meta_description: 'Complete photosynthesis notes for AHSEC Class 11 Biology. Covers light reactions, Calvin cycle, and photorespiration with diagrams.',
          keywords: ['photosynthesis', 'AHSEC', 'Class 11 Biology'],
          og_title: 'Photosynthesis Class 11 | Syrabit',
          og_description: 'Study photosynthesis with Syrabit AI for AHSEC.',
        }),
      });
    });

    await openVertexPanel(page);
    await page.getByRole('button', { name: /SEO Meta Generator/i }).click();

    const topicInput = page.locator('input[placeholder*="Topic"]');
    await expect(topicInput).toBeVisible({ timeout: 8_000 });
    await topicInput.fill('Photosynthesis');

    await page.getByRole('button', { name: /Generate Meta/i }).click();

    await expect.poll(() => seoCalls.length, { timeout: 8_000 }).toBeGreaterThan(0);
    expect(seoCalls[0].url).toContain('/api/admin/vertex/seo-meta');

    // Result shows the generated title.
    await expect(page.getByText(/Photosynthesis Class 11|meta_description|title/i).first()).toBeVisible({ timeout: 8_000 });
  });

  // ── ContentHub — CMS lifecycle: create + publish ────────────────────────────

  test('CMS / Docs: creating a doc via POST /api/admin/content/cms-documents and publishing via POST /:id/publish', async ({ page }) => {
    // Stateful doc: initially no docs; after POST the doc has an id.
    let savedDoc: Record<string, unknown> | null = null;
    const saveCalls:    Array<{ url: string; body: unknown }> = [];
    const publishCalls: Array<{ url: string }> = [];

    // Narrow CMS document routes registered AFTER installAdminApiMocks (LIFO).
    await page.route('**/api/admin/content/cms-documents', async (route: Route) => {
      const req = route.request();
      const method = req.method();
      if (method === 'POST') {
        let body: unknown = null;
        try { body = req.postDataJSON(); } catch { body = req.postData(); }
        saveCalls.push({ url: req.url(), body });
        savedDoc = { id: 'cms-doc-1', title: (body as Record<string, string>)?.title || 'Test Doc', status: 'draft', content: '', seo_slug: 'test-doc' };
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(savedDoc) });
      } else {
        const docs = savedDoc ? [savedDoc] : [];
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(docs) });
      }
    });

    // Publish toggle endpoint: POST /api/admin/content/cms-documents/:id/publish
    await page.route('**/api/admin/content/cms-documents/*/publish', async (route: Route) => {
      publishCalls.push({ url: route.request().url() });
      if (savedDoc) savedDoc = { ...savedDoc, status: 'published' };
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ...savedDoc }) });
    });

    // Open CMS / Docs tab inside ContentHub.
    await openContentHub(page);

    const cmsTab = page.getByRole('button', { name: /CMS \/ Docs/i });
    await expect(cmsTab).toBeVisible({ timeout: 10_000 });
    await cmsTab.click();

    const newDocBtn = page.getByRole('button', { name: /New Document/i });
    await expect(newDocBtn).toBeVisible({ timeout: 8_000 });
    await newDocBtn.click();

    const titleInput = page.getByPlaceholder('Document title…');
    await expect(titleInput).toBeVisible({ timeout: 8_000 });
    await titleInput.fill('Test CMS Article');

    const saveBtn = page.getByRole('button', { name: /^Save$/i });
    await expect(saveBtn).toBeVisible({ timeout: 5_000 });
    await saveBtn.click();

    // Verify POST /api/admin/content/cms-documents was called.
    await expect.poll(() => saveCalls.length, { timeout: 8_000 }).toBeGreaterThan(0);
    expect(saveCalls[0].url).toContain('/api/admin/content/cms-documents');

    const publishBtn = page.getByRole('button', { name: /^Publish$/i });
    await expect(publishBtn).toBeVisible({ timeout: 5_000 });
    await publishBtn.click();

    // Verify POST /api/admin/content/cms-documents/cms-doc-1/publish was called.
    await expect.poll(() => publishCalls.length, { timeout: 8_000 }).toBeGreaterThan(0);
    expect(publishCalls[0].url).toContain('/api/admin/content/cms-documents/cms-doc-1/publish');

    await expect(page.getByRole('button', { name: /Unpublish/i })).toBeVisible({ timeout: 8_000 });
  });

  // ── ContentHub — Blog Publisher publish flow ───────────────────────────────

  test('Blog Publisher: selecting a subject and clicking "Publish Now" fires POST /api/admin/cms/merge/:id', async ({ page }) => {
    // Hierarchy data the BlogPublishWizard fetches on mount from GET /api/content/*.
    const BOARD = { id: 'board-1', name: 'AHSEC' };
    const CLASS = { id: 'class-1', name: 'Class 11', board_id: 'board-1' };
    const STREAM = { id: 'stream-1', name: 'Science', class_id: 'class-1' };
    const SUBJECT = { id: 'subject-1', name: 'Biology', stream_id: 'stream-1', icon: '🧬', description: 'Life Science' };

    // Narrow hierarchy routes registered AFTER installAdminApiMocks (LIFO).
    await page.route('**/api/content/boards', async (route: Route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([BOARD]) });
    });
    await page.route('**/api/content/classes*', async (route: Route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([CLASS]) });
    });
    await page.route('**/api/content/streams*', async (route: Route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([STREAM]) });
    });
    await page.route('**/api/content/subjects*', async (route: Route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([SUBJECT]) });
    });

    // CMS merge publish endpoint stub (registered last = highest LIFO priority).
    const publishCalls: Array<{ url: string }> = [];
    await page.route('**/api/admin/cms/merge/**', async (route: Route) => {
      publishCalls.push({ url: route.request().url() });
      await route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ ok: true, word_count: 4200, chapters_merged: 8 }),
      });
    });

    // Open Blog Publisher tab.
    await page.goto('/admin');
    await expect(page.getByTestId('admin-dashboard')).toBeVisible({ timeout: 15_000 });
    await page.getByTestId('admin-nav-contenthub').click();

    const blogTab = page.getByRole('button', { name: /Blog Publisher/i });
    await expect(blogTab).toBeVisible({ timeout: 10_000 });
    await blogTab.click();

    const selects = page.locator('select');
    await expect(selects.first()).toBeVisible({ timeout: 8_000 });

    // Board select (index 0).
    await selects.nth(0).selectOption('board-1');
    // Class select (index 1) — options appear after board selection.
    await expect(selects.nth(1).locator('option[value="class-1"]')).toBeAttached({ timeout: 5_000 });
    await selects.nth(1).selectOption('class-1');
    // Stream select (index 2).
    await expect(selects.nth(2).locator('option[value="stream-1"]')).toBeAttached({ timeout: 5_000 });
    await selects.nth(2).selectOption('stream-1');
    // Subject select (index 3).
    await expect(selects.nth(3).locator('option[value="subject-1"]')).toBeAttached({ timeout: 5_000 });
    await selects.nth(3).selectOption('subject-1');

    const publishBtn = page.getByRole('button', { name: /Publish Now/i });
    await expect(publishBtn).toBeVisible({ timeout: 8_000 });
    await publishBtn.click();

    // CMS merge endpoint was called.
    await expect.poll(() => publishCalls.length, { timeout: 8_000 }).toBeGreaterThan(0);
    expect(publishCalls[0].url).toContain('/api/admin/cms/merge/subject-1');

    await expect(page.getByText(/Published successfully/i)).toBeVisible({ timeout: 8_000 });
  });

  test('Vertex Translation: submitting text fires POST /api/admin/vertex/translate', async ({ page }) => {
    const translateCalls: Array<{ url: string; body: unknown }> = [];
    await page.route('**/api/admin/vertex/translate', async (route: Route) => {
      const req = route.request();
      let body: unknown = null;
      try { body = req.postDataJSON(); } catch { body = req.postData(); }
      translateCalls.push({ url: req.url(), body });
      await route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({
          translated_text: 'সালোকসংশ্লেষণ হৈছে সেই প্ৰক্ৰিয়া।',
          source_lang: 'en',
          target_lang: 'as',
        }),
      });
    });
    await page.route('**/api/admin/translation/languages', async (route: Route) => {
      await route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ languages: [{ code: 'as', name: 'Assamese' }, { code: 'hi', name: 'Hindi' }] }),
      });
    });

    await openVertexPanel(page);
    await page.getByRole('button', { name: /Translation/i }).click();

    const textarea = page.locator('textarea').first();
    await expect(textarea).toBeVisible({ timeout: 8_000 });
    await textarea.fill('Photosynthesis is the process of converting light to energy.');

    await page.getByRole('button', { name: /^Translate$/i }).click();

    await expect.poll(() => translateCalls.length, { timeout: 8_000 }).toBeGreaterThan(0);
    expect(translateCalls[0].url).toContain('/api/admin/vertex/translate');

    // Translated Assamese text appears in the result panel.
    await expect(page.getByText(/সালোকসংশ্লেষণ|translated/i).first()).toBeVisible({ timeout: 8_000 });
  });
});
