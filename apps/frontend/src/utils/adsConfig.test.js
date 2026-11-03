import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

describe('adsConfig account gating', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubEnv('PROD', true);
    window.localStorage.clear();
  });

  afterEach(() => vi.unstubAllEnvs());

  it('preserves AdSense outside Monetag learning pages and disables replaced slots', async () => {
    const { ADSENSE_PLACEMENT_ENV_KEYS } = await import('./adsenseSlotConfig');
    const { MONETAG_REPLACED_AD_PLACEMENTS } = await import('./monetagAds');
    const placements = Object.entries(ADSENSE_PLACEMENT_ENV_KEYS)
      .filter(([placement]) => placement !== 'chat.afterAssistant');
    for (const [, key] of placements) vi.stubEnv(key, '1234567890');
    const { getAdConfig } = await import('./adsConfig');
    for (const [placement] of placements) {
      if (MONETAG_REPLACED_AD_PLACEMENTS.has(placement)) {
        expect(getAdConfig(placement)).toEqual({
          enabled: false, height: 0, reason: 'replaced-by-monetag',
        });
      } else {
        expect(getAdConfig(placement)).toMatchObject({
          enabled: true, network: 'adsense', slotId: '1234567890',
          publisherId: 'ca-pub-8958003374183515',
        });
      }
    }
  });

  it('uses the requested AdSense publisher script as its single source of truth', async () => {
    const { ADSENSE_PUBLISHER_ID, ADSENSE_SCRIPT_URL } = await import('./adsConfig');

    expect(ADSENSE_PUBLISHER_ID).toBe('ca-pub-8958003374183515');
    expect(ADSENSE_SCRIPT_URL).toBe(
      'https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-8958003374183515',
    );
  });

  it('suppresses ads for paid plans and restores them after logout', async () => {
    const adsConfig = await import('./adsConfig');
    adsConfig.setAdsAuthChecked(true);

    expect(adsConfig.adsConsentGranted()).toBe(true);

    adsConfig.setAdsPlan('pro');
    expect(adsConfig.adsConsentGranted()).toBe(false);

    adsConfig.setAdsPlan(null);
    expect(adsConfig.adsConsentGranted()).toBe(true);
  });

  it('keeps the server preference mirrored in local opt-out storage', async () => {
    const adsConfig = await import('./adsConfig');
    adsConfig.setAdsAuthChecked(true);

    adsConfig.hydrateAdsOptOutFromServer(true);
    expect(adsConfig.getAdsOptOut()).toBe(true);
    expect(adsConfig.adsConsentGranted()).toBe(false);

    adsConfig.hydrateAdsOptOutFromServer(false);
    expect(adsConfig.getAdsOptOut()).toBe(false);
    expect(adsConfig.adsConsentGranted()).toBe(true);
  });
});