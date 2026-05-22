// Syrabit.ai — Firebase Performance Monitoring (Task #610)
//
// Production-only browser RUM for Core Web Vitals + custom chat traces.
// Gated three ways so dev / preview / unconfigured deploys are zero-cost:
//
//   1. Only runs in production builds (`import.meta.env.PROD`).
//   2. Only runs when VITE_FIREBASE_API_KEY + VITE_FIREBASE_APP_ID are set.
//   3. Optional VITE_FIREBASE_PERF_SAMPLE_RATE (0–1, default 0.2) controls
//      instrumentation sampling so we trace ~10–20% of sessions.
//
// All exports are safe to call before init(): they return null / no-op
// until Firebase is loaded, so call sites in webVitals.js and ChatPage.jsx
// can stay clean.

let _perf = null;
let _initPromise = null;
let _enabled = false;

function _readConfig() {
  const env = import.meta.env || {};
  const cfg = {
    apiKey: env.VITE_FIREBASE_API_KEY,
    authDomain: env.VITE_FIREBASE_AUTH_DOMAIN,
    projectId: env.VITE_FIREBASE_PROJECT_ID,
    storageBucket: env.VITE_FIREBASE_STORAGE_BUCKET,
    messagingSenderId: env.VITE_FIREBASE_MESSAGING_SENDER_ID,
    appId: env.VITE_FIREBASE_APP_ID,
    measurementId: env.VITE_FIREBASE_MEASUREMENT_ID,
  };
  if (!cfg.apiKey || !cfg.appId || !cfg.projectId) return null;
  return cfg;
}

function _sampleRate() {
  const raw = parseFloat(import.meta.env?.VITE_FIREBASE_PERF_SAMPLE_RATE || '0.2');
  if (!Number.isFinite(raw)) return 0.2;
  return Math.min(1, Math.max(0, raw));
}

function isPerfEnabled() {
  return _enabled;
}

export function initFirebasePerf() {
  if (_initPromise) return _initPromise;
  if (!import.meta.env?.PROD) return Promise.resolve(false);
  const cfg = _readConfig();
  if (!cfg) return Promise.resolve(false);

  // Sampling guard: pick a stable random per session so sub-resource
  // traces & custom traces from the same page-view stick together.
  const session = (() => {
    try {
      const k = '_syr_perf_sample';
      const cached = sessionStorage.getItem(k);
      if (cached) return parseFloat(cached);
      const r = Math.random();
      sessionStorage.setItem(k, String(r));
      return r;
    } catch {
      return Math.random();
    }
  })();
  if (session > _sampleRate()) {
    return Promise.resolve(false);
  }

  _initPromise = (async () => {
    try {
      const [{ initializeApp, getApps }, { getPerformance }] = await Promise.all([
        import('firebase/app'),
        import('firebase/performance'),
      ]);
      const app = getApps().length ? getApps()[0] : initializeApp(cfg);
      _perf = getPerformance(app);
      _enabled = true;
      return true;
    } catch (e) {
      try { console.warn('[firebase-perf] init failed:', e?.message || e); } catch {}
      _perf = null;
      _enabled = false;
      return false;
    }
  })();
  return _initPromise;
}

// Create a custom trace. Returns an object with stop() / putMetric() / putAttribute()
// that is safe to call even when Perf is disabled (becomes a no-op stub).
export function startTrace(name, attributes = {}) {
  const stub = {
    putMetric: () => {},
    putAttribute: () => {},
    stop: () => {},
    isStub: true,
  };
  if (!_enabled || !_perf) return stub;
  try {
    // trace() is sync, returns Trace which has start()/stop().
    // Use the modular API form.
    // eslint-disable-next-line global-require
    const perfMod = window.__firebasePerfMod;
    const traceFn = perfMod?.trace;
    let tr;
    if (traceFn) {
      tr = traceFn(_perf, name);
    } else {
      // Fallback: dynamic import once and cache.
      // We can't await synchronously, so return stub for the very first call;
      // subsequent calls will use the cached module.
      import('firebase/performance').then((m) => {
        try { window.__firebasePerfMod = m; } catch {}
      });
      return stub;
    }
    tr.start();
    Object.entries(attributes || {}).forEach(([k, v]) => {
      try { tr.putAttribute(String(k).slice(0, 40), String(v).slice(0, 100)); } catch {}
    });
    return {
      putMetric: (k, v) => { try { tr.putMetric(k, Math.round(v)); } catch {} },
      putAttribute: (k, v) => {
        try { tr.putAttribute(String(k).slice(0, 40), String(v).slice(0, 100)); } catch {}
      },
      stop: () => { try { tr.stop(); } catch {} },
    };
  } catch (e) {
    return stub;
  }
}

// One-shot helper: report a Core Web Vital as a Firebase Perf custom
// trace with a single metric. We use a 1ms trace so the metric value
// itself carries the signal — Firebase aggregates by metric name.
export function reportWebVitalToPerf(metric) {
  if (!_enabled || !_perf || !metric) return;
  try {
    // Dynamic import of trace() each time is cheap once cached.
    import('firebase/performance').then((m) => {
      try {
        const tr = m.trace(_perf, `web_vital_${metric.name}`);
        tr.start();
        const value = metric.name === 'CLS'
          ? Math.round((metric.value || 0) * 1000)
          : Math.round(metric.value || 0);
        tr.putMetric(metric.name, value);
        if (metric.rating) tr.putAttribute('rating', String(metric.rating).slice(0, 100));
        if (metric.id) tr.putAttribute('metric_id', String(metric.id).slice(0, 100));
        tr.putAttribute('page', window.location.pathname.slice(0, 100));
        tr.stop();
      } catch {}
    }).catch(() => {});
  } catch {}
}

// W3C traceparent generator so the chat fetch can be correlated end-to-end
// with the backend OpenTelemetry span (auto-instrumentor reads `traceparent`).
// Format: "00-<32-hex trace>-<16-hex span>-01" (sampled). When tracing is
// disabled in this session, we emit a NOT-sampled flag so the backend
// won't oversample either.
export function makeTraceparent() {
  try {
    const rand = (n) => {
      const buf = new Uint8Array(n);
      (crypto || window.crypto).getRandomValues(buf);
      return Array.from(buf).map((b) => b.toString(16).padStart(2, '0')).join('');
    };
    const traceId = rand(16); // 32 hex chars
    const spanId = rand(8);   // 16 hex chars
    const flags = _enabled ? '01' : '00';
    return { traceparent: `00-${traceId}-${spanId}-${flags}`, traceId, spanId };
  } catch {
    return null;
  }
}
