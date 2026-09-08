import { afterEach, describe, expect, it, vi } from 'vitest';

import { currentQuotaPeriod } from './anonymous';

describe('anonymous quota period', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('uses a UTC calendar day rather than a month', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-09-08T23:59:59.000Z'));
    expect(currentQuotaPeriod()).toBe('2026-09-08');

    vi.setSystemTime(new Date('2026-09-09T00:00:00.000Z'));
    expect(currentQuotaPeriod()).toBe('2026-09-09');
  });
});