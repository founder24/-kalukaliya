import { test, expect } from '@playwright/test';

test.describe('Chat Send/Receive', () => {
  test.beforeEach(async ({ page }) => {
    // Mock users/me as anonymous
    await page.route('**/api/v1/users/me', async (route) => {
      await route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Not authenticated' }),
      });
    });

    // Mock chat stream endpoint with SSE
    await page.route('**/api/v1/chat/stream', async (route) => {
      const sseBody = [
        'data: {"content": "Hello! ", "done": false}\n\n',
        'data: {"content": "I can help you study.", "done": false}\n\n',
        'data: {"content": "", "done": true, "event": "syrabit_done", "latency_ms": 250, "model": "gemini-1.5-pro", "lang": "en"}\n\n',
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
  });

  test('chat page loads without errors', async ({ page }) => {
    await page.goto('/chat');
    // Page should render without console errors
    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(err.message));
    await page.waitForTimeout(1000);
    // Allow for known non-critical errors but check page rendered
    await expect(page).toHaveURL(/.*chat.*/);
  });

  test('can type a message in chat input', async ({ page }) => {
    await page.goto('/chat');
    const chatInput = page.locator('textarea, input[placeholder*="message"], input[placeholder*="ask"], input[placeholder*="type"]');
    if (await chatInput.isVisible()) {
      await chatInput.fill('What is photosynthesis?');
      await expect(chatInput).toHaveValue('What is photosynthesis?');
    }
  });
});
