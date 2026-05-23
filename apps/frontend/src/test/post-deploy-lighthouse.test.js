/**
 * post-deploy-lighthouse.test.js
 *
 * Task #142 — Unit and integration-style tests for post-deploy-lighthouse.js.
 *
 * Unit tests (pure functions, no network):
 *   extractMetrics   — LHR audit extraction; also tested against real fixture JSON
 *   checkThresholds  — threshold comparison logic (all-pass, per-metric breach, missing)
 *   deployMatchesCommit — SHA matching (full, truncated, prefix)
 *
 * Integration tests (main() path):
 *   Stubs globalThis.fetch for Cloudflare Observatory API calls.
 *   Spies on process.exit to capture the exit code without terminating the process.
 *   Uses vi.resetModules() + dynamic import so env vars evaluated at module-load
 *   time (SKIP_PAGES_WAIT, CLOUDFLARE_API_TOKEN, …) are picked up fresh per test.
 *
 * @vitest-environment node
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { createRequire } from 'node:module';

// Static imports for pure-function unit tests.
import {
  extractMetrics,
  checkThresholds,
  deployMatchesCommit,
  THRESHOLDS,
} from '../../scripts/post-deploy-lighthouse.js';

// Real Lighthouse report fixtures checked-in to src/test/fixtures/.
// passLhr: all CWV metrics within thresholds (LCP 1.85 s, CLS 0.06, INP 112 ms)
// breachLhr: all three metrics breach their limits (LCP 4.2 s, CLS 0.28, INP 320 ms)
const require = createRequire(import.meta.url);
const passLhr   = require('./fixtures/lighthouse-report-pass.json');
const breachLhr = require('./fixtures/lighthouse-report-breach.json');

// ── Synthetic LHR builder (unit tests only) ───────────────────────────────────
function makeLhr({ lcp, cls, inp } = {}) {
  const audits = {};
  if (lcp !== undefined) audits['largest-contentful-paint'] = { numericValue: lcp };
  if (cls !== undefined) audits['cumulative-layout-shift']  = { numericValue: cls };
  if (inp !== undefined) audits['interaction-to-next-paint'] = { numericValue: inp };
  return { audits };
}

// Silence console output from ok/fail/info/warn helpers so test output is clean.
beforeEach(() => { vi.spyOn(console, 'log').mockImplementation(() => {}); });
afterEach(()  => { vi.restoreAllMocks(); });

// ═══════════════════════════════════════════════════════════════════════════════
// extractMetrics
// ═══════════════════════════════════════════════════════════════════════════════

describe('extractMetrics', () => {
  it('extracts all three metrics from a synthetic LHR', () => {
    const { lcp, cls, inp } = extractMetrics(makeLhr({ lcp: 1200, cls: 0.04, inp: 80 }));
    expect(lcp).toBe(1200);
    expect(cls).toBe(0.04);
    expect(inp).toBe(80);
  });

  it('extracts correct values from the real fixture (pass)', () => {
    const { lcp, cls, inp } = extractMetrics(passLhr);
    expect(lcp).toBe(1850);
    expect(cls).toBe(0.06);
    expect(inp).toBe(112);
  });

  it('extracts correct values from the real fixture (breach)', () => {
    const { lcp, cls, inp } = extractMetrics(breachLhr);
    expect(lcp).toBe(4200);
    expect(cls).toBeCloseTo(0.28);
    expect(inp).toBe(320);
  });

  it('returns undefined for a metric absent from the LHR audits', () => {
    const { lcp, cls, inp } = extractMetrics(makeLhr({ lcp: 1800, cls: 0.02 }));
    expect(lcp).toBe(1800);
    expect(cls).toBe(0.02);
    expect(inp).toBeUndefined();
  });

  it('returns all undefined when passed a null / empty lhr', () => {
    expect(extractMetrics(null)).toEqual({ lcp: undefined, cls: undefined, inp: undefined });
    expect(extractMetrics({})).toEqual({ lcp: undefined, cls: undefined, inp: undefined });
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// checkThresholds
// ═══════════════════════════════════════════════════════════════════════════════

describe('checkThresholds', () => {
  const label = 'homepage';

  it('returns breached=false when all metrics are within thresholds', () => {
    const { breached, results } = checkThresholds(label, { lcp: 1000, cls: 0.05, inp: 100 });
    expect(breached).toBe(false);
    expect(results.lcp.status).toBe('pass');
    expect(results.cls.status).toBe('pass');
    expect(results.inp.status).toBe('pass');
  });

  it('returns breached=true when LCP exceeds 2 500 ms', () => {
    const { breached, results } = checkThresholds(label, { lcp: 3200, cls: 0.05, inp: 100 });
    expect(breached).toBe(true);
    expect(results.lcp.status).toBe('fail');
    expect(results.cls.status).toBe('pass');
    expect(results.inp.status).toBe('pass');
  });

  it('returns breached=true when CLS exceeds 0.1', () => {
    const { breached, results } = checkThresholds(label, { lcp: 1000, cls: 0.25, inp: 100 });
    expect(breached).toBe(true);
    expect(results.lcp.status).toBe('pass');
    expect(results.cls.status).toBe('fail');
    expect(results.inp.status).toBe('pass');
  });

  it('returns breached=true when INP exceeds 200 ms', () => {
    const { breached, results } = checkThresholds(label, { lcp: 1000, cls: 0.05, inp: 350 });
    expect(breached).toBe(true);
    expect(results.lcp.status).toBe('pass');
    expect(results.cls.status).toBe('pass');
    expect(results.inp.status).toBe('fail');
  });

  it('treats undefined metric as "unavailable" and marks breached=true', () => {
    const { breached, results } = checkThresholds(label, { lcp: undefined, cls: 0.05, inp: 100 });
    expect(breached).toBe(true);
    expect(results.lcp.status).toBe('unavailable');
    expect(results.cls.status).toBe('pass');
    expect(results.inp.status).toBe('pass');
  });

  it('treats null metric value as "unavailable" and marks breached=true', () => {
    const { breached, results } = checkThresholds(label, { lcp: null, cls: 0.05, inp: 100 });
    expect(breached).toBe(true);
    expect(results.lcp.status).toBe('unavailable');
  });

  it('returns breached=true and all "fail" when every threshold is breached', () => {
    const { breached, results } = checkThresholds(label, { lcp: 5000, cls: 0.5, inp: 600 });
    expect(breached).toBe(true);
    expect(results.lcp.status).toBe('fail');
    expect(results.cls.status).toBe('fail');
    expect(results.inp.status).toBe('fail');
  });

  it('passes at exactly the limit boundary (value === limit)', () => {
    const { breached, results } = checkThresholds(label, {
      lcp: THRESHOLDS.lcp.limit,
      cls: THRESHOLDS.cls.limit,
      inp: THRESHOLDS.inp.limit,
    });
    expect(breached).toBe(false);
    expect(results.lcp.status).toBe('pass');
    expect(results.cls.status).toBe('pass');
    expect(results.inp.status).toBe('pass');
  });

  it('fails one unit above the limit', () => {
    const { breached, results } = checkThresholds(label, {
      lcp: THRESHOLDS.lcp.limit + 1,
      cls: 0.05,
      inp: 100,
    });
    expect(breached).toBe(true);
    expect(results.lcp.status).toBe('fail');
  });

  it('records raw value and limit in each result entry', () => {
    const { results } = checkThresholds(label, { lcp: 3000, cls: 0.05, inp: 100 });
    expect(results.lcp.value).toBe(3000);
    expect(results.lcp.limit).toBe(THRESHOLDS.lcp.limit);
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// deployMatchesCommit
// ═══════════════════════════════════════════════════════════════════════════════

describe('deployMatchesCommit', () => {
  const FULL_SHA = 'abc1234567890def1234567890def1234567890aa';
  const dep = hash => ({ deployment_trigger: { metadata: { commit_hash: hash } } });

  it('returns true on exact full-SHA match', () => {
    expect(deployMatchesCommit(dep(FULL_SHA), FULL_SHA)).toBe(true);
  });

  it('returns true when Pages has 12-char truncated SHA and CI provides full SHA', () => {
    expect(deployMatchesCommit(dep(FULL_SHA.slice(0, 12)), FULL_SHA)).toBe(true);
  });

  it('returns true when CI provides short prefix and Pages has full SHA', () => {
    expect(deployMatchesCommit(dep(FULL_SHA), FULL_SHA.slice(0, 12))).toBe(true);
  });

  it('returns false when SHAs do not match', () => {
    expect(deployMatchesCommit(dep('f'.repeat(40)), FULL_SHA)).toBe(false);
  });

  it('returns false when deployment has no commit_hash', () => {
    expect(deployMatchesCommit(dep(''), FULL_SHA)).toBe(false);
    expect(deployMatchesCommit({}, FULL_SHA)).toBe(false);
    expect(deployMatchesCommit(null, FULL_SHA)).toBe(false);
  });

  it('returns false when commitSha is empty', () => {
    expect(deployMatchesCommit(dep(FULL_SHA), '')).toBe(false);
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// Integration tests: main() with stubbed fetch and spied process.exit
//
// Pattern:
//   1. vi.resetModules() — ensures env vars set below are re-read at import time.
//   2. Set process.env before the dynamic import so module-level constants
//      (TOKEN, SKIP_PAGES_WAIT, LIGHTHOUSE_POLL_INTERVAL_MS) use test values.
//   3. vi.stubGlobal('fetch', ...) — intercepts Cloudflare Observatory API calls.
//   4. vi.spyOn(process, 'exit').mockImplementation(code => { throw { code } })
//      — captures exit code without killing the test process.
//   5. Await main(); catch the thrown pseudo-exit object; assert exit code.
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Build a fetch stub that handles the two Observatory call types:
 *   POST /speed/tests       — trigger response (returns testId)
 *   GET  /speed/tests?url=  — poll response (returns lhr for the testId)
 *
 * Two sequential POST calls are expected (one per TARGETS entry); each gets
 * a distinct testId (test-id-1, test-id-2). The GET stub returns the same
 * lhr for both testIds in a single result array.
 */
