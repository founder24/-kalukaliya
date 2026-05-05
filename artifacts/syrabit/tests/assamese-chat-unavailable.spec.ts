/**
 * Assamese chat unavailable card — end-to-end spec (Task #375).
 *
 * Drives the full flow Task #370 wired up:
 *   1. User picks Assamese mode in the language selector (seeded via
 *      localStorage so the page rehydrates with responseLang='as').
 *   2. Backend SSE stream emits `{error, error_kind: 'assamese_unavailable'}`.
 *   3. UI shows the localized অসমীয়া card (no auto-retry countdown,
 *      `data-testid="assamese-unavailable-card"`).
 *   4. Clicking the "Switch to English mode" button flips `responseLang`
 *      to en, persists `syrabit_response_lang=en` to localStorage, and
 *      re-sends the same query through the English chain — the second
 *      POST body must omit `response_lang` (the chat code drops the
 *      field when responseLang === 'en') and the assistant message must
 *      stream from the English fallback handler.
 *
 * Companion of MessageBubble.assamese-unavailable.test.jsx (vitest unit).
 */
import { test, expect, type Page, type Route } from '@playwright/test';

interface ChatStreamCall {
  body: Record<string, unknown> | null;
  responseLang: unknown;
}

async function installAssameseChatMocks(
  page: Page,
  opts: { seedAssamese?: boolean } = {},
): Promise<{ calls: ChatStreamCall[] }> {
  const calls: ChatStreamCall[] = [];

  // Optionally seed the language toggle to অসমীয়া BEFORE the React app
  // boots so ChatPage's useEffect rehydrates `responseLang='as'` on
  // first paint. The "selector interaction" test below skips this and
  // drives the dropdown directly instead.
  if (opts.seedAssamese !== false) {
    await page.addInitScript(() => {
      try { window.localStorage.setItem('syrabit_response_lang', 'as'); } catch {}
    });
  }

  await page.route('**/api/**', async (route: Route) => {
    const req = route.request();
    const url = req.url();
    const method = req.method();

    if (method === 'OPTIONS') { await route.fulfill({ status: 204, body: '' }); return; }

    // Anonymous user — /auth/me 401 keeps the chat path on the anon
    // branch (which is fine for this spec; the SSE error handling is
    // identical for anon and logged-in users).
    if (url.includes('/auth/me')) {
      await route.fulfill({
        status: 401, contentType: 'application/json',
        body: JSON.stringify({ detail: 'Not authenticated' }),
      });
      return;
    }

    if (url.includes('/api/user/credits')) {
      await route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ used: 0, limit: 30, plan: 'free' }),
      });
      return;
    }

    if (url.includes('/ai/chat/stream') && method === 'POST') {
      let body: Record<string, unknown> | null = null;
      try { body = req.postDataJSON() as Record<string, unknown>; } catch { body = null; }
      const responseLang = body?.response_lang;
      calls.push({ body, responseLang });

      // First call (Assamese) → SSE error event with the explicit
      // assamese_unavailable kind so ChatPage hits the localized
      // branch even if the message text is generic.
      if (calls.length === 1) {
        const sseBody =
          'data: ' + JSON.stringify({
            error: 'Assamese chat service temporarily unavailable',
            error_kind: 'assamese_unavailable',
          }) + '\n\n' +
          'data: [DONE]\n\n';
        await route.fulfill({
          status: 200, contentType: 'text/event-stream', body: sseBody,
        });
        return;
      }

      // Second call (English fallback after "Switch to English mode")
      // streams a normal answer.
      const sseBody =
        'data: ' + JSON.stringify({ content: 'Photosynthesis ' }) + '\n\n' +
        'data: ' + JSON.stringify({ content: 'is the process ' }) + '\n\n' +
        'data: ' + JSON.stringify({ content: 'plants use to make food.' }) + '\n\n' +
        'data: ' + JSON.stringify({ event: 'syrabit_done', conversation_id: 'conv-as-fallback-1' }) + '\n\n' +
        'data: [DONE]\n\n';
      await route.fulfill({
        status: 200, contentType: 'text/event-stream', body: sseBody,
      });
      return;
    }

    // Catch-all so unrelated /api/** calls (analytics, conversations
    // history, content cards, etc.) never escape to the network and
    // break the SSE assertions.
    await route.fulfill({
      status: 200, contentType: 'application/json', body: JSON.stringify({}),
    });
  });

  return { calls };
}

