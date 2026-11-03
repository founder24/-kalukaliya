import { useEffect } from 'react';
import { adsConsentGranted } from '@/utils/adsConfig';
import { injectMonetagZones, MONETAG_ZONES } from '@/utils/monetagAds';

/**
 * Loads the publisher's Monetag zones only on eligible learning pages.
 * Monetag owns the default OnClick and In-Page Push presentation. The
 * route boundary in App.jsx performs a document navigation when leaving
 * these pages, which clears the provider's page-level runtime.
 */
export default function MonetagPageAds() {
  useEffect(() => {
    let mounted = true;

    const apply = () => {
      if (!mounted || typeof window === 'undefined') return;
      if (adsConsentGranted()) {
        injectMonetagZones();
        return;
      }

      // A provider script cannot be reliably stopped after execution.
      // Reloading clears its listeners/widgets when account or opt-out
      // state changes from allowed to blocked during a learning session.
      const hasMonetagScript = [...document.scripts].some(
        (script) => Object.values(MONETAG_ZONES)
          .some((zone) => script.dataset.zone === zone.zoneId),
      );
      if (hasMonetagScript) window.location.reload();
    };

    apply();
    window.addEventListener('syrabit:ads-consent-changed', apply);
    return () => {
      mounted = false;
      window.removeEventListener('syrabit:ads-consent-changed', apply);
    };
  }, []);

  return null;
}
