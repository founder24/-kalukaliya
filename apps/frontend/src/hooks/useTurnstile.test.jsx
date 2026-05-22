/**
 * Task #422 — Tests for useTurnstile module-scope helpers.
 *
 * Covers:
 *   - fetchTurnstileConfig() returns disabled fallback on fetch failure
 *     (network error and non-2xx response).
 *   - fetchTurnstileConfig() caches a successful response so repeated
 *     callers do not re-hit the network.
 *   - loadTurnstileScript() is idempotent — concurrent calls share one
 *     in-flight promise and only inject one <script> tag.
 *   - loadTurnstileScript() resolves with the existing window.turnstile
 *     shortcut without injecting a new script.
 *   - _resetTurnstileForTests clears module-scope cache between cases.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('@/utils/api', () => ({
  API_BASE: 'http://test.local/api',
}));

import {
  fetchTurnstileConfig,
  loadTurnstileScript,
  _resetTurnstileForTests,
} from './useTurnstile';

const SCRIPT_SRC =
  'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';

beforeEach(() => {
  _resetTurnstileForTests();
  // Clean up any script tags or globals leaked from previous tests.
  document.querySelectorAll(`script[src="${SCRIPT_SRC}"]`).forEach((s) => s.remove());
  delete window.turnstile;
});

afterEach(() => {
  _resetTurnstileForTests();
  document.querySelectorAll(`script[src="${SCRIPT_SRC}"]`).forEach((s) => s.remove());
  delete window.turnstile;
  vi.restoreAllMocks();
});

describe('fetchTurnstileConfig', () => {
  it('returns disabled fallback when fetch rejects (network error)', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new Error('network down'))));
    const cfg = await fetchTurnstileConfig();
    expect(cfg).toEqual({ enabled: false, site_key: null });
  });

  it('returns disabled fallback when response is not ok', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}) })),
    );
    const cfg = await fetchTurnstileConfig();
    expect(cfg).toEqual({ enabled: false, site_key: null });
  });

  it('returns disabled fallback when backend says enabled but omits site_key', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ enabled: true, site_key: null }),
        }),
      ),
    );
    const cfg = await fetchTurnstileConfig();
    expect(cfg.enabled).toBe(false);
    expect(cfg.site_key).toBeNull();
  });

  it('caches a successful enabled config so repeated calls do not re-fetch', async () => {
    const mockFetch = vi.fn(() =>
      Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ enabled: true, site_key: 'KEY-123' }),
      }),
    );
    vi.stubGlobal('fetch', mockFetch);

    const first = await fetchTurnstileConfig();
    const second = await fetchTurnstileConfig();
    const third = await fetchTurnstileConfig();

    expect(first).toEqual({ enabled: true, site_key: 'KEY-123' });
    expect(second).toBe(first);
    expect(third).toBe(first);
    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(mockFetch).toHaveBeenCalledWith(
      'http://test.local/api/turnstile/config',
      { credentials: 'omit' },
    );
  });

  it('hits the network again after _resetTurnstileForTests clears the cache', async () => {
    const mockFetch = vi.fn(() =>
      Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ enabled: true, site_key: 'KEY-A' }),
      }),
    );
    vi.stubGlobal('fetch', mockFetch);

    await fetchTurnstileConfig();
    expect(mockFetch).toHaveBeenCalledTimes(1);

    _resetTurnstileForTests();
    await fetchTurnstileConfig();
    expect(mockFetch).toHaveBeenCalledTimes(2);
  });
});

describe('loadTurnstileScript', () => {
  it('returns the existing window.turnstile without injecting a script', async () => {
    const stub = { render: vi.fn() };
    window.turnstile = stub;
    const result = await loadTurnstileScript();
    expect(result).toBe(stub);
    expect(document.querySelectorAll(`script[src="${SCRIPT_SRC}"]`)).toHaveLength(0);
  });

  it('shares one in-flight promise across concurrent callers', () => {
    const p1 = loadTurnstileScript();
    const p2 = loadTurnstileScript();
    const p3 = loadTurnstileScript();
    expect(p2).toBe(p1);
    expect(p3).toBe(p1);
    // Only one <script> tag injected for the in-flight load.
    expect(document.querySelectorAll(`script[src="${SCRIPT_SRC}"]`)).toHaveLength(1);
  });

  it('resolves with window.turnstile once the injected script fires onload', async () => {
    const promise = loadTurnstileScript();
    const script = document.querySelector(`script[src="${SCRIPT_SRC}"]`);
    expect(script).not.toBeNull();

    const stub = { render: vi.fn(), reset: vi.fn(), remove: vi.fn() };
    window.turnstile = stub;
    script.onload();

    const result = await promise;
    expect(result).toBe(stub);
  });

  it('rejects and clears the cached promise on script error so a retry can re-inject', async () => {
    const failing = loadTurnstileScript();
    const script = document.querySelector(`script[src="${SCRIPT_SRC}"]`);
    expect(script).not.toBeNull();
    const err = new Event('error');
    script.onerror(err);

    await expect(failing).rejects.toBeDefined();

    // Second call after failure must produce a *new* promise, not the
    // cached rejected one.
    document.querySelectorAll(`script[src="${SCRIPT_SRC}"]`).forEach((s) => s.remove());
    const retry = loadTurnstileScript();
    expect(document.querySelectorAll(`script[src="${SCRIPT_SRC}"]`)).toHaveLength(1);
    expect(retry).not.toBe(failing);
  });
});
