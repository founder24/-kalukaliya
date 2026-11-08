import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import worker, { isPublicTollbitContentPath, TOLLBIT_FETCH_TIMEOUT_MS } from './worker';
import {
  AGENT_SITE_USER_AGENTS,
  matchesAgent,
  TRAINING_ONLY_USER_AGENTS,
} from './policy';

const PAGE = 'https://syrabit.ai/ahsec/hs-1st-year/physics/laws-of-motion';
const LOG = 'https://log.tollbit.com/log';
function context() {
  const pending: Promise<unknown>[] = [];
  return { pending, waitUntil: (p: Promise<unknown>) => pending.push(p) } as unknown as
    ExecutionContext & { pending: Promise<unknown>[] };
}
const request = (ua: string, headers = {}, method = 'GET', url = PAGE) =>
  new Request(url, { method, headers: { 'User-Agent': ua, ...headers } });
const withCf = (req: Request, cf: Record<string, unknown>) => {
  Object.defineProperty(req, 'cf', { value: cf });
  return req;
};
const target = (input: RequestInfo | URL) => input instanceof Request ? input.url : String(input);

beforeEach(() => {
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  vi.spyOn(console, 'info').mockImplementation(() => {});
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('Tollbit public-content gate', () => {
  it('matches supplied agent tokens without matching generic substrings', () => {
    expect(matchesAgent(
      'Mozilla/5.0 (compatible; DeepSeekBot/1.0)',
      AGENT_SITE_USER_AGENTS,
    )).toBe(true);
    expect(matchesAgent(
      'Mozilla/5.0 (CodeMirror/6.0)',
      AGENT_SITE_USER_AGENTS,
    )).toBe(false);
    expect(matchesAgent(
      'Mozilla/5.0 (VSCode/1.0)',
      AGENT_SITE_USER_AGENTS,
    )).toBe(false);
    expect(matchesAgent(
      'Mozilla/5.0 (compatible; GPTBot/1.0)',
      TRAINING_ONLY_USER_AGENTS,
    )).toBe(true);
  });

  it.each(AGENT_SITE_USER_AGENTS)('gates retrieval agent %s', async (ua) => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL) => new Response('token required', { status: 402 }));
    vi.stubGlobal('fetch', fetchMock);
    const response = await worker.fetch(request(ua), {}, context());
    expect(response.status).toBe(402);
    expect(target(fetchMock.mock.calls[0]![0]!)).toBe(PAGE.replace('syrabit.ai', 'tollbit.syrabit.ai'));
    expect(response.headers.get('cache-control')).toBe('no-store');
  });

  it.each(TRAINING_ONLY_USER_AGENTS)('blocks training agent %s locally', async (ua) => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    const response = await worker.fetch(request(ua, { Cookie: 'audit_probe=1' }), {}, context());
    expect(response.status).toBe(403);
    expect(await response.json()).toEqual({ error: 'ai_training_not_permitted' });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('does not let a retrieval name override a training match', async () => {
    vi.stubGlobal('fetch', vi.fn());
    expect((await worker.fetch(request('GPTBot ChatGPT-User'), {}, context())).status).toBe(403);
    expect(fetch).not.toHaveBeenCalled();
  });

  it.each(['Cookie', 'Authorization', 'X-Api-Key', 'X-User-Id', 'X-Anon-Id'])(
    '%s cannot bypass the gate or be forwarded', async (credential) => {
      const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
        const upstream = input as Request;
        expect(upstream.headers.get(credential)).toBeNull();
        expect(upstream.url).toContain('https://tollbit.syrabit.ai/');
        return new Response(null, { status: 402 });
      });
      vi.stubGlobal('fetch', fetchMock);
      const response = await worker.fetch(request('ChatGPT-User', { [credential]: 'private-test-value' }), {}, context());
      expect(response.status).toBe(402);
      expect(fetchMock).toHaveBeenCalledTimes(1);
    },
  );

  it.each(['/', '/home', '/home/'])('gates homepage %s', async (path) => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(null, { status: 402 })));
    expect(isPublicTollbitContentPath(path)).toBe(true);
    expect((await worker.fetch(request('ChatGPT-User', {}, 'GET', `https://www.syrabit.ai${path}`), {}, context())).status).toBe(402);
  });

  it.each([
    '/as/ahsec/hs-1st-year/physics/laws-of-motion',
    '/degree/1st-semester/ba/course',
    '/as/degree/1st-semester/ba/course/chapter/topic/topic-one',
  ])('gates bilingual study route %s', async (path) => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(null, { status: 402 })));
    expect((await worker.fetch(request('PerplexityBot', {}, 'GET', `https://syrabit.ai${path}`), {}, context())).status).toBe(402);
  });

  it.each(['/chat', '/profile', '/staff', '/admin', '/api/health', '/assets/main.js', '/tollbit/subjects.json', '/llms.txt', '/robots.txt'])(
    'preserves origin routing outside study pages: %s', async (path) => {
      vi.stubGlobal('fetch', vi.fn(async () => new Response('origin')));
      const ctx = context();
      expect(await (await worker.fetch(request('ChatGPT-User', {}, 'GET', `https://syrabit.ai${path}`), {}, ctx)).text()).toBe('origin');
      expect(target(vi.mocked(fetch).mock.calls[0]![0])).toBe(`https://syrabit.ai${path}`);
      expect(ctx.pending).toHaveLength(0);
    },
  );

  it.each(['Mozilla/5.0', 'tollbot/1.0'])('preserves %s origin responses', async (ua) => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('origin')));
    expect(await (await worker.fetch(request(ua), {}, context())).text()).toBe('origin');
    expect(target(vi.mocked(fetch).mock.calls[0]![0])).toBe(PAGE);
  });

  it.each(['CodeMirror/6.0', 'VSCode/1.0', 'MysteryCrawler/1.0'])(
    'does not treat an unlisted User-Agent (%s) as bot proof', async (ua) => {
      vi.stubGlobal('fetch', vi.fn(async () => new Response('origin')));
      const response = await worker.fetch(request(ua), {}, context());
      expect(response.status).toBe(200);
      expect(target(vi.mocked(fetch).mock.calls[0]![0])).toBe(PAGE);
    },
  );

  it.each([1, 2, 29])(
    'routes an unknown agent to TollBit with Cloudflare automated score %i',
    async (score) => {
      const fetchMock = vi.fn(async (_input: RequestInfo | URL) =>
        new Response('licensed path', { status: 402 }));
      vi.stubGlobal('fetch', fetchMock);
      const req = withCf(request('MysteryCrawler/1.0'), {
        botManagement: { score },
      });

      expect((await worker.fetch(req, {}, context())).status).toBe(402);
      expect(target(fetchMock.mock.calls[0]![0]!)).toBe(
        PAGE.replace('syrabit.ai', 'tollbit.syrabit.ai'),
      );
    },
  );

  it.each([
    ['score 0 means not computed', { score: 0 }],
    ['score 30 is likely human', { score: 30 }],
    ['score 99 is likely human', { score: 99 }],
    ['corporate proxy is exempt', { score: 5, corporateProxy: true }],
    ['static resource is exempt', { score: 5, staticResource: true }],
  ])('keeps ambiguous or human-like traffic on origin: %s', async (_case, botManagement) => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('origin')));
    const req = withCf(request('Mozilla/5.0'), { botManagement });

    expect((await worker.fetch(req, {}, context())).status).toBe(200);
    expect(target(vi.mocked(fetch).mock.calls[0]![0])).toBe(PAGE);
  });

  it('fails open when Bot Management score is missing or the User-Agent is blank', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('origin')));
    const noScore = withCf(request('MysteryCrawler/1.0'), { botManagement: {} });
    const noUserAgent = withCf(
      new Request(PAGE),
      { botManagement: { score: 1 } },
    );

    expect((await worker.fetch(noScore, {}, context())).status).toBe(200);
    expect(target(vi.mocked(fetch).mock.calls[0]![0])).toBe(PAGE);
    vi.mocked(fetch).mockClear();
    expect((await worker.fetch(noUserAgent, {}, context())).status).toBe(200);
    expect(target(vi.mocked(fetch).mock.calls[0]![0])).toBe(PAGE);
  });

  it.each([
    ['Cloudflare verified bot', { verifiedBot: true }],
    ['Cloudflare signed agent', { signedAgent: true }],
  ])('routes an unlisted %s through TollBit', async (_case, botManagement) => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL) =>
      new Response('paid path', { status: 402 }));
    vi.stubGlobal('fetch', fetchMock);
    const req = withCf(request('UnlistedAgent/1.0'), { botManagement });

    expect((await worker.fetch(req, {}, context())).status).toBe(402);
    expect(target(fetchMock.mock.calls[0]![0]!)).toBe(
      PAGE.replace('syrabit.ai', 'tollbit.syrabit.ai'),
    );
  });

  it('does not let a low bot score override the training block', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    const req = withCf(request('GPTBot/1.0'), {
      botManagement: { score: 1, verifiedBot: true },
    });

    expect((await worker.fetch(req, {}, context())).status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('blocks Cloudflare-verified AI Crawler category before Agent Site routing', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    const req = withCf(request('UnlistedAgent/1.0'), {
      botManagement: { score: 1, verifiedBot: true },
      verifiedBotCategory: 'AI Crawler',
    });

    expect((await worker.fetch(req, {}, context())).status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('keeps Cloudflare-verified AI Search on the paid Agent Site path', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL) =>
      new Response('paid path', { status: 402 }));
    vi.stubGlobal('fetch', fetchMock);
    const req = withCf(request('UnlistedSearchBot/1.0'), {
      botManagement: { verifiedBot: true },
      verifiedBotCategory: 'AI Search',
    });

    expect((await worker.fetch(req, {}, context())).status).toBe(402);
    expect(target(fetchMock.mock.calls[0]![0]!)).toBe(
      PAGE.replace('syrabit.ai', 'tollbit.syrabit.ai'),
    );
  });

  it.each(['Googlebot', 'Bingbot', 'Applebot', 'DuckDuckBot'])(
    'blocks unverified %s claims before they reach origin', async (ua) => {
      const fetchMock = vi.fn();
      vi.stubGlobal('fetch', fetchMock);
      const response = await worker.fetch(request(ua), {}, context());

      expect(response.status).toBe(403);
      expect(await response.json()).toEqual({
        error: 'unverified_search_crawler',
      });
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it('fails closed without fetching the origin on provider connection failure', async () => {
    const fetchMock = vi.fn(async () => { throw new Error('private upstream detail'); });
    vi.stubGlobal('fetch', fetchMock);
    const response = await worker.fetch(request('ChatGPT-User'), {}, context());
    expect(response.status).toBe(503);
    expect(response.headers.get('retry-after')).toBe('60');
    expect(await response.text()).not.toContain('private');
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('bounds stalled Agent Site fetches and aborts them', async () => {
    vi.useFakeTimers();
    let signal: AbortSignal | undefined;
    vi.stubGlobal('fetch', vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      signal = init?.signal ?? undefined;
      return new Promise<Response>(() => {});
    }));
    const pending = worker.fetch(request('ChatGPT-User'), {}, context());
    await vi.advanceTimersByTimeAsync(TOLLBIT_FETCH_TIMEOUT_MS);
    expect((await pending).status).toBe(503);
    expect(signal?.aborted).toBe(true);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('does not follow provider redirects back to the publisher', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(null, { status: 302, headers: { Location: PAGE } })));
    expect((await worker.fetch(request('ChatGPT-User'), {}, context())).status).toBe(503);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('returns bodyless HEAD responses when blocking or failing', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('unavailable'); }));
    for (const ua of ['GPTBot', 'ChatGPT-User']) {
      expect(await (await worker.fetch(request(ua, {}, 'HEAD'), {}, context())).text()).toBe('');
    }
  });
});

