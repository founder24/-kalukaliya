import { afterEach, describe, expect, it, vi } from 'vitest';

import tollbitWorker, {
  buildTollbitLogEntry,
  isPublicTollbitContentPath,
  makeTollbitSiteRequest,
} from './worker';

const makeContext = () => {
  const pending: Promise<unknown>[] = [];
  return {
    pending,
    waitUntil(promise: Promise<unknown>) {
      pending.push(promise);
    },
  } as unknown as ExecutionContext & { pending: Promise<unknown>[] };
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('Tollbit Worker routing and analytics', () => {
  it('routes only approved agents on public GET/HEAD page paths', async () => {
    const request = new Request(
      'https://www.syrabit.ai/degree/1st-semester/ba/course/chapter-one',
      {
        headers: { 'User-Agent': 'OAI-SearchBot/1.0', Accept: 'text/html' },
      },
    );

    let targetUrl = '';
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      targetUrl = input instanceof Request ? input.url : String(input);
      return new Response('page', {
        status: 200,
        headers: { 'Content-Type': 'text/html' },
      });
    }));

    const response = await tollbitWorker.fetch(
      request,
      {},
      makeContext(),
    );

    expect(response.status).toBe(200);
    expect(targetUrl).toBe(
      'https://tollbit.syrabit.ai/degree/1st-semester/ba/course/chapter-one',
    );
  });

  it('routes Cloudflare-verified Search Engine requests through Tollbit', async () => {
    const request = new Request(
      'https://syrabit.ai/ahsec/hs-1st-year/physics/laws-of-motion',
      {
        headers: {
          Accept: 'text/html',
          'User-Agent': 'Googlebot/2.1 (+http://www.google.com/bot.html)',
        },
      },
    );
    Object.defineProperty(request, 'cf', {
      value: {
        botManagement: { verifiedBot: true },
        verifiedBotCategory: 'Search Engine',
      },
    });
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = input instanceof Request ? input.url : String(input);
      return new Response(url.startsWith('https://tollbit.syrabit.ai/')
        ? 'Tollbit Agent Site'
        : 'publisher origin', { status: 200 });
    });
    vi.stubGlobal('fetch', fetchMock);
    vi.spyOn(console, 'warn').mockImplementation(() => {});

    const response = await tollbitWorker.fetch(request, {}, makeContext());

    expect(response.status).toBe(200);
    expect(await response.text()).toBe('Tollbit Agent Site');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[0]).toBeInstanceOf(Request);
    expect((fetchMock.mock.calls[0]?.[0] as Request).url).toBe(
      'https://tollbit.syrabit.ai/ahsec/hs-1st-year/physics/laws-of-motion',
    );
  });

  it('blocks a Googlebot user-agent claim that Cloudflare did not verify', async () => {
    const request = new Request(
      'https://syrabit.ai/ahsec/hs-1st-year/physics/laws-of-motion',
      {
        headers: {
          'User-Agent': 'Googlebot/2.1 (+http://www.google.com/bot.html)',
        },
      },
    );
    const fetchMock = vi.fn(async (input: RequestInfo | URL) =>
      new Response(input instanceof Request ? input.url : String(input), {
        status: 200,
      }),
    );
    vi.stubGlobal('fetch', fetchMock);
    vi.spyOn(console, 'warn').mockImplementation(() => {});

    const response = await tollbitWorker.fetch(request, {}, makeContext());

    expect(response.status).toBe(403);
    expect(await response.json()).toEqual({
      error: 'unverified_search_crawler',
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('blocks a verified bot in a non-search category from free access', async () => {
    const request = new Request(
      'https://syrabit.ai/ahsec/hs-1st-year/physics/laws-of-motion',
      {
        headers: {
          'User-Agent': 'Googlebot/2.1 (+http://www.google.com/bot.html)',
        },
      },
    );
    Object.defineProperty(request, 'cf', {
      value: {
        botManagement: { verifiedBot: true },
        verifiedBotCategory: 'AI Search',
      },
    });
    const fetchMock = vi.fn(async (input: RequestInfo | URL) =>
      new Response(input instanceof Request ? input.url : String(input), {
        status: 200,
      }),
    );
    vi.stubGlobal('fetch', fetchMock);
    vi.spyOn(console, 'warn').mockImplementation(() => {});

    const response = await tollbitWorker.fetch(request, {}, makeContext());

    expect(response.status).toBe(403);
    expect(await response.json()).toEqual({
      error: 'unverified_search_crawler',
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('keeps training denial ahead of verified Search Engine forwarding', async () => {
    const request = new Request(
      'https://syrabit.ai/ahsec/hs-1st-year/physics/laws-of-motion',
      { headers: { 'User-Agent': 'Googlebot/2.1 GPTBot' } },
    );
    Object.defineProperty(request, 'cf', {
      value: {
        botManagement: { verifiedBot: true },
        verifiedBotCategory: 'Search Engine',
      },
    });
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    vi.spyOn(console, 'warn').mockImplementation(() => {});

    const response = await tollbitWorker.fetch(request, {}, makeContext());

    expect(response.status).toBe(403);
    expect(await response.json()).toEqual({
      error: 'ai_training_not_permitted',
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('limits the workers.dev test endpoint to Tollbit source IPs', async () => {
    const fetchMock = vi.fn(async () => new Response('should not fetch'));
    vi.stubGlobal('fetch', fetchMock);
    const env = {
      TOLLBIT_TEST_MODE: 'true',
      TOLLBIT_TEST_PUBLISHER_HOST: 'syrabit.ai',
      TOLLBIT_TEST_SOURCE_IPS: '52.22.183.94,3.220.109.109',
    };

    const blocked = await tollbitWorker.fetch(
      new Request(
        'https://syrabit-tollbit-worker-draft.test-account.workers.dev/degree/1st-semester/ba/course',
      ),
      env,
      makeContext(),
    );
    expect(blocked.status).toBe(404);

    const health = await tollbitWorker.fetch(
      new Request(
        'https://syrabit-tollbit-worker-draft.test-account.workers.dev/__tollbit_test_health',
      ),
      env,
      makeContext(),
    );
    expect(health.status).toBe(200);
    expect(await health.text()).toBe('OK');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('maps allowlisted workers.dev requests to the fixed publisher host', async () => {
    const fetchCalls: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = input instanceof Request ? input.url : String(input);
        const requestInit = input instanceof Request
          ? {
              method: input.method,
              headers: input.headers,
              body: input.body,
            }
          : init;
        fetchCalls.push({ url, init: requestInit });
        return url === 'https://log.tollbit.com/log'
          ? new Response(null, { status: 200 })
          : new Response('Agent Site page', {
              status: 200,
              headers: { 'Content-Type': 'text/html' },
            });
      }),
    );
    const ctx = makeContext();
    const response = await tollbitWorker.fetch(
      new Request(
        'https://syrabit-tollbit-worker-draft.test-account.workers.dev/degree/1st-semester/ba/course?email=private%40example.com',
        {
          headers: {
            'cf-connecting-ip': '52.22.183.94',
            'User-Agent': 'OAI-SearchBot/1.0',
            Signature: 'sig1=:test-signature:',
            'Signature-Agent': 'https://agent.example',
            'Signature-Input': 'sig1=("@method" "@target-uri")',
          },
        },
      ),
      {
        TOLLBIT_TEST_MODE: 'true',
        TOLLBIT_TEST_PUBLISHER_HOST: 'syrabit.ai',
        TOLLBIT_TEST_SOURCE_IPS: '52.22.183.94,3.220.109.109',
        TOLLBIT_API_KEY: 'test-only-key',
      },
      ctx,
    );

    expect(response.status).toBe(200);
    expect(fetchCalls[0]?.url).toBe(
      'https://tollbit.syrabit.ai/degree/1st-semester/ba/course',
    );
    expect(ctx.pending).toHaveLength(1);
    await Promise.all(ctx.pending);

    const logCall = fetchCalls.find(
      (call) => call.url === 'https://log.tollbit.com/log',
    );
    expect(logCall).toBeDefined();
    const record = JSON.parse(String(logCall?.init?.body));
    expect(record).toMatchObject({
      host: 'syrabit.ai',
      url: '/degree/1st-semester/ba/course',
      request_user_agent: 'OAI-SearchBot/1.0',
    });
    expect(JSON.stringify(record)).not.toContain('private@example.com');
  });

  it('accepts only the staged public content route shapes', () => {
    expect(isPublicTollbitContentPath('/library')).toBe(true);
    expect(
      isPublicTollbitContentPath(
        '/as/degree/1st-semester/ba/course/chapter-one',
      ),
    ).toBe(true);
    expect(isPublicTollbitContentPath('/unknown/path')).toBe(false);
    expect(isPublicTollbitContentPath('/degree/%2e%2e/admin')).toBe(false);
    expect(isPublicTollbitContentPath('/degree/%2Fstaff')).toBe(false);
  });

  it('does not proxy private or non-GET requests', async () => {
    const targets: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      targets.push(input instanceof Request ? input.url : String(input));
      return new Response('origin', {
        status: 200,
        headers: { 'Content-Type': 'text/html' },
      });
    }));

    const requests = [
      new Request('https://syrabit.ai/profile', {
        headers: { 'User-Agent': 'OAI-SearchBot' },
      }),
      new Request('https://syrabit.ai/degree/1st-semester/ba/course', {
        method: 'POST',
        headers: { 'User-Agent': 'OAI-SearchBot' },
        body: 'not-forwarded',
      }),
    ];

    for (const request of requests) {
      const response = await tollbitWorker.fetch(request, {}, makeContext());
      expect(response.status).toBe(200);
    }

    expect(targets).toEqual(requests.map((request) => request.url));
  });

  it('forwards attribution signatures but strips credentials and query data', () => {
    const request = new Request(
      'https://www.syrabit.ai/degree/1st-semester/ba/course?email=student%40example.com',
      {
        headers: {
          Accept: 'text/html',
          Authorization: 'Bearer private-token',
          Cookie: 'session=private',
          'User-Agent': 'OAI-SearchBot',
          'X-Anon-ID': 'anon-private',
          'Signature-Agent': 'https://agent.example',
          'Signature-Input': 'sig1=("@method" "@target-uri")',
          Signature: 'sig1=:opaque-signature:',
        },
      },
    );

    const forwarded = makeTollbitSiteRequest(request);

    expect(forwarded.url).toBe(
      'https://tollbit.syrabit.ai/degree/1st-semester/ba/course',
    );
    expect(forwarded.headers.get('authorization')).toBeNull();
    expect(forwarded.headers.get('cookie')).toBeNull();
    expect(forwarded.headers.get('x-anon-id')).toBeNull();
    expect(forwarded.headers.get('accept')).toBe('text/html');
    expect(forwarded.headers.get('user-agent')).toBe('OAI-SearchBot');
    expect(forwarded.headers.get('signature-agent')).toBe(
      'https://agent.example',
    );
    expect(forwarded.headers.get('signature-input')).toBe(
      'sig1=("@method" "@target-uri")',
    );
    expect(forwarded.headers.get('signature')).toBe(
      'sig1=:opaque-signature:',
    );
  });

  it('preserves verified search user-agent while stripping credentials', () => {
    const request = new Request(
      'https://www.syrabit.ai/degree/1st-semester/ba/course?email=student%40example.com',
      {
        headers: {
          Authorization: 'Bearer private-token',
          Cookie: 'session=private',
          'User-Agent': 'Bingbot/2.0',
        },
      },
    );
    Object.defineProperty(request, 'cf', {
      value: {
        botManagement: { verifiedBot: true },
        verifiedBotCategory: 'Search Engine',
      },
    });

    const forwarded = makeTollbitSiteRequest(request);

    expect(forwarded.url).toBe(
      'https://tollbit.syrabit.ai/degree/1st-semester/ba/course',
    );
    expect(forwarded.headers.get('user-agent')).toBe('Bingbot/2.0');
    expect(forwarded.headers.get('authorization')).toBeNull();
    expect(forwarded.headers.get('cookie')).toBeNull();
  });

  it('logs standard request and geo fields without query or credential data', () => {
    const request = new Request(
      'https://syrabit.ai/degree/1st-semester/ba/course?email=student%40example.com',
      {
        headers: {
          Authorization: 'Bearer private-token',
          Cookie: 'session=private',
          'CF-Connecting-IP': '203.0.113.8',
          Referer: 'https://private.example/study?private=value#section',
          'User-Agent': 'OAI-SearchBot',
          Signature: 'sig1=:opaque-signature:',
          'Signature-Agent': 'https://agent.example',
          'Signature-Input': 'sig1=("@method" "@target-uri")',
        },
      },
    );
    Object.defineProperty(request, 'cf', {
      value: {
        httpProtocol: 'HTTP/2',
        country: 'IN',
        city: 'Guwahati',
        postalCode: '781001',
        latitude: '26.1445',
        longitude: '91.7362',
      },
    });
    const response = new Response('page', {
      status: 200,
      headers: { 'Content-Length': '4', 'Content-Type': 'text/html' },
    });

    const record = buildTollbitLogEntry(
      request,
      response,
      new Date('2026-11-02T00:00:00.000Z'),
    );

    expect(record).toMatchObject({
      timestamp: '2026-11-02T00:00:00.000Z',
      ip_address: '203.0.113.8',
      geo_country: 'IN',
      geo_city: 'Guwahati',
      geo_postal_code: '781001',
      geo_latitude: '26.1445',
      geo_longitude: '91.7362',
      host: 'syrabit.ai',
      url: '/degree/1st-semester/ba/course',
      request_method: 'GET',
      request_protocol: 'HTTP/2',
      response_status: 200,
      response_body_size: 4,
      signature: 'sig1=:opaque-signature:',
      request_referer: 'https://private.example/study',
    });
    expect(JSON.stringify(record)).not.toContain('student@example.com');
    expect(JSON.stringify(record)).not.toContain('private-token');
    expect(JSON.stringify(record)).not.toContain('private=value');
    expect(JSON.stringify(record)).not.toContain('session=private');
  });

  it('posts the telemetry asynchronously and leaves page delivery independent', async () => {
    const info = vi.spyOn(console, 'info').mockImplementation(() => {});
    const fetchCalls: Array<{ url: string; init?: RequestInit }> = [];
    let resolveLogResponse!: (response: Response) => void;
    const logResponse = new Promise<Response>((resolve) => {
      resolveLogResponse = resolve;
    });
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = input instanceof Request ? input.url : String(input);
        const requestInit = input instanceof Request
          ? {
              method: input.method,
              headers: input.headers,
              body: input.body,
            }
          : init;
        fetchCalls.push({ url, init: requestInit });
        return url === 'https://log.tollbit.com/log'
          ? logResponse
          : new Response('page', {
              status: 200,
              headers: { 'Content-Type': 'text/html' },
            });
      }),
    );
    const ctx = makeContext();

    const response = await tollbitWorker.fetch(
      new Request('https://syrabit.ai/degree/1st-semester/ba/course', {
        headers: {
          'User-Agent': 'Mozilla/5.0',
          Signature: 'sig1=:opaque-signature:',
          'Signature-Agent': 'https://agent.example',
          'Signature-Input': 'sig1=("@method" "@target-uri")',
        },
      }),
      { TOLLBIT_API_KEY: 'test-only-key' },
      ctx,
    );

    expect(response.status).toBe(200);
    expect(fetchCalls).toHaveLength(2);
    expect(ctx.pending).toHaveLength(1);
    resolveLogResponse(new Response(null, { status: 200 }));
    await Promise.all(ctx.pending);

    const logCall = fetchCalls.find(
      (call) => call.url === 'https://log.tollbit.com/log',
    );
    expect(logCall).toBeDefined();
    const headers = new Headers(logCall?.init?.headers);
    expect(headers.get('TollbitKey')).toBe('test-only-key');
    expect(headers.get('Content-Type')).toBe('application/json');
    const body = String(logCall?.init?.body);
    expect(body.endsWith('\n')).toBe(true);
    expect(JSON.parse(body)).toMatchObject({
      url: '/degree/1st-semester/ba/course',
      request_method: 'GET',
      response_status: 200,
    });
    expect(info).toHaveBeenCalledWith(
      'Tollbit analytics delivery accepted.',
      expect.objectContaining({
        status: 200,
        durationMs: expect.any(Number),
      }),
    );
  });

  it('reports a sanitized Tollbit rejection status without affecting the page', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) =>
      (input instanceof Request ? input.url : String(input)) === 'https://log.tollbit.com/log'
        ? new Response('private provider error', { status: 401 })
        : new Response('page', { status: 200 }),
    ));
    const ctx = makeContext();

    const response = await tollbitWorker.fetch(
      new Request('https://syrabit.ai/library'),
      { TOLLBIT_API_KEY: 'never-log-this-key' },
      ctx,
    );
    await Promise.all(ctx.pending);

    expect(response.status).toBe(200);
    expect(await response.text()).toBe('page');
    expect(warn).toHaveBeenCalledWith(
      'Tollbit analytics delivery rejected.',
      expect.objectContaining({
        status: 401,
        durationMs: expect.any(Number),
      }),
    );
    const diagnostics = JSON.stringify(warn.mock.calls);
    expect(diagnostics).not.toContain('never-log-this-key');
    expect(diagnostics).not.toContain('private provider error');
  });

  it('logs eligible public HTML traffic without requiring signature headers', async () => {
    const fetchCalls: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = input instanceof Request ? input.url : String(input);
        const requestInit = input instanceof Request
          ? {
              method: input.method,
              headers: input.headers,
              body: input.body,
            }
          : init;
        fetchCalls.push({ url, init: requestInit });
        return new Response('page', {
          status: 200,
          headers: { 'Content-Type': 'text/html' },
        });
      }),
    );
    const ctx = makeContext();

    const response = await tollbitWorker.fetch(
      new Request('https://syrabit.ai/degree/1st-semester/ba/course', {
        headers: { 'User-Agent': 'Mozilla/5.0' },
      }),
      { TOLLBIT_API_KEY: 'test-only-key' },
      ctx,
    );

    expect(response.status).toBe(200);
    expect(fetchCalls.map((call) => call.url)).toEqual([
      'https://syrabit.ai/degree/1st-semester/ba/course',
      'https://log.tollbit.com/log',
    ]);
    expect(ctx.pending).toHaveLength(1);
    await Promise.all(ctx.pending);
    const logCall = fetchCalls.find(
      (call) => call.url === 'https://log.tollbit.com/log',
    );
    const entry = JSON.parse(String(logCall?.init?.body));
    expect(entry).toMatchObject({
      url: '/degree/1st-semester/ba/course',
      request_user_agent: 'Mozilla/5.0',
      response_status: 200,
    });
    expect(entry).not.toHaveProperty('signature');
  });

  it('does not send telemetry or fail the page when no secret is configured', async () => {
    const fetchMock = vi.fn(async () =>
      new Response('page', {
        status: 200,
        headers: { 'Content-Type': 'text/html' },
      }),
    );
    vi.stubGlobal('fetch', fetchMock);
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});

    const response = await tollbitWorker.fetch(
      new Request('https://syrabit.ai/library', {
        headers: {
          Signature: 'sig1=:opaque-signature:',
          'Signature-Agent': 'https://agent.example',
          'Signature-Input': 'sig1=("@method" "@target-uri")',
        },
      }),
      {},
      makeContext(),
    );

    expect(response.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(warn).toHaveBeenCalledWith(
      'Tollbit analytics disabled: secret binding is missing.',
    );
  });
});
