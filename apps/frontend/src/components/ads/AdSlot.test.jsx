import React from 'react';
import { render, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('@/utils/adsConfig', () => ({
  adsConsentGranted: vi.fn(() => true),
  getAdConfig: vi.fn((placement) => ({
    enabled: true,
    network: 'adsense',
    scriptUrl: '/adsense.js',
    publisherId: 'ca-pub-test',
    slotId: {
      'chapter.notes.top': 'slot-notes-top',
      'chapter.qa.end': 'slot-qa-end',
      'chapter.qa.inContent': 'slot-qa-inline',
      'chapter.pyq.top': 'slot-pyq-top',
    }[placement] || 'slot-fallback',
    height: 250,
    label: 'Advertisement',
    adFormat: 'auto',
    adLayout: null,
    crossorigin: 'anonymous',
  })),
}));

vi.mock('@/utils/analytics', () => ({
  default: { adSlotViewed: vi.fn() },
}));

import AdSlot from './AdSlot';

describe('AdSlot AdSense queue protection', () => {
  beforeEach(() => {
    // A missing IntersectionObserver makes the test exercise the same
    // shouldLoad path immediately without depending on viewport geometry.
    vi.stubGlobal('IntersectionObserver', undefined);
    window.adsbygoogle = [];
    document.head.innerHTML = '';
  });

  it('does not re-queue Notes after a rapid Notes → Q&A → PYQ → Notes sequence', async () => {
    const push = vi.spyOn(window.adsbygoogle, 'push');
    const notes = render(<AdSlot placement="chapter.notes.top" />);

    await waitFor(() => expect(push).toHaveBeenCalledTimes(1));
    notes.unmount();

    const questions = render(<AdSlot placement="chapter.qa.end" />);
    await waitFor(() => expect(push).toHaveBeenCalledTimes(2));
    questions.unmount();

    const pyq = render(<AdSlot placement="chapter.pyq.top" />);
    await waitFor(() => expect(push).toHaveBeenCalledTimes(3));
    pyq.unmount();

    render(<AdSlot placement="chapter.notes.top" />);
    await waitFor(() => expect(push).toHaveBeenCalledTimes(3));
  });

  it('does not queue duplicate in-content instances that share a slot ID', async () => {
    const push = vi.spyOn(window.adsbygoogle, 'push');

    render(
      <>
        <AdSlot placement="chapter.qa.inContent" />
        <AdSlot placement="chapter.qa.inContent" />
      </>,
    );

    await waitFor(() => expect(push).toHaveBeenCalledTimes(1));
  });
});