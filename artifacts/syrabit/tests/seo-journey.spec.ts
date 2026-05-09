/**
 * tests/seo-journey.spec.ts — Task #15 §3.
 *
 * SEO/AEO journey end-to-end: walk a sample chapter page as Googlebot
 * and as PerplexityBot, then assert the contract every ranking page
 * has to honour.
 *
 * Note on path: the task brief asked for `tests/e2e/seo_journey.spec.ts`
 * at repo root, but the existing Playwright suite lives under
 * `artifacts/syrabit/tests/*.spec.ts` (the only place
 * `playwright.config.ts` is wired). Putting the spec there keeps it
 * runnable via `pnpm --filter @workspace/syrabit test:e2e` and the
 * existing `scripts/run-e2e.sh` Replit/NixOS shim — no parallel
 * harness needed. Drift documented in `.local/.commit_message`.
 *
 * Why the assertions fail loud (V4 §12)
 * -------------------------------------
 * If the Quick-Answer block, the FAQPage JSON-LD, or the
 * BreadcrumbList JSON-LD is missing, the page does not qualify for
 * the rich-result surfaces we are targeting (Featured Snippet, FAQ
 * accordion, breadcrumb chip). A pass with one of those silently
 * absent would mean we shipped a "ranking page" that cannot rank.
 *
 * Cache assertion (PerplexityBot leg)
 * ----------------------------------
 * Perplexity / GPTBot crawl us frequently; the per-page LLM cost
 * has to be amortised across crawls. The second request as
 * PerplexityBot must come back HIT (`x-cache: HIT` from CF, or the
 * `cf-cache-status` header). A MISS twice in a row is a regression.
 *
 * Deferred until upstream tasks merge
 * -----------------------------------
 * The chapter slug used here (`sample-chapter-01`) is the same
 * deterministic placeholder `scripts/seo_baseline.py` uses; once
 * Task #11 (SEO chapter content) lands, swap it for a real top-N
 * slug pulled from `/api/seo/sitemap-sample`. The LCP threshold
 * (2 500 ms per task brief) only becomes meaningful after Task #10
 * (semantic-fingerprint cache + deterministic templates) ships —
 * before that, cold renders will frequently miss the budget and
 * mask real regressions.
 */

import { test, expect, type Page, type Response } from "@playwright/test";

const BASE_URL = process.env.PUBLIC_BASE_URL?.replace(/\/$/, "") ?? "https://syrabit.ai";
const SAMPLE_CHAPTER_PATH =
  process.env.SEO_JOURNEY_SAMPLE_PATH ??
  "/board/ahsec/class/12/subject/general/chapter/sample-chapter-01/notes";

const GOOGLEBOT_UA =
  "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)";
const PERPLEXITYBOT_UA =
  "Mozilla/5.0 (compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)";

const LCP_BUDGET_MS = Number(process.env.SEO_JOURNEY_LCP_BUDGET_MS ?? "2500");

/**
 * Collect every `@type` that appears anywhere in the page's JSON-LD,
 * descending into lists AND `@graph` arrays. The real SEO template
 * (`routes/seo_pages.py::_jsonld(...)`) emits a single
 * `{ @context, @graph: [...] }` block, so a surface-level scan would
 * miss every type. The page also escapes `</` to `<\/` to keep the
 * `<script>` block intact; we reverse that before parsing.
 */