function makeFetch(lhr) {
  let postCount = 0;
  return async (url, options = {}) => {
    if (options?.method === 'POST') {
      postCount += 1;
      return { json: async () => ({ success: true, result: { id: `test-id-${postCount}` } }) };
    }
    // GET — poll for test results; return lhr for whichever id was just triggered
    return {
      json: async () => ({
        success: true,
        result: [
          { id: 'test-id-1', lhr },
          { id: 'test-id-2', lhr },
        ],
      }),
    };
  };
}

describe('main() — integration (fetch-stubbed, process.exit spied)', () => {
  // Saved env values to restore after each test
  const savedEnv = {};
  const integrationEnv = {
    CLOUDFLARE_API_TOKEN:        'test-token',
    SKIP_PAGES_WAIT:             '1',
    SKIP_LIGHTHOUSE:             '',
    LIGHTHOUSE_POLL_INTERVAL_MS: '1',    // near-zero sleep to keep tests fast
    LIGHTHOUSE_POLL_TIMEOUT_MS:  '10000',
  };

  beforeEach(() => {
    vi.resetModules();
    for (const [k, v] of Object.entries(integrationEnv)) {
      savedEnv[k] = process.env[k];
      process.env[k] = v;
    }
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    for (const [k, v] of Object.entries(savedEnv)) {
      if (v === undefined) delete process.env[k];
      else process.env[k] = v;
    }
  });

  /**
   * Run main() from a freshly imported module (env vars already set above).
   * Captures the process.exit code rather than letting it kill the test.
   * Returns the exit code passed to process.exit (0 or 1).
   */
  async function runMain(lhr) {
    vi.spyOn(console, 'log').mockImplementation(() => {});
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.stubGlobal('fetch', makeFetch(lhr));

    const exitSpy = vi.spyOn(process, 'exit').mockImplementation((code) => {
      // Throw a sentinel so main() halts at the exit call without killing the runner.
      throw Object.assign(new Error('process.exit'), { exitCode: code });
    });

    const { main } = await import('../../scripts/post-deploy-lighthouse.js');
    try {
      await main();
    } catch (e) {
      if (e.message !== 'process.exit') throw e;
    }

    return exitSpy.mock.calls[0]?.[0];
  }

  it('exits 0 when all CWV metrics are within thresholds (pass fixture)', async () => {
    const code = await runMain(passLhr);
    expect(code).toBe(0);
  });

  it('exits 1 when LCP, CLS, and INP all exceed thresholds (breach fixture)', async () => {
    const code = await runMain(breachLhr);
    expect(code).toBe(1);
  });

  it('exits 1 when a metric is absent from the LHR (Observatory capture failure)', async () => {
    // Simulate a run where INP was not captured — audit key missing entirely
    const incompletePassLhr = {
      ...passLhr,
      audits: {
        'largest-contentful-paint': passLhr.audits['largest-contentful-paint'],
        'cumulative-layout-shift':  passLhr.audits['cumulative-layout-shift'],
        // 'interaction-to-next-paint' intentionally omitted
      },
    };
    const code = await runMain(incompletePassLhr);
    expect(code).toBe(1);
  });

  it('exits 0 when metrics are exactly at the "needs improvement" boundary', async () => {
    const boundaryLhr = makeLhr({
      lcp: THRESHOLDS.lcp.limit,   // 2500 ms — exactly at limit → pass
      cls: THRESHOLDS.cls.limit,   // 0.1     — exactly at limit → pass
      inp: THRESHOLDS.inp.limit,   // 200 ms  — exactly at limit → pass
    });
    const code = await runMain(boundaryLhr);
    expect(code).toBe(0);
  });

  it('exits 1 when only LCP breaches (CLS and INP within limits)', async () => {
    const lcpBreachLhr = makeLhr({
      lcp: THRESHOLDS.lcp.limit + 500,  // 3000 ms — breach
      cls: 0.05,
      inp: 100,
    });
    const code = await runMain(lcpBreachLhr);
    expect(code).toBe(1);
  });
});
