/**
 * Turnstile has been removed (Task #278).
 * This stub is kept so any remaining import sites compile without changes.
 * All functions are no-ops and `enabled` is always false.
 */
export function useTurnstile({ skip = false } = {}) {
  return {
    getToken: async () => '',
    ready: true,
    enabled: false,
    reset: () => {},
  };
}
