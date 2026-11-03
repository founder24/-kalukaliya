import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, waitFor } from '@testing-library/react';

describe('MonetagPageAds account gating', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubEnv('PROD', true);
    window.localStorage.clear();
    document.head.querySelectorAll('script[data-zone]').forEach((script) => script.remove());
  });

  afterEach(() => {
    cleanup();
    document.head.querySelectorAll('script[data-zone]').forEach((script) => script.remove());
    vi.unstubAllEnvs();
  });

  it('waits for auth check and then loads both zones once', async () => {
    const adsConfig = await import('@/utils/adsConfig');
    const { default: MonetagPageAds } = await import('./MonetagPageAds');

    render(<MonetagPageAds />);
    expect(document.querySelectorAll('script[data-zone]')).toHaveLength(0);

    adsConfig.setAdsAuthChecked(true);

    await waitFor(() => {
      expect([...document.querySelectorAll('script[data-zone]')].map((script) => script.dataset.zone))
        .toEqual(['12180636', '12180642']);
    });
  });

  it('does not load Monetag for opted-out visitors', async () => {
    const adsConfig = await import('@/utils/adsConfig');
    adsConfig.setAdsAuthChecked(true);
    adsConfig.setAdsOptOut(true);
    const { default: MonetagPageAds } = await import('./MonetagPageAds');

    render(<MonetagPageAds />);

    await waitFor(() => expect(document.querySelectorAll('script[data-zone]')).toHaveLength(0));
  });
});
