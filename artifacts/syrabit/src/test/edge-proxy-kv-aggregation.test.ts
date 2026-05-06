/**
 * Task #454 — verify the artifacts edge worker sums CF_EDGE_CACHE
 * counters across every isolate that has handled traffic, instead of
 * only reporting the isolate that happened to serve the snapshot probe.
 *
 * The shared-key trick lives in
 * `artifacts/syrabit/workers/edge-proxy/src/index.ts`:
 *   - each isolate writes its counters under
 *     `__kv_usage:CF_EDGE_CACHE:<utc-day>:<isolate-id>`
 *   - the snapshot endpoint lists+sums every isolate's key
 *
 * This test simulates two isolates by switching the test-only
 * isolate-id between two values while writing to the SAME fake KV
 * namespace, then asserts the aggregated counters reflect both.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import {
  _aggregateKvCountersAcrossIsolates,
  _resetKvCountersForTests,
  _setKvIsolateIdForTests,
  _seedKvCountersForTests,
} from '../../workers/edge-proxy/src/index';

class FakeKv {
  public store = new Map<string, string>();

  async get(key: string): Promise<string | null> {
    return this.store.has(key) ? (this.store.get(key) as string) : null;
  }

  async put(key: string, value: string): Promise<void> {
    this.store.set(key, value);
  }

  async list(opts: { prefix?: string } = {}): Promise<{
    keys: { name: string }[];
    list_complete: boolean;
  }> {
    const prefix = opts.prefix ?? '';
    const keys = Array.from(this.store.keys())
      .filter((k) => k.startsWith(prefix))
      .map((name) => ({ name }));
    return { keys, list_complete: true };
  }

  async delete(key: string): Promise<void> {
    this.store.delete(key);
  }
}

beforeEach(() => {
  _resetKvCountersForTests();
});

describe('CF_EDGE_CACHE cross-isolate aggregation', () => {
  it('sums counters from two simulated isolates in the snapshot', async () => {
    const kv = new FakeKv();
    const ns = kv as unknown as KVNamespace;

    // ── Isolate A flushes 50 reads + 5 writes ──
    _setKvIsolateIdForTests('isolate-a');
    _seedKvCountersForTests('CF_EDGE_CACHE', {
      read: 50,
      write: 5,
      list: 0,
      delete: 0,
    });
    // Aggregating from isolate-a flushes its counters into the shared
    // store under the isolate-a key.
    let counters = await _aggregateKvCountersAcrossIsolates(
      'CF_EDGE_CACHE',
      ns,
    );
    expect(counters.read).toBe(50);
    expect(counters.write).toBe(5);

    // ── Now we ARE isolate-b: 7 reads + 2 writes + 1 list locally ──
    // Reset the in-memory module state so we look like a fresh isolate.
    // The shared __kv_usage:* keys in `kv.store` survive because that's
    // the underlying KV namespace, not module memory.
    _resetKvCountersForTests();
    _setKvIsolateIdForTests('isolate-b');
    _seedKvCountersForTests('CF_EDGE_CACHE', {
      read: 7,
      write: 2,
      list: 1,
      delete: 0,
    });

    counters = await _aggregateKvCountersAcrossIsolates('CF_EDGE_CACHE', ns);

    // Aggregated totals must include BOTH isolates' contributions —
    // not just whichever one happened to serve the probe.
    expect(counters.read).toBe(50 + 7);
    expect(counters.write).toBe(5 + 2);
    expect(counters.list).toBe(1);
    expect(counters.delete).toBe(0);

    // Sanity: there really are two distinct shared keys in the store.
    const day = new Date().toISOString().slice(0, 10);
    expect(
      kv.store.has(`__kv_usage:CF_EDGE_CACHE:${day}:isolate-a`),
    ).toBe(true);
    expect(
      kv.store.has(`__kv_usage:CF_EDGE_CACHE:${day}:isolate-b`),
    ).toBe(true);
  });

  it('falls back to local counters when listing fails', async () => {
    const broken = {
      get: async () => null,
      put: async () => undefined,
      delete: async () => undefined,
      list: async () => {
        throw new Error('kv simulated outage');
      },
    } as unknown as KVNamespace;

    _seedKvCountersForTests('CF_EDGE_CACHE', { read: 3, write: 1 });
    const counters = await _aggregateKvCountersAcrossIsolates(
      'CF_EDGE_CACHE',
      broken,
    );
    // Listing failed → return local counters so the panel still
    // renders something instead of going blank.
    expect(counters.read).toBe(3);
    expect(counters.write).toBe(1);
  });
});
