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

  it.each([
    { pathname: '/robots.txt', edgeTtl: 300, cacheControl: 'max-age=300' },
    { pathname: '/llms.txt', edgeTtl: 600, cacheControl: 'max-age=300, s-maxage=600' },
    { pathname: '/llms-full.txt', edgeTtl: 600, cacheControl: 'max-age=300, s-maxage=600' },
    { pathname: '/tollbit/subjects.json', edgeTtl: 600, cacheControl: 'max-age=300, s-maxage=600' },
  ])('keeps $pathname discovery data on a short edge cache', async ({ pathname, edgeTtl, cacheControl }) => {
    const upstreamFetch = vi.fn(async () => new Response('discovery artifact', { status: 200 }));
    vi.stubGlobal('fetch', upstreamFetch);

    const response = await pagesWorker.fetch(
      new Request(`https://syrabit.ai${pathname}`),
      {
        SEO_BACKEND_URL: 'https://api.syrabit.ai',
        BACKEND_BOT_URL: 'https://api.syrabit.ai',
        ASSETS: { fetch: async () => new Response('missing', { status: 404 }) },
      },
    );

    expect(response.status).toBe(200);
    expect(response.headers.get('cache-control')).toContain(cacheControl);
    expect(upstreamFetch.mock.calls[0][1].cf.cacheTtl).toBe(edgeTtl);
  });

  it('keeps authenticated API traffic on the first-party API origin', async () => {
    const apiFetch = vi.fn(async () => new Response('ok', { status: 200 }));
    vi.stubGlobal('fetch', apiFetch);
    const request = new Request('https://syrabit.ai/api/v1/admin/content', {
      headers: {
        Accept: 'application/json',
        Authorization: 'Bearer test-session',
        Cookie: 'session=test-cookie',
      },
    });

    const response = await pagesWorker.fetch(request, {
      API_BACKEND_URL: 'https://api.syrabit.ai',
      ASSETS: { fetch: async () => new Response('missing', { status: 404 }) },
    });

    expect(response.status).toBe(200);
    const upstream = apiFetch.mock.calls[0][0];
    expect(upstream.url).toBe('https://api.syrabit.ai/api/v1/admin/content');
    expect(upstream.headers.get('authorization')).toBe('Bearer test-session');
    expect(upstream.headers.get('cookie')).toBe('session=test-cookie');
    expect(upstream.url).not.toContain('tollbit');
  });

  it('proxies the public Tollbit subject directory with a JSON response', async () => {
    const backendFetch = vi.fn(async () => new Response(
      JSON.stringify({ schema_version: '1.0', subjects: [] }),
      { status: 200, headers: { 'Content-Type': 'application/json; charset=utf-8' } },
    ));
    vi.stubGlobal('fetch', backendFetch);

    const response = await pagesWorker.fetch(
      new Request('https://syrabit.ai/tollbit/subjects.json', {
        headers: { 'User-Agent': 'OAI-SearchBot/1.0' },
      }),
      {
        SEO_BACKEND_URL: 'https://api.syrabit.ai',
        ASSETS: { fetch: async () => new Response('missing', { status: 404 }) },
      },
    );

    expect(response.status).toBe(200);
    expect(String(backendFetch.mock.calls[0][0])).toBe(
      'https://api.syrabit.ai/api/v1/seo/tollbit/subjects.json',
    );
    expect(response.headers.get('content-type')).toContain('application/json');
    await expect(response.json()).resolves.toEqual({
      schema_version: '1.0',
      subjects: [],
    });
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

  it('returns 404 for a topic that is not reviewed and substantial', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
      chapter_id: 'chapter-1',
      published_topics: [{
        topic_slug: 'short-topic',
        title: 'Short topic',
        definition_status: 'pending',
        definition: 'A short definition.',
      }],
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })));
    const response = await pagesWorker.fetch(
      new Request('https://syrabit.ai/ahsec/hs-1st-year/physics/laws-of-motion/topic/short-topic', {
        headers: { Accept: 'text/html', 'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1)' },
      }),
      { ASSETS: { fetch: async () => new Response('missing', { status: 404 }) } },
    );

    expect(response.status).toBe(404);
    expect(response.headers.get('x-source')).toBe('bot-render-not-found');
  });

  it('resolves a percent-encoded Assamese topic slug against its published slug', async () => {
    const topicSlug = 'সাৰ্থক-সংখ্যা';
    const definitionAs = Array(45).fill('অসমীয়া').join(' ');
    const backendFetch = vi.fn(async (input) => {
      const url = String(input);
      if (url.includes('/api/v1/content/chapter-by-slug-as/')) {
        return new Response(JSON.stringify({
          chapter_id: 'chapter-1',
          published_topics: [{
            title: 'Significant Figures',
            topic_slug: 'significant-figures',
            definition: Array(45).fill('definition').join(' '),
            title_as: 'সাৰ্থক সংখ্যা',
            topic_slug_as: topicSlug,
            definition_as: definitionAs,
            definition_status: 'ok',
          }],
        }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response(
        '<!doctype html><html lang="as"><head><title>সাৰ্থক সংখ্যা</title></head><body><h1>সাৰ্থক সংখ্যা</h1></body></html>',
        { status: 200, headers: { 'Content-Type': 'text/html; charset=utf-8' } },
      );
    });
    vi.stubGlobal('fetch', backendFetch);

    const path = `/as/ahsec/hs-1st-year/physics/units-and-measurements/topic/${encodeURIComponent(topicSlug)}`;
    const response = await pagesWorker.fetch(
      new Request(`https://syrabit.ai${path}`, {
        headers: { Accept: 'text/html', 'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1)' },
      }),
      { ASSETS: { fetch: async () => new Response('missing', { status: 404 }) } },
    );

    expect(response.status).toBe(200);
    expect(response.headers.get('x-source')).toBe('bot-render');
    expect(String(backendFetch.mock.calls[0][0])).toContain(
      '/api/v1/content/chapter-by-slug-as/ahsec/hs-1st-year/physics/units-and-measurements',
    );
    expect(await response.text()).toContain('<h1>সাৰ্থক সংখ্যা</h1>');
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

  it('serves an Assamese prerender when its raw Unicode path matches the encoded request', async () => {
    const topicSlug = 'সাৰ্থক-সংখ্যা';
    const route = '/as/ahsec/hs-1st-year/physics/units-and-measurements/topic';
    const encodedPath = `${route}/${encodeURIComponent(topicSlug)}`;
    const rawUnicodePath = `${route}/${topicSlug}`;
    const html = `<!doctype html><html lang="as"><head>
      <link rel="canonical" href="https://syrabit.ai${encodedPath}" />
      <meta name="syrabit-prerender-path" content="${rawUnicodePath}" />
      <title>সাৰ্থক সংখ্যা</title></head><body><h1>সাৰ্থক সংখ্যা</h1></body></html>`;
    const fallbackFetch = vi.fn(async () => new Response(
      '<!doctype html><title>Syrabit.ai — Educational Browser For Assam Board Students</title>',
      { status: 200, headers: { 'Content-Type': 'text/html; charset=utf-8' } },
    ));
    vi.stubGlobal('fetch', fallbackFetch);

    const response = await pagesWorker.fetch(
      new Request(`https://syrabit.ai${encodedPath}`, {
        headers: {
          Accept: 'text/html',
          'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1)',
        },
      }),
      {
        ASSETS: {
          fetch: async (request) => new URL(request.url).pathname.endsWith('/index.html')
            ? new Response(html, {
              status: 200,
              headers: { 'Content-Type': 'text/html; charset=utf-8' },
            })
            : new Response('missing', { status: 404 }),
        },
      },
    );

    expect(response.status).toBe(200);
    expect(response.headers.get('x-source')).toBe('prerender');
    expect(await response.text()).toContain('<h1>সাৰ্থক সংখ্যা</h1>');
    expect(fallbackFetch).not.toHaveBeenCalled();
  });

  it('redirects decomposed Assamese topic paths to their NFC canonical URL', async () => {
    const topicSlugNfc = 'ভৌতিক';
    const topicSlugNfd = topicSlugNfc.normalize('NFD');
    expect(topicSlugNfd).not.toBe(topicSlugNfc);

    const route = '/as/ahsec/hs-1st-year/physics/units-and-measurements/topic';
    const nfdPath = `${route}/${encodeURIComponent(topicSlugNfd)}`;
    const nfcPath = `${route}/${encodeURIComponent(topicSlugNfc)}`;
    const assetFetch = vi.fn();
    const backendFetch = vi.fn();
    vi.stubGlobal('fetch', backendFetch);

    const response = await pagesWorker.fetch(
      new Request(`https://syrabit.ai${nfdPath}?source=unicode-check`, {
        headers: {
          Accept: 'text/html',
          'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1)',
        },
      }),
      { ASSETS: { fetch: assetFetch } },
    );

    expect(response.status).toBe(301);
    expect(response.headers.get('location')).toBe(`${nfcPath}?source=unicode-check`);
    expect(response.headers.get('x-source')).toBe('unicode-nfc-canonical');
    expect(assetFetch).not.toHaveBeenCalled();
    expect(backendFetch).not.toHaveBeenCalled();
  });
});
