import { useEffect, useRef } from 'react';
import { useLocation, matchPath } from 'react-router-dom';
import axios from 'axios';
import { Analytics } from './analytics';
import { API_BASE } from './api';
import { incrementVisitIfNewSession } from './visitTracker';

const KNOWN_PATTERNS = [
  '/',
  '/pricing',
  '/terms',
  '/privacy',
  '/exam-routine',
  '/payment/success',
  '/payment/cancel',
  '/login',
  '/signup',
  '/reset-password',
  '/onboarding',
  '/library',
  '/curriculum',
  '/subject/:subjectId',
  '/learn/:slug',
  '/chat',
  '/history',
  '/profile',
  '/admin/login',
  '/admin',
  '/:board/:classSlug/:subjectSlug/:topicSlug/:pageType',
  '/:board/:classSlug/:subjectSlug/:topicSlug',
  '/:board/:classSlug/:subjectSlug',
];

function detectIs404(pathname) {
  return !KNOWN_PATTERNS.some((pattern) => matchPath({ path: pattern, end: true }, pathname));
}

function getOrCreateVisitorId() {
  try {
    let vid = localStorage.getItem('syrabit:visitor_id');
    if (!vid) {
      // Use crypto.randomUUID() for cryptographically secure random IDs
      vid = 'v_' + (crypto.randomUUID ? crypto.randomUUID().replace(/-/g, '').slice(0, 22) : Math.random().toString(36).slice(2, 11) + Date.now().toString(36));
      localStorage.setItem('syrabit:visitor_id', vid);
    }
    return vid;
  } catch {
    // Fallback for environments without crypto.randomUUID
    return 'v_anon_' + (crypto.randomUUID ? crypto.randomUUID().replace(/-/g, '').slice(0, 22) : Math.random().toString(36).slice(2, 11));
  }
}

function getOrCreateSessionId() {
  try {
    let sid = sessionStorage.getItem('syrabit:session_id');
    if (!sid) {
      // Use crypto.randomUUID() for cryptographically secure random IDs
      sid = 's_' + (crypto.randomUUID ? crypto.randomUUID().replace(/-/g, '').slice(0, 22) : Math.random().toString(36).slice(2, 11) + Date.now().toString(36));
      sessionStorage.setItem('syrabit:session_id', sid);
    }
    return sid;
  } catch {
    // Fallback for environments without crypto.randomUUID
    return 's_anon_' + (crypto.randomUUID ? crypto.randomUUID().replace(/-/g, '').slice(0, 22) : Math.random().toString(36).slice(2, 11));
  }
}

let heartbeatInterval = null;
let lastSessionId = null;
let hiddenAt = null;

const SESSION_RESUME_WINDOW_MS = 30 * 60 * 1000;

// ── Per-visit page-view boost (Task #483) ─────────────────────────────────
// Fires 4 additional page-view events on the first navigation of every
// new session so the visit total reaches 5+ across all trackers
// (internal /api/analytics/page-view, PostHog $pageview, Cloudflare Web
// Analytics SPA beacon, GA4 if configured). Spaced 600ms apart so neither
// the CF beacon dedup nor PostHog batching drops them.
//
// Resilience: remaining-count is stored in sessionStorage and decremented
// only AFTER each synthetic event actually fires. If the tracker unmounts
// mid-boost (React StrictMode, HMR, route remount) the next mount picks
// up the remainder rather than losing it permanently.
const PV_BOOST_KEY = 'syrabit:pv_boost_remaining';
const PV_BOOST_PATH_KEY = 'syrabit:pv_boost_path';
const PV_BOOST_TITLE_KEY = 'syrabit:pv_boost_title';
const PV_BOOST_EXTRA = 4;
const PV_BOOST_INTERVAL_MS = 600;

function getPvBoostRemaining() {
  try {
    const raw = sessionStorage.getItem(PV_BOOST_KEY);
    if (raw === null) return null;
    const n = parseInt(raw, 10);
    return Number.isFinite(n) ? n : null;
  } catch {
    return null;
  }
}

function setPvBoostRemaining(n) {
  try { sessionStorage.setItem(PV_BOOST_KEY, String(n)); } catch {}
}

function clearPvBoostRemaining() {
  try {
    sessionStorage.removeItem(PV_BOOST_KEY);
    sessionStorage.removeItem(PV_BOOST_PATH_KEY);
    sessionStorage.removeItem(PV_BOOST_TITLE_KEY);
  } catch {}
}

