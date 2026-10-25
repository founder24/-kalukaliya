import React from 'react';
import { render } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/utils/adsConfig', () => ({
  adsConsentGranted: vi.fn(() => true),
}));

import { adsConsentGranted } from '@/utils/adsConfig';
import useAdsenseAutoAds from './useAdsenseAutoAds';

function HookProbe({ options }) {
  useAdsenseAutoAds(options);
  return null;
}

describe('useAdsenseAutoAds page modes', () => {
  beforeEach(() => {
    document.head.innerHTML = '';
    window.adsbygoogle = [];
    window.adsbygoogle.pauseAdRequests = 1;
    vi.mocked(adsConsentGranted).mockReturnValue(true);
  });

  it('enables Auto Ads for the default mode used by LearnPage and PYQReplicaPage', () => {
    render(<HookProbe />);

    expect(window.adsbygoogle.pauseAdRequests).toBeUndefined();
  });

  it('keeps Auto Ads paused for ChapterPage’s manual-only mode', () => {
    render(<HookProbe options={{ autoAds: false }} />);

    expect(window.adsbygoogle.pauseAdRequests).toBe(1);
  });

  it('does not inject the AdSense loader when advertising consent is denied', () => {
    vi.mocked(adsConsentGranted).mockReturnValue(false);

    render(<HookProbe />);

    expect(document.querySelector('script[src*="adsbygoogle.js"]')).toBeNull();
    expect(window.adsbygoogle.pauseAdRequests).toBe(1);
  });

  it('removes the loader when advertising consent is withdrawn', () => {
    render(<HookProbe />);
    expect(document.querySelector('script[src*="adsbygoogle.js"]')).not.toBeNull();

    vi.mocked(adsConsentGranted).mockReturnValue(false);
    window.dispatchEvent(new CustomEvent('syrabit:ads-consent-changed'));

    expect(document.querySelector('script[src*="adsbygoogle.js"]')).toBeNull();
    expect(window.adsbygoogle.pauseAdRequests).toBe(1);
  });
});