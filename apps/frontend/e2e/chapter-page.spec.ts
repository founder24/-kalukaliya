import { expect, test, type Page } from '@playwright/test';

const PUBLIC_ROUTE = '/ahsec/class-12/physics/public-image-fixture';
const PAGE_ONE_URL = 'https://cdn.fixture.test/public-page-1.png';
const PAGE_TWO_URL = 'https://cdn.fixture.test/public-page-2.png';
const ONE_BY_ONE_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
  'base64',
);

const fixtureChapter = {
  chapter_id: 'public-image-fixture',
  chapter_title: 'Image-backed chapter',
  title: 'Image-backed chapter',
  topic_title: 'Image-backed chapter',
  subject_name: 'Physics',
  subject_id: 'physics',
  board_name: 'AHSEC',
  class_name: 'Class 12',
  content_type: 'notes',
  content: [
    '# Image-backed notes',
    '',
    'These pages were saved by the staff chapter editor.',
    '',
    `![Page 1](${PAGE_ONE_URL})`,
    '',
    `![Page 2](${PAGE_TWO_URL})`,
  ].join('\n'),
  content_en: [
    '# Image-backed notes',
    '',
    'These pages were saved by the staff chapter editor.',
    '',
    `![Page 1](${PAGE_ONE_URL})`,
    '',
    `![Page 2](${PAGE_TWO_URL})`,
  ].join('\n'),
  notes_en: [
    '# Image-backed notes',
    '',
    'These pages were saved by the staff chapter editor.',
    '',
    `![Page 1](${PAGE_ONE_URL})`,
    '',
    `![Page 2](${PAGE_TWO_URL})`,
  ].join('\n'),
  published_topics: [],
};

async function installFixture(page: Page) {
  await page.route('**/api/v1/**', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({}),
  }));
  await page.route('**/api/v1/users/me', route => route.fulfill({
    status: 401,
    contentType: 'application/json',
    body: JSON.stringify({ detail: 'Not authenticated' }),
  }));
  await page.route('**/api/v1/content/chapter-by-slug/**', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(fixtureChapter),
  }));
  await page.route('**/api/v1/content/chapters/public-image-fixture/topic-pyqs**', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ total: 0, pyqs: [], mark_wise: {} }),
  }));
  await page.route('**/api/v1/content/chapters/public-image-fixture/topics-published**', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ topics: [] }),
  }));
  await page.route('**/api/v1/content/chapters/public-image-fixture/topics-related**', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ siblings: [], cross_chapter: [] }),
  }));
  await page.route('**/api/v1/content/chapters/public-image-fixture/pyq-images', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ papers: [] }),
  }));
  await page.route('**/api/v1/content/chapters/public-image-fixture/faq-jsonld', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ entries: [] }),
  }));
  await page.route(PAGE_ONE_URL, route => route.fulfill({
    status: 200,
    contentType: 'image/png',
    body: ONE_BY_ONE_PNG,
  }));
  await page.route(PAGE_TWO_URL, route => route.fulfill({
    status: 200,
    contentType: 'image/png',
    body: ONE_BY_ONE_PNG,
  }));
}

test('public English chapter notes render persisted uploaded page links as images', async ({ page }) => {
  await installFixture(page);
  await page.goto(`${PUBLIC_ROUTE}?tab=notes`);

  const content = page.locator('#chapter-content-top');
  await expect(page.getByRole('heading', { name: 'Image-backed chapter', exact: true })).toBeVisible();
  await expect(content.locator('img[alt="Page 1"]')).toHaveCount(1);
  await expect(content.locator('img[alt="Page 2"]')).toHaveCount(1);

  const pageOne = content.locator('img[alt="Page 1"]');
  const pageTwo = content.locator('img[alt="Page 2"]');
  await expect(pageOne).toHaveAttribute('src', PAGE_ONE_URL);
  await expect(pageTwo).toHaveAttribute('src', PAGE_TWO_URL);
  await expect(pageOne).toHaveJSProperty('naturalWidth', 1);
  await expect(pageTwo).toHaveJSProperty('naturalWidth', 1);
});