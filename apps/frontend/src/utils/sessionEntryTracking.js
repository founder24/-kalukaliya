const SESSION_ENTRY_KEY = 'syrabit:session_entry_tracked';

export function claimSessionEntry() {
  try {
    if (sessionStorage.getItem(SESSION_ENTRY_KEY) === '1') return false;
    sessionStorage.setItem(SESSION_ENTRY_KEY, '1');
    return true;
  } catch {
    return false;
  }
}

export function resetSessionEntryClaim() {
  try {
    sessionStorage.removeItem(SESSION_ENTRY_KEY);
  } catch {
    // Storage may be unavailable; in that case entry claims stay unverified.
  }
}
