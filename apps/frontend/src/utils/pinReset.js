/**
 * Local "pin reset needed" flag — Task #611.
 *
 * Set by the AuthContext claim handler when the backend reports that
 * the offline guardian PIN was dropped during the anon → user merge,
 * read by `PinResetBanner` on the Guardian / Notebook / Flashcards
 * pages, and cleared either on dismiss or when a new PIN is set.
 *
 * Lives in its own module (instead of inside the banner component) to
 * avoid a circular dependency between `AuthContext.jsx` and
 * `PinResetBanner.jsx`.
 */
const FLAG_KEY = 'syrabit:pin_reset_needed';

export function pinResetMarkNeeded() {
  try { localStorage.setItem(FLAG_KEY, '1'); } catch {}
}

export function pinResetClear() {
  try { localStorage.removeItem(FLAG_KEY); } catch {}
}

export function pinResetIsNeeded() {
  try { return localStorage.getItem(FLAG_KEY) === '1'; } catch { return false; }
}
