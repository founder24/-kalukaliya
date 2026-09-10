const CHUNK_RELOAD_KEY = 'syrabit:chunk-reload-at';
const RELOAD_COOLDOWN_MS = 30_000;

export function handlePreloadError(event, {
  location = window.location,
  storage = window.sessionStorage,
  now = Date.now(),
} = {}) {
  event.preventDefault();

  let lastReload = 0;
  try {
    lastReload = Number(storage.getItem(CHUNK_RELOAD_KEY) || 0);
  } catch {
    // Restricted storage must not prevent recovery.
  }
  if (now - lastReload < RELOAD_COOLDOWN_MS) return false;

  try {
    storage.setItem(CHUNK_RELOAD_KEY, String(now));
  } catch {
    // Reload still recovers the current document without storage.
  }
  location.reload();
  return true;
}

export function installChunkRecovery(target = window) {
  target.addEventListener('vite:preloadError', handlePreloadError);
}