function getPinnedBoostTarget() {
  try {
    return {
      path: sessionStorage.getItem(PV_BOOST_PATH_KEY),
      title: sessionStorage.getItem(PV_BOOST_TITLE_KEY),
    };
  } catch {
    return { path: null, title: null };
  }
}

function setPinnedBoostTarget(path, title) {
  try {
    sessionStorage.setItem(PV_BOOST_PATH_KEY, path || '/');
    sessionStorage.setItem(PV_BOOST_TITLE_KEY, title || '');
  } catch {}
}

function fireSyntheticPageView({ path, title, visitorId, sessionId, referrer, is404Hint }) {
  // 1) Internal analytics endpoint — same payload shape as the real
  // page-view post so backend aggregation works identically.
  try {
    axios.post(
      `${API_BASE}/analytics/page-view`,
      {
        path,
        visitor_id: visitorId,
        session_id: sessionId,
        referrer,
        user_agent: navigator.userAgent,
        screen_width: window.screen.width,
        is_404_hint: is404Hint,
      },
      { withCredentials: true }
    ).catch(() => {});
  } catch {}

  // 2) PostHog $pageview — same path/title as the landing page.
  try { Analytics.pageView(path, title); } catch {}

  // 3) Cloudflare Web Analytics SPA beacon — the beacon hooks
  // window.history.pushState/replaceState and sends a hit on every call.
  // A same-URL replaceState is a no-op for routing (no popstate, same
  // location) but still fires the CF beacon hit.
  try {
    if (typeof window.history?.replaceState === 'function') {
      window.history.replaceState(window.history.state, '', window.location.href);
    }
  } catch {}

  // 4) GA4 — only if gtag is loaded (not currently bundled, but if a
  // GA4 tag is added at runtime by ops it will receive these events).
  try {
    if (typeof window.gtag === 'function') {
      window.gtag('event', 'page_view', {
        page_path: path,
        page_title: title,
        page_location: window.location.href,
      });
    }
  } catch {}
}

function schedulePageViewBoost(remaining, args) {
  const timers = [];
  for (let i = 1; i <= remaining; i++) {
    const t = setTimeout(() => {
      fireSyntheticPageView(args);
      // Decrement using the latest persisted value to stay correct
      // across remounts/HMR (other timer chains may also be writing).
      const cur = getPvBoostRemaining();
      const next = cur === null ? 0 : Math.max(0, cur - 1);
      setPvBoostRemaining(next);
    }, i * PV_BOOST_INTERVAL_MS);
    timers.push(t);
  }
  return () => timers.forEach((t) => clearTimeout(t));
}

function startHeartbeat(sessionId, visitorId) {
  if (heartbeatInterval) clearInterval(heartbeatInterval);
  lastSessionId = sessionId;

  const sendPing = () => {
    const sid = sessionStorage.getItem('syrabit:session_id') || sessionId;
    const vid = localStorage.getItem('syrabit:visitor_id') || visitorId;
    axios.post(
      `${API_BASE}/analytics/session-ping`,
      { session_id: sid, visitor_id: vid },
      { withCredentials: true }
    ).catch(() => {});
  };

  heartbeatInterval = setInterval(sendPing, 30000);
}

function sendSessionEnd(sessionId, visitorId, endTimestamp) {
  const sid = sessionId || lastSessionId || sessionStorage.getItem('syrabit:session_id');
  const vid = visitorId || localStorage.getItem('syrabit:visitor_id');
  if (sid && vid) {
    const payload = { session_id: sid, visitor_id: vid };
    if (endTimestamp) {
      payload.end_timestamp = new Date(endTimestamp).toISOString();
    }
    const blob = new Blob(
      [JSON.stringify(payload)],
      { type: 'application/json' }
    );
    navigator.sendBeacon(`${API_BASE}/analytics/session-end`, blob);
  }
}

function stopHeartbeatAndSendEnd(sessionId, visitorId) {
  if (heartbeatInterval) {
    clearInterval(heartbeatInterval);
    heartbeatInterval = null;
  }
  sendSessionEnd(sessionId, visitorId);
}

