import { expect, test, type Page } from '@playwright/test';
import {
  ASSAMESE_PAGE_ONE_URL,
  ASSAMESE_PAGE_TWO_URL,
  assameseChapter,
  fixtureChapter,
  PAGE_ONE_URL,
  PAGE_TWO_URL,
  PUBLIC_ROUTE,
} from '../src/test/fixtures/public-image-chapter';
const ONE_BY_ONE_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
  'base64',
);

const ASSAMESE_SUBJECT_ROUTE = '/as/ahsec/hs-1st-year/physics';
const ASSAMESE_CHAPTER_ROUTE = `${ASSAMESE_SUBJECT_ROUTE}/goti`;

const assameseSubject = {
  id: 'as-subject-physics',
  name: 'Physics',
  name_as: 'পদাৰ্থবিজ্ঞান',
  board_name: 'AHSEC',
  class_name: 'HS 1st Year',
  description: 'Physics study material',
  description_as: 'পদাৰ্থবিজ্ঞানৰ অধ্যয়ন সামগ্ৰী',
  pyq_papers: [],
};

const assameseChapters = [
  {
    id: 'as-chapter-goti',
    title: 'Motion',
    title_as: 'গতি',
    slug: 'motion',
    slug_as: 'goti',
    chapter_number: 1,
    description: 'Motion notes',
    description_as: 'গতিৰ নোটছ',
    has_qa: true,
    has_qa_as: true,
    notes_generated: true,
  },
];

async function installFixture(page: Page, { pageTwoAvailable = false } = {}) {
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
    status: pageTwoAvailable ? 200 : 404,
    contentType: 'image/png',
    body: pageTwoAvailable ? ONE_BY_ONE_PNG : '',
  }));
}

async function installAssameseFixture(page: Page, requests: {
  subject: string[];
  chapter: string[];
}) {
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
  await page.route('**/api/v1/content/resolve-subject/**', route => {
    requests.subject.push(route.request().url());
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(assameseSubject),
    });
  });
  await page.route('**/api/v1/content/chapters/as-subject-physics', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(assameseChapters),
  }));
  await page.route('**/api/v1/content/subjects/as-subject-physics/topic-index', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ chapters: [], total_topics: 0 }),
  }));
  await page.route('**/api/v1/content/chapter-by-slug-as/**', route => {
    requests.chapter.push(route.request().url());
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(assameseChapter),
    });
  });
  await page.route(ASSAMESE_PAGE_ONE_URL, route => route.fulfill({
    status: 200,
    contentType: 'image/png',
    body: ONE_BY_ONE_PNG,
  }));
  await page.route(ASSAMESE_PAGE_TWO_URL, route => route.fulfill({
    status: 200,
    contentType: 'image/png',
    body: ONE_BY_ONE_PNG,
  }));
}

test('public English chapter notes show a fallback when a saved page image is unavailable', async ({ page }) => {
  await installFixture(page);
  await page.goto(`${PUBLIC_ROUTE}?tab=notes`);

  const content = page.locator('#chapter-content-top');
  await expect(page.getByRole('heading', { name: 'Image-backed chapter', exact: true })).toBeVisible();
  await expect(content.locator('img[alt="Page 1"]')).toHaveCount(1);

  const pageOne = content.locator('img[alt="Page 1"]');
  await expect(pageOne).toHaveAttribute('src', PAGE_ONE_URL);
  await expect(pageOne).toHaveJSProperty('naturalWidth', 1);
  const unavailablePage = content.getByTestId('markdown-image-fallback');
  await expect(unavailablePage).toBeVisible();
  await expect(unavailablePage).toHaveAttribute('role', 'alert');
  await expect(unavailablePage).toHaveText('Page 2 image is unavailable.');
  await expect(content.locator('img[alt="Page 2"]')).toHaveCount(0);
  await expect(content).toContainText('These pages were saved by the staff chapter editor.');
});

test('public English chapter notes preserve both saved page images through hydration', async ({ page }) => {
  await installFixture(page, { pageTwoAvailable: true });
  await page.goto(`${PUBLIC_ROUTE}?tab=notes`);

  const content = page.locator('#chapter-content-top');
  await expect(page.getByRole('heading', { name: 'Image-backed chapter', exact: true })).toBeVisible();
  await expect(content.locator('img[alt="Page 1"]')).toHaveAttribute('src', PAGE_ONE_URL);
  await expect(content.locator('img[alt="Page 2"]')).toHaveAttribute('src', PAGE_TWO_URL);
  await expect(content.locator('img[alt="Page 1"]')).toHaveJSProperty('naturalWidth', 1);
  await expect(content.locator('img[alt="Page 2"]')).toHaveJSProperty('naturalWidth', 1);
});

test('direct Assamese subject and chapter routes keep localized resolution and QA links', async ({ page }) => {
  const requests = { subject: [] as string[], chapter: [] as string[] };
  await installAssameseFixture(page, requests);

  await page.goto(ASSAMESE_SUBJECT_ROUTE);
  await expect(page.getByRole('heading', { name: 'পদাৰ্থবিজ্ঞান', exact: true })).toBeVisible();
  await expect.poll(() => requests.subject.find((url) => url.includes('lang=as'))).toBeTruthy();

  await page.getByRole('button', { name: /^প্ৰশ্ন/ }).click();
  const questionsChapter = page.locator(`a[href="${ASSAMESE_CHAPTER_ROUTE}?tab=qa"]`);
  await expect(questionsChapter).toHaveAttribute(
    'href',
    `${ASSAMESE_CHAPTER_ROUTE}?tab=qa`,
  );
  await questionsChapter.click();
  await expect(page).toHaveURL(new RegExp(`${ASSAMESE_CHAPTER_ROUTE}\\?tab=qa$`));

  await page.goto(ASSAMESE_CHAPTER_ROUTE);
  await expect(page.getByRole('banner').getByRole('heading', { name: 'গতি', exact: true })).toBeVisible();
  await expect.poll(() => requests.chapter.find((url) => url.includes('chapter-by-slug-as/'))).toBeTruthy();

  await page.getByRole('button', { name: 'প্ৰশ্নোত্তৰ', exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`${ASSAMESE_CHAPTER_ROUTE}\\?tab=qa$`));
});

test('localized Assamese chapter notes preserve both saved page images through hydration', async ({ page }) => {
  const requests = { subject: [] as string[], chapter: [] as string[] };
  await installAssameseFixture(page, requests);
  await page.goto(`${ASSAMESE_CHAPTER_ROUTE}?tab=notes`);

  const content = page.locator('#chapter-content-top');
  await expect(page.getByRole('banner').getByRole('heading', { name: 'গতি', exact: true })).toBeVisible();
  await expect(content.locator('img[alt="পৃষ্ঠা ১"]')).toHaveAttribute('src', ASSAMESE_PAGE_ONE_URL);
  await expect(content.locator('img[alt="পৃষ্ঠা ২"]')).toHaveAttribute('src', ASSAMESE_PAGE_TWO_URL);
  await expect(content.locator('img[alt="পৃষ্ঠা ১"]')).toHaveJSProperty('naturalWidth', 1);
  await expect(content.locator('img[alt="পৃষ্ঠা ২"]')).toHaveJSProperty('naturalWidth', 1);
});