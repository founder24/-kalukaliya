import { describe, expect, it } from 'vitest';

import { shouldPollChatCancellation } from './chat';

describe('stream cancellation polling', () => {
  it('bounds D1 tombstone checks during bursts of streamed chunks', () => {
    let lastCheckedAt = 0;
    const checks: number[] = [];

    for (let now = 100; now <= 1_000; now += 100) {
      if (shouldPollChatCancellation(now, lastCheckedAt)) {
        checks.push(now);
        lastCheckedAt = now;
      }
    }

    expect(checks).toEqual([500, 1_000]);
  });

  it('polls again once the fallback interval has elapsed', () => {
    expect(shouldPollChatCancellation(1_499, 1_000)).toBe(false);
    expect(shouldPollChatCancellation(1_500, 1_000)).toBe(true);
  });
});