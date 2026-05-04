/**
 * useAdsenseAutoAds — page-level Google AdSense Auto Ads injector.
 *
 * AdSense's Auto Ads tag (`adsbygoogle.js?client=…`) is a single
 * page-level script that auto-discovers ad slots once loaded. It is
 * AdSense is the sole monetised network as of Task #347 (Quge5 removed). Lives on the only two
 * monetised routes: Notes (`/learn/:slug`) and PYQ (`/pyq/:slug`).
 * All other routes stay ad-free — see `scripts/verify-no-ads.mjs`.
 *
 * Gating mirrors `<AdSlot />`: production
 * build only AND `adsConsentGranted()` true (which also honours the
 * `syrabit_ads_optout` localStorage flag and the Task #552 paid-plan
 * gate). Dev builds, opted-out users, and paying subscribers never
 * see this script.
 *
 * Consent is reactive: the hook listens for
 * `syrabit:ads-consent-changed` and re-evaluates. If consent flips
 * to false mid-session (e.g. a returning paid subscriber whose
 * `/auth/me` finally resolved, or a user toggling the privacy
 * opt-out), the previously injected script tag is removed from
 * `<head>`. If consent later flips back to true, the script is
 * re-injected.
 *
 * The script is appended to <head> at most once per page (de-duped by
 * `src` against both an in-module Set and the live DOM).
 */
import { useEffect } from 'react';
import { adsConsentGranted } from '@/utils/adsConfig';

const ADSENSE_CLIENT = 'ca-pub-8958003374183515';
const ADSENSE_SRC = `https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=${ADSENSE_CLIENT}`;

const _injected = new Set();

function pauseAutoAds() {
  try {
    if (typeof window !== 'undefined') {
      window.adsbygoogle = window.adsbygoogle || [];
      window.adsbygoogle.pauseAdRequests = 1;
    }
  } catch { /* ignore */ }
}

function resumeAutoAds() {
  try {
    if (typeof window !== 'undefined' && window.adsbygoogle) {
      delete window.adsbygoogle.pauseAdRequests;
    }
  } catch { /* ignore */ }
}

function removeInjectedAdsense() {
  if (typeof document === 'undefined') return;
  pauseAutoAds();
  const tags = document.querySelectorAll(`script[src="${ADSENSE_SRC}"]`);
  tags.forEach((t) => {
    try {
      if (t.parentNode) t.parentNode.removeChild(t);
    } catch {
      /* ignore */
    }
  });
  _injected.delete(ADSENSE_SRC);
}

function injectAdsense() {
  if (typeof document === 'undefined') return null;
  resumeAutoAds();
  if (_injected.has(ADSENSE_SRC)) return null;
  if (document.querySelector(`script[src="${ADSENSE_SRC}"]`)) {
    _injected.add(ADSENSE_SRC);
    return null;
  }
  const s = document.createElement('script');
  s.src = ADSENSE_SRC;
  s.async = true;
  s.crossOrigin = 'anonymous';
  s.setAttribute('data-ad-client', ADSENSE_CLIENT);
  document.head.appendChild(s);
  _injected.add(ADSENSE_SRC);
  return s;
}

export default function useAdsenseAutoAds() {
  useEffect(() => {
    if (typeof document === 'undefined') return undefined;
    let mounted = true;

    const apply = () => {
      if (!mounted) return;
      if (adsConsentGranted()) {
        injectAdsense();
      } else {
        removeInjectedAdsense();
      }
    };

    apply();
    window.addEventListener('syrabit:ads-consent-changed', apply);

    return () => {
      mounted = false;
      window.removeEventListener('syrabit:ads-consent-changed', apply);
      removeInjectedAdsense();
    };
  }, []);
}

;
