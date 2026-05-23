/**
 * Track which sync-claim timestamp the learner has already seen so the
 * "Recently synced" badge on Notebook / Flashcards only shows during
 * the first session after sign-in (Task #612).
 *
 * The backend stamps `claimed_at` on notes & flashcards when an offline
 * (anon) row is adopted into the user's account. We compare that
 * timestamp to a per-surface localStorage high-water mark so each
 * page (Notebook, Flashcards) shows the badge independently the first
 * time it is visited after a claim. The mark is only advanced when
 * the page unmounts — that way badges stay visible the whole time the
 * page is open (covering reloads, filter changes, deck reshuffles)
 * and only disappear once the learner has navigated away and come
 * back, ending that surface's "first viewing".
 */
const KEY_PREFIX = 'syrabit:claim_seen_at';
const SURFACES = new Set(['notes', 'cards']);

function key(surface) {
  if (!SURFACES.has(surface)) {
    throw new Error(
      `claimSeen: surface must be one of ${[...SURFACES].join(', ')} (got ${surface})`,
    );
  }
  return `${KEY_PREFIX}:${surface}`;
}

export function getClaimSeenAt(surface) {
  try { return localStorage.getItem(key(surface)) || ''; } catch { return ''; }
}

export function markClaimSeen(surface, iso) {
  if (!iso) return;
  try {
    const k = key(surface);
    const cur = localStorage.getItem(k) || '';
    if (!cur || iso > cur) localStorage.setItem(k, iso);
  } catch {}
}

export function isRecentlyClaimed(claimedAt, seenAt) {
  if (!claimedAt) return false;
  if (!seenAt) return true;
  return claimedAt > seenAt;
}
