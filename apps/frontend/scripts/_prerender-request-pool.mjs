export function createJsonRequestPool({
  concurrency,
  timeoutMs,
  fetchImpl = globalThis.fetch,
}) {
  if (!Number.isInteger(concurrency) || concurrency < 1) {
    throw new TypeError("concurrency must be a positive integer");
  }
  if (!Number.isFinite(timeoutMs) || timeoutMs < 1) {
    throw new TypeError("timeoutMs must be positive");
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

  return async function fetchJson(url) {
    await acquire();
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const res = await fetchImpl(url, {
        signal: ctrl.signal,
        headers: { Accept: "application/json" },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status} ${url}`);
      return await res.json();
    } finally {
      clearTimeout(timer);
      release();
    }
  };
}