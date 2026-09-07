/**
 * Page-scoped registry for third-party ad scripts.
 *
 * AdSlot and page-level ad hooks can both request the same loader. Keeping
 * the Set and DOM lookup here makes the request idempotent regardless of
 * which caller runs first.
 */
const _injected = new Set();
const _ownedScripts = new Map();

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
  if (opts.crossorigin) script.crossOrigin = opts.crossorigin;
  if (opts.dataAdClient) script.setAttribute('data-ad-client', opts.dataAdClient);
  document.head.appendChild(script);
  _injected.add(url);
  _ownedScripts.set(url, script);
  return script;
}

export function removeAdScript(url) {
  if (typeof document === 'undefined' || !url) return;

  // Keep ownership in module state rather than adding a custom attribute to
  // the third-party script. A matching publisher tag supplied outside this
  // registry must not be removed.
  const script = _ownedScripts.get(url);
  if (script) {
    try {
      script.remove();
    } catch {
      /* ignore */
    }
    _ownedScripts.delete(url);
  }
  _injected.delete(url);
}