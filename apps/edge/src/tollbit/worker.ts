import {
  AGENT_SITE_USER_AGENTS,
  LIKELY_AUTOMATED_BOT_SCORE_MAX,
  OPEN_ACCESS_SEARCH_USER_AGENTS,
  TRAINING_ONLY_USER_AGENTS,
  matchesAgent,
} from './policy';

const TOLLBIT_LOG_ENDPOINT = 'https://log.tollbit.com/log';
export const TOLLBIT_FETCH_TIMEOUT_MS = 8000;
const TOLLBIT_LOG_TIMEOUT_MS = 5000;
const MAX_SIGNATURE_HEADER_LENGTH = 8192;
const SIGNATURE_HEADERS = ['signature', 'signature-agent', 'signature-input'] as const;
const SITE_HOSTS = new Set(['syrabit.ai', 'www.syrabit.ai']);
const TOLLBIT_TEST_HEALTH_PATH = '/__tollbit_test_health';

type CloudflareBotManagement = {
  score?: number;
  verifiedBot?: boolean;
  signedAgent?: boolean;
  staticResource?: boolean;
  corporateProxy?: boolean;
};

type CloudflareRequestMetadata = {
  botManagement?: CloudflareBotManagement;
  verifiedBotCategory?: string;
};

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

// Public informational and student-facing pages whose requests may be sent to
// Tollbit analytics. This list intentionally does not determine paid routing:
// legal/marketing pages should be measurable without becoming paywalled.
const PUBLIC_ANALYTICS_STATIC_PATHS = new Set([
  '/',
  '/home',
  '/terms',
  '/privacy',
  '/about',
  '/technology',
  '/status',
  '/exam-routine',
  '/library',
  '/as/library',
  '/browser',
  '/browse',
  '/browser-tabs',
  '/curriculum',
]);

const PUBLIC_CURRICULUM_BOARDS = new Set([
  'ahsec',
  'seba',
  'degree',
  'cbse',
  'icse',
]);

