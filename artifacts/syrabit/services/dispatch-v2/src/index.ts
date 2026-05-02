/**
 * dispatch-v2/src/index.ts
 *
 * GCP Cloud Run service that replaces Cloudflare Workers for Platforms.
 * Receives forwarded requests from the edge-proxy Cloudflare Worker,
 * validates the shared secret, and routes traffic to the appropriate
 * tenant backend: Railway (default) or an internal Cloud Run service.
 *
 * Tenant routing strategy
 * ───────────────────────
 * 1. TENANT_ROUTES env var (JSON): explicit subdomain → backend mapping.
 *    Example:
 *      TENANT_ROUTES='{"app.syrabit.ai":"cloudrun","api.syrabit.ai":"cloudrun"}'
 *    Values: "cloudrun" → BACKEND_CLOUDRUN_URL, "railway" → BACKEND_RAILWAY_URL
 *
 * 2. CLOUDRUN_HOST_PATTERN env var (regex string): if the incoming host
 *    matches, route to the internal Cloud Run backend.
 *    Example: CLOUDRUN_HOST_PATTERN='^(app|api)\\.syrabit\\.ai$'
 *
 * 3. Default: route all remaining tenants to BACKEND_RAILWAY_URL.
 *
 * Environment variables (injected via Cloud Run secrets):
 *   PORT                   — defaults to 8080
 *   NODE_ENV               — "production" in Cloud Run
 *   DISPATCH_SHARED_SECRET — 256-bit hex secret shared with edge-proxy
 *   BACKEND_RAILWAY_URL    — base URL of the Railway backend service
 *   BACKEND_CLOUDRUN_URL   — base URL of the internal Cloud Run service
 *   TENANT_ROUTES          — JSON map of host → "railway"|"cloudrun"
 *   CLOUDRUN_HOST_PATTERN  — regex; hosts matching it are sent to Cloud Run
 */

import http from 'http';
import https from 'https';
import { URL } from 'url';

const PORT = parseInt(process.env.PORT ?? '8080', 10);
const DISPATCH_SHARED_SECRET = process.env.DISPATCH_SHARED_SECRET ?? '';
const BACKEND_RAILWAY_URL = process.env.BACKEND_RAILWAY_URL ?? '';
const BACKEND_CLOUDRUN_URL = process.env.BACKEND_CLOUDRUN_URL ?? '';
const TENANT_ROUTES_RAW = process.env.TENANT_ROUTES ?? '';
const CLOUDRUN_HOST_PATTERN = process.env.CLOUDRUN_HOST_PATTERN ?? '';

const UPSTREAM_TIMEOUT_MS = 25_000;

// ─── Structured JSON logger (GCP Cloud Logging format) ───────────────────────

type Severity = 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL';

function log(severity: Severity, message: string, extra?: Record<string, unknown>): void {
  const entry: Record<string, unknown> = {
    severity,
    message,
    time: new Date().toISOString(),
    ...extra,
  };
  process.stdout.write(JSON.stringify(entry) + '\n');
}

// ─── Startup validation ───────────────────────────────────────────────────────

function parseAndValidateUrl(raw: string, label: string): URL | null {
  if (!raw) return null;
  try {
    const parsed = new URL(raw);
    log('INFO', `${label} validated`, { url: raw });
    return parsed;
  } catch {
    log('CRITICAL', `${label} is not a valid URL — exiting`, { value: raw });
    process.exit(1);
  }
}

const parsedRailwayUrl = parseAndValidateUrl(BACKEND_RAILWAY_URL, 'BACKEND_RAILWAY_URL');
const parsedCloudRunUrl = parseAndValidateUrl(BACKEND_CLOUDRUN_URL, 'BACKEND_CLOUDRUN_URL');

if (!parsedRailwayUrl && !parsedCloudRunUrl) {
  log('WARNING', 'Neither BACKEND_RAILWAY_URL nor BACKEND_CLOUDRUN_URL is configured; all dispatch requests will fail');
}

