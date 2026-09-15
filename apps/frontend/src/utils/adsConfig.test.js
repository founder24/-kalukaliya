import { beforeEach, describe, expect, it, vi } from 'vitest';

describe('adsConfig account gating', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubEnv('PROD', true);
    window.localStorage.clear();
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

  it('suppresses ads for an active referral entitlement and clears on revocation', async () => {
    const adsConfig = await import('./adsConfig');
    adsConfig.setAdsAuthChecked(true);

    adsConfig.setAdsReferralEntitlement({
      active: true,
      expires_at: new Date(Date.now() + 60_000).toISOString(),
    });
    expect(adsConfig.adsConsentGranted()).toBe(false);

    adsConfig.setAdsReferralEntitlement({ active: false, revoked: true });
    expect(adsConfig.adsConsentGranted()).toBe(true);
  });

  it('does not suppress ads after a referral entitlement expires', async () => {
    const adsConfig = await import('./adsConfig');
    adsConfig.setAdsAuthChecked(true);

    adsConfig.setAdsReferralEntitlement({ active: true, expires_at: Date.now() - 1 });
    expect(adsConfig.adsConsentGranted()).toBe(true);
  });
});