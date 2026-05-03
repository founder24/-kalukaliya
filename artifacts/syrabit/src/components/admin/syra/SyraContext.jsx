/**
 * Task #298 — SyraContext
 *
 * Lightweight React context that exposes the admin panel's "screen
 * awareness" state to the Syra orb without forcing every section to
 * thread props through. Sections call ``useSyraSelection()`` /
 * ``useSyraFilters()`` / ``useSyraVisibleError()`` to publish what the
 * operator can currently see; the orb subscribes via
 * ``useSyraContext()`` and forwards the snapshot with every chat turn
 * so the LLM can resolve pronouns ("ban him") and suggest contextual
 * actions.
 *
 * Operator preferences (wake word, briefing, voice rate, mute
 * categories, persona) are persisted **per-admin** to the backend via
 * ``GET/PUT /admin/syra/prefs`` (so two operators sharing a workstation
 * don't clobber each other's settings). The server response is also
 * mirrored into a namespaced ``localStorage`` entry keyed by admin
 * email so reloads / offline have a fast first paint and we can
 * survive a brief backend hiccup.
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
import { adminSyraGetPrefs, adminSyraSavePrefs } from '@/utils/api';

const PREFS_KEY_PREFIX = 'syra:prefs:v2:';

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

function prefsKeyFor(email) {
  const slug = String(email || 'anon').toLowerCase().trim() || 'anon';
  return `${PREFS_KEY_PREFIX}${slug}`;
}

function loadLocalPrefs(email) {
  if (typeof window === 'undefined') return { ...DEFAULT_PREFS };
  try {
    const raw = window.localStorage.getItem(prefsKeyFor(email));
    if (!raw) return { ...DEFAULT_PREFS };
    const parsed = JSON.parse(raw);
    return { ...DEFAULT_PREFS, ...(parsed || {}) };
  } catch {
    return { ...DEFAULT_PREFS };
  }
}

function persistLocalPrefs(email, next) {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(prefsKeyFor(email), JSON.stringify(next));
  } catch { /* quota / private mode */ }
}

const SyraContext = createContext(null);

export function SyraProvider({ activeSection, adminToken, adminEmail, children }) {
  const [selectedEntity, setSelectedEntity] = useState(null);
  const [filters, setFilters] = useState(null);
  const [visibleError, setVisibleError] = useState(null);
  // Per-admin namespaced first paint so reloads keep the operator's
  // own settings even before the backend roundtrip resolves.
  const [prefs, setPrefsState] = useState(() => loadLocalPrefs(adminEmail));

  const prefsRef = useRef(prefs);
  useEffect(() => { prefsRef.current = prefs; }, [prefs]);

  // Server load: replace local-first paint with the canonical per-admin
  // record once we have a verified session. Failures fall back silently
  // to the local cache (already in state) so the orb keeps working.
  useEffect(() => {
    if (!adminToken) return;
    let cancelled = false;
    adminSyraGetPrefs(adminToken)
      .then((res) => {
        if (cancelled) return;
        const remote = res?.data?.prefs || {};
        const merged = { ...DEFAULT_PREFS, ...remote };
        setPrefsState(merged);
        persistLocalPrefs(adminEmail, merged);
      })
      .catch(() => { /* keep local-cached prefs */ });
    return () => { cancelled = true; };
  }, [adminToken, adminEmail]);

  // Debounced server save — coalesces rapid toggles (e.g. dragging the
  // voice-rate slider) into a single PUT.
  const saveTimerRef = useRef(null);
  const queueServerSave = useCallback((next) => {
    if (!adminToken) return;
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => {
      adminSyraSavePrefs(adminToken, next).catch(() => {});
    }, 600);
  }, [adminToken]);
  useEffect(() => () => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
  }, []);

  const setPrefs = useCallback((patch) => {
    setPrefsState((prev) => {
      const next = { ...prev, ...(typeof patch === 'function' ? patch(prev) : patch) };
      persistLocalPrefs(adminEmail, next);
      queueServerSave(next);
      return next;
    });
  }, [adminEmail, queueServerSave]);

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
  }, [entity, setSelectedEntity]);
}

/**
 * Publish the active filter set (search query, tab, plan, etc.) so
 * "show me only the suspended ones" can resolve against what the
 * operator is already filtering by. Cleared on unmount.
 */
export function useSyraFilters(filters) {
  const { setFilters } = useSyraContext();
  useEffect(() => {
    setFilters(filters || null);
    return () => setFilters(null);
  }, [filters, setFilters]);
}

/**
 * Publish a section-level error banner so Syra can offer to retry /
 * explain it ("the conversations failed to load — try refreshing").
 */
export function useSyraVisibleError(message) {
  const { setVisibleError } = useSyraContext();
  useEffect(() => {
    setVisibleError(message || null);
    return () => setVisibleError(null);
  }, [message, setVisibleError]);
}
