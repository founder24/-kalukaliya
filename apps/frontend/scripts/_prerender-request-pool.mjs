const RETRY_MAX_ATTEMPTS = (() => {
  const n = Number.parseInt(process.env.PRERENDER_FETCH_RETRIES || "", 10);
  return Number.isFinite(n) && n >= 1 && n <= 10 ? n : 4;
})();

const RETRY_BASE_DELAY_MS = (() => {
  const n = Number.parseInt(process.env.PRERENDER_FETCH_RETRY_BASE_MS || "", 10);
  return Number.isFinite(n) && n >= 100 && n <= 30_000 ? n : 750;
})();

const RETRY_MAX_DELAY_MS = 30_000;

function parseRetryAfter(value) {
  if (!value) return null;
  const seconds = Number(value);
  if (Number.isFinite(seconds) && seconds >= 0) {
    return Math.min(seconds * 1000, RETRY_MAX_DELAY_MS);
  }
  const date = Date.parse(value);
  return Number.isFinite(date)
    ? Math.min(Math.max(0, date - Date.now()), RETRY_MAX_DELAY_MS)
    : null;
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export function createJsonRequestPool({
  concurrency,
  timeoutMs,
  fetchImpl = globalThis.fetch,
  maxAttempts = RETRY_MAX_ATTEMPTS,
  retryBaseDelayMs = RETRY_BASE_DELAY_MS,
  retryJitterMs = 250,
  sleepImpl = sleep,
}) {
  if (!Number.isInteger(concurrency) || concurrency < 1) {
    throw new TypeError("concurrency must be a positive integer");
  }
  if (!Number.isFinite(timeoutMs) || timeoutMs < 1) {
    throw new TypeError("timeoutMs must be positive");
  }
  if (!Number.isInteger(maxAttempts) || maxAttempts < 1) {
    throw new TypeError("maxAttempts must be a positive integer");
  }
  if (!Number.isFinite(retryBaseDelayMs) || retryBaseDelayMs < 0) {
    throw new TypeError("retryBaseDelayMs must be non-negative");
  }
  if (!Number.isFinite(retryJitterMs) || retryJitterMs < 0) {
    throw new TypeError("retryJitterMs must be non-negative");
  }

  let active = 0;
  const waiters = [];

  const acquire = () => {
    if (active < concurrency) {
      active++;
      return Promise.resolve();
    }
    return new Promise((resolve) => waiters.push(resolve));
  };

  const release = () => {
    const next = waiters.shift();
    if (next) {
      next();
      return;
    }
    active--;
  };

  async function fetchAttempt(url) {
    await acquire();
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const res = await fetchImpl(url, {
        signal: ctrl.signal,
        headers: { Accept: "application/json" },
      });
      if (res.ok) {
        return { value: await res.json() };
      }

      const error = new Error(`HTTP ${res.status} ${url}`);
      return {
        error,
        retryable: res.status === 429 || res.status >= 500,
        retryAfterMs: parseRetryAfter(res.headers?.get?.("retry-after")),
      };
    } catch (error) {
      return { error, retryable: true };
    } finally {
      clearTimeout(timer);
      release();
    }
  }

  return async function fetchJson(url) {
    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
      const result = await fetchAttempt(url);
      if ("value" in result) return result.value;
      if (!result.retryable || attempt === maxAttempts) {
        throw result.error;
      }

      const backoff = Math.min(
        retryBaseDelayMs * 2 ** (attempt - 1),
        RETRY_MAX_DELAY_MS,
      );
      const jitter = retryJitterMs
        ? Math.floor(Math.random() * retryJitterMs)
        : 0;
      const wait = Math.min(
        (result.retryAfterMs ?? backoff) + jitter,
        RETRY_MAX_DELAY_MS,
      );
      console.warn(
        `[prerender-request-pool] ${url} ${result.error?.name || "Error"}: ` +
          `${result.error?.message || result.error} (attempt ${attempt}/${maxAttempts}); ` +
          `retrying in ${wait}ms`,
      );
      await sleepImpl(wait);
    }
  };
}
