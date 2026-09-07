import { afterEach, describe, expect, it } from 'vitest';
import {
  ANALYTICS_CONSENT_KEY,
  hasAnalyticsConsent,
  setAnalyticsConsent,
} from './analyticsConsent';

afterEach(() => localStorage.clear());

describe('analytics consent', () => {
  it('fails closed until affirmative consent is stored', () => {
    expect(hasAnalyticsConsent()).toBe(false);
    localStorage.setItem(ANALYTICS_CONSENT_KEY, 'declined');
    expect(hasAnalyticsConsent()).toBe(false);
  });

  it('only enables optional analytics after acceptance', () => {
    setAnalyticsConsent('accepted');
    expect(hasAnalyticsConsent()).toBe(true);
    setAnalyticsConsent('declined');
    expect(hasAnalyticsConsent()).toBe(false);
  });
});