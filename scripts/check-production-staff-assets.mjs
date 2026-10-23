import { setTimeout as sleep } from 'node:timers/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

export const STAFF_ASSET_MAX_ATTEMPTS = 4;
export const STAFF_ASSET_RETRY_DELAY_MS = 45_000;
export const STAFF_ASSET_REQUEST_TIMEOUT_MS = 15_000;

const ERROR_PREFIX = '[staff-asset-readiness]';
const HASHED_BUNDLE_PATH = /^\/assets\/[^/]+-[A-Za-z0-9_-]{8,}\.(?:js|css)$/;

function safeErrorName(error) {
  const name = error instanceof Error ? error.name : '';
  return ['AbortError', 'TimeoutError', 'TypeError'].includes(name) ? name : 'request error';
}

function isNetworkFailure(error) {
  return error instanceof TypeError
    || error?.name === 'AbortError'
    || error?.name === 'TimeoutError';
}

function makeRequestOptions({ headers, requestTimeoutMs }) {
  const options = {
    method: 'GET',
    headers,
    redirect: 'manual',
  };
  if (requestTimeoutMs > 0) {
    options.signal = AbortSignal.timeout(requestTimeoutMs);
  }
  return options;
}

function contentType(response) {
  return (response.headers?.get('content-type') || '')
    .split(';', 1)[0]
    .trim()
    .toLowerCase();
}

function hasExpectedAssetContentType(pathname, type) {
  if (pathname.endsWith('.js')) {
    return /^(?:application|text)\/(?:javascript|ecmascript)$/.test(type);
  }
  if (pathname.endsWith('.css')) return type === 'text/css';
  return Boolean(type) && type !== 'text/html';
}

async function discardBody(response) {
  try {
    await response.body?.cancel();
  } catch {
    // A response body is already closed; status handling remains authoritative.
  }
}

export function collectStaffAssetReferences(html, documentUrl) {
  const origin = new URL(documentUrl).origin;
  const assets = new Map();

  for (const match of html.matchAll(/\b(?:src|href)\s*=\s*["']([^"']+)["']/gi)) {
    let assetUrl;
    try {
      assetUrl = new URL(match[1], documentUrl);
    } catch {
      continue;
    }
    if (assetUrl.origin !== origin || !assetUrl.pathname.startsWith('/assets/')) continue;

    const pathname = assetUrl.pathname;
    assets.set(assetUrl.href, {
      href: assetUrl.href,
      pathname,
      hashedBundle: HASHED_BUNDLE_PATH.test(pathname),
    });
  }

  return [...assets.values()];
}

async function verifyAsset(asset, fetchImpl, requestTimeoutMs) {
  let response;
  try {
    response = await fetchImpl(
      asset.href,
      makeRequestOptions({
        headers: { Accept: '*/*' },
        requestTimeoutMs,
      }),
    );
  } catch (error) {
    if (asset.hashedBundle && isNetworkFailure(error)) {
      return { retryable: true, reason: 'network', asset };
    }
    throw new Error(
      `${ERROR_PREFIX} GET ${asset.pathname} failed (${safeErrorName(error)}); not retrying this asset.`,
    );
  }

  if (response.status === 404 && asset.hashedBundle) {
    await discardBody(response);
    return { retryable: true, reason: 'HTTP 404', asset };
  }
  if (response.status !== 200) {
    await discardBody(response);
    throw new Error(`${ERROR_PREFIX} GET ${asset.pathname} returned HTTP ${response.status}.`);
  }

  if (!hasExpectedAssetContentType(asset.pathname, contentType(response))) {
    await discardBody(response);
    throw new Error(`${ERROR_PREFIX} GET ${asset.pathname} returned an unexpected content type.`);
  }

  let byteLength;
  try {
    byteLength = (await response.arrayBuffer()).byteLength;
  } catch (error) {
    if (asset.hashedBundle && isNetworkFailure(error)) {
      return { retryable: true, reason: 'network', asset };
    }
    throw new Error(
      `${ERROR_PREFIX} reading ${asset.pathname} failed (${safeErrorName(error)}); not retrying this asset.`,
    );
  }
  if (byteLength === 0) {
    throw new Error(`${ERROR_PREFIX} GET ${asset.pathname} returned an empty asset.`);
  }
  return { retryable: false, asset };
}

function freshStaffDocumentUrl(site, attempt, now) {
  const url = new URL('/staff', site.origin);
  url.searchParams.set('__staff_asset_readiness', `${attempt}-${now()}`);
  return url;
}

