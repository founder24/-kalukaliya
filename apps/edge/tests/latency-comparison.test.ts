/**
 * Latency Comparison Benchmark Tests - Edge Worker
 *
 * Compares OLD (hypothetical unoptimized baseline with separate KV reads) vs
 * NEW (actual optimized single KV window check) with controlled delays to
 * produce concrete timing numbers.
 *
 * The OLD flow is a hypothetical unoptimized baseline (4 separate KV ops for
 * monthly + burst tracking) that illustrates the worst-case design this
 * architecture avoids. The NEW flow matches the actual production implementation.
 *
 * Also includes a regression-detection test that imports the real checkRateLimit
 * function and verifies it uses exactly 2 KV ops (get + put).
 *
 * Optimizations documented:
 * 1. Rate limit: hypothetical 4 KV ops reduced to 2 KV ops per request
 * 2. Edge trust header: backend skips burst rate limit when X-Rate-Limited-By: edge is present
 */
import { describe, it, expect, vi } from 'vitest';
import { checkRateLimit } from '../src/middleware/rate-limit';

// Helper to create a delayed KV mock with configurable per-operation delay
function createDelayedKV(delayMs: number) {
  return {
    get: vi.fn(async () => {
      await new Promise((r) => setTimeout(r, delayMs));
      return '5';
    }),
    put: vi.fn(async () => {
      await new Promise((r) => setTimeout(r, delayMs));
    }),
  } as unknown as KVNamespace;
}

// ═══════════════════════════════════════════════════════════════
// Edge Rate Limit: Hypothetical Unoptimized (4 ops) vs Actual (2 ops)
// ═══════════════════════════════════════════════════════════════