async function readJsonLdTypes(page: Page): Promise<string[]> {
  return page.$$eval('script[type="application/ld+json"]', (els) => {
    const types: string[] = [];
    const walk = (node: unknown): void => {
      if (Array.isArray(node)) {
        node.forEach(walk);
        return;
      }
      if (node && typeof node === "object") {
        const obj = node as Record<string, unknown>;
        const t = obj["@type"];
        if (typeof t === "string") types.push(t);
        else if (Array.isArray(t)) {
          for (const x of t) if (typeof x === "string") types.push(x);
        }
        if (obj["@graph"] !== undefined) walk(obj["@graph"]);
      }
    };
    for (const el of els) {
      const raw = (el.textContent ?? "").replace(/<\\\//g, "</");
      try {
        walk(JSON.parse(raw));
      } catch {
        /* invalid block — surfaced separately by seo_baseline.py */
      }
    }
    return types;
  });
}

async function measureLcpMs(page: Page): Promise<number | null> {
  return page.evaluate(
    () =>
      new Promise<number | null>((resolve) => {
        let lcp: number | null = null;
        try {
          const obs = new PerformanceObserver((list) => {
            const entries = list.getEntries();
            const last = entries[entries.length - 1];
            if (last) lcp = last.startTime;
          });
          obs.observe({ type: "largest-contentful-paint", buffered: true });
          // Resolve a beat later so the observer drains
          setTimeout(() => {
            obs.disconnect();
            resolve(lcp);
          }, 1500);
        } catch {
          resolve(null);
        }
      }),
  );
}

test.describe("SEO/AEO ranking-page contract", () => {
  test("Googlebot sees JSON-LD, Quick-Answer, and meets the LCP budget", async ({
    browser,
  }) => {
    const ctx = await browser.newContext({ userAgent: GOOGLEBOT_UA });
    const page = await ctx.newPage();
    const url = `${BASE_URL}${SAMPLE_CHAPTER_PATH}`;
    const resp = await page.goto(url, { waitUntil: "load" });

    expect(resp, `navigation failed for ${url}`).not.toBeNull();
    expect(resp!.status(), `expected 200 from ${url}`).toBe(200);

    const types = await readJsonLdTypes(page);
    expect(types.length, "page must ship at least one JSON-LD @type").toBeGreaterThan(0);
    expect(
      types.includes("BreadcrumbList"),
      "BreadcrumbList JSON-LD missing — breadcrumb chip will not appear in SERPs",
    ).toBe(true);
    expect(
      types.includes("FAQPage") || types.includes("QAPage"),
      "FAQPage / QAPage JSON-LD missing — FAQ accordion will not appear in SERPs",
    ).toBe(true);

    // Quick-Answer block — the AEO surface. Selector matches the
    // template in `artifacts/syrabit-backend/templates/seo/chapter.html.j2`
    // (`<aside class="quick-answer" data-aeo-block="1">`). If the
    // template ever changes, change both sides in lock-step.
    const quickAnswer = page.locator('[data-aeo-block="1"]').first();
    await expect(
      quickAnswer,
      "Quick-Answer block missing — page will not be picked for Featured Snippet / Perplexity citation",
    ).toBeVisible();

    // LCP — only enforced when the page is genuinely past first-paint.
    const lcp = await measureLcpMs(page);
    if (lcp != null) {
      expect(
        lcp,
        `LCP ${lcp.toFixed(0)}ms exceeds the ${LCP_BUDGET_MS}ms ranking budget`,
      ).toBeLessThanOrEqual(LCP_BUDGET_MS);
    } else {
      test.info().annotations.push({
        type: "skip",
        description: "LCP could not be measured (browser did not emit a paint entry)",
      });
    }

    await ctx.close();
  });

  test("PerplexityBot gets a 200 and the second hit is edge-cached", async ({
    browser,
  }) => {
    const ctx = await browser.newContext({ userAgent: PERPLEXITYBOT_UA });
    const page = await ctx.newPage();
    const url = `${BASE_URL}${SAMPLE_CHAPTER_PATH}`;

    const cold: Response | null = await page.goto(url, { waitUntil: "load" });
    expect(cold!.status(), `cold fetch from ${url} should be 200`).toBe(200);

    // Force a fresh request; the page-level fetch goes through CF.
    const warm = await page.evaluate(async (u) => {
      const r = await fetch(u, { cache: "reload" });
      return {
        status: r.status,
        cfCache: r.headers.get("cf-cache-status"),
        xCache: r.headers.get("x-cache"),
      };
    }, url);
    expect(warm.status, "warm fetch should also be 200").toBe(200);
    const cacheState = (warm.cfCache ?? warm.xCache ?? "").toUpperCase();
    expect(
      ["HIT", "REVALIDATED", "STALE"],
      `expected edge cache HIT/REVALIDATED/STALE on warm fetch, got ${cacheState}`,
    ).toContain(cacheState);

    await ctx.close();
  });
});
