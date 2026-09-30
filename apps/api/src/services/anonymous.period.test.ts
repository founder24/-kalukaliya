import { afterEach, describe, expect, it, vi } from 'vitest';

import { currentQuotaMonthPeriod, currentQuotaPeriod } from './anonymous';

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

  it('uses a UTC calendar month for the free chat allowance', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-09-30T23:59:59.999Z'));
    expect(currentQuotaMonthPeriod()).toBe('2026-09');

    vi.setSystemTime(new Date('2026-10-01T00:00:00.000Z'));
    expect(currentQuotaMonthPeriod()).toBe('2026-10');

    vi.setSystemTime(new Date('2026-12-31T23:59:59.999Z'));
    expect(currentQuotaMonthPeriod()).toBe('2026-12');
    vi.setSystemTime(new Date('2027-01-01T00:00:00.000Z'));
    expect(currentQuotaMonthPeriod()).toBe('2027-01');
  });
});