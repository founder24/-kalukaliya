import { beforeEach, describe, expect, it } from 'vitest';
import { claimSessionEntry, resetSessionEntryClaim } from './sessionEntryTracking';

describe('session-entry page-view marker', () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it('marks only the first consented page view in a browser session', () => {
    expect(claimSessionEntry()).toBe(true);
    expect(claimSessionEntry()).toBe(false);
  });

  it('starts a new entry claim when the browser tracker rotates the session', () => {
    expect(claimSessionEntry()).toBe(true);
    resetSessionEntryClaim();
    expect(claimSessionEntry()).toBe(true);
  });
});
