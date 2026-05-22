const VISITOR_ID_KEY = 'syrabit:visitor_id';
const VISIT_COUNT_KEY = 'syrabit:visit_count';
const SESSION_COUNTED_KEY = 'syrabit:session_counted';

function getOrCreateVisitorId() {
  try {
    let vid = localStorage.getItem(VISITOR_ID_KEY);
    if (!vid) {
      vid = 'v_' + Math.random().toString(36).slice(2, 11) + Date.now().toString(36);
      localStorage.setItem(VISITOR_ID_KEY, vid);
    }
    return vid;
  } catch {
    return null;
  }
}

export function incrementVisitIfNewSession() {
  try {
    getOrCreateVisitorId();
    if (sessionStorage.getItem(SESSION_COUNTED_KEY)) return false;
    const current = parseInt(localStorage.getItem(VISIT_COUNT_KEY) || '0', 10);
    localStorage.setItem(VISIT_COUNT_KEY, String(current + 1));
    sessionStorage.setItem(SESSION_COUNTED_KEY, '1');
    return true;
  } catch {
    return false;
  }
}

function getVisitCount() {
  try {
    return parseInt(localStorage.getItem(VISIT_COUNT_KEY) || '0', 10);
  } catch {
    return 0;
  }
}