export async function verifyProductionStaffAssets({
  siteUrl,
  accessClientId,
  accessClientSecret,
  fetchImpl = fetch,
  maxAttempts = STAFF_ASSET_MAX_ATTEMPTS,
  retryDelayMs = STAFF_ASSET_RETRY_DELAY_MS,
  requestTimeoutMs = STAFF_ASSET_REQUEST_TIMEOUT_MS,
  wait = sleep,
  now = Date.now,
  logger = console,
}) {
  if (!siteUrl) throw new Error(`${ERROR_PREFIX} FRONTEND_URL is required.`);
  if (!accessClientId || !accessClientSecret) {
    throw new Error(`${ERROR_PREFIX} Cloudflare Access service-token headers are required.`);
  }
  if (!Number.isInteger(maxAttempts) || maxAttempts < 1) {
    throw new Error(`${ERROR_PREFIX} maxAttempts must be a positive integer.`);
  }
  if (!Number.isFinite(retryDelayMs) || retryDelayMs < 0) {
    throw new Error(`${ERROR_PREFIX} retryDelayMs must be a non-negative number.`);
  }

  let site;
  try {
    site = new URL(siteUrl);
  } catch {
    throw new Error(`${ERROR_PREFIX} FRONTEND_URL must be a valid URL.`);
  }
  if (site.protocol !== 'https:' || site.username || site.password) {
    throw new Error(`${ERROR_PREFIX} FRONTEND_URL must be an HTTPS origin without embedded credentials.`);
  }

  const accessHeaders = {
    'CF-Access-Client-Id': accessClientId,
    'CF-Access-Client-Secret': accessClientSecret,
    Accept: 'text/html',
    'Cache-Control': 'no-cache, no-store',
    Pragma: 'no-cache',
  };

  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    const documentUrl = freshStaffDocumentUrl(site, attempt, now);
    let documentResponse;
    try {
      documentResponse = await fetchImpl(
        documentUrl,
        makeRequestOptions({ headers: accessHeaders, requestTimeoutMs }),
      );
    } catch (error) {
      throw new Error(
        `${ERROR_PREFIX} fresh GET /staff failed (${safeErrorName(error)}); document and auth failures are not retried.`,
      );
    }

    if (documentResponse.status !== 200) {
      await discardBody(documentResponse);
      throw new Error(
        `${ERROR_PREFIX} fresh GET /staff returned HTTP ${documentResponse.status}; document and auth responses are not retried.`,
      );
    }
    if (contentType(documentResponse) !== 'text/html') {
      await discardBody(documentResponse);
      throw new Error(`${ERROR_PREFIX} fresh GET /staff did not return an HTML document.`);
    }

    let html;
    try {
      html = await documentResponse.text();
    } catch (error) {
      throw new Error(
        `${ERROR_PREFIX} reading the fresh /staff document failed (${safeErrorName(error)}); document failures are not retried.`,
      );
    }

    const assets = collectStaffAssetReferences(html, documentUrl);
    if (!assets.some(asset => asset.hashedBundle && asset.pathname.endsWith('.js'))) {
      throw new Error(`${ERROR_PREFIX} fresh /staff document contains no hashed JavaScript bundle.`);
    }

    logger.info?.(
      `${ERROR_PREFIX} attempt ${attempt}/${maxAttempts}: checking ${assets.length} same-origin /assets/* reference(s).`,
    );
    const results = await Promise.all(
      assets.map(asset => verifyAsset(asset, fetchImpl, requestTimeoutMs)),
    );
    const retryable = results.filter(result => result.retryable);
    if (!retryable.length) {
      logger.info?.(
        `${ERROR_PREFIX} fresh /staff document and ${assets.length} asset(s) returned successfully.`,
      );
      return { attempt, assetCount: assets.length };
    }

    const unresolved = [...new Map(
      retryable.map(({ asset, reason }) => [asset.pathname, `${asset.pathname} (${reason})`]),
    ).values()];
    logger.warn?.(
      `${ERROR_PREFIX} attempt ${attempt}/${maxAttempts} found missing/network-failed hashed assets: ${unresolved.join(', ')}.`,
    );
    if (attempt === maxAttempts) {
      throw new Error(
        `${ERROR_PREFIX} readiness window ended after ${maxAttempts} attempts; unresolved hashed assets: ${unresolved.join(', ')}.`,
      );
    }
    await wait(retryDelayMs);
  }

  throw new Error(`${ERROR_PREFIX} readiness check ended unexpectedly.`);
}

async function main() {
  await verifyProductionStaffAssets({
    siteUrl: process.env.PUBLIC_SITE_URL || process.env.FRONTEND_URL,
    accessClientId: process.env.CF_ACCESS_CLIENT_ID,
    accessClientSecret: process.env.CF_ACCESS_CLIENT_SECRET,
  });
}

if (
  process.argv[1]
  && import.meta.url === pathToFileURL(resolve(process.argv[1])).href
) {
  main().catch(error => {
    console.error(error instanceof Error ? error.message : `${ERROR_PREFIX} failed.`);
    process.exitCode = 1;
  });
}