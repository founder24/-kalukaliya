import { test, expect } from '@playwright/test';

test.describe('Auth Flow - Deployment Verification', () => {
  test.beforeEach(async ({ page }) => {
    // Mock login endpoint
    await page.route('**/api/v1/auth/login', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          access_token: 'mock-jwt-token-12345',
          refresh_token: 'mock-refresh-token-67890',
          token_type: 'bearer',
        }),
      });
    });

    // Mock users/me - returns user profile (simulates authenticated state)
    await page.route('**/api/v1/users/me', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: 'user-abc-123',
          email: 'student@example.com',
          name: 'Test Student',
          plan: 'pro',
          subscription_tier: 'pro',
        }),
      });
    });

    // Mock any other API calls
    await page.route('**/api/v1/**', async (route) => {
      const url = route.request().url();
      if (url.includes('auth/login') || url.includes('users/me')) {
        return route.fallback();
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({}),
      });
    });
  });

  test('login page shows email and password fields', async ({ page }) => {
    await page.goto('/login');
    await expect(page.locator('input[type="email"], input[name="email"]')).toBeVisible({
      timeout: 10000,
    });
    await expect(page.locator('input[type="password"]')).toBeVisible();
  });

  test('can fill login form fields', async ({ page }) => {
    await page.goto('/login');

    const emailInput = page.locator('input[type="email"], input[name="email"]');
    const passwordInput = page.locator('input[type="password"]');

    await expect(emailInput).toBeVisible({ timeout: 10000 });
    await emailInput.fill('student@example.com');
    await passwordInput.fill('SecurePass123!');

    await expect(emailInput).toHaveValue('student@example.com');
    await expect(passwordInput).toHaveValue('SecurePass123!');
  });

  test('login API is called on form submission', async ({ page }) => {
    let loginCalled = false;
    let loginPayload: Record<string, unknown> = {};

    await page.route('**/api/v1/auth/login', async (route) => {
      loginCalled = true;
      const request = route.request();
      try {
        loginPayload = JSON.parse(request.postData() || '{}');
      } catch {
        loginPayload = {};
      }

      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          access_token: 'mock-jwt-token-12345',
          refresh_token: 'mock-refresh-token-67890',
          token_type: 'bearer',
        }),
      });
    });

    await page.goto('/login');

    const emailInput = page.locator('input[type="email"], input[name="email"]');
    const passwordInput = page.locator('input[type="password"]');

    await expect(emailInput).toBeVisible({ timeout: 10000 });
    await emailInput.fill('student@example.com');
    await passwordInput.fill('SecurePass123!');

    // Submit the form
    const submitBtn = page.locator(
      'button[type="submit"], button:has-text("Log in"), button:has-text("Sign in")'
    );
    await expect(submitBtn).toBeVisible();

    // Set up response listener before clicking to avoid race condition
    const responsePromise = page.waitForResponse(
      (resp) => resp.url().includes('/api/v1/auth/login') && resp.status() === 200
    );
    await submitBtn.click();

    // Wait for the API call to complete deterministically
    await responsePromise;

    expect(loginCalled).toBe(true);
    expect(loginPayload.email).toBe('student@example.com');
    expect(loginPayload.password).toBe('SecurePass123!');
  });

  test('successful login navigates away from /login', async ({ page }) => {
    await page.goto('/login');

    const emailInput = page.locator('input[type="email"], input[name="email"]');
    const passwordInput = page.locator('input[type="password"]');

    await expect(emailInput).toBeVisible({ timeout: 10000 });
    await emailInput.fill('student@example.com');
    await passwordInput.fill('SecurePass123!');

    const submitBtn = page.locator(
      'button[type="submit"], button:has-text("Log in"), button:has-text("Sign in")'
    );
    await expect(submitBtn).toBeVisible();
    await submitBtn.click();

    // After successful login, should navigate away from /login
    await page.waitForURL((url) => !url.pathname.includes('/login'), { timeout: 10000 });
    expect(page.url()).not.toContain('/login');
  });
});