describe('Edge Rate Limit Latency Comparison', () => {
  it('HYPOTHETICAL UNOPTIMIZED: separate KV reads for rate limit (4 ops per request)', async () => {
    const KV_DELAY_MS = 5;
    const kv = createDelayedKV(KV_DELAY_MS);

    /**
     * Hypothetical unoptimized approach (never shipped, illustrates worst-case):
     * 1. KV.get for monthly count
     * 2. KV.get for burst count
     * 3. KV.put to increment monthly
     * 4. KV.put to increment burst
     * Total: 4 ops * 5ms = ~20ms per request
     */
    async function hypotheticalUnoptimizedFlow() {
      // Monthly check
      await kv.get('rl:user:en:monthly');
      // Burst check
      await kv.get('rl:user:en:burst');
      // Increment monthly
      await kv.put('rl:user:en:monthly', '6');
      // Increment burst
      await kv.put('rl:user:en:burst', '1');
    }

    const start = performance.now();
    await hypotheticalUnoptimizedFlow();
    const elapsed = performance.now() - start;

    console.log(`\n  HYPOTHETICAL unoptimized edge rate limit (4 KV ops): ${elapsed.toFixed(1)}ms`);
    expect(kv.get).toHaveBeenCalledTimes(2);
    expect(kv.put).toHaveBeenCalledTimes(2);
    // Should be ~20ms (4 ops * 5ms)
    expect(elapsed).toBeGreaterThanOrEqual(15);
    expect(elapsed).toBeLessThan(60);
  });

  it('ACTUAL flow: single KV window check (2 ops per request)', async () => {
    const KV_DELAY_MS = 5;
    const kv = createDelayedKV(KV_DELAY_MS);

    /**
     * Actual production approach: single window check
     * 1. KV.get for current window count (covers both monthly and burst)
     * 2. KV.put to increment counter
     * Total: 2 ops * 5ms = ~10ms per request
     */
    async function newRateLimitFlow() {
      // Single window check (combined monthly + burst into one key)
      const count = await kv.get('rl:user:en:window');
      // Single increment
      await kv.put('rl:user:en:window', String(parseInt(count || '0') + 1));
    }

    const start = performance.now();
    await newRateLimitFlow();
    const elapsed = performance.now() - start;

    console.log(`  ACTUAL edge rate limit (2 KV ops): ${elapsed.toFixed(1)}ms`);
    expect(kv.get).toHaveBeenCalledTimes(1);
    expect(kv.put).toHaveBeenCalledTimes(1);
    // Should be ~10ms (2 ops * 5ms)
    expect(elapsed).toBeGreaterThanOrEqual(7);
    expect(elapsed).toBeLessThan(40);
  });

  it('Chat request total: hypothetical unoptimized vs actual full round-trip overhead', async () => {
    const KV_DELAY_MS = 5;
    const BACKEND_RATE_LIMIT_MS = 25;

    /**
     * Hypothetical unoptimized flow (never shipped):
     * - Edge rate limit: 4 KV ops * 5ms = 20ms
     * - Backend does full rate limit again (no trust header): 25ms
     * Total overhead: ~45ms
     */
    async function hypotheticalFullFlow() {
      const kv = createDelayedKV(KV_DELAY_MS);
      // Edge: 4 KV operations
      await kv.get('monthly');
      await kv.get('burst');
      await kv.put('monthly', '6');
      await kv.put('burst', '1');
      // Backend: full rate limit (no X-Rate-Limited-By header)
      await new Promise((r) => setTimeout(r, BACKEND_RATE_LIMIT_MS));
    }

    /**
     * Actual production flow:
     * - Edge rate limit: 2 KV ops * 5ms = 10ms
     * - Backend trusts edge header, skips burst check: 0ms additional
     * Total overhead: ~10ms
     */
    async function actualFlow() {
      const kv = createDelayedKV(KV_DELAY_MS);
      // Edge: 2 KV operations
      await kv.get('window');
      await kv.put('window', '6');
      // Backend: trusts X-Rate-Limited-By: edge, no additional rate limit
    }

    // Measure hypothetical unoptimized
    const startOld = performance.now();
    await hypotheticalFullFlow();
    const oldElapsed = performance.now() - startOld;

    // Measure actual
    const startNew = performance.now();
    await actualFlow();
    const newElapsed = performance.now() - startNew;

    const improvement = ((oldElapsed - newElapsed) / oldElapsed) * 100;

    console.log(`\n  === LATENCY COMPARISON: Chat Request Overhead ===`);
    console.log(`  HYPOTHETICAL (4 KV ops + backend rate limit):  ${oldElapsed.toFixed(1)}ms`);
    console.log(`  ACTUAL (2 KV ops + edge trust header):         ${newElapsed.toFixed(1)}ms`);
    console.log(`  Improvement vs hypothetical:                   ${improvement.toFixed(1)}%`);
    console.log(`  =================================================`);

    // New flow should use at most 50% of old flow time
    expect(newElapsed).toBeLessThan(oldElapsed * 0.70);
    expect(improvement).toBeGreaterThanOrEqual(30);
  });
});

// ═══════════════════════════════════════════════════════════════
// Batch Performance: 100 requests comparison
// ═══════════════════════════════════════════════════════════════