test.describe('Assamese chat unavailable card (Task #375)', () => {
  test('shows the localized অসমীয়া card and switches to English on click', async ({ page }) => {
    const { calls } = await installAssameseChatMocks(page);

    await page.goto('/chat');

    // Sanity — the language selector reflects the seeded অসমীয়া mode.
    await expect(page.getByTestId('lang-selector')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId('lang-selector')).toContainText('অসমীয়া');
    // Same applies to localStorage — the rehydration effect has run.
    await expect.poll(
      () => page.evaluate(() => window.localStorage.getItem('syrabit_response_lang')),
      { timeout: 5_000 },
    ).toBe('as');

    // 1. Send a question while in Assamese mode.
    const input = page.getByRole('textbox').first();
    await expect(input).toBeVisible({ timeout: 10_000 });
    await input.fill('সালোকসংশ্লেষণ কি?');
    await page.keyboard.press('Enter');

    // 2. The backend SSE stream emits the assamese_unavailable error →
    //    the localized অসমীয়া card must render (and the generic
    //    English variant must NOT appear).
    const card = page.getByTestId('assamese-unavailable-card');
    await expect(card).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId('ai-unavailable-card')).toHaveCount(0);
    await expect(card).toContainText('অসমীয়া চেট সেৱা সাময়িকভাৱে অনুপলব্ধ');

    // No auto-retry countdown for the Assamese variant — the strict
    // 2-leg chain has no further fallback.
    await expect(card.getByText(/Auto-retry in/)).toHaveCount(0);

    // The escape-hatch button is wired up.
    const switchBtn = page.getByTestId('assamese-switch-english');
    await expect(switchBtn).toBeVisible();

    // The first POST carried response_lang='as'.
    await expect.poll(() => calls.length, { timeout: 5_000 }).toBe(1);
    expect(calls[0].responseLang).toBe('as');

    // 3. Click "Switch to English mode" → re-sends through the English
    //    chain and persists `syrabit_response_lang=en` to localStorage.
    await switchBtn.click();

    await expect.poll(() => calls.length, { timeout: 10_000 }).toBe(2);

    // The English fallback POST must omit response_lang entirely
    // (ChatPage drops the field when responseLang === 'en').
    expect(calls[1].responseLang).toBeUndefined();
    expect(calls[1].body).not.toHaveProperty('response_lang');

    // …and it must re-send the *same* original query (the
    // "Switch to English" handler reuses retryText), not a fresh
    // empty/placeholder message.
    expect(calls[1].body?.message).toBe(calls[0].body?.message);
    expect(calls[1].body?.message).toBe('সালোকসংশ্লেষণ কি?');

    // The streamed answer renders, replacing the error card.
    await expect(page.getByText(/Photosynthesis is the process/)).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId('assamese-unavailable-card')).toHaveCount(0);

    // Language toggle has flipped back to English in the header…
    await expect(page.getByTestId('lang-selector')).toContainText('English');

    // …and the choice is persisted so the next reload starts in English.
    const stored = await page.evaluate(() => window.localStorage.getItem('syrabit_response_lang'));
    expect(stored).toBe('en');
  });

  test('user picks অসমীয়া via the language selector then sees the localized error card', async ({ page }) => {
    // Start fresh (no localStorage seed) so the dropdown is what flips
    // responseLang to 'as' — exercises the full user-driven toggle path.
    const { calls } = await installAssameseChatMocks(page, { seedAssamese: false });

    await page.goto('/chat');

    // Language toggle starts on English by default.
    const langSelector = page.getByTestId('lang-selector');
    await expect(langSelector).toBeVisible({ timeout: 10_000 });
    await expect(langSelector).toContainText('English');

    // Open the dropdown and pick অসমীয়া.
    await langSelector.click();
    await page.getByRole('button', { name: /অসমীয়া/ }).click();

    // The header reflects the new selection and localStorage was
    // written by the dropdown click handler.
    await expect(langSelector).toContainText('অসমীয়া');
    await expect.poll(
      () => page.evaluate(() => window.localStorage.getItem('syrabit_response_lang')),
      { timeout: 5_000 },
    ).toBe('as');

    // Send a question — the SSE mock returns assamese_unavailable.
    const input = page.getByRole('textbox').first();
    await expect(input).toBeVisible({ timeout: 10_000 });
    await input.fill('অসমীয়াত উত্তৰ দিয়ক');
    await page.keyboard.press('Enter');

    // Localized অসমীয়া error card renders, generic card does not.
    await expect(page.getByTestId('assamese-unavailable-card')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId('ai-unavailable-card')).toHaveCount(0);

    // The first POST carried the dropdown's response_lang='as' choice
    // — proving the selector wiring fed all the way through to the
    // chat send payload.
    await expect.poll(() => calls.length, { timeout: 5_000 }).toBe(1);
    expect(calls[0].responseLang).toBe('as');
  });
});
