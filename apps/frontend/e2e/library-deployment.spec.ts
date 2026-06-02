import { test, expect } from '@playwright/test';

const mockLibraryBundle = {
  boards: [
    { id: 'board1', name: 'AHSEC', slug: 'ahsec', classes: ['cls1', 'cls2'] },
  ],
  classes: [
    { id: 'cls1', name: 'Class 11', slug: 'class-11', board_id: 'board1' },
    { id: 'cls2', name: 'Class 12', slug: 'class-12', board_id: 'board1' },
  ],
  streams: [
    { id: 'str1', name: 'Science', slug: 'science', class_id: 'cls1' },
    { id: 'str2', name: 'Arts', slug: 'arts', class_id: 'cls2' },
  ],
  subjects: [
    {
      id: 'sub1',
      name: 'Physics',
      slug: 'physics',
      stream_id: 'str1',
      status: 'published',
      description: 'Advanced physics for Class 11',
      tags: ['physics', 'science'],
      icon: 'atom',
      gradient: 'from-blue-500 to-purple-500',
      chapter_count: 12,
      notes_count: 8,
      notes_pct: 67,
      seo_stats: { topic_count: 45 },
    },
    {
      id: 'sub2',
      name: 'Chemistry',
      slug: 'chemistry',
      stream_id: 'str1',
      status: 'published',
      description: 'Organic and inorganic chemistry',
      tags: ['chemistry', 'science'],
      icon: 'flask',
      gradient: 'from-green-500 to-teal-500',
      chapter_count: 15,
      notes_count: 10,
      notes_pct: 70,
      seo_stats: { topic_count: 50 },
    },
    {
      id: 'sub3',
      name: 'History',
      slug: 'history',
      stream_id: 'str2',
      status: 'published',
      description: 'World history and Indian history',
      tags: ['history', 'arts'],
      icon: 'book',
      gradient: 'from-amber-500 to-orange-500',
      chapter_count: 10,
      notes_count: 6,
      notes_pct: 60,
      seo_stats: { topic_count: 30 },
    },
  ],
};

const mockLibraryBundleFull = {
  ...mockLibraryBundle,
  chapters: [
    { id: 'ch1', title: 'Kinematics', slug: 'kinematics', subject_id: 'sub1', order: 1 },
    { id: 'ch2', title: 'Thermodynamics', slug: 'thermodynamics', subject_id: 'sub1', order: 2 },
    { id: 'ch3', title: 'Atomic Structure', slug: 'atomic-structure', subject_id: 'sub2', order: 1 },
    { id: 'ch4', title: 'Ancient Civilizations', slug: 'ancient-civilizations', subject_id: 'sub3', order: 1 },
  ],
};

test.describe('Library Page - Deployment Verification', () => {
  test.beforeEach(async ({ page }) => {
    // Mock slim bundle (first fetch)
    await page.route('**/api/v1/content/library-bundle?slim=1', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(mockLibraryBundle),
      });
    });

    // Mock full bundle (deferred fetch)
    await page.route('**/api/v1/content/library-bundle', async (route) => {
      const url = route.request().url();
      // Only handle requests without slim=1 (the full bundle)
      if (url.includes('slim=1')) return route.continue();
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(mockLibraryBundleFull),
      });
    });

    // Mock auth as unauthenticated
    await page.route('**/api/v1/users/me', async (route) => {
      await route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Not authenticated' }),
      });
    });

    // Mock saved subjects endpoint
    await page.route('**/api/v1/users/saved-subjects', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([]),
      });
    });

    // Mock any other API calls
    await page.route('**/api/v1/**', async (route) => {
      const url = route.request().url();
      if (
        url.includes('library-bundle') ||
        url.includes('users/me') ||
        url.includes('saved-subjects')
      ) {
        return route.fallback();
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({}),
      });
    });
  });

  test('renders heading and subheading', async ({ page }) => {
    await page.goto('/library');
    await expect(page.getByText('Educational Browser')).toBeVisible();
    await expect(page.getByText('For Assam Board Students')).toBeVisible();
  });

  test('renders subject cards with names from mock data', async ({ page }) => {
    await page.goto('/library');
    // Wait for subjects to render - use heading elements in subject cards
    // Subject cards render the name as an h3 element
    await expect(page.locator('h3').filter({ hasText: 'Physics' })).toBeVisible({ timeout: 10000 });
    await expect(page.locator('h3').filter({ hasText: 'Chemistry' })).toBeVisible();
    await expect(page.locator('h3').filter({ hasText: 'History' })).toBeVisible();
  });

  test('displays search input', async ({ page }) => {
    await page.goto('/library');
    const searchInput = page.locator('[data-testid="library-search-input"]');
    await expect(searchInput).toBeVisible();
  });

  test('displays browse subjects count text', async ({ page }) => {
    await page.goto('/library');
    // The text is "Browse 3 subjects . 0 chapters" (slim has no chapters)
    // After full bundle loads it would show chapter count
    await expect(page.getByText(/Browse\s+3\s+subjects/)).toBeVisible({ timeout: 10000 });
  });

  test('search filtering works - filters subjects by name', async ({ page }) => {
    await page.goto('/library');
    // Wait for initial render - use h3 headings in subject cards
    await expect(page.locator('h3').filter({ hasText: 'Physics' })).toBeVisible({ timeout: 10000 });
    await expect(page.locator('h3').filter({ hasText: 'Chemistry' })).toBeVisible();
    await expect(page.locator('h3').filter({ hasText: 'History' })).toBeVisible();

    // Type in search to filter
    const searchInput = page.locator('[data-testid="library-search-input"]');
    await searchInput.fill('Physics');

    // Physics should remain, others should be filtered out
    await expect(page.locator('h3').filter({ hasText: 'Physics' })).toBeVisible();
    // Wait for filter to take effect
    await expect(page.locator('h3').filter({ hasText: 'Chemistry' })).not.toBeVisible({ timeout: 5000 });
    await expect(page.locator('h3').filter({ hasText: 'History' })).not.toBeVisible();
  });

  test('shows error state when API returns 500', async ({ page }) => {
    // Override all library bundle routes to return 500
    await page.route('**/api/v1/content/library-bundle**', async (route) => {
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Internal server error' }),
      });
    });

    // Also intercept the static fallback path and content endpoints
    await page.route('**/static/library-bundle**', async (route) => {
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Internal server error' }),
      });
    });

    await page.goto('/library');
    // React-query retries 4 times with exponential backoff (1s, 2s, 4s, 8s)
    // Total wait ~15s before error state shows
    await expect(page.getByText('Failed to load library')).toBeVisible({ timeout: 30000 });
  });

  test('/library route alias works', async ({ page }) => {
    const response = await page.goto('/library');
    expect(response?.status()).toBeLessThan(400);
    await expect(page.getByText('Educational Browser')).toBeVisible({ timeout: 10000 });
  });
});
