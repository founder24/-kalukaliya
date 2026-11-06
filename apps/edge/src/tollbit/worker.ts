import { AGENT_SITE_USER_AGENTS, TRAINING_ONLY_USER_AGENTS, matchesAgent } from './policy';

const TOLLBIT_LOG_ENDPOINT = 'https://log.tollbit.com/log';
export const TOLLBIT_FETCH_TIMEOUT_MS = 8000;
const TOLLBIT_LOG_TIMEOUT_MS = 5000;
const SITE_HOSTS = new Set(['syrabit.ai', 'www.syrabit.ai']);
const TOLLBIT_TEST_HEALTH_PATH = '/__tollbit_test_health';

const PRIVATE_CONTENT_ROOTS = new Set([
  'account',
  'admin',
  'api',
  'auth',
  'cms',
  'flashcards',
  'guardian',
  'history',
  'login',
  'notebook',
  'payment',
  'profile',
  'reset-password',
  'settings',
  'signup',
  'staff',
  'subscribe',
]);

export interface TollbitWorkerEnv {
  TOLLBIT_API_KEY?: string;
  TOLLBIT_TEST_MODE?: string;
  TOLLBIT_TEST_PUBLISHER_HOST?: string;
  TOLLBIT_TEST_SOURCE_IPS?: string;
}

export interface TollbitLogEntry {
  timestamp: string;
  ip_address: string | null;
  geo_country: string | null;
  geo_city: string | null;
  geo_postal_code: string | null;
  geo_latitude: string | number | null;
  geo_longitude: string | number | null;
  host: string;
  url: string;
  request_method: string;
  request_protocol: string;
  request_user_agent: string | null;
  request_latency: null;
  request_referer: string | null;
  response_state: null;
  response_status: number;
  response_reason: string;
  response_body_size: number | null;
  signature?: string;
  signature_agent?: string;
  signature_input?: string;
}

function isWorkersDevHost(request: Request): boolean {
  return new URL(request.url).hostname.toLowerCase().endsWith('.workers.dev');
}

function isTollbitTestSource(request: Request, env: TollbitWorkerEnv): boolean {
  const sourceIp = request.headers.get('cf-connecting-ip');
  if (!sourceIp) return false;
  const allowedIps = (env.TOLLBIT_TEST_SOURCE_IPS || '')
    .split(',')
    .map((ip) => ip.trim())
    .filter(Boolean);
  return allowedIps.includes(sourceIp);
}

function publisherHostForRequest(
  request: Request,
  env: TollbitWorkerEnv = {},
): string | null {
  const requestedHost = new URL(request.url).hostname.toLowerCase();
  if (env.TOLLBIT_TEST_MODE === 'true') {
    if (!isWorkersDevHost(request)) return null;
    const configuredHost = env.TOLLBIT_TEST_PUBLISHER_HOST?.toLowerCase();
    return configuredHost && SITE_HOSTS.has(configuredHost)
      ? configuredHost
      : null;
  }
  return SITE_HOSTS.has(requestedHost) ? requestedHost : null;
}

function isPublicPageRequest(
  request: Request,
  env: TollbitWorkerEnv = {},
): boolean {
  const url = new URL(request.url);
  const host = publisherHostForRequest(request, env);
  const method = request.method.toUpperCase();

  if (!host) return false;
  if (
    env.TOLLBIT_TEST_MODE === 'true'
    && !isTollbitTestSource(request, env)
  ) {
    return false;
  }
  if (method !== 'GET' && method !== 'HEAD') return false;
  return isPublicTollbitContentPath(url.pathname);
}

