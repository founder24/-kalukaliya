import { test, expect } from '@playwright/test';

test.describe('Chat Page - Deployment Verification', () => {
  test.beforeEach(async ({ page }) => {
    // Mock users/me as anonymous
    await page.route('**/api/v1/users/me', async (route) => {
      await route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Not authenticated' }),
      });
    });

    // Mock chat stream endpoint with SSE response (content field, syrabit_done event)
    await page.route('**/api/v1/chat/stream', async (route) => {
      const sseBody = [
        'data: {"content": "Photosynthesis is the process by which plants convert sunlight into energy.", "done": false}\n\n',
        'data: {"content": "", "done": true, "event": "syrabit_done", "latency_ms": 320, "model": "gemini-1.5-pro", "lang": "en"}\n\n',
      ].join('');

      await route.fulfill({
        status: 200,
        headers: {
          'Content-Type': 'text/event-stream',
          'Cache-Control': 'no-store',
        },
        body: sseBody,
      });
    });

    // Mock any other API calls
    await page.route('**/api/v1/**', async (route) => {
      const url = route.request().url();
      if (url.includes('users/me') || url.includes('chat/stream')) {
        return route.fallback();
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({}),
      });
    });
  });

  test('chat page loads without errors', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(err.message));

    await page.goto('/chat');
    await expect(page).toHaveURL(/.*chat.*/);
    // The page should render the chat input area
    await expect(page.locator('[data-testid="chat-input"]')).toBeVisible({ timeout: 10000 });
  });

  test('message input is visible and can accept text', async ({ page }) => {
    await page.goto('/chat');
    // The textarea has aria-label "Type your message"
    const chatTextarea = page.locator('textarea[aria-label="Type your message"]');
    await expect(chatTextarea).toBeVisible({ timeout: 10000 });

    await chatTextarea.fill('What is photosynthesis?');
    await expect(chatTextarea).toHaveValue('What is photosynthesis?');
  });

  test('sending a message triggers SSE and response appears', async ({ page }) => {
    await page.goto('/chat');
    const chatTextarea = page.locator('textarea[aria-label="Type your message"]');
    await expect(chatTextarea).toBeVisible({ timeout: 10000 });

    // Type a message
    await chatTextarea.fill('What is photosynthesis?');

    // Send via Enter key
    await chatTextarea.press('Enter');

    // Wait for SSE response to render in the chat
    await expect(
      page.getByText('Photosynthesis is the process by which plants convert sunlight into energy', { exact: false })
    ).toBeVisible({ timeout: 10000 });
  });

  test('verifies chat stream API is called with correct method', async ({ page }) => {
    let streamCalled = false;
    let streamMethod = '';

    await page.route('**/api/v1/chat/stream', async (route) => {
      streamCalled = true;
      streamMethod = route.request().method();

      const sseBody = [
        'data: {"content": "Response text here.", "done": false}\n\n',
        'data: {"content": "", "done": true, "event": "syrabit_done", "latency_ms": 150, "model": "gemini-1.5-pro", "lang": "en"}\n\n',
      ].join('');

      await route.fulfill({
        status: 200,
        headers: {
          'Content-Type': 'text/event-stream',
          'Cache-Control': 'no-store',
        },
        body: sseBody,
      });
    });

    await page.goto('/chat');
    const chatTextarea = page.locator('textarea[aria-label="Type your message"]');
    await expect(chatTextarea).toBeVisible({ timeout: 10000 });

    await chatTextarea.fill('Hello');
    await chatTextarea.press('Enter');

    // Wait for the response to appear
    await expect(page.getByText('Response text here.', { exact: false })).toBeVisible({
      timeout: 10000,
    });

    expect(streamCalled).toBe(true);
    expect(streamMethod).toBe('POST');
  });
});
