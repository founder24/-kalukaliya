// A single, deliberately conservative consent decision for optional analytics.
// Missing/unreadable storage is not consent. Essential operational telemetry is
// classified separately by analytics.jsx and does not use this helper.
export const ANALYTICS_CONSENT_KEY = 'syrabit_cookie_consent';

export function hasAnalyticsConsent() {
  try {
    return localStorage.getItem(ANALYTICS_CONSENT_KEY) === 'accepted';
  } catch {
    return false;
  }
}

export function setAnalyticsConsent(value) {
  try {
    localStorage.setItem(ANALYTICS_CONSENT_KEY, value);
    window.dispatchEvent(new CustomEvent('syrabit:analytics-consent-changed', {
      detail: { consent: value },
    }));
  } catch {
    // Storage may be blocked; optional analytics stays disabled in that case.
  }
}