function usePageTracking() {
  const location = useLocation();
  const lastPath = useRef(null);
  const sessionIdRef = useRef(null);
  const visitorIdRef = useRef(null);
  const cancelBoostRef = useRef(null);

  useEffect(() => {
    const visitorId = getOrCreateVisitorId();
    const sessionId = getOrCreateSessionId();
    visitorIdRef.current = visitorId;
    sessionIdRef.current = sessionId;

    // Preserve the legacy visit_count side effect that previously lived
    // at the module top-level of SignupEncouragementPopup.jsx (Task #483
    // removed the popup but kept the visitor_id/visit_count state intact).
    incrementVisitIfNewSession();

    startHeartbeat(sessionId, visitorId);

    const handleVisibilityChange = () => {
      if (document.visibilityState === 'hidden') {
        if (heartbeatInterval) {
          clearInterval(heartbeatInterval);
          heartbeatInterval = null;
        }
        hiddenAt = Date.now();
      } else {
        const elapsed = hiddenAt ? Date.now() - hiddenAt : 0;
        hiddenAt = null;
        if (elapsed > SESSION_RESUME_WINDOW_MS) {
          const actualEndTime = Date.now() - elapsed;
          sendSessionEnd(sessionIdRef.current, visitorIdRef.current, actualEndTime);
          try { sessionStorage.removeItem('syrabit:session_id'); } catch {}
          // Reset boost so the resumed session also gets its 4 extras
          // on its next route change.
          clearPvBoostRemaining();
          const newSid = getOrCreateSessionId();
          sessionIdRef.current = newSid;
          lastSessionId = newSid;

          const currentPath = window.location.pathname;
          axios.post(
            `${API_BASE}/analytics/page-view`,
            {
              path: currentPath,
              visitor_id: visitorIdRef.current,
              session_id: newSid,
              referrer: document.referrer || null,
              user_agent: navigator.userAgent,
              screen_width: window.screen.width,
              is_404_hint: detectIs404(currentPath),
            },
            { withCredentials: true }
          ).catch(() => {});
        }
        startHeartbeat(sessionIdRef.current, visitorIdRef.current);
      }
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      stopHeartbeatAndSendEnd(sessionIdRef.current, visitorIdRef.current);
      if (cancelBoostRef.current) {
        try { cancelBoostRef.current(); } catch {}
        cancelBoostRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    const path = location.pathname;
    if (path === lastPath.current) return;
    lastPath.current = path;

    const visitorId = getOrCreateVisitorId();
    const sessionId = getOrCreateSessionId();
    sessionIdRef.current = sessionId;
    visitorIdRef.current = visitorId;
    const referrer = document.referrer || null;
    const is404Hint = detectIs404(path);

    axios.post(
      `${API_BASE}/analytics/page-view`,
      {
        path,
        visitor_id: visitorId,
        session_id: sessionId,
        referrer,
        user_agent: navigator.userAgent,
        screen_width: window.screen.width,
        is_404_hint: is404Hint,
      },
      { withCredentials: true }
    ).catch(() => {});

    Analytics.pageView(path, document.title);

    // GA4 page_view — only when gtag.js was injected at build time
    // (vite ga4Plugin gates on a valid VITE_GA4_ID). No-op otherwise.
    try {
      if (typeof window.gtag === 'function') {
        window.gtag('event', 'page_view', {
          page_path: path,
          page_title: document.title,
          page_location: window.location.href,
        });
      }
    } catch {}

    // ── Fire the per-session page-view boost on the FIRST real
    // navigation of this session. Subsequent route changes within the
    // same session count normally (one event per route change) — we
    // never re-fire the full boost. If a previous mount started the
    // boost but unmounted before all 4 events fired (StrictMode, HMR,
    // route remount), the remaining count was persisted to
    // sessionStorage and we resume here.
    let remaining = getPvBoostRemaining();
    if (remaining === null) {
      remaining = PV_BOOST_EXTRA;
      setPvBoostRemaining(remaining);
      // Pin the landing path/title so a mid-boost navigation does not
      // shift the synthetic events to the new route (architect note).
      setPinnedBoostTarget(path, document.title);
    }
    if (remaining > 0) {
      if (cancelBoostRef.current) {
        try { cancelBoostRef.current(); } catch {}
      }
      const pinned = getPinnedBoostTarget();
      cancelBoostRef.current = schedulePageViewBoost(remaining, {
        path: pinned.path || path,
        title: pinned.title || document.title,
        visitorId,
        sessionId,
        referrer,
        is404Hint: pinned.path ? detectIs404(pinned.path) : is404Hint,
      });
    }
  }, [location.pathname]);
}

export function PageTracker() {
  usePageTracking();
  return null;
}
