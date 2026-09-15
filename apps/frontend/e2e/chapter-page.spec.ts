import { expect, test, type Page } from '@playwright/test';

const PUBLIC_ROUTE = '/ahsec/class-12/physics/public-image-fixture';
const PAGE_ONE_URL = 'https://cdn.fixture.test/public-page-1.png';
const PAGE_TWO_URL = 'https://cdn.fixture.test/public-page-2.png';
const ONE_BY_ONE_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
  'base64',
);

const ASSAMESE_SUBJECT_ROUTE = '/as/ahsec/hs-1st-year/physics';
const ASSAMESE_CHAPTER_ROUTE = `${ASSAMESE_SUBJECT_ROUTE}/goti`;

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

const assameseChapter = {
  chapter_id: 'as-chapter-goti',
  chapter_title: 'গতি',
  title: 'Motion',
  topic_title: 'Motion',
  subject_name: 'পদাৰ্থবিজ্ঞান',
  subject_id: 'as-subject-physics',
  board_name: 'AHSEC',
  class_name: 'HS 1st Year',
  slug: 'motion',
  slug_as: 'goti',
  content_type: 'notes',
  content: '# Motion\n\nAssamese route fixture notes.',
  content_en: '# Motion\n\nEnglish route fixture notes.',
  notes_en: '# Motion\n\nEnglish route fixture notes.',
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
  await expect(page.getByRole('heading', { name: 'গতি', exact: true })).toBeVisible();
  await expect.poll(() => requests.chapter.find((url) => url.includes('chapter-by-slug-as/'))).toBeTruthy();

  await page.getByRole('button', { name: 'প্ৰশ্নোত্তৰ', exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`${ASSAMESE_CHAPTER_ROUTE}\\?tab=qa$`));
});