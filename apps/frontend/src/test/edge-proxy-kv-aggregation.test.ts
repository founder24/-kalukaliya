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
  _collectKvIsolatesBreakdown,
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

  it('returns per-isolate breakdown sorted hottest-first (Task #510)', async () => {
    const kv = new FakeKv();
    const ns = kv as unknown as KVNamespace;

    // Isolate A — light load.
    _setKvIsolateIdForTests('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa');
    _seedKvCountersForTests('CF_EDGE_CACHE', { read: 5, write: 1 });
    await _collectKvIsolatesBreakdown('CF_EDGE_CACHE', ns);

    // Isolate B — heavy load.
    _resetKvCountersForTests();
    _setKvIsolateIdForTests('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb');
    _seedKvCountersForTests('CF_EDGE_CACHE', { read: 500, write: 20 });

    const { total, isolates } = await _collectKvIsolatesBreakdown(
      'CF_EDGE_CACHE',
      ns,
    );
    expect(total.read).toBe(505);
    expect(total.write).toBe(21);
    expect(isolates).toHaveLength(2);
    // Hottest isolate (B) listed first.
    expect(isolates[0].counters.read).toBe(500);
    expect(isolates[1].counters.read).toBe(5);
    // Anonymised id — short, dash-stripped, distinct.
    expect(isolates[0].id).toBe('bbbbbbbb');
    expect(isolates[1].id).toBe('aaaaaaaa');
    expect(isolates[0].id).not.toBe(isolates[1].id);
  });

  it("does not leak a sibling isolate's previous-UTC-day counter into today's total (Task #512)", async () => {
    const kv = new FakeKv();
    const ns = kv as unknown as KVNamespace;

    // ── Pre-seed a sibling isolate's HUGE counter under YESTERDAY's
    //    day suffix, as if it had flushed right before midnight UTC and
    //    then died — its 6h TTL hasn't fired yet so the key is still
    //    physically present in the namespace. ──
    const today = new Date().toISOString().slice(0, 10);
    const y = new Date();
    y.setUTCDate(y.getUTCDate() - 1);
    const yesterday = y.toISOString().slice(0, 10);
    expect(yesterday).not.toBe(today);
    await ns.put(
      `__kv_usage:CF_EDGE_CACHE:${yesterday}:dead-isolate`,
      JSON.stringify({ read: 99_999, write: 999, list: 0, delete: 0 }),
    );

    // ── A live isolate handles 4 reads + 1 write today and serves the
    //    snapshot probe. ──
    _setKvIsolateIdForTests('live-isolate');
    _seedKvCountersForTests('CF_EDGE_CACHE', { read: 4, write: 1 });

    const { total, isolates } = await _collectKvIsolatesBreakdown(
      'CF_EDGE_CACHE',
      ns,
    );

    // The dead isolate's yesterday counter MUST NOT be summed into
    // today's totals — otherwise a single stuck isolate inflates the
    // global edge-cache tally for up to a full TTL window after its
    // traffic has stopped.
    expect(total.read).toBe(4);
    expect(total.write).toBe(1);
    expect(isolates).toHaveLength(1);
    expect(isolates[0].counters.read).toBe(4);

    // Sanity: the yesterday key really is still present in the
    // underlying namespace — this test only succeeds because the
    // aggregator filters it out, not because the store was empty.
    expect(
      kv.store.has(`__kv_usage:CF_EDGE_CACHE:${yesterday}:dead-isolate`),
    ).toBe(true);
  });

  it('defensively filters non-today keys even if listing returns them (Task #512)', async () => {
    // Simulate a misbehaving / overly broad list result by handing back
    // a yesterday-suffixed key alongside a today-suffixed one. The
    // aggregator must skip the yesterday entry based on the parsed day
    // suffix, even though the prefix-based listing wouldn't normally
    // surface it.
    const today = new Date().toISOString().slice(0, 10);
    const y = new Date();
    y.setUTCDate(y.getUTCDate() - 1);
    const yesterday = y.toISOString().slice(0, 10);
    const store = new Map<string, string>([
      [
        `__kv_usage:CF_EDGE_CACHE:${yesterday}:ghost`,
        JSON.stringify({ read: 50_000, write: 500, list: 0, delete: 0 }),
      ],
      [
        `__kv_usage:CF_EDGE_CACHE:${today}:fresh`,
        JSON.stringify({ read: 3, write: 1, list: 0, delete: 0 }),
      ],
    ]);
    const broadList = {
      get: async (k: string) => store.get(k) ?? null,
      put: async (k: string, v: string) => {
        store.set(k, v);
      },
      delete: async (k: string) => {
        store.delete(k);
      },
      // Returns BOTH days' keys regardless of the requested prefix —
      // this is what exercises the defensive day-suffix filter.
      list: async () => ({
        keys: Array.from(store.keys()).map((name) => ({ name })),
        list_complete: true,
      }),
    } as unknown as KVNamespace;

    _setKvIsolateIdForTests('local');
    const { total } = await _collectKvIsolatesBreakdown(
      'CF_EDGE_CACHE',
      broadList,
    );
    // Only the today key + this isolate's flush (zero counters) count.
    expect(total.read).toBe(3);
    expect(total.write).toBe(1);
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
