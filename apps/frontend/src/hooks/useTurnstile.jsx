/**
 * Task #404 — Cloudflare Turnstile config + script loader hooks.
 *
 * The backend exposes ``GET /api/turnstile/config`` (Task #404) which
 * returns ``{enabled, site_key}``. ``enabled`` is true only when the
 * operator has flipped ``TURNSTILE_ON`` and set ``TURNSTILE_SITE_KEY``
 * — both halves required so a half-configured rollout cannot mount a
 * widget that will never resolve.
 *
 * ``useTurnstileConfig`` is the building block consumed by
 * ``<TurnstileWidget />``: it fetches the config once per page load
 * (cached via module-scope promise), and then every consumer reads
 * from the same in-memory copy. The fetch is wrapped in a ``try`` so
 * the disabled state is the failure mode — a network blip or a
 * misconfigured ``/api/turnstile/config`` cannot lock users out of the
 * sign-in page.
 *
 * ``useTurnstile`` is kept as a thin compatibility shim so any older
 * call sites (and the ``vi.mock('@/hooks/useTurnstile', ...)`` lines
 * in existing tests) keep compiling without changes.
 */
import { useEffect, useState } from 'react';
import { API_BASE } from '@/utils/api';

const TURNSTILE_SCRIPT_SRC =
  'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';

let _configPromise = null;
let _cachedConfig = null;
let _scriptPromise = null;

export function _resetTurnstileForTests() {
  _configPromise = null;
  _cachedConfig = null;
  _scriptPromise = null;
}

/**
 * Fetch ``/api/turnstile/config`` exactly once per page load. Failure
 * (network error, non-2xx, missing site key) collapses to the
 * disabled config so the form keeps working.
 */
export function fetchTurnstileConfig() {
  if (_cachedConfig) return Promise.resolve(_cachedConfig);
  if (_configPromise) return _configPromise;
  _configPromise = (async () => {
    const fallback = { enabled: false, site_key: null };
    try {
      if (typeof fetch !== 'function') return fallback;
      const res = await fetch(`${API_BASE}/turnstile/config`, {
        credentials: 'omit',
      });
      if (!res.ok) return fallback;
      const data = await res.json();
      const cfg = {
        enabled: !!(data && data.enabled && data.site_key),
        site_key: (data && data.site_key) || null,
      };
      _cachedConfig = cfg;
      return cfg;
    } catch {
      return fallback;
    } finally {
      _configPromise = null;
    }
  })();
  return _configPromise;
}

/**
 * Inject the Cloudflare Turnstile API script tag and resolve once the
 * global ``window.turnstile`` object is available. Idempotent — every
 * call shares the same in-flight promise. Safe to call from a
 * non-browser environment (returns ``null``).
 */
export function loadTurnstileScript() {
  if (typeof window === 'undefined' || typeof document === 'undefined') {
    return Promise.resolve(null);
  }
  if (window.turnstile) return Promise.resolve(window.turnstile);
  if (_scriptPromise) return _scriptPromise;
  _scriptPromise = new Promise((resolve, reject) => {
    const existing = document.querySelector(
      `script[src="${TURNSTILE_SCRIPT_SRC}"]`,
    );
    if (existing) {
      existing.addEventListener('load', () => resolve(window.turnstile || null));
      existing.addEventListener('error', (err) => {
        _scriptPromise = null;
        reject(err);
      });
      if (window.turnstile) resolve(window.turnstile);
      return;
    }
    const s = document.createElement('script');
    s.src = TURNSTILE_SCRIPT_SRC;
    s.async = true;
    s.defer = true;
    s.onload = () => resolve(window.turnstile || null);
    s.onerror = (err) => {
      _scriptPromise = null;
      reject(err);
    };
    document.head.appendChild(s);
  });
  return _scriptPromise;
}

/**
 * React hook returning the Turnstile config from the backend.
 *
 * ``ready`` flips true once the fetch settles (success or fallback);
 * ``enabled`` is true only when the backend reports both flag-on and
 * site-key-set. ``siteKey`` is null when disabled.
 */
export function useTurnstileConfig() {
  const [state, setState] = useState(() => {
    if (_cachedConfig) {
      return {
        ready: true,
        enabled: !!_cachedConfig.enabled,
        siteKey: _cachedConfig.site_key || null,
      };
    }
    return { ready: false, enabled: false, siteKey: null };
  });

  useEffect(() => {
    let cancelled = false;
    fetchTurnstileConfig().then((cfg) => {
      if (cancelled) return;
      setState({
        ready: true,
        enabled: !!cfg.enabled,
        siteKey: cfg.site_key || null,
      });
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return state;
}

/**
 * Backwards-compatible no-op-ish hook kept so legacy import sites
 * (and existing test mocks like ``vi.mock('@/hooks/useTurnstile')``)
 * continue to compile. New code should use ``<TurnstileWidget />``
 * directly to render the widget and call its imperative ``getToken``.
 */
export function useTurnstile() {
  const cfg = useTurnstileConfig();
  return {
    enabled: cfg.enabled,
    ready: cfg.ready,
    getToken: async () => '',
    reset: () => {},
  };
}
