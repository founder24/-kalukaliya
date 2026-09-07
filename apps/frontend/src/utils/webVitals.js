import { onCLS, onINP, onLCP, onFCP, onTTFB } from 'web-vitals';
import { initFirebasePerf, reportWebVitalToPerf } from './firebasePerf';
import { hasAnalyticsConsent } from './analyticsConsent';

function sendToPostHog(metric) {
  if (!hasAnalyticsConsent()) return;
  try {
    if (window.posthog && typeof window.posthog.capture === 'function') {
      window.posthog.capture('web_vital', {
        metric_name: metric.name,
        metric_value: metric.value,
        metric_delta: metric.delta,
        metric_id: metric.id,
        metric_rating: metric.rating,
        navigation_type: metric.navigationType,
        page_path: window.location.pathname,
      });
    }
  } catch {}
}

function reportMetric(metric) {
  if (!hasAnalyticsConsent()) return;
  sendToPostHog(metric);
  // Task #610 — Firebase Performance Monitoring sink. No-op until
  // initFirebasePerf() resolves (production + configured + sampled-in).
  reportWebVitalToPerf(metric);
}

export function initWebVitals() {
  if (import.meta.env.DEV || !hasAnalyticsConsent()) return;
  // Kick off Firebase Perf init in parallel; the web-vitals callbacks below
  // fire later (LCP/INP) so the SDK has time to load before the first
  // metric arrives. If init fails / is gated off, reportWebVitalToPerf
  // becomes a no-op.
  initFirebasePerf();
  onCLS(reportMetric);
  onINP(reportMetric);
  onLCP(reportMetric);
  onFCP(reportMetric);
  onTTFB(reportMetric);
}
