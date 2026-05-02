/**
 * <AdSlot placement="..." /> — the only component allowed to inject a
 * third-party ad script on Syrabit.ai. All wiring lives in
 * `src/utils/adsConfig.js`.
 *
 * Behavior:
 *   - Reserves a fixed height before any script loads (no CLS).
 *   - Renders nothing when the placement is disabled (missing env var)
 *     OR consent is not granted OR the build is not production.
 *   - Lazy-loads the network's script with IntersectionObserver — the
 *     script tag is appended to <head> only once the slot is within
 *     ~200px of the viewport.
 *   - De-dupes scripts by URL so two slots on the same page never
 *     load the same script twice.
 */
import { useEffect, useRef, useState } from 'react';
import { getAdConfig, adsConsentGranted } from '@/utils/adsConfig';
import Analytics from '@/utils/analytics';

// Scripts injected this session, keyed by URL. Survives across mounts
// because it lives on the module, not on a component instance.
const _injected = new Set();

function injectScript(url, opts = {}) {
  if (typeof document === 'undefined') return;
  if (_injected.has(url)) return;
  // Also de-dupe against any matching script already in the DOM (e.g.
  // injected by a sibling slot before this module's Set was hydrated,
  // or by `useAdsenseAutoAds` for the AdSense loader).
  const existing = document.querySelector(`script[src="${url}"]`);
  if (existing) { _injected.add(url); return; }
  const s = document.createElement('script');
  s.src = url;
  s.async = true;
  s.dataset.syrabitAd = '1';
  if (opts.crossorigin) s.crossOrigin = opts.crossorigin;
  if (opts.dataAdClient) s.setAttribute('data-ad-client', opts.dataAdClient);
  document.head.appendChild(s);
  _injected.add(url);
}

export default function AdSlot({ placement, className = '', style = {} }) {
  const cfg = getAdConfig(placement);
  const ref = useRef(null);
  const [shouldLoad, setShouldLoad] = useState(false);
  const [consentOk, setConsentOk] = useState(false);
  // Task #528: ensure the viewability ping fires at most once per mount.
  const viewedRef = useRef(false);

  // Resolve consent on the client. Avoids SSR/hydration mismatch
  // because `adsConsentGranted()` reads localStorage and import.meta.env.PROD.
  // Re-evaluates on `syrabit:ads-consent-changed` so that toggling the
  // privacy opt-out, hydrating a paid plan from `/auth/me` (Task #552),
  // or logging out flips this slot on/off without a page reload.
  useEffect(() => {
    if (typeof window === 'undefined') return undefined;
    const apply = () => setConsentOk(adsConsentGranted());
    apply();
    window.addEventListener('syrabit:ads-consent-changed', apply);
    return () => window.removeEventListener('syrabit:ads-consent-changed', apply);
  }, []);

  // Lazy-load the network script once the slot scrolls near the viewport.
  useEffect(() => {
    if (!cfg.enabled || !consentOk) return;
    const el = ref.current;
    if (!el || typeof window === 'undefined') return;
    if (typeof IntersectionObserver === 'undefined') {
      setShouldLoad(true);
      return;
    }
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) {
        setShouldLoad(true);
        io.disconnect();
      }
    }, { rootMargin: '200px' });
    io.observe(el);
    return () => io.disconnect();
  }, [cfg.enabled, consentOk]);

  useEffect(() => {
    if (!shouldLoad || !cfg.enabled) return;
    injectScript(cfg.scriptUrl, {
      crossorigin: cfg.crossorigin,
      dataAdClient: cfg.network === 'adsense' ? cfg.publisherId : '',
    });
    // AdSense: queue an empty config onto `window.adsbygoogle` so the
    // network fills the <ins> element rendered below. The array is
    // AdSense's own command queue — pushes made before the loader has
    // evaluated are picked up and processed once it boots, so this is
    // safe to call before the script tag has executed. Wrapped in a
    // try/catch purely as a defensive guard against a hostile global.
    if (cfg.network === 'adsense') {
      try {
        (window.adsbygoogle = window.adsbygoogle || []).push({});
      } catch {
        /* ignore */
      }
    }
  }, [shouldLoad, cfg.enabled, cfg.scriptUrl, cfg.network, cfg.publisherId, cfg.crossorigin]);

  // Task #528: viewability ping. Fire one PostHog event the first time
  // this slot is at least 50% within the viewport. Gated on the same
  // consent + enabled checks as the script loader so opt-out users
  // emit nothing. The observer disconnects after the first ping so
  // each mount produces at most one `ad_slot_viewed` event.
  useEffect(() => {
    if (!cfg.enabled || !consentOk) return;
    const el = ref.current;
    if (!el || typeof window === 'undefined') return;
    if (typeof IntersectionObserver === 'undefined') return;
    const io = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting && entry.intersectionRatio >= 0.5) {
          if (!viewedRef.current) {
            viewedRef.current = true;
            try {
              Analytics.adSlotViewed({
                placement,
                network: cfg.network,
              });
            } catch {}
          }
          io.disconnect();
          return;
        }
      }
    }, { threshold: 0.5 });
    io.observe(el);
    return () => io.disconnect();
  }, [cfg.enabled, cfg.network, consentOk, placement]);

  // Disabled placements collapse completely — no reserved space, no DOM
  // beyond an empty fragment. Required so /chat, /library, /browser, and
  // chapter routes never reserve ad real-estate even if a contributor
  // accidentally drops an <AdSlot /> there. Same collapse policy applies
  // when consent is denied / build is not production: per the task spec
  // ("when disabled, the slot collapses with no reserved space"), a
  // user who has not granted ad consent must see zero reserved height.
  if (!cfg.enabled || !consentOk) return null;

  // Fluid in-article ads let Google control height entirely — no reserved
  // minHeight so the page never holds empty space when Google skips the slot.
  // Display/auto ads reserve their configured height to prevent CLS on load.
  const isFluid = cfg.adFormat === 'fluid';

  return (
    <div
      ref={ref}
      className={className}
      data-ad-placement={placement}
      data-ad-network={cfg.network}
      aria-label={cfg.label || 'Advertisement'}
      style={{
        width: '100%',
        display: 'block',
        // Fluid slots: no height reservation — Google sizes them.
        // Display slots: reserve configured height to prevent CLS.
        ...(isFluid ? {} : { minHeight: `${cfg.height}px` }),
        // Tighter vertical margin on mobile (12px) keeps ad density
        // comfortable without eating into reading space.
        margin: '12px 0',
        ...style,
      }}
    >
      {cfg.network === 'adsense' ? (
        // AdSense per-slot manual unit.
        // — fluid/in-article: Google picks height; no size constraints set.
        // — auto/display: full-width-responsive lets Google pick IAB size
        //   (320×50 banner or 300×250 rectangle on mobile).
        <ins
          className="adsbygoogle"
          style={{
            display: 'block',
            width: '100%',
            ...(isFluid ? {} : { minHeight: `${cfg.height}px` }),
          }}
          data-ad-client={cfg.publisherId}
          data-ad-slot={cfg.slotId}
          data-ad-format={cfg.adFormat}
          {...(cfg.adLayout ? { 'data-ad-layout': cfg.adLayout } : {})}
          {...(!isFluid ? { 'data-full-width-responsive': 'true' } : {})}
        />
      ) : (
        <div
          id={`syrabit-ad-${placement.replace(/\./g, '-')}`}
          data-slot-id={cfg.slotId}
          data-publisher-id={cfg.publisherId || undefined}
          style={{ minHeight: `${cfg.height}px`, width: '100%' }}
        />
      )}
    </div>
  );
}