export function isPublicTollbitContentPath(pathname: string): boolean {
  if (
    typeof pathname !== 'string'
    || !pathname.startsWith('/')
    || pathname.includes('//')
  ) {
    return false;
  }
  // Forward the homepage itself; relying on the publisher's bot renderer here
  // previously produced a 503 instead of Tollbit's token-required response.
  if (pathname === '/' || pathname === '/home' || pathname === '/home/') return true;

  const rawSegments = pathname.split('/').slice(1);
  if (rawSegments[rawSegments.length - 1] === '') rawSegments.pop();
  if (rawSegments.some((segment) => !segment)) return false;

  let segments: string[];
  try {
    segments = rawSegments.map((segment) =>
      decodeURIComponent(segment).normalize('NFC'),
    );
  } catch {
    return false;
  }

  if (
    segments.some((segment) =>
      segment === '.'
      || segment === '..'
      || segment.includes('/')
      || segment.includes('\\')
      || /[\u0000-\u001f\u007f]/u.test(segment)
      || !/^[\p{L}\p{N}][\p{L}\p{M}\p{N}_-]*$/u.test(segment),
    )
  ) {
    return false;
  }

  const normalized = segments.map((segment) => segment.toLowerCase());
  const contentSegments = normalized[0] === 'as'
    ? normalized.slice(1)
    : normalized;
  if (
    !contentSegments.length
    || PRIVATE_CONTENT_ROOTS.has(contentSegments[0])
  ) {
    return false;
  }
  if (contentSegments.length === 1 && contentSegments[0] === 'library') {
    return true;
  }

  const [section, ...route] = contentSegments;
  if (section === 'ahsec') {
    return route.length === 2
      || route.length === 3
      || route.length === 4
      || (route.length === 5 && route[3] === 'topic')
      || (route.length === 6 && route[4] === 'topic');
  }
  if (section === 'degree') {
    return route.length === 3
      || route.length === 4
      || (route.length === 6 && route[4] === 'topic');
  }
  return false;
}

function matchedAgentToken(userAgent: string | null): string | null {
  if (!userAgent || userAgent.length > 2048) return null;
  const normalized = userAgent.toLowerCase();
  return AGENT_SITE_USER_AGENTS.find((agent) =>
    normalized.includes(agent.toLowerCase()),
  ) ?? null;
}

function isAgentSiteRequest(
  request: Request,
  env: TollbitWorkerEnv = {},
): boolean {
  if (!isPublicPageRequest(request, env)) return false;
  // Credential presence must not bypass the gate. makeTollbitSiteRequest uses
  // an explicit safe header allowlist even on credentialed public requests.
  return matchedAgentToken(request.headers.get('user-agent')) !== null;
}

function tollbitSiteUrl(
  request: Request,
  env: TollbitWorkerEnv = {},
): URL {
  const sourceUrl = new URL(request.url);
  const configuredHost = publisherHostForRequest(request, env);
  if (!configuredHost) throw new TypeError('Unsupported publisher host.');
  const publisherHost = configuredHost.startsWith('www.')
    ? configuredHost.slice(4)
    : configuredHost;

  // Query parameters are intentionally excluded to avoid forwarding tracking
  // values or user-entered data. Agent Site serves the public canonical path.
  return new URL(sourceUrl.pathname, `https://tollbit.${publisherHost}`);
}

export function makeTollbitSiteRequest(
  request: Request,
  env: TollbitWorkerEnv = {},
): Request {
  const headers = new Headers();
  const agentToken = matchedAgentToken(request.headers.get('user-agent'));
  if (!agentToken) throw new TypeError('No approved Tollbit agent matched.');
  const userAgent = request.headers.get('user-agent');
  if (userAgent && userAgent.length <= 2048) {
    headers.set('User-Agent', userAgent);
  } else {
    headers.set('User-Agent', agentToken);
  }

  const accept = request.headers.get('accept');
  if (
    accept
    && accept.length <= 512
    && !/[\u0000-\u001f\u007f]/u.test(accept)
  ) {
    headers.set('Accept', accept);
  }

  // Tollbit uses these signed-request headers for Agent Site attribution.
  // Forward them, but never forward cookies, authorization, or user IDs.
  for (const name of ['signature', 'signature-agent', 'signature-input']) {
    const value = request.headers.get(name);
    if (
      value
      && value.length <= 8192
      && !/[\u0000-\u001f\u007f]/u.test(value)
    ) {
      headers.set(name, value);
    }
  }

  return new Request(tollbitSiteUrl(request, env), {
    method: request.method,
    headers,
    redirect: 'manual',
  });
}