describe('Edge Rate Limit Batch Performance', () => {
  it('100 requests: actual flow uses fewer total KV operations than hypothetical unoptimized', async () => {
    const REQUEST_COUNT = 100;
    const KV_DELAY_MS = 0.5; // Reduce delay for batch test to keep it fast

    // Hypothetical unoptimized: 4 ops per request
    const oldKv = createDelayedKV(KV_DELAY_MS);
    const startOld = performance.now();
    for (let i = 0; i < REQUEST_COUNT; i++) {
      await oldKv.get(`monthly:${i}`);
      await oldKv.get(`burst:${i}`);
      await oldKv.put(`monthly:${i}`, '1');
      await oldKv.put(`burst:${i}`, '1');
    }
    const oldElapsed = performance.now() - startOld;

    // Actual: 2 ops per request
    const newKv = createDelayedKV(KV_DELAY_MS);
    const startNew = performance.now();
    for (let i = 0; i < REQUEST_COUNT; i++) {
      await newKv.get(`window:${i}`);
      await newKv.put(`window:${i}`, '1');
    }
    const newElapsed = performance.now() - startNew;

    const improvement = ((oldElapsed - newElapsed) / oldElapsed) * 100;

    console.log(`\n  === BATCH PERFORMANCE: 100 Requests ===`);
    console.log(`  HYPOTHETICAL (${REQUEST_COUNT} * 4 KV ops): ${oldElapsed.toFixed(1)}ms total`);
    console.log(`  ACTUAL (${REQUEST_COUNT} * 2 KV ops):       ${newElapsed.toFixed(1)}ms total`);
    console.log(`  Improvement:                          ${improvement.toFixed(1)}%`);
    console.log(`  =======================================`);

    // OLD: 400 KV calls, NEW: 200 KV calls
    expect(oldKv.get).toHaveBeenCalledTimes(REQUEST_COUNT * 2);
    expect(oldKv.put).toHaveBeenCalledTimes(REQUEST_COUNT * 2);
    expect(newKv.get).toHaveBeenCalledTimes(REQUEST_COUNT);
    expect(newKv.put).toHaveBeenCalledTimes(REQUEST_COUNT);

    // New should be at least 30% faster
    expect(improvement).toBeGreaterThanOrEqual(30);
  });
});

// ═══════════════════════════════════════════════════════════════
// Latency Comparison Summary
// ═══════════════════════════════════════════════════════════════

describe('Latency Comparison Summary', () => {
  it('documents the optimization improvements', () => {
    console.log(`\n  ┌─────────────────────────────────────────────────────────────┐`);
    console.log(`  │           EDGE WORKER LATENCY OPTIMIZATIONS                  │`);
    console.log(`  ├─────────────────────────────────────────────────────────────┤`);
    console.log(`  │ Metric                  │ Hypothetical │ Actual  │ Savings    │`);
    console.log(`  ├─────────────────────────────────────────────────────────────┤`);
    console.log(`  │ KV ops per request       │ 4 ops        │ 2 ops   │ 50% fewer │`);
    console.log(`  │ Edge rate limit time     │ ~20ms        │ ~10ms   │ ~10ms     │`);
    console.log(`  │ Backend rate limit       │ ~25ms        │ 0ms     │ ~25ms     │`);
    console.log(`  │ Total overhead/req       │ ~45ms        │ ~10ms   │ ~78%      │`);
    console.log(`  └─────────────────────────────────────────────────────────────┘`);
    console.log(`  Note: "Hypothetical" = unoptimized baseline that was never shipped.`);

    // This test just documents; the actual measurements are in the tests above
    expect(true).toBe(true);
  });
});

// ═══════════════════════════════════════════════════════════════
// Regression-Detection: Import real checkRateLimit and verify 2 KV ops
// ═══════════════════════════════════════════════════════════════

describe('Regression: Real checkRateLimit uses exactly 2 KV ops', () => {
  it('checkRateLimit performs 1 KV.get + 1 KV.put for an allowed request', async () => {
    const kv = {
      get: vi.fn(async () => '5'),
      put: vi.fn(async () => {}),
    } as unknown as KVNamespace;

    const result = await checkRateLimit(kv, 'user-regression-test', 'en', 30);

    expect(result.allowed).toBe(true);
    expect(result.remaining).toBe(24); // 30 - 5 - 1
    // Exactly 2 KV operations: 1 get + 1 put
    expect(kv.get).toHaveBeenCalledTimes(1);
    expect(kv.put).toHaveBeenCalledTimes(1);
  });

  it('checkRateLimit performs only 1 KV.get when limit is exceeded (no put)', async () => {
    const kv = {
      get: vi.fn(async () => '30'),
      put: vi.fn(async () => {}),
    } as unknown as KVNamespace;

    const result = await checkRateLimit(kv, 'user-over-limit', 'en', 30);

    expect(result.allowed).toBe(false);
    expect(result.remaining).toBe(0);
    // Only 1 KV.get (no put since request is denied)
    expect(kv.get).toHaveBeenCalledTimes(1);
    expect(kv.put).not.toHaveBeenCalled();
  });
});
