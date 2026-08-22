import React from 'react';
import { render } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/utils/adsConfig', () => ({
  adsConsentGranted: vi.fn(() => true),
}));

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
  });

  it('enables Auto Ads for the default mode used by LearnPage and PYQReplicaPage', () => {
    render(<HookProbe />);

    expect(window.adsbygoogle.pauseAdRequests).toBeUndefined();
  });

  it('keeps Auto Ads paused for ChapterPage’s manual-only mode', () => {
    render(<HookProbe options={{ autoAds: false }} />);

    expect(window.adsbygoogle.pauseAdRequests).toBe(1);
  });
});