/**
 * Task #298 — SyraContext
 *
 * Lightweight React context that exposes the admin panel's "screen
 * awareness" state to the Syra orb without forcing every section to
 * thread props through. Sections call ``useSyraPublisher()`` to
 * publish their selected entity / filters / visible error; the orb
 * subscribes via ``useSyraContext()`` and forwards the snapshot with
 * every chat turn so the LLM can resolve pronouns ("ban him") and
 * suggest contextual actions.
 *
 * It also persists the operator's orb preferences (wake word on/off,
 * briefing on/off, voice rate, muted alert categories, persona name)
 * in localStorage under ``syra:prefs`` so they survive reloads.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';

const PREFS_KEY = 'syra:prefs:v1';

export const DEFAULT_PREFS = {
  wakeWord: false,         // opt-in — always-on mic is intrusive by default
  briefing: true,          // first-open-of-day spoken briefing
  voiceRate: 1.0,          // 0.7 .. 1.3
  persona: 'Syra',
  mutedCategories: [],     // e.g. ['provider_outage', 'queue_lag']
  proactiveAlerts: true,   // poll alert/health endpoints and announce
  greeting: true,          // first-open-of-day persona greeting
};

const ALERT_CATEGORIES = [
  { id: 'provider_outage', label: 'Provider outage / latency' },
  { id: 'queue_lag',       label: 'Queue lag / failed jobs' },
  { id: 'feedback_spike',  label: 'Negative feedback spike' },
  { id: 'security',        label: 'Bot security' },
  { id: 'general',         label: 'General alerts' },
];

function loadPrefs() {
  if (typeof window === 'undefined') return { ...DEFAULT_PREFS };
  try {
    const raw = window.localStorage.getItem(PREFS_KEY);
    if (!raw) return { ...DEFAULT_PREFS };
    const parsed = JSON.parse(raw);
    return { ...DEFAULT_PREFS, ...(parsed || {}) };
  } catch {
    return { ...DEFAULT_PREFS };
  }
}

function persistPrefs(next) {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(PREFS_KEY, JSON.stringify(next));
  } catch { /* quota / private mode */ }
}

const SyraContext = createContext(null);

export function SyraProvider({ activeSection, children }) {
  const [selectedEntity, setSelectedEntity] = useState(null);
  const [filters, setFilters] = useState(null);
  const [visibleError, setVisibleError] = useState(null);
  const [prefs, setPrefsState] = useState(loadPrefs);

  const prefsRef = useRef(prefs);
  useEffect(() => { prefsRef.current = prefs; }, [prefs]);

  const setPrefs = useCallback((patch) => {
    setPrefsState((prev) => {
      const next = { ...prev, ...(typeof patch === 'function' ? patch(prev) : patch) };
      persistPrefs(next);
      return next;
    });
  }, []);

  const toggleMute = useCallback((category) => {
    setPrefs((p) => {
      const set = new Set(p.mutedCategories || []);
      if (set.has(category)) set.delete(category); else set.add(category);
      return { mutedCategories: Array.from(set) };
    });
  }, [setPrefs]);

  const value = useMemo(() => ({
    activeSection: activeSection || 'dashboard',
    selectedEntity,
    setSelectedEntity,
    filters,
    setFilters,
    visibleError,
    setVisibleError,
    prefs,
    setPrefs,
    toggleMute,
    alertCategories: ALERT_CATEGORIES,
    prefsRef,
  }), [activeSection, selectedEntity, filters, visibleError, prefs, setPrefs, toggleMute]);

  return <SyraContext.Provider value={value}>{children}</SyraContext.Provider>;
}

export function useSyraContext() {
  const ctx = useContext(SyraContext);
  // Tolerant fallback so individual admin sections can be unit-tested
  // (or rendered outside the provider) without exploding.
  if (!ctx) {
    return {
      activeSection: 'dashboard',
      selectedEntity: null,
      setSelectedEntity: () => {},
      filters: null,
      setFilters: () => {},
      visibleError: null,
      setVisibleError: () => {},
      prefs: { ...DEFAULT_PREFS },
      setPrefs: () => {},
      toggleMute: () => {},
      alertCategories: ALERT_CATEGORIES,
      prefsRef: { current: { ...DEFAULT_PREFS } },
    };
  }
  return ctx;
}

/**
 * Helper hook for admin sections to publish their selected entity in
 * one line. Resets the selection when the section unmounts so a stale
 * pick from "Users" doesn't leak into "Conversations".
 */
export function useSyraSelection(entity) {
  const { setSelectedEntity } = useSyraContext();
  useEffect(() => {
    setSelectedEntity(entity || null);
    return () => setSelectedEntity(null);
    // entity reference change is enough — sections build a stable
    // object so we don't need a deep diff.
  }, [entity, setSelectedEntity]);
}
