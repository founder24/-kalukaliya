/**
 * Page-scoped registry for third-party ad scripts.
 *
 * AdSlot and page-level ad hooks can both request the same loader. Keeping
 * the Set and DOM lookup here makes the request idempotent regardless of
 * which caller runs first.
 */
const _injected = new Set();

export function injectAdScript(url, opts = {}) {
  if (typeof document === 'undefined' || !url) return null;
  if (_injected.has(url)) return null;

  // Also account for a matching tag added before this module was evaluated
  // or by code outside the registry.
  const existing = document.querySelector(`script[src="${url}"]`);
  if (existing) {
    _injected.add(url);
    return null;
  }

  const script = document.createElement('script');
  script.src = url;
  script.async = true;
  script.dataset.syrabitAd = '1';
  if (opts.crossorigin) script.crossOrigin = opts.crossorigin;
  if (opts.dataAdClient) script.setAttribute('data-ad-client', opts.dataAdClient);
  document.head.appendChild(script);
  _injected.add(url);
  return script;
}

export function removeAdScript(url) {
  if (typeof document === 'undefined' || !url) return;

  // Only remove scripts owned by this registry. An unrelated existing
  // publisher tag should still satisfy future dedupe checks.
  document
    .querySelectorAll(`script[data-syrabit-ad][src="${url}"]`)
    .forEach((script) => {
      try {
        script.remove();
      } catch {
        /* ignore */
      }
    });
  _injected.delete(url);
}