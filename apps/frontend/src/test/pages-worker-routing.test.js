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
    expect(response.headers.get('x-source')).toBe('study-route-validation-unavailable');
    expect(response.headers.get('cache-control')).toBe('no-store');
  });

  it('returns a real 404 for a stale curriculum path in browser navigation', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{"detail":"Chapter not found"}', {
      status: 404,
      headers: { 'Content-Type': 'application/json' },
    })));
    const response = await pagesWorker.fetch(
      new Request('https://syrabit.ai/ahsec/class-12/physics/not-a-real-chapter', {
        headers: { Accept: 'text/html', 'User-Agent': 'Mozilla/5.0' },
      }),
      { ASSETS: { fetch: async () => new Response('missing', { status: 404 }) } },
    );

    expect(response.status).toBe(404);
    expect(response.headers.get('x-robots-tag')).toBe('noindex, nofollow');
    expect(response.headers.get('x-source')).toBe('bot-render-not-found');
  });

  it('returns a real 404 for a missing subject instead of serving the SPA shell', async () => {
    const apiFetch = vi.fn(async () => new Response('{"detail":"Subject not found"}', {
      status: 404,
      headers: { 'Content-Type': 'application/json' },
    }));
    vi.stubGlobal('fetch', apiFetch);
    const response = await pagesWorker.fetch(
      new Request('https://syrabit.ai/ahsec/class-12/missing-subject', {
        headers: { Accept: 'text/html', 'User-Agent': 'Mozilla/5.0' },
      }),
      { ASSETS: { fetch: async () => new Response(genericShell, {
        status: 200,
        headers: { 'Content-Type': 'text/html; charset=utf-8' },
      }) } },
    );

    expect(response.status).toBe(404);
    expect(apiFetch.mock.calls[0][0]).toContain('/api/v1/content/resolve-subject/ahsec/class-12/missing-subject');
  });

  it('keeps HEAD probes for stale curriculum paths at 404 too', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{"detail":"Chapter not found"}', {
      status: 404,
      headers: { 'Content-Type': 'application/json' },
    })));
    const response = await pagesWorker.fetch(
      new Request('https://syrabit.ai/ahsec/class-12/physics/not-a-real-chapter', {
        method: 'HEAD',
        headers: { Accept: '*/*', 'User-Agent': 'Mozilla/5.0' },
      }),
      { ASSETS: { fetch: async () => new Response('missing', { status: 404 }) } },
    );

    expect(response.status).toBe(404);
    expect(response.headers.get('x-robots-tag')).toBe('noindex, nofollow');
  });

  it('redirects only an API-verified old chapter slug to its canonical URL', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
      chapter_id: 'chapter-1',
      canonical_slug: 'official-chapter',
      slug_redirect: { from: 'old-chapter', to: 'official-chapter' },
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })));
    const response = await pagesWorker.fetch(
      new Request('https://syrabit.ai/ahsec/class-12/physics/old-chapter', {
        headers: { Accept: 'text/html', 'User-Agent': 'Mozilla/5.0' },
      }),
      { ASSETS: { fetch: async () => new Response('missing', { status: 404 }) } },
    );

    expect(response.status).toBe(301);
    expect(response.headers.get('location')).toBe('/ahsec/class-12/physics/official-chapter');
    expect(response.headers.get('x-source')).toBe('verified-chapter-alias');
  });

  it('redirects stale prerender aliases using their source-backed canonical metadata', async () => {
    const response = await pagesWorker.fetch(
      new Request('https://syrabit.ai/ahsec/class-12/physics/old-chapter', {
        headers: { Accept: 'text/html', 'User-Agent': 'Mozilla/5.0' },
      }),
      {
        ASSETS: {
          fetch: async () => new Response(
            '<html><head><link rel="canonical" href="https://syrabit.ai/ahsec/class-12/physics/official-chapter"><meta name="syrabit-prerender-path" content="/ahsec/class-12/physics/old-chapter"></head></html>',
            { status: 200, headers: { 'Content-Type': 'text/html' } },
          ),
        },
      },
    );

    expect(response.status).toBe(301);
    expect(response.headers.get('location')).toBe('/ahsec/class-12/physics/official-chapter');
    expect(response.headers.get('x-source')).toBe('verified-prerender-canonical');
  });
});
