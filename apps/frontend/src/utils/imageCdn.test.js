import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

describe('image CDN availability probe', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubEnv('DEV', false);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  it('accepts Cloudflare current resize headers with internal cache/status details', async () => {
    const fetch = vi.fn().mockResolvedValue({
      headers: {
        get: (name) => (
          name.toLowerCase() === 'cf-resized'
            ? 'internal=ram/h q=0 n=0+0 c=0+0 v=2026.10.5'
            : null
        ),
      },
    });
    vi.stubGlobal('fetch', fetch);

    const { isImageResizerAvailable, probeImageResizer } = await import('./imageCdn.js');
    await probeImageResizer('/logo-56.webp');

    expect(fetch).toHaveBeenCalledOnce();
    expect(isImageResizerAvailable()).toBe(true);
  });

  it('falls back when Cloudflare does not return a resize marker', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      headers: { get: () => null },
    }));

    const { isImageResizerAvailable, probeImageResizer } = await import('./imageCdn.js');
    await probeImageResizer('/logo-56.webp');

    expect(isImageResizerAvailable()).toBe(false);
  });

  it('uses original image URLs in local Vite development', async () => {
    vi.stubEnv('DEV', true);
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);

    const { cdnImage, probeImageResizer } = await import('./imageCdn.js');
    const original = '/logo-56.webp';
    await probeImageResizer(original);

    expect(cdnImage(original, { width: 320 })).toBe(original);
    expect(fetch).not.toHaveBeenCalled();
  });
});
