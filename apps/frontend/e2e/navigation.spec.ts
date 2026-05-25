import { test, expect } from '@playwright/test';

test.describe('Navigation', () => {
  test.beforeEach(async ({ page }) => {
    // Mock API calls to prevent network errors
    await page.route('**/api/**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({}),
      });
    });
  });

  test('home page loads successfully', async ({ page }) => {
    const response = await page.goto('/');
    expect(response?.status()).toBeLessThan(400);
    // Check that React rendered something
    await expect(page.locator('body')).not.toBeEmpty();
  });

  test('login page is accessible', async ({ page }) => {
    const response = await page.goto('/login');
    expect(response?.status()).toBeLessThan(400);
    await expect(page.locator('body')).not.toBeEmpty();
  });

  test('chat page is accessible', async ({ page }) => {
    const response = await page.goto('/chat');
    expect(response?.status()).toBeLessThan(400);
    await expect(page.locator('body')).not.toBeEmpty();
  });

  test('navigating between pages works', async ({ page }) => {
    await page.goto('/');
    // Find a link to chat or login and click it
    const chatLink = page.locator('a[href*="chat"]').first();
    if (await chatLink.isVisible()) {
      await chatLink.click();
      await expect(page).toHaveURL(/.*chat.*/);
    }
  });
});
