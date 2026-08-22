/**
 * useAdsenseAutoAds — Google AdSense script loader with optional Auto Ads.
 *
 * AdSense's tag (`adsbygoogle.js?client=…`) is a single page-level script.
 * When loaded without suppression, it auto-discovers and fills ad slots
 * (Auto Ads). This hook supports two modes:
 *
 *   useAdsenseAutoAds()              — default: inject script + enable Auto Ads
 *   useAdsenseAutoAds({ autoAds: false }) — inject script only; keep
 *       `adsbygoogle.pauseAdRequests = 1` so Google's auto-discovery never
 *       fires. Manual <AdSlot> units on the page still work because each
 *       one calls `adsbygoogle.push({})` independently.
 *
 * Use `autoAds: false` on routes that control ad density with explicit
 * per-slot placements (e.g. ChapterPage). Use the default on routes where
 * Google's auto-placement is welcome (LearnPage, PYQReplicaPage).
 *
 * Gating mirrors `<AdSlot />`: production build only AND `adsConsentGranted()`
 * true (honours `syrabit_ads_optout` and the paid-plan gate). Dev builds,
 * opted-out users, and paying subscribers never see this script.
 *
 * Consent is reactive: the hook listens for `syrabit:ads-consent-changed`
 * and re-evaluates. If consent flips to false mid-session the previously
 * injected script tag is removed from `<head>`. If it flips back, the
 * script is re-injected.
 *
 * The script is appended to <head> at most once per page (de-duped by
 * `src` against both an in-module Set and the live DOM).
 */
import { useEffect } from 'react';
import { adsConsentGranted } from '@/utils/adsConfig';
import { injectAdScript, removeAdScript } from '@/utils/adScriptRegistry';

const ADSENSE_CLIENT = 'ca-pub-8958003374183515';
const ADSENSE_SRC = `https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=${ADSENSE_CLIENT}`;

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
  removeAdScript(ADSENSE_SRC);
}

function injectAdsense({ autoAds = true } = {}) {
  if (typeof document === 'undefined') return null;
  // Only resume (remove pauseAdRequests) when auto-ads are wanted.
  // Manual-only callers keep pauseAdRequests = 1 so Google's auto-discovery
  // never fires; individual <AdSlot> units still work via their own push({}).
  if (autoAds) {
    resumeAutoAds();
  } else {
    pauseAutoAds();
  }
  return injectAdScript(ADSENSE_SRC, {
    crossorigin: 'anonymous',
    dataAdClient: ADSENSE_CLIENT,
  });
}

/**
 * @param {{ autoAds?: boolean }} [options]
 *   autoAds (default true) — when false the AdSense loader script is injected
 *   but `adsbygoogle.pauseAdRequests` is kept set, preventing Google's
 *   automatic ad-placement algorithm from running on this page.
 */
export default function useAdsenseAutoAds({ autoAds = true } = {}) {
  useEffect(() => {
    if (typeof document === 'undefined') return undefined;
    let mounted = true;

    const apply = () => {
      if (!mounted) return;
      if (adsConsentGranted()) {
        injectAdsense({ autoAds });
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
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoAds]);
}

;
