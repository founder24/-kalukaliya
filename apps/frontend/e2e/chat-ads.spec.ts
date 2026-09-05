import { expect, test, type Page } from '@playwright/test';

test.skip(
  process.env.E2E_CHAT_ADS !== '1',
  'Run with pnpm test:e2e:chat-ads so the production-only ad gate is enabled.',
);

const desktop = { width: 1280, height: 800 };
const mobile = { width: 390, height: 844 };

async function installDeterministicChatStream(page: Page) {
  await page.addInitScript(() => {
    const originalFetch = window.fetch.bind(window);
    let chatRequestCount = 0;
    const encode = (value: object) =>
      new TextEncoder().encode(`data: ${JSON.stringify(value)}\n\n`);

    window.fetch = async (input, init) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
      if (!url.includes('/chat/stream')) return originalFetch(input, init);

      chatRequestCount += 1;
      const requestNumber = chatRequestCount;
      const stream = new ReadableStream({
        start(controller) {
          const firstContent = requestNumber === 1
            ? 'First answer is streaming.'
            : requestNumber === 2
              ? 'Second answer started before disconnect.'
              : 'Second answer completed after reconnect.';
          controller.enqueue(encode({ content: firstContent, done: false }));

          window.setTimeout(() => {
            if (requestNumber === 2) {
              controller.error(new TypeError('Simulated connection interruption'));
              return;
            }
            controller.enqueue(encode({
              content: '',
              done: true,
              event: 'syrabit_done',
              latency_ms: 400,
              model: 'e2e-model',
              lang: 'en',
            }));
            controller.close();
          }, 600);
        },
      });

      return new Response(stream, {
        status: 200,
        headers: {
          'Content-Type': 'text/event-stream',
          'Cache-Control': 'no-store',
          'X-Request-ID': `chat-ad-e2e-${requestNumber}`,
        },
      });
    };
  });
}

async function mockNonChatApis(page: Page) {
  await page.route('**/api/v1/users/me', (route) =>
    route.fulfill({
      status: 401,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'Not authenticated' }),
    }),
  );
  await page.route('https://pagead2.googlesyndication.com/**', (route) => route.abort());
  await page.route('**/api/v1/**', async (route) => {
    if (route.request().url().includes('/users/me')) return route.fallback();
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({}),
    });
  });
}

async function send(page: Page, text: string) {
  const input = page.locator('textarea[aria-label="Type your message"]');
  await input.fill(text);
  await input.press('Enter');
}

for (const [layout, viewport] of Object.entries({ desktop, mobile })) {
  test(`sponsored card waits for two completed turns and survives reconnect on ${layout}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await installDeterministicChatStream(page);
    await mockNonChatApis(page);
    await page.addInitScript(() => localStorage.setItem('syrabit_ads_optout', '1'));

    await page.goto('/chat');
    await expect(page.locator('[data-testid="chat-input"]')).toBeVisible();

    // Accept through the same visible consent control a visitor uses, then
    // enable the ads preference for this deterministic browser session.
    const cookieNotice = page.getByRole('heading', { name: 'Cookie Notice' });
    if (await cookieNotice.isVisible()) {
      await page.getByRole('button', { name: 'Accept', exact: true }).click();
    }
    await page.evaluate(() => {
      localStorage.removeItem('syrabit_ads_optout');
      window.dispatchEvent(new CustomEvent('syrabit:ads-consent-changed'));
    });

    await send(page, 'Give me the first answer');
    await expect(page.getByText('First answer is streaming.', { exact: true })).toBeVisible();
    await expect(page.locator('[data-testid="chat-scroll-spacer"]')).toBeVisible();
    await expect(page.locator('[data-testid^="chat-sponsored-"]')).toHaveCount(0);
    await expect(page.locator('[data-testid="chat-scroll-spacer"]')).toHaveCount(0);
    await expect(page.locator('[data-testid^="chat-sponsored-"]')).toHaveCount(0);

    await send(page, 'Give me the second answer');
    await expect(page.getByText('Second answer started before disconnect.', { exact: true })).toBeVisible();
    await expect(page.locator('[data-testid="chat-scroll-spacer"]')).toBeVisible();
    await expect(page.locator('[data-testid^="chat-sponsored-"]')).toHaveCount(0);

    // The failed attempt is replaced in place, then automatically retried with
    // the same logical message identity. Neither state may create an ad.
    await expect(page.locator('[data-testid="chat-scroll-spacer"]')).toHaveCount(0);
    await expect(page.locator('[data-testid^="chat-sponsored-"]')).toHaveCount(0);
    const reconnectedAnswer = page.getByText('Second answer completed after reconnect.', { exact: true });
    await expect(reconnectedAnswer).toBeVisible({ timeout: 8_000 });
    await expect(page.locator('[data-testid="chat-scroll-spacer"]')).toBeVisible();
    await expect(page.locator('[data-testid^="chat-sponsored-"]')).toHaveCount(0);
    await expect(page.locator('[data-testid="chat-scroll-spacer"]')).toHaveCount(0);

    const sponsored = page.getByRole('complementary', { name: 'Sponsored content' });
    await expect(sponsored).toHaveCount(1);
    await expect(sponsored).toContainText('Sponsored');
    await expect(sponsored).toHaveAttribute('data-testid', 'chat-sponsored-1');

    // The chat remains usable at both target sizes and no element forces the
    // page wider than its viewport.
    await expect(page.locator('textarea[aria-label="Type your message"]')).toBeVisible();
    const hasHorizontalOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
    );
    expect(hasHorizontalOverflow).toBe(false);
  });
}