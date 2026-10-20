import { describe, expect, it } from 'vitest';
import { shouldCollapseEmptyAdSlot } from '../utils/emptyAdSlotPolicy';

describe('empty AdSense slot fallback', () => {
  it('collapses an explicit unfilled response', () => {
    expect(shouldCollapseEmptyAdSlot({
      adStatus: 'unfilled',
      requestStatus: 'done',
      hasCreative: false,
      timedOut: false,
    })).toBe(true);
  });

  it('keeps filled creatives even after the fallback timeout', () => {
    expect(shouldCollapseEmptyAdSlot({
      adStatus: 'filled',
      requestStatus: 'done',
      hasCreative: true,
      timedOut: true,
    })).toBe(false);
  });

  it('waits briefly after the script completes in case its creative is still arriving', () => {
    expect(shouldCollapseEmptyAdSlot({
      adStatus: null,
      requestStatus: 'done',
      hasCreative: false,
      timedOut: false,
    })).toBe(false);
  });

  it('bounds unresolved empty slots after the request timeout', () => {
    expect(shouldCollapseEmptyAdSlot({
      adStatus: null,
      requestStatus: null,
      hasCreative: false,
      timedOut: true,
    })).toBe(true);
  });

  it('does not collapse a request still waiting for a creative', () => {
    expect(shouldCollapseEmptyAdSlot({
      adStatus: null,
      requestStatus: null,
      hasCreative: false,
      timedOut: false,
    })).toBe(false);
  });
});
