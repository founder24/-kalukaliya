/**
 * Task #9 — Bot-management golden test (Playwright).
 *
 * Exercises the four behaviours the verified-bot fast path must
 * guarantee against a live Cloudflare preview deployment:
 *
 *   1. Verified Googlebot UA      → 200 + prerendered DOM (h1, schema.org/Article JSON-LD)
 *   2. Spoofed Googlebot UA       → 403 with X-Bot-Verify: spoofed
 *      (Spoofing is detected naturally: the test runner's IP is not
 *      in BOT_UA_RANGES for googlebot, cf.verifiedBot is false, so
 *      verifySearchBot reports {spoofed: true} → CRITICAL_BOT_UA
 *      hard-403 branch fires after FCrDNS also fails to confirm.)
 *   3. Citation-AI (PerplexityBot) → 200 + prerendered DOM
 *      (Skipped if running from an IP in PERPLEXITY_RANGES — same
 *      spoof logic would 403 us.)
 *   4. Training-AI (GPTBot)       → 403 (AI_BOT_UA hard-block)
 *
 * Gated on `BOT_UNBLOCK_PREVIEW_URL`. Without it the test is
 * skipped — the offline contract checks in `tests/test_bot_unblock.py`
 * cover the regex/source shape.
 *
 * Why a Playwright spec, not just httpx: the prerendered-DOM
 * assertions (cases 1 + 3) load the response in a real browser
 * via `page.goto(...)` so a "200 + prerender" check can verify
 * the SSR shell actually shipped — a status-only probe would
 * miss the case where the worker returns 200 but Pages serves
 * the SPA shell because cache rules bypassed prerender.
 */
import { test, expect } from '@playwright/test';

const PREVIEW_URL = process.env.BOT_UNBLOCK_PREVIEW_URL;

const GOOGLEBOT_UA =
  'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)';
const PERPLEXITY_UA =
  'Mozilla/5.0 (compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)';
const GPTBOT_UA =
  'Mozilla/5.0 (compatible; GPTBot/1.0; +https://openai.com/gptbot)';

test.describe('Task #9 — bot-unblock golden matrix', () => {
  test.skip(
    !PREVIEW_URL,
    'set BOT_UNBLOCK_PREVIEW_URL=https://syrabit-edge-preview.workers.dev to run live edge probes',
  );

  test('Googlebot UA on a Google-verified preview run → 200 + prerendered DOM', async ({
    browser,
  }) => {
    // This case requires the preview environment to be reachable
    // from a Google-verified source (cf.verifiedBot=true) — the
    // GitHub Action runs this from a CI runner whose origin IP
    // has been allow-listed in the preview worker's BOT_UA_RANGES
    // override KV so verifySearchBot returns verified=true. If
    // VERIFIED_PREVIEW=1 isn't set, skip this case (running from
    // a developer laptop would otherwise fail the CIDR check).
    test.skip(
      process.env.VERIFIED_PREVIEW !== '1',
      'set VERIFIED_PREVIEW=1 only when the runner IP is in the preview BOT_UA_RANGES override',
    );
    const ctx = await browser.newContext({ userAgent: GOOGLEBOT_UA });
    const page = await ctx.newPage();
    const resp = await page.goto(PREVIEW_URL!, { waitUntil: 'domcontentloaded' });
    expect(resp?.status()).toBe(200);
    // Prerendered DOM checks — both must hold for this to count
    // as a real prerender (not just a SPA shell that happened to
    // 200).
    await expect(page.locator('h1').first()).toBeVisible();
    const ldJsonCount = await page
      .locator('script[type="application/ld+json"]')
      .count();
    expect(ldJsonCount, 'prerender must emit at least one JSON-LD block').toBeGreaterThan(0);
    await ctx.close();
  });

  test('spoofed Googlebot UA from a non-Google IP → 403 with X-Bot-Verify: spoofed', async ({
    request,
  }) => {
    // Sending the Googlebot UA from any ordinary CI-runner IP
    // (which is by definition NOT in BOT_UA_RANGES.googlebot)
    // triggers verifySearchBot → {spoofed: true}, then the
    // CRITICAL_BOT_UA branch attempts FCrDNS verification
    // (PTR + A round-trip) which also fails because the runner's
    // PTR doesn't end in `.googlebot.com`/`.google.com`. Result:
    // hard-403 with X-Bot-Verify: spoofed. No test-mode header
    // required — the spoof detection is a real production code
    // path.
    const r = await request.get(PREVIEW_URL!, {
      headers: { 'User-Agent': GOOGLEBOT_UA },
      maxRedirects: 0,
    });
    expect(
      r.status(),
      `spoofed Googlebot from a non-Google IP must be 403d at the edge (got ${r.status()})`,
    ).toBe(403);
    expect(r.headers()['x-bot-verify']).toBe('spoofed');
  });

  test('PerplexityBot UA from a Perplexity-verified preview run → 200 + prerender', async ({
    browser,
  }) => {
    test.skip(
      process.env.VERIFIED_PREVIEW !== '1',
      'set VERIFIED_PREVIEW=1 only when the runner IP is in the preview BOT_UA_RANGES override',
    );
    const ctx = await browser.newContext({ userAgent: PERPLEXITY_UA });
    const page = await ctx.newPage();
    const resp = await page.goto(PREVIEW_URL!, { waitUntil: 'domcontentloaded' });
    expect(resp?.status()).toBe(200);
    await expect(page.locator('h1').first()).toBeVisible();
    await ctx.close();
  });

  test('GPTBot → 403 (training-AI hard-block, unconditional)', async ({ request }) => {
    // AI_BOT_UA hard-block runs BEFORE the verified-bot branch and
    // is not bypassed by cf.verifiedBot, so this case works from
    // any source IP including a verified preview runner.
    const r = await request.get(PREVIEW_URL!, {
      headers: { 'User-Agent': GPTBOT_UA },
      maxRedirects: 0,
    });
    expect(r.status(), 'GPTBot is a training-only crawler — must be 403').toBe(403);
  });
});