describe('Tollbit analytics completeness and discovery', () => {
  it.each([402, 403, 500])('logs JSON outcome %i without reading content', async (status) => {
    const records: unknown[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (target(input) === LOG) {
        records.push(JSON.parse(String(init?.body)));
        return new Response(null, { status: 204 });
      }
      return new Response('{"message":"denied"}', { status, headers: { 'Content-Type': 'application/json' } });
    }));
    const ctx = context();
    expect((await worker.fetch(request('ChatGPT-User'), { TOLLBIT_API_KEY: 'test-key' }, ctx)).status).toBe(status);
    await Promise.all(ctx.pending);
    expect(records).toHaveLength(1);
    expect(records[0]).toMatchObject({ response_status: status });
    expect(JSON.stringify(records)).not.toContain('test-key');
  });

  it('records a local training denial, without making a content fetch', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(null, { status: 204 })));
    const ctx = context();
    expect((await worker.fetch(request('GPTBot'), { TOLLBIT_API_KEY: 'test-key' }, ctx)).status).toBe(403);
    await Promise.all(ctx.pending);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(target(vi.mocked(fetch).mock.calls[0]![0])).toBe(LOG);
  });

  it('bounds telemetry delivery without delaying or replacing the page response', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) =>
      target(input) === LOG ? new Promise<Response>(() => {}) : new Response('page'),
    ));
    const ctx = context();
    expect(await (await worker.fetch(request('Mozilla/5.0'), { TOLLBIT_API_KEY: 'test-key' }, ctx)).text()).toBe('page');
    await vi.advanceTimersByTimeAsync(5000);
    await Promise.all(ctx.pending);
    expect(console.warn).toHaveBeenCalledWith(
      'Tollbit analytics delivery timed out.',
      expect.objectContaining({
        status: null,
        durationMs: expect.any(Number),
      }),
    );
    expect(fetch).toHaveBeenCalledTimes(2);
  });

});