export interface TollbitWorkerEnv {
  TOLLBIT_API_KEY?: string;
  TOLLBIT_BOT_SCORE_MODE?: string;
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

function isEligiblePublisherPageRequest(
  request: Request,
  env: TollbitWorkerEnv = {},
): boolean {
  const host = publisherHostForRequest(request, env);
  const method = request.method.toUpperCase();

  if (!host) return false;
  if (
    env.TOLLBIT_TEST_MODE === 'true'
    && !isTollbitTestSource(request, env)
  ) {
    return false;
  }
  return method === 'GET' || method === 'HEAD';
}

function isTollbitContentRequest(
  request: Request,
  env: TollbitWorkerEnv = {},
): boolean {
  return isEligiblePublisherPageRequest(request, env)
    && isPublicTollbitContentPath(new URL(request.url).pathname);
}

function isTollbitAnalyticsRequest(
  request: Request,
  env: TollbitWorkerEnv = {},
): boolean {
  return isEligiblePublisherPageRequest(request, env)
    && isPublicTollbitAnalyticsPath(new URL(request.url).pathname);
}

function safeDecodedPathSegments(pathname: string): string[] | null {
  if (
    typeof pathname !== 'string'
    || !pathname.startsWith('/')
    || pathname.includes('//')
  ) {
    return null;
  }

  const rawSegments = pathname.split('/').slice(1);
  if (rawSegments[rawSegments.length - 1] === '') rawSegments.pop();
  if (rawSegments.some((segment) => !segment)) return null;

  let segments: string[];
  try {
    segments = rawSegments.map((segment) =>
      decodeURIComponent(segment).normalize('NFC'),
    );
  } catch {
    return null;
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
    return null;
  }

  return segments.map((segment) => segment.toLowerCase());
}

export function isPublicTollbitContentPath(pathname: string): boolean {
  // Forward the homepage itself; relying on the publisher's bot renderer here
  // previously produced a 503 instead of Tollbit's token-required response.
  const normalized = safeDecodedPathSegments(pathname);
  if (!normalized) return false;
  if (normalized.length === 0) return true;
  if (normalized.length === 1 && normalized[0] === 'home') return true;

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

export function isPublicTollbitAnalyticsPath(pathname: string): boolean {
  const segments = safeDecodedPathSegments(pathname);
  if (!segments) return false;

  const pageSegments = segments[0] === 'as' ? segments.slice(1) : segments;
  const normalizedPath = pageSegments.length
    ? `/${pageSegments.join('/')}`
    : '/';
  if (
    PUBLIC_ANALYTICS_STATIC_PATHS.has(normalizedPath)
    || PUBLIC_ANALYTICS_STATIC_PATHS.has(`/${segments.join('/')}`)
  ) {
    return true;
  }

  if (
    pageSegments.length === 2
    && ['subject', 'learn', 'pyq'].includes(pageSegments[0]!)
  ) {
    return true;
  }

  // Public curriculum routes share the frontend's board/class/subject,
  // chapter, legacy-page, and topic URL shapes. Keep this allowlist separate
  // from Tollbit content gating so newly measured routes are not paywalled.
  return PUBLIC_CURRICULUM_BOARDS.has(pageSegments[0] ?? '')
    && pageSegments.length >= 3
    && pageSegments.length <= 7;
}

function matchedAgentToken(userAgent: string | null): string | null {
  if (!userAgent || userAgent.length > 2048) return null;
  return AGENT_SITE_USER_AGENTS.find((agent) =>
    matchesAgent(userAgent, [agent]),
  ) ?? null;
}

function cloudflareMetadata(request: Request): CloudflareRequestMetadata | undefined {
  return (request as Request & { cf?: CloudflareRequestMetadata }).cf;
}

function isCloudflareIdentifiedAgent(request: Request): boolean {
  const botManagement = cloudflareMetadata(request)?.botManagement;
  return botManagement?.verifiedBot === true
    || botManagement?.signedAgent === true;
}

function isLikelyAutomatedRequest(request: Request): boolean {
  const userAgent = request.headers.get('user-agent');
  const botManagement = cloudflareMetadata(request)?.botManagement;

  // Missing/unusable metadata is ambiguous. Keep ordinary study access open,
  // and avoid the known low-score false positives for blank UAs and proxies.
  if (
    !userAgent?.trim()
    || userAgent.length > 2048
    || !botManagement
    || botManagement.staticResource === true
    || botManagement.corporateProxy === true
  ) {
    return false;
  }

  const score = botManagement.score;
  return typeof score === 'number'
    && Number.isInteger(score)
    && score >= 1
    && score <= LIKELY_AUTOMATED_BOT_SCORE_MAX;
}

function isUnlistedLikelyAutomatedRequest(request: Request): boolean {
  const userAgent = request.headers.get('user-agent');
  return matchedAgentToken(userAgent) === null
    && !matchesAgent(userAgent, OPEN_ACCESS_SEARCH_USER_AGENTS)
    && !matchesAgent(userAgent, TRAINING_ONLY_USER_AGENTS)
    && !isCloudflareTrainingAgent(request)
    && !isCloudflareIdentifiedAgent(request)
    && isLikelyAutomatedRequest(request);
}

function botScoreMode(env: TollbitWorkerEnv): 'disabled' | 'shadow' | 'enforce' {
  if (env.TOLLBIT_BOT_SCORE_MODE === 'shadow') return 'shadow';
  if (env.TOLLBIT_BOT_SCORE_MODE === 'enforce') return 'enforce';
  return 'disabled';
}

function isVerifiedSearchEngineRequest(request: Request): boolean {
  const cf = cloudflareMetadata(request);

  // User-Agent text is spoofable. Free search forwarding requires Cloudflare
  // to verify the bot and classify it specifically as a Search Engine.
  return cf?.botManagement?.verifiedBot === true
    && cf.verifiedBotCategory === 'Search Engine';
}

function isCloudflareTrainingAgent(request: Request): boolean {
  // Cloudflare's verified-bot legacy category defines AI Crawler as content
  // collection used for training; keep it ahead of paid-agent routing.
  return cloudflareMetadata(request)?.verifiedBotCategory === 'AI Crawler';
}

function safeSignatureHeader(request: Request, name: string): string | null {
  const value = request.headers.get(name);
  if (
    !value
    || value.length > MAX_SIGNATURE_HEADER_LENGTH
    || /[\u0000-\u001f\u007f]/u.test(value)
  ) {
    return null;
  }
  return value;
}

function isUnverifiedOpenAccessSearchClaim(request: Request): boolean {
  return matchesAgent(
    request.headers.get('user-agent'),
    OPEN_ACCESS_SEARCH_USER_AGENTS,
  ) && !isVerifiedSearchEngineRequest(request);
}

function isAgentSiteRequest(
  request: Request,
  env: TollbitWorkerEnv = {},
): boolean {
  if (!isTollbitContentRequest(request, env)) return false;
  // Credential presence must not bypass the gate. makeTollbitSiteRequest uses
  // an explicit safe header allowlist even on credentialed public requests.
  return matchedAgentToken(request.headers.get('user-agent')) !== null
    || isCloudflareIdentifiedAgent(request)
    || (
      botScoreMode(env) === 'enforce'
      && isUnlistedLikelyAutomatedRequest(request)
    );
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
  const userAgent = request.headers.get('user-agent');
  const agentToken = matchedAgentToken(userAgent);
  const cloudflareRoutedAgent = isCloudflareIdentifiedAgent(request)
    || (
      botScoreMode(env) === 'enforce'
      && isUnlistedLikelyAutomatedRequest(request)
    );
  if (!agentToken && !cloudflareRoutedAgent) {
    throw new TypeError('No approved Tollbit agent matched.');
  }
  if (userAgent && userAgent.length <= 2048) {
    headers.set('User-Agent', userAgent);
  } else if (agentToken) {
    headers.set('User-Agent', agentToken);
  } else {
    throw new TypeError('Approved Agent Site request has no usable user-agent.');
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
  for (const name of SIGNATURE_HEADERS) {
    const value = safeSignatureHeader(request, name);
    if (value) {
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
  const signature = safeSignatureHeader(request, 'signature');
  const signatureAgent = safeSignatureHeader(request, 'signature-agent');
  const signatureInput = safeSignatureHeader(request, 'signature-input');
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
    const scoreMode = botScoreMode(env);
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

    if (!isTollbitContentRequest(request, env) && testMode) {
      return new Response(null, { status: 404 });
    }

    let response: Response;

    const tollbitContentRequest = isTollbitContentRequest(request, env);
    const tollbitAnalyticsRequest = isTollbitAnalyticsRequest(request, env);
    if (
      tollbitContentRequest
      && scoreMode === 'shadow'
      && isUnlistedLikelyAutomatedRequest(request)
    ) {
      const score = cloudflareMetadata(request)?.botManagement?.score;
      console.info('tollbit_bot_score_shadow', {
        score_bucket: score === 1 ? 'automated' : 'likely_automated',
      });
    }
    if (
      tollbitContentRequest
      && (
        matchesAgent(request.headers.get('user-agent'), TRAINING_ONLY_USER_AGENTS)
        || isCloudflareTrainingAgent(request)
      )
    ) {
      response = policyResponse(request, 403, 'ai_training_not_permitted');
    } else if (tollbitContentRequest && isUnverifiedOpenAccessSearchClaim(request)) {
      // A claimed free search bot that Cloudflare cannot verify must not fall
      // through to origin and bypass Tollbit's Open Access decision.
      response = policyResponse(request, 403, 'unverified_search_crawler');
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
    if (tollbitAnalyticsRequest) {
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
