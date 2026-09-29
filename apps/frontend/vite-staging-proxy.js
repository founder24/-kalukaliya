const STAGING_HEADER = 'X-Syrabit-Staging-Token';
const STAGING_WORKER_HOST = 'syrabitworker-staging.axomxplain.workers.dev';

function parseWorkerUrl(target) {
  try {
    return new URL(target);
  } catch {
    throw new Error('Staging backend must be an HTTPS workers.dev origin.');
  }
}

function isStagingWorkerUrl(url, expectedHost) {
  return url.protocol === 'https:'
    && expectedHost === STAGING_WORKER_HOST
    && url.hostname === expectedHost
    && !url.username
    && !url.password
    && !url.port
    && url.pathname === '/'
    && !url.search
    && !url.hash;
}

export function validateStagingViteEnvironment(env) {
  if (env.STAGING_E2E !== '1') return;

  if ((env.VITE_BACKEND_URL || '').trim() || (env.VITE_CHAT_API_ORIGIN || '').trim()) {
    throw new Error('Staging E2E must use same-origin browser requests through the Vite proxy.');
  }
  if (!(env.STAGING_ACCESS_TOKEN || '').trim()) {
    throw new Error('STAGING_ACCESS_TOKEN is required for staging E2E.');
  }

  const expectedHost = (env.STAGING_ACCESS_HOST || '').trim().toLowerCase();
  const backendUrl = parseWorkerUrl(env.BACKEND_PROXY_URL || '');
  if (!expectedHost || !isStagingWorkerUrl(backendUrl, expectedHost)) {
    throw new Error('Staging E2E backend must exactly match STAGING_ACCESS_HOST on HTTPS workers.dev.');
  }
}

export function stagingProxyHeaders(target, env = process.env) {
  const token = env.STAGING_ACCESS_TOKEN;
  const expectedHost = (env.STAGING_ACCESS_HOST || '').trim().toLowerCase();
  if (!token || !expectedHost) return {};

  let url;
  try {
    url = new URL(target);
  } catch {
    return {};
  }
  if (!isStagingWorkerUrl(url, expectedHost)) return {};

  return { [STAGING_HEADER]: token };
}