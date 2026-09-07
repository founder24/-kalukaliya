import { useEffect, useRef } from 'react';
import { useLocation, matchPath } from 'react-router-dom';
import axios from 'axios';
import { Analytics } from './analytics';
import { API_BASE } from './api';
import { incrementVisitIfNewSession } from './visitTracker';
import { hasAnalyticsConsent } from './analyticsConsent';

const KNOWN_PATTERNS = [
  '/',
  '/terms',
  '/privacy',
  '/exam-routine',
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
let lastPageViewFingerprint = null;
let lastPageViewAt = 0;

const SESSION_RESUME_WINDOW_MS = 30 * 60 * 1000;
const PAGE_VIEW_DEDUP_WINDOW_MS = 1000;

// React StrictMode and a rapid route remount can run the tracking effect twice
// for one navigation. Keep the dedupe process-local: it never suppresses a
// legitimate later visit after the short navigation window.
function claimPageView(sessionId, path) {
  const fingerprint = `${sessionId}:${path}`;
  const now = Date.now();
  if (fingerprint === lastPageViewFingerprint && now - lastPageViewAt < PAGE_VIEW_DEDUP_WINDOW_MS) {
    return false;
  }
  lastPageViewFingerprint = fingerprint;
  lastPageViewAt = now;
  return true;
}

function startHeartbeat(sessionId, visitorId) {
  if (!hasAnalyticsConsent()) return;
  if (heartbeatInterval) clearInterval(heartbeatInterval);
  lastSessionId = sessionId;

  const sendPing = () => {
    if (!hasAnalyticsConsent()) return;
    const sid = sessionStorage.getItem('syrabit:session_id') || sessionId;
    const vid = localStorage.getItem('syrabit:visitor_id') || visitorId;
    axios.post(
      `${API_BASE}/analytics/session-ping`,
      { session_id: sid, visitor_id: vid, analytics_consent: 'granted' },
      { withCredentials: true }
    ).catch(() => {});
  };

  heartbeatInterval = setInterval(sendPing, 30000);
}

function sendSessionEnd(sessionId, visitorId, endTimestamp) {
  if (!hasAnalyticsConsent()) return;
  const sid = sessionId || lastSessionId || sessionStorage.getItem('syrabit:session_id');
  const vid = visitorId || localStorage.getItem('syrabit:visitor_id');
  if (sid && vid) {
    const payload = { session_id: sid, visitor_id: vid, analytics_consent: 'granted' };
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

  useEffect(() => {
    if (!hasAnalyticsConsent()) return undefined;
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
        if (!hasAnalyticsConsent()) return;
        const elapsed = hiddenAt ? Date.now() - hiddenAt : 0;
        hiddenAt = null;
        if (elapsed > SESSION_RESUME_WINDOW_MS) {
          const actualEndTime = Date.now() - elapsed;
          sendSessionEnd(sessionIdRef.current, visitorIdRef.current, actualEndTime);
          try { sessionStorage.removeItem('syrabit:session_id'); } catch {}
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
              analytics_consent: 'granted',
            },
            { withCredentials: true, timeout: 5000 }
          ).catch(() => {});
        }
        startHeartbeat(sessionIdRef.current, visitorIdRef.current);
      }
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      stopHeartbeatAndSendEnd(sessionIdRef.current, visitorIdRef.current);
    };
  }, []);

  useEffect(() => {
    const path = location.pathname;
    if (path === lastPath.current) return;
    lastPath.current = path;
    if (!hasAnalyticsConsent()) return;

    const visitorId = getOrCreateVisitorId();
    const sessionId = getOrCreateSessionId();
    sessionIdRef.current = sessionId;
    visitorIdRef.current = visitorId;
    const referrer = document.referrer || null;
    const is404Hint = detectIs404(path);
    if (!claimPageView(sessionId, path)) return;

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
        analytics_consent: 'granted',
      },
      { withCredentials: true, timeout: 5000 }
    ).catch(() => {});

    Analytics.pageView(path, document.title);

  }, [location.pathname]);
}

export function PageTracker() {
  usePageTracking();
  return null;
}
