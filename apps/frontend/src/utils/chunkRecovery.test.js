import { describe, expect, it, vi } from 'vitest';
import { handlePreloadError } from './chunkRecovery';

describe('stale chunk recovery', () => {
  it('reloads once when a deployed chunk hash no longer exists', () => {
    const event = { preventDefault: vi.fn() };
    const location = { reload: vi.fn() };
    const values = new Map();
    const storage = {
      getItem: key => values.get(key) ?? null,
      setItem: (key, value) => values.set(key, value),
    };

    expect(handlePreloadError(event, { location, storage, now: 50_000 })).toBe(true);
    // Do not prevent Vite's rejection. preventDefault() makes Vite resolve the
    // lazy import with undefined, which crashes React while reading .default.
    expect(event.preventDefault).not.toHaveBeenCalled();
    expect(location.reload).toHaveBeenCalledOnce();
    expect(handlePreloadError(event, { location, storage, now: 55_000 })).toBe(false);
    expect(location.reload).toHaveBeenCalledOnce();
  });
});