function makePublisherOriginRequest(
  request: Request,
  env: TollbitWorkerEnv,
): Request {
  const publisherHost = publisherHostForRequest(request, env);
  if (!publisherHost) throw new TypeError('Unsupported publisher host.');
  const sourceUrl = new URL(request.url);
  const targetUrl = new URL(sourceUrl.pathname, `https://${publisherHost}`);
  const headers = new Headers();
  const userAgent = request.headers.get('user-agent');
  if (userAgent) headers.set('User-Agent', userAgent);
  const accept = request.headers.get('accept');
  if (accept && accept.length <= 512 && !/[\u0000-\u001f\u007f]/u.test(accept)) {
    headers.set('Accept', accept);
  }
  return new Request(targetUrl, {
    method: request.method,
    headers,
    redirect: 'manual',
  });
}

function getResponseBodySize(response: Response): number | null {
  const value = response.headers.get('content-length');
  if (!value || !/^\d+$/.test(value)) return null;
  const size = Number(value);
  return Number.isSafeInteger(size) ? size : null;
}

export function buildTollbitLogEntry(
  request: Request,
  response: Response,
  now = new Date(),
  env: TollbitWorkerEnv = {},
): TollbitLogEntry {
  const host = publisherHostForRequest(request, env);
  if (!host) throw new TypeError('Unsupported publisher host.');
  const url = new URL(request.url);
  const cf = (request as Request & {
    cf?: {
      httpProtocol?: string;
      country?: string;
      city?: string;
      postalCode?: string;
      latitude?: string | number;
      longitude?: string | number;
    };
  }).cf;
  const rawReferer = request.headers.get('referer');
  let referer: string | null = null;
  if (rawReferer && rawReferer.length <= 4096) {
    try {
      const refererUrl = new URL(rawReferer);
      if (
        refererUrl.protocol === 'https:'
        || refererUrl.protocol === 'http:'
      ) {
        // Keep the referrer origin and path but discard query/fragment values.
        referer = `${refererUrl.origin}${refererUrl.pathname}`.slice(0, 2048);
      }
    } catch {
      referer = null;
    }
  }
  const entry: TollbitLogEntry = {
    timestamp: now.toISOString(),
    ip_address: request.headers.get('cf-connecting-ip'),
    geo_country: cf?.country ?? null,
    geo_city: cf?.city ?? null,
    geo_postal_code: cf?.postalCode ?? null,
    geo_latitude: cf?.latitude ?? null,
    geo_longitude: cf?.longitude ?? null,
    host,
    // Do not send page query strings, which can contain user-entered data.
    url: url.pathname.slice(0, 2048),
    request_method: request.method,
    request_protocol: cf?.httpProtocol || url.protocol.replace(':', ''),
    request_user_agent: request.headers.get('user-agent')?.slice(0, 2048) ?? null,
    request_latency: null,
    request_referer: referer,
    response_state: null,
    response_status: response.status,
    response_reason: response.statusText.slice(0, 256),
    response_body_size: getResponseBodySize(response),
  };

  // Tollbit's documented Cloudflare integration uses these signature headers
  // for request attribution. Do not forward cookies or authorization headers.
  const signature = request.headers.get('signature');
  const signatureAgent = request.headers.get('signature-agent');
  const signatureInput = request.headers.get('signature-input');
  if (signature) entry.signature = signature;
  if (signatureAgent) entry.signature_agent = signatureAgent;
  if (signatureInput) entry.signature_input = signatureInput;

  return entry;
}

async function postTollbitLog(
  entry: TollbitLogEntry,
  apiKey: string,
): Promise<void> {
  const startedAt = Date.now();
  try {
    const response = await boundedFetch(TOLLBIT_LOG_ENDPOINT, {
      method: 'POST',
      headers: {
        TollbitKey: apiKey,
        'Content-Type': 'application/json',
      },
      // Tollbit documents newline-delimited JSON records. A one-record batch
      // ends with a newline and avoids isolate-local batching state.
      body: `${JSON.stringify(entry)}\n`,
    }, TOLLBIT_LOG_TIMEOUT_MS);

    const status = response.status;
    await response.body?.cancel();
    const durationMs = Math.max(0, Date.now() - startedAt);

    // Keep logs limited to transport evidence; never include the key, body,
    // URL, visitor data, or provider response text.
    if (!response.ok) {
      console.warn('Tollbit analytics delivery rejected.', {
        status,
        durationMs,
      });
      return;
    }

    console.info('Tollbit analytics delivery accepted.', {
      status,
      durationMs,
    });
  } catch (error) {
    const durationMs = Math.max(0, Date.now() - startedAt);
    const timedOut = error instanceof Error
      && error.message === 'Tollbit upstream timed out';
    console.warn(
      timedOut
        ? 'Tollbit analytics delivery timed out.'
        : 'Tollbit analytics transport failed.',
      { status: null, durationMs },
    );
  }
}

async function boundedFetch(
  input: RequestInfo | URL,
  init: RequestInit = {},
  timeoutMs = TOLLBIT_FETCH_TIMEOUT_MS,
): Promise<Response> {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  const deadline = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      controller.abort();
      reject(new Error('Tollbit upstream timed out'));
    }, timeoutMs);
  });
  try {
    return await Promise.race([
      fetch(input, { ...init, signal: controller.signal }),
      deadline,
    ]);
  } finally {
    if (timer !== undefined) clearTimeout(timer);
  }
}

function policyResponse(request: Request, status: number, error: string): Response {
  return new Response(request.method === 'HEAD' ? null : JSON.stringify({ error }), {
    status,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Cache-Control': 'no-store',
      'X-Robots-Tag': 'noindex',
      ...(status === 503 ? { 'Retry-After': '60' } : {}),
    },
  });
}

function withoutCache(response: Response): Response {
  const headers = new Headers(response.headers);
  headers.set('Cache-Control', 'no-store');
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

export default {
  async fetch(
    request: Request,
    env: TollbitWorkerEnv,
    ctx: ExecutionContext,
  ): Promise<Response> {
    const testMode = env.TOLLBIT_TEST_MODE === 'true';
    if (testMode) {
      const url = new URL(request.url);
      if (!isWorkersDevHost(request)) {
        return new Response(null, { status: 404 });
      }
      if (
        request.method === 'GET'
        && url.pathname === TOLLBIT_TEST_HEALTH_PATH
      ) {
        return new Response('OK', {
          status: 200,
          headers: {
            'Cache-Control': 'no-store',
            'Content-Type': 'text/plain; charset=utf-8',
          },
        });
      }
      if (!isTollbitTestSource(request, env)) {
        return new Response(null, { status: 404 });
      }
    }

    if (!isPublicPageRequest(request, env) && testMode) {
      return new Response(null, { status: 404 });
    }

    let response: Response;

    const publicPage = isPublicPageRequest(request, env);
    if (publicPage && matchesAgent(request.headers.get('user-agent'), TRAINING_ONLY_USER_AGENTS)) {
      response = policyResponse(request, 403, 'ai_training_not_permitted');
    } else if (isAgentSiteRequest(request, env)) {
      try {
        const upstream = await boundedFetch(makeTollbitSiteRequest(request, env));
        // Never send bots through redirect loops or into an ungated origin.
        if (upstream.status >= 300 && upstream.status < 400) {
          await upstream.body?.cancel();
          response = policyResponse(request, 503, 'agent_site_unavailable');
        } else {
          response = withoutCache(upstream);
        }
      } catch {
        // Fail closed for gated bots only. Ordinary students still use the
        // publisher origin; an Agent Site outage must not expose content.
        response = policyResponse(request, 503, 'agent_site_unavailable');
      }
    } else {
      response = await fetch(
        testMode ? makePublisherOriginRequest(request, env) : request,
      );
    }

    // Eligibility is a page-route decision, not a response MIME decision:
    // token-denied JSON, training blocks and upstream failures count too.
    if (publicPage) {
      if (env.TOLLBIT_API_KEY) {
        const entry = buildTollbitLogEntry(request, response, new Date(), env);
        // Analytics is best-effort and must not delay or break the page.
        ctx.waitUntil(postTollbitLog(entry, env.TOLLBIT_API_KEY));
      } else {
        console.warn('Tollbit analytics disabled: secret binding is missing.');
      }
    }

    return response;
  },
};