if (!DISPATCH_SHARED_SECRET) {
  log('WARNING', 'DISPATCH_SHARED_SECRET is not set; all non-probe requests will be rejected');
}

// ── Parse explicit tenant route map ──

type BackendKey = 'railway' | 'cloudrun';
let tenantRoutes: Map<string, BackendKey> = new Map();

if (TENANT_ROUTES_RAW) {
  try {
    const raw = JSON.parse(TENANT_ROUTES_RAW) as Record<string, string>;
    for (const [host, backend] of Object.entries(raw)) {
      if (backend !== 'railway' && backend !== 'cloudrun') {
        log('WARNING', 'TENANT_ROUTES entry has unknown backend value; skipping', { host, backend });
        continue;
      }
      tenantRoutes.set(host.toLowerCase(), backend as BackendKey);
    }
    log('INFO', 'TENANT_ROUTES loaded', { count: tenantRoutes.size });
  } catch {
    log('CRITICAL', 'TENANT_ROUTES is not valid JSON — exiting', { value: TENANT_ROUTES_RAW });
    process.exit(1);
  }
}

// ── Compile optional host-pattern regex ──

let cloudRunHostRegex: RegExp | null = null;
if (CLOUDRUN_HOST_PATTERN) {
  try {
    cloudRunHostRegex = new RegExp(CLOUDRUN_HOST_PATTERN, 'i');
    log('INFO', 'CLOUDRUN_HOST_PATTERN compiled', { pattern: CLOUDRUN_HOST_PATTERN });
  } catch {
    log('CRITICAL', 'CLOUDRUN_HOST_PATTERN is not a valid regex — exiting', {
      value: CLOUDRUN_HOST_PATTERN,
    });
    process.exit(1);
  }
}

// ─── Tenant routing ───────────────────────────────────────────────────────────

type ResolvedBackend = {
  url: URL;
  kind: BackendKey;
};

/**
 * Resolve the upstream URL for a given tenant host.
 *
 * Priority:
 *  1. Explicit entry in TENANT_ROUTES map.
 *  2. CLOUDRUN_HOST_PATTERN regex match → internal Cloud Run.
 *  3. Default → Railway.
 */
function resolveBackend(tenantHost: string): ResolvedBackend | null {
  const host = tenantHost.toLowerCase();

  const explicitKey = tenantRoutes.get(host);
  if (explicitKey) {
    const url = explicitKey === 'cloudrun' ? parsedCloudRunUrl : parsedRailwayUrl;
    if (!url) {
      log('ERROR', 'explicit route maps to unconfigured backend', { host, backend: explicitKey });
      return null;
    }
    log('DEBUG', 'tenant routed via explicit map', { host, backend: explicitKey });
    return { url, kind: explicitKey };
  }

  if (cloudRunHostRegex?.test(host)) {
    if (!parsedCloudRunUrl) {
      log('ERROR', 'host matches CLOUDRUN_HOST_PATTERN but BACKEND_CLOUDRUN_URL is not configured', { host });
      return null;
    }
    log('DEBUG', 'tenant routed via host pattern to Cloud Run', { host });
    return { url: parsedCloudRunUrl, kind: 'cloudrun' };
  }

  if (!parsedRailwayUrl) {
    log('ERROR', 'no backend resolved and BACKEND_RAILWAY_URL is not configured', { host });
    return null;
  }
  log('DEBUG', 'tenant routed to default Railway backend', { host });
  return { url: parsedRailwayUrl, kind: 'railway' };
}

// ─── Proxy helper ─────────────────────────────────────────────────────────────

function sendError(res: http.ServerResponse, statusCode: number, message: string): void {
  if (!res.headersSent) {
    res.writeHead(statusCode, { 'content-type': 'application/json' });
  }
  res.end(JSON.stringify({ error: message }));
}

function proxyRequest(
  incomingReq: http.IncomingMessage,
  incomingRes: http.ServerResponse,
  target: URL,
): void {
  const isHttps = target.protocol === 'https:';
  const transport = isHttps ? https : http;
  const targetPath = incomingReq.url ?? '/';

  const outHeaders: http.OutgoingHttpHeaders = { ...incomingReq.headers };
  outHeaders['host'] = target.host;
  delete outHeaders['x-dispatch-secret'];

  const options: http.RequestOptions = {
    hostname: target.hostname,
    port: target.port || (isHttps ? 443 : 80),
    path: targetPath,
    method: incomingReq.method,
    headers: outHeaders,
    timeout: UPSTREAM_TIMEOUT_MS,
  };

  const proxyReq = transport.request(options, (proxyRes) => {
    const statusCode = proxyRes.statusCode ?? 502;
    incomingRes.writeHead(statusCode, proxyRes.headers);
    proxyRes.pipe(incomingRes, { end: true });
  });

  proxyReq.on('timeout', () => {
    log('ERROR', 'upstream request timed out', {
      hostname: target.hostname,
      timeoutMs: UPSTREAM_TIMEOUT_MS,
    });
    proxyReq.destroy();
    sendError(incomingRes, 504, 'upstream timeout');
  });

  proxyReq.on('error', (err) => {
    log('ERROR', 'upstream proxy request failed', { error: err.message });
    sendError(incomingRes, 502, 'upstream unavailable');
  });

  if (incomingReq.method !== 'GET' && incomingReq.method !== 'HEAD') {
    incomingReq.pipe(proxyReq, { end: true });
  } else {
    proxyReq.end();
  }
}

// ─── HTTP server ──────────────────────────────────────────────────────────────

const server = http.createServer((req, res) => {
  const method = req.method ?? 'GET';
  const url = req.url ?? '/';

  // ── Health probes (no auth required) ──
  if (url === '/healthz') {
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ status: 'ok' }));
    return;
  }

  if (url === '/readyz') {
    const ready = parsedRailwayUrl !== null || parsedCloudRunUrl !== null;
    const statusCode = ready ? 200 : 503;
    res.writeHead(statusCode, { 'content-type': 'application/json' });
    res.end(JSON.stringify({
      status: ready ? 'ready' : 'not ready',
      railwayConfigured: parsedRailwayUrl !== null,
      cloudRunConfigured: parsedCloudRunUrl !== null,
    }));
    return;
  }

  // ── Secret validation ──
  const incomingSecret = req.headers['x-dispatch-secret'] ?? '';
  if (!DISPATCH_SHARED_SECRET || incomingSecret !== DISPATCH_SHARED_SECRET) {
    log('WARNING', 'rejected request with invalid dispatch secret', {
      url,
      method,
      remoteAddress: req.socket.remoteAddress,
    });
    res.writeHead(401, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ error: 'unauthorized' }));
    return;
  }

  // ── Tenant routing ──
  const tenantHost = (req.headers['x-forwarded-host'] as string) ?? req.headers['host'] ?? '';
  const backend = resolveBackend(tenantHost);

  if (!backend) {
    res.writeHead(503, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ error: 'no backend configured for tenant' }));
    return;
  }

  log('INFO', 'dispatching request', {
    method,
    url,
    tenantHost,
    backendUrl: backend.url.href,
    backendKind: backend.kind,
    country: req.headers['x-cf-ipcountry'] ?? '',
    ip: req.headers['x-real-ip'] ?? req.socket.remoteAddress,
  });

  proxyRequest(req, res, backend.url);
});

server.listen(PORT, () => {
  log('INFO', 'dispatch-v2 listening', { port: PORT, nodeEnv: process.env.NODE_ENV });
});

server.on('error', (err) => {
  log('CRITICAL', 'server error', { error: err.message });
  process.exit(1);
});

process.on('SIGTERM', () => {
  log('INFO', 'received SIGTERM, shutting down gracefully');
  server.close(() => {
    log('INFO', 'server closed');
    process.exit(0);
  });
});
