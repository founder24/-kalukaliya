/**
 * Performance Monitoring - Web Vitals Only
 * 
 * Previously used Firebase Performance Monitoring, now uses web-vitals
 * for Core Web Vitals tracking. All trace functions are no-op stubs
 * that maintain API compatibility.
 */

let _enabled = false;

/**
 * Initialize performance monitoring.
 * Returns a resolved promise (no Firebase dependency).
 */
export function initFirebasePerf() {
  _enabled = import.meta.env?.PROD || false;
  return Promise.resolve(_enabled);
}

/**
 * Check if performance monitoring is enabled.
 */
export function isPerfEnabled() {
  return _enabled;
}

/**
 * Create a custom trace stub. Returns an object with stop()/putMetric()/putAttribute()
 * that are safe to call (no-op implementation).
 */
export function startTrace(name, attributes = {}) {
  return {
    putMetric: () => {},
    putAttribute: () => {},
    stop: () => {},
    isStub: true,
  };
}

/**
 * Report a Core Web Vital metric.
 * Logs to console in development, no-op in production (PostHog handles it).
 */
export function reportWebVitalToPerf(metric) {
  if (!metric) return;
  if (!import.meta.env?.PROD) {
    // eslint-disable-next-line no-console
    console.debug(`[web-vital] ${metric.name}: ${metric.value} (${metric.rating})`);
  }
}

/**
 * W3C traceparent generator for end-to-end tracing correlation.
 * Generates a random trace/span ID pair for the chat fetch to be
 * correlated with backend OpenTelemetry spans.
 */
export function makeTraceparent() {
  try {
    const rand = (n) => {
      const buf = new Uint8Array(n);
      (crypto || window.crypto).getRandomValues(buf);
      return Array.from(buf).map((b) => b.toString(16).padStart(2, '0')).join('');
    };
    const traceId = rand(16);
    const spanId = rand(8);
    const flags = _enabled ? '01' : '00';
    return { traceparent: `00-${traceId}-${spanId}-${flags}`, traceId, spanId };
  } catch {
    return null;
  }
}
