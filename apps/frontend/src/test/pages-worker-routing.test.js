import { afterEach, describe, expect, it, vi } from 'vitest';
import pagesWorker from '../../public/_worker.js';

const genericShell = `<!doctype html><html><head>
  <title>Syrabit.ai — Educational Browser For Assam Board Students | AHSEC &amp; Degree</title>
  </head><body><h1>Syrabit.ai — Educational Browser For Assam Board Students</h1></body></html>`;

afterEach(() => vi.unstubAllGlobals());

describe('Pages worker route fallbacks', () => {
  it('returns a real 404 for undeclared browser navigation routes', async () => {
    const response = await pagesWorker.fetch(
      new Request('https://syrabit.ai/contact', {
        headers: { Accept: 'text/html', 'User-Agent': 'Mozilla/5.0' },
      }),
      { ASSETS: { fetch: async () => new Response('missing', { status: 404 }) } },
    );

    expect(response.status).toBe(404);
    expect(response.headers.get('x-robots-tag')).toBe('noindex, nofollow');
  });

  it('does not return a generic SPA shell as successful crawler HTML', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(genericShell, {
      status: 200,
      headers: { 'Content-Type': 'text/html; charset=utf-8' },
    })));
    const response = await pagesWorker.fetch(
      new Request('https://syrabit.ai/ahsec/hs-1st-year/physics/newtons-laws', {
        headers: {
          Accept: 'text/html',
          'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1)',
        },
      }),
      { ASSETS: { fetch: async () => new Response('missing', { status: 404 }) } },
    );

    expect(response.status).toBe(503);
    expect(response.headers.get('x-source')).toBe('bot-render-generic-rejected');
    expect(response.headers.get('cache-control')).toBe('no-store');
  });
});
