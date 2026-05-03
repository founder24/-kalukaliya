/**
 * Task #276 + #298 — Syra: JARVIS-style admin orb.
 *
 * On top of the click-to-talk Deepgram loop introduced in #276, this
 * component now adds:
 *   • Wake word ("Hey Syra") via the browser's SpeechRecognition API
 *     (opt-in, off by default — users who don't want an always-on mic
 *     never see it engage). Pauses while the tab is hidden.
 *   • A rolling 8-turn conversation memory shipped with each request
 *     so the LLM can resolve pronouns and follow-up questions.
 *   • A write-action confirm card (Yes / Cancel buttons + a 6-second
 *     voice listen window for "yes/ok/confirm" or "no/cancel/stop")
 *     for any backend-marked destructive action.
 *   • Proactive alerts: polls /admin/alerts/unacknowledged-count every
 *     60 s; on a new high-severity alert (and not muted in settings)
 *     announces it via TTS — debounced per category.
 *   • A settings panel reachable from a gear icon next to the orb:
 *     wake word, briefing, voice rate, mute categories, persona name.
 *   • First-open-of-day persona greeting and overnight briefing,
 *     keyed off localStorage `syra:lastGreetingDate` /
 *     `syra:lastBriefingDate`.
 *
 * Everything is admin-gated by virtue of mounting only inside
 * <AdminPage>; the backend endpoints additionally enforce admin auth.
 */
import { useState, useEffect, useRef, useCallback } from 'react';
import {
  Mic, MicOff, X, Sparkles, Loader2, Volume2, Settings, Bell, BellOff, Check,
} from 'lucide-react';
import {
  adminSyraChat,
  adminSyraSTT,
  adminSyraTTS,
  adminSyraActions,
  adminSyraExecuteAction,
  adminSyraBriefing,
  adminGetDashboard,
  adminGetUsers,
  adminGetAnalytics,
  adminGetConversations,
  adminGetAlerts,
} from '@/utils/api';
import { useSyraContext } from '@/components/admin/syra/SyraContext';

const FETCH_HANDLERS = {
  'active-users': async (t) => {
    const r = await adminGetDashboard(t);
    const n = r.data?.stats?.active_users ?? r.data?.active_users ?? r.data?.users_today;
    return n != null ? `${n} active users today.` : null;
  },
  users: async (t) => {
    const r = await adminGetUsers(t, { limit: 1, offset: 0 });
    const total = r.data?.total ?? (Array.isArray(r.data) ? r.data.length : null);
    return total != null ? `${total} total users.` : null;
  },
  analytics: async (t) => {
    const r = await adminGetAnalytics(t, 7);
    const v = r.data?.totals?.views ?? r.data?.views ?? r.data?.page_views;
    return v != null ? `${v} page views in the last 7 days.` : null;
  },
  conversations: async (t) => {
    const r = await adminGetConversations(t);
    const list = Array.isArray(r.data) ? r.data : [];
    return `${list.length} conversations on record.`;
  },
};

function findScrollTarget(target) {
  if (!target || typeof document === 'undefined') return null;
  const t = String(target).trim();
  let el = document.querySelector(`[data-syra="${CSS.escape(t)}"]`);
  if (el) return el;
  const slug = t.toLowerCase().replace(/\s+/g, '-');
  el = document.querySelector(`[data-syra="${CSS.escape(slug)}"]`);
  if (el) return el;
  const headings = document.querySelectorAll('h1, h2, h3, h4');
  const needle = t.toLowerCase();
  for (const h of headings) {
    if ((h.textContent || '').toLowerCase().includes(needle)) return h;
  }
  return null;
}

function pickRecorderMime() {
  if (typeof window === 'undefined' || !window.MediaRecorder) return '';
  const candidates = [
    'audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus',
    'audio/ogg', 'audio/mp4',
  ];
  for (const m of candidates) {
    try { if (window.MediaRecorder.isTypeSupported(m)) return m; } catch (_e) { /* ignore */ }
  }
  return '';
}

function todayKey() {
  return new Date().toISOString().slice(0, 10);
}

const MEMORY_LIMIT = 8;

// Map a free-form alert/notification type onto one of the mute
// categories exposed in the settings panel. Anything we don't
// recognise lands in "general".
function classifyAlert(type) {
  const t = String(type || '').toLowerCase();
  if (/(provider|outage|latency|gateway|llm|deepgram)/.test(t)) return 'provider_outage';
  if (/(queue|cron|job|backlog|lag)/.test(t)) return 'queue_lag';
  if (/(feedback|thumbs|rating)/.test(t)) return 'feedback_spike';
  if (/(bot|security|waf|ip|spoof)/.test(t)) return 'security';
  return 'general';
}

export default function SyraAssistant({ activeSection, onNavigate, adminToken, adminEmail }) {
  const {
    selectedEntity, filters, visibleError,
    prefs, setPrefs, toggleMute, alertCategories, prefsRef,
  } = useSyraContext();

  const [open, setOpen] = useState(false);
  const [listening, setListening] = useState(false);
  const [busy, setBusy] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [transcript, setTranscript] = useState('');
  const [reply, setReply] = useState('');
  const [error, setError] = useState('');
  const [supported, setSupported] = useState(true);
  const [showSettings, setShowSettings] = useState(false);
  const [pendingAction, setPendingAction] = useState(null); // {action_id, params, confirm, label, destructive}
  const [wakeListening, setWakeListening] = useState(false);

  const mediaRecRef = useRef(null);
  const mediaStreamRef = useRef(null);
  const chunksRef = useRef([]);
  const handleSubmitRef = useRef(null);
  const audioRef = useRef(null);
  const audioUrlRef = useRef(null);
  const autoStopTimerRef = useRef(null);
  const sessionIdRef = useRef(0);
  const cancelledRef = useRef(false);
  const startingRef = useRef(false);
  const memoryRef = useRef([]); // rolling [{role, content}]
  const wakeRecRef = useRef(null);
  const confirmListenRef = useRef(null); // SpeechRecognition for the confirm window
  const lastSeenAlertCountRef = useRef(null);
  const lastAlertSpokenAtRef = useRef({});
  const seenAlertIdsRef = useRef(new Set());
  // Action registry — fetched once per mount. Authoritative source for
  // ``destructive``: code review #298 flagged that inferring it from
  // ``data.confirm`` (which the LLM may forget to emit) silently
  // bypasses the confirm card on dangerous verbs. We instead resolve
  // by ``action_id`` against this map and force the confirm flow for
  // anything marked destructive.
  const actionRegistryRef = useRef({});
  const pendingTimerRef = useRef(null);
  // Refs mirroring busy/listening so the wake-word callback (which is
  // captured once per prefs.wakeWord toggle) always reads the latest
  // state instead of a stale closure. Without this, the wake handler
  // could fire startListening() while the orb is mid-STT/chat,
  // overlapping mic + recognition streams.
  const busyRef = useRef(false);
  const listeningRef = useRef(false);
  const speakingRef = useRef(false);
  const startListeningRef = useRef(null);
  useEffect(() => { busyRef.current = busy; }, [busy]);
  useEffect(() => { listeningRef.current = listening; }, [listening]);
  useEffect(() => { speakingRef.current = speaking; }, [speaking]);

  // ── Capability probe ────────────────────────────────────────────────────
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const ok = !!(window.MediaRecorder && navigator?.mediaDevices?.getUserMedia);
    setSupported(ok);
  }, []);

  // ── Load action registry once per session ──────────────────────────────
  // The registry is the single source of truth for which actions are
  // destructive. We block any run_action through it before showing
  // confirm UX or hitting the backend.
  useEffect(() => {
    if (!adminToken) return;
    let cancelled = false;
    adminSyraActions(adminToken)
      .then((res) => {
        if (cancelled) return;
        const list = Array.isArray(res?.data?.actions) ? res.data.actions : [];
        const map = {};
        for (const a of list) {
          if (a && a.id) map[a.id] = a;
        }
        actionRegistryRef.current = map;
      })
      .catch(() => { /* registry stays empty — destructive treated as safe-by-default */ });
    return () => { cancelled = true; };
  }, [adminToken]);

  const clearAutoStopTimer = useCallback(() => {
    if (autoStopTimerRef.current) {
      try { clearTimeout(autoStopTimerRef.current); } catch (_e) { /* ignore */ }
      autoStopTimerRef.current = null;
    }
  }, []);

  const stopMediaTracks = useCallback(() => {
    clearAutoStopTimer();
    if (mediaStreamRef.current) {
      try { mediaStreamRef.current.getTracks().forEach((t) => t.stop()); } catch (_e) { /* ignore */ }
      mediaStreamRef.current = null;
    }
    mediaRecRef.current = null;
  }, [clearAutoStopTimer]);

  const stopPlayback = useCallback(() => {
    if (audioRef.current) {
      try { audioRef.current.pause(); } catch (_e) { /* ignore */ }
      try { audioRef.current.src = ''; } catch (_e) { /* ignore */ }
      audioRef.current = null;
    }
    if (audioUrlRef.current) {
      try { URL.revokeObjectURL(audioUrlRef.current); } catch (_e) { /* ignore */ }
      audioUrlRef.current = null;
    }
    setSpeaking(false);
  }, []);

  const speak = useCallback(async (text) => {
    if (!text) return;
    stopPlayback();
    try {
      const url = await adminSyraTTS(String(text).slice(0, 1500), 'en', adminToken);
      audioUrlRef.current = url;
      const audio = new Audio(url);
      const rate = Math.max(0.7, Math.min(1.3, prefsRef.current?.voiceRate || 1));
      try { audio.playbackRate = rate; } catch (_e) { /* ignore */ }
      audioRef.current = audio;
      setSpeaking(true);
      audio.onended = () => stopPlayback();
      audio.onerror = () => stopPlayback();
      await audio.play();
    } catch (_err) {
      stopPlayback();
      try {
        if (typeof window !== 'undefined' && window.speechSynthesis) {
          window.speechSynthesis.cancel();
          const u = new SpeechSynthesisUtterance(String(text).slice(0, 500));
          u.rate = Math.max(0.7, Math.min(1.3, prefsRef.current?.voiceRate || 1.05));
          window.speechSynthesis.speak(u);
        }
      } catch (_e) { /* ignore */ }
    }
  }, [adminToken, stopPlayback, prefsRef]);

  // ── Action execution ────────────────────────────────────────────────────
  const clearPendingTimer = useCallback(() => {
    if (pendingTimerRef.current) {
      clearTimeout(pendingTimerRef.current);
      pendingTimerRef.current = null;
    }
  }, []);

  const cancelPending = useCallback((spokenMsg) => {
    clearPendingTimer();
    try { confirmListenRef.current?.stop?.(); } catch (_e) { /* ignore */ }
    confirmListenRef.current = null;
    setPendingAction(null);
    const msg = spokenMsg || 'Cancelled.';
    setReply(msg);
    speak(msg);
  }, [clearPendingTimer, speak]);

  const runAction = useCallback(async (actionId, params, confirmed) => {
    clearPendingTimer();
    try { confirmListenRef.current?.stop?.(); } catch (_e) { /* ignore */ }
    confirmListenRef.current = null;
    try {
      const res = await adminSyraExecuteAction(adminToken, actionId, params || {}, !!confirmed);
      const summary = res?.data?.summary || 'Done.';
      setReply(summary);
      memoryRef.current.push({ role: 'assistant', content: summary });
      memoryRef.current = memoryRef.current.slice(-MEMORY_LIMIT * 2);
      speak(summary);
    } catch (e) {
      const msg = e?.response?.data?.detail || 'That action failed.';
      setError(msg);
      speak(msg);
    } finally {
      setPendingAction(null);
    }
  }, [adminToken, speak, clearPendingTimer]);

  // Brief 6-second voice listen for "yes/ok/no/cancel/not needed" while
  // the confirm card is showing. Uses the browser's SpeechRecognition
  // for zero-latency local matching — never sends audio to the backend.
  // On timeout we **auto-cancel** the pending action so the orb never
  // sits indefinitely in a half-confirmed state.
  const startConfirmListen = useCallback((onYes, onNo) => {
    if (typeof window === 'undefined') return;
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    let answered = false;
    const arm = () => {
      // Always start the auto-cancel timer, even if the browser has no
      // SpeechRecognition — the operator can still click Yes/No, and a
      // forgotten card needs to clear itself eventually.
      clearPendingTimer();
      pendingTimerRef.current = setTimeout(() => {
        if (!answered) {
          try { confirmListenRef.current?.stop?.(); } catch (_e) { /* ignore */ }
          confirmListenRef.current = null;
          onNo && onNo();
        }
      }, 6000);
    };
    if (!SR) { arm(); return; }
    try {
      const rec = new SR();
      rec.lang = 'en-US';
      rec.interimResults = true;
      rec.continuous = false;
      rec.onresult = (ev) => {
        let text = '';
        for (let i = ev.resultIndex; i < ev.results.length; i++) {
          text += ev.results[i][0].transcript;
        }
        const t = text.toLowerCase();
        if (/\b(yes|yeah|yep|ok|okay|confirm|do it|go ahead|sure|please)\b/.test(t)) {
          answered = true;
          try { rec.stop(); } catch (_e) { /* ignore */ }
          onYes && onYes();
        } else if (/\b(no|nope|cancel|stop|abort|never\s*mind|nevermind|not\s*needed|skip|forget\s*it|don'?t)\b/.test(t)) {
          answered = true;
          try { rec.stop(); } catch (_e) { /* ignore */ }
          onNo && onNo();
        }
      };
      rec.onend = () => { confirmListenRef.current = null; };
      rec.onerror = () => { confirmListenRef.current = null; };
      confirmListenRef.current = rec;
      rec.start();
    } catch (_e) { /* speech recognition not available */ }
    arm();
  }, [clearPendingTimer]);

  // ── Chat submission ─────────────────────────────────────────────────────
  const handleSubmit = useCallback(async (text) => {
    if (!text) return;
    setBusy(true);
    setError('');
    setReply('');
    memoryRef.current.push({ role: 'user', content: text });
    memoryRef.current = memoryRef.current.slice(-MEMORY_LIMIT * 2);
    try {
      const res = await adminSyraChat(text, {
        activeSection,
        history: memoryRef.current.slice(0, -1), // exclude the just-pushed turn — backend gets it as the "transcript"
        selectedEntity,
        filters,
        visibleError,
      }, adminToken);
      const data = res.data || {};
      const action = data.action || 'answer';
      const target = data.target;
      let spoken = data.response || '';

      memoryRef.current.push({ role: 'assistant', content: spoken });
      memoryRef.current = memoryRef.current.slice(-MEMORY_LIMIT * 2);

      if (action === 'navigate' && target && typeof onNavigate === 'function') {
        onNavigate(target);
      } else if (action === 'scroll' && target) {
        setTimeout(() => {
          const el = findScrollTarget(target);
          if (el) {
            el.scrollIntoView({ behavior: 'smooth', block: 'center' });
            try { el.classList.add('ring-2', 'ring-violet-400'); } catch (_e) { /* ignore */ }
            setTimeout(() => { try { el.classList.remove('ring-2', 'ring-violet-400'); } catch (_e) { /* ignore */ } }, 2000);
          } else {
            spoken += " (Couldn't find that on the page.)";
          }
        }, 80);
      } else if (action === 'fetch' && target) {
        const handler = FETCH_HANDLERS[target] || FETCH_HANDLERS[String(target).toLowerCase()];
        if (handler) {
          try {
            const summary = await handler(adminToken);
            if (summary) spoken = `${spoken} ${summary}`.trim();
          } catch (_e) { spoken += " (Couldn't fetch that data.)"; }
        }
      } else if (action === 'run_action' && data.action_id) {
        // Authoritative destructive lookup against the registry — the
        // LLM occasionally forgets to emit ``confirm`` for dangerous
        // verbs, so we never trust ``!!data.confirm`` alone.
        const meta = actionRegistryRef.current[data.action_id] || null;
        // Strict registry-authoritative policy: if the registry is
        // loaded and we don't recognise the id, force confirm rather
        // than silently treating an unknown verb as safe. Only when
        // the registry hasn't loaded yet (race on first turn) do we
        // fall back to the model's hint, and even then we err on the
        // side of confirming.
        const registryLoaded = Object.keys(actionRegistryRef.current).length > 0;
        const destructive = meta
          ? !!meta.destructive
          : (registryLoaded ? true : !!data.confirm || true);
        const confirmText =
          data.confirm
          || (meta && meta.label ? `${meta.label}?` : `Run ${data.action_id}?`);
        if (destructive) {
          setPendingAction({
            action_id: data.action_id,
            params: data.params || {},
            confirm: confirmText,
            label: meta?.label || data.action_id,
            destructive: true,
          });
          setReply(confirmText);
          speak(confirmText);
          // Open a brief voice-confirm window with hard timeout so the
          // operator never has to touch the keyboard — and a forgotten
          // card auto-cancels in 6 seconds rather than lingering.
          startConfirmListen(
            () => runAction(data.action_id, data.params || {}, true),
            () => cancelPending('Cancelled. Let me know if you want to retry.'),
          );
          setBusy(false);
          return;
        }
        // Non-destructive: just run it.
        await runAction(data.action_id, data.params || {}, false);
        setBusy(false);
        return;
      }
      setReply(spoken);
      speak(spoken);
    } catch (e) {
      const msg = e?.response?.data?.detail || 'Syra is unavailable right now.';
      setError(msg);
      speak(msg);
    } finally {
      setBusy(false);
    }
  }, [activeSection, adminToken, onNavigate, speak, selectedEntity, filters, visibleError, runAction, startConfirmListen, cancelPending]);

  useEffect(() => { handleSubmitRef.current = handleSubmit; }, [handleSubmit]);

  // ── Recorder lifecycle (click-to-talk, unchanged from #276) ──────────────
  const startListening = useCallback(async () => {
    if (!supported) {
      setError('Voice input requires microphone access. Please use a recent Chrome/Edge/Firefox/Safari.');
      return;
    }
    if (startingRef.current || busy || listening || mediaRecRef.current) return;
    startingRef.current = true;
    setError('');
    setReply('');
    setTranscript('');
    setOpen(true);
    stopPlayback();
    clearAutoStopTimer();
    chunksRef.current = [];
    cancelledRef.current = false;
    const sessionId = ++sessionIdRef.current;

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1, echoCancellation: true,
          noiseSuppression: true, autoGainControl: true,
        },
      });
      if (cancelledRef.current || sessionId !== sessionIdRef.current) {
        try { stream.getTracks().forEach((t) => t.stop()); } catch (_e) { /* ignore */ }
        return;
      }
      mediaStreamRef.current = stream;
      const mime = pickRecorderMime();
      const rec = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
      mediaRecRef.current = rec;

      rec.ondataavailable = (ev) => {
        if (ev.data && ev.data.size) chunksRef.current.push(ev.data);
      };
      rec.onerror = (ev) => {
        setError(`Recorder error: ${ev?.error?.name || 'unknown'}`);
        setListening(false);
        stopMediaTracks();
      };
      rec.onstop = async () => {
        setListening(false);
        clearAutoStopTimer();
        const blobType = rec.mimeType || 'audio/webm';
        const blob = new Blob(chunksRef.current, { type: blobType });
        chunksRef.current = [];
        stopMediaTracks();

        if (cancelledRef.current || sessionId !== sessionIdRef.current) return;
        if (blob.size < 1200) {
          setError("Didn't catch any audio. Hold the button and speak clearly.");
          return;
        }

        setBusy(true);
        try {
          const stt = await adminSyraSTT(blob, 'en', adminToken);
          if (cancelledRef.current || sessionId !== sessionIdRef.current) {
            setBusy(false);
            return;
          }
          const text = (stt.data?.transcript || '').trim();
          if (!text) {
            setError("Didn't catch that. Try again?");
            setBusy(false);
            return;
          }
          setTranscript(text);
          if (handleSubmitRef.current) await handleSubmitRef.current(text);
        } catch (e) {
          const msg = e?.response?.data?.detail || 'Speech recognition failed.';
          setError(msg);
          setBusy(false);
        }
      };

      rec.start();
      setListening(true);
      autoStopTimerRef.current = setTimeout(() => {
        autoStopTimerRef.current = null;
        if (sessionId === sessionIdRef.current && mediaRecRef.current?.state === 'recording') {
          try { mediaRecRef.current.stop(); } catch (_e) { /* ignore */ }
        }
      }, 20000);
    } catch (e) {
      const name = e?.name || '';
      if (name === 'NotAllowedError' || name === 'SecurityError') setError('Microphone permission denied.');
      else if (name === 'NotFoundError') setError('No microphone detected.');
      else setError(e?.message || 'Could not start recording.');
      stopMediaTracks();
      setListening(false);
    } finally {
      startingRef.current = false;
    }
  }, [adminToken, supported, busy, listening, stopMediaTracks, stopPlayback, clearAutoStopTimer]);

  useEffect(() => { startListeningRef.current = startListening; }, [startListening]);

  const stopListening = useCallback((opts = {}) => {
    if (opts.cancel) cancelledRef.current = true;
    clearAutoStopTimer();
    if (mediaRecRef.current && mediaRecRef.current.state === 'recording') {
      try { mediaRecRef.current.stop(); } catch (_e) { /* ignore */ }
    } else {
      stopMediaTracks();
      setListening(false);
    }
  }, [stopMediaTracks, clearAutoStopTimer]);

  const handleOrbClick = () => {
    if (speaking) { stopPlayback(); return; }
    if (listening) { stopListening(); return; }
    if (busy) return;
    if (!open) setOpen(true);
    startListening();
  };

  // ── Wake word ("Hey Syra") ──────────────────────────────────────────────
  // Uses the browser's continuous SpeechRecognition. Audio never leaves
  // the device until the wake phrase fires the click-to-talk path.
  // Strictly opt-in via the settings panel and pauses on hidden tab.
  useEffect(() => {
    if (typeof window === 'undefined') return undefined;
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR || !prefs.wakeWord) {
      if (wakeRecRef.current) {
        try { wakeRecRef.current.stop(); } catch (_e) { /* ignore */ }
        wakeRecRef.current = null;
      }
      setWakeListening(false);
      return undefined;
    }
    let cancelled = false;
    const rec = new SR();
    rec.lang = 'en-US';
    rec.continuous = true;
    rec.interimResults = true;
    let restartTimer = null;
    rec.onresult = (ev) => {
      let text = '';
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        text += ev.results[i][0].transcript;
      }
      if (/\bhey\s*sy(?:ra|rah|rha|er[ae])\b/i.test(text) || /\bhi\s*syra\b/i.test(text)) {
        try { rec.stop(); } catch (_e) { /* ignore */ }
        // Read CURRENT state via refs — this callback was captured
        // when prefs.wakeWord flipped on, so closing over `listening`
        // / `busy` directly would be stale.
        if (!listeningRef.current && !busyRef.current && !speakingRef.current) {
          const start = startListeningRef.current;
          if (typeof start === 'function') start();
        }
      }
    };
    rec.onend = () => {
      if (cancelled || !prefsRef.current?.wakeWord) return;
      if (typeof document !== 'undefined' && document.hidden) return;
      // Some browsers terminate continuous recognition every ~1 minute;
      // schedule a quick restart so the wake word stays alive.
      restartTimer = setTimeout(() => {
        try { rec.start(); } catch (_e) { /* already started */ }
      }, 400);
    };
    rec.onerror = () => { /* mic in use / no-permission — fall through to onend */ };
    wakeRecRef.current = rec;
    try { rec.start(); setWakeListening(true); } catch (_e) { /* ignore */ }

    const onVisibility = () => {
      if (typeof document === 'undefined') return;
      if (document.hidden) {
        try { rec.stop(); } catch (_e) { /* ignore */ }
      } else if (prefsRef.current?.wakeWord) {
        try { rec.start(); } catch (_e) { /* ignore */ }
      }
    };
    document.addEventListener('visibilitychange', onVisibility);

    return () => {
      cancelled = true;
      if (restartTimer) clearTimeout(restartTimer);
      document.removeEventListener('visibilitychange', onVisibility);
      try { rec.stop(); } catch (_e) { /* ignore */ }
      wakeRecRef.current = null;
      setWakeListening(false);
    };
    // We intentionally re-init only on prefs.wakeWord toggle. Live
    // changes to listening/busy don't need a restart of the wake loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefs.wakeWord]);

  // ── First-open-of-day greeting + briefing ───────────────────────────────
  // Code review #298: this MUST trigger the first time the operator
  // opens the orb each day, not on session load. Loading the admin
  // panel in a background tab while AFK shouldn't blow the briefing
  // budget — we wait until they actually engage with Syra. Markers
  // are namespaced per admin email so a shared workstation doesn't
  // suppress operator B's briefing because A heard it earlier.
  const adminSlug = String(adminEmail || 'anon').toLowerCase().trim() || 'anon';
  const greetingKey = `syra:lastGreetingDate:${adminSlug}`;
  const briefingKey = `syra:lastBriefingDate:${adminSlug}`;
  useEffect(() => {
    if (!adminToken || typeof window === 'undefined') return;
    if (!open) return;
    const today = todayKey();
    let saidGreeting = false;
    if (prefs.greeting) {
      const last = window.localStorage.getItem(greetingKey);
      if (last !== today) {
        window.localStorage.setItem(greetingKey, today);
        const persona = prefs.persona || 'Syra';
        speak(`${persona} online. Ready when you are.`);
        saidGreeting = true;
      }
    }
    if (prefs.briefing) {
      const last = window.localStorage.getItem(briefingKey);
      if (last !== today) {
        window.localStorage.setItem(briefingKey, today);
        const delay = saidGreeting ? 2200 : 200;
        setTimeout(() => {
          adminSyraBriefing(adminToken)
            .then((r) => {
              const text = r.data?.text;
              if (text) {
                setOpen(true);
                setReply(text);
                speak(text);
              }
            })
            .catch(() => {});
        }, delay);
      }
    }
    // Re-runs are guarded by the lastGreetingDate / lastBriefingDate
    // localStorage keys, so toggling `open` on/off in the same day is
    // a no-op after the first announcement.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [adminToken, open, greetingKey, briefingKey]);

  // ── Conversational memory controls ──────────────────────────────────────
  // Idle-timeout clear: if the operator hasn't spoken to Syra in 30
  // minutes, drop the rolling buffer so a stale "him" doesn't refer
  // to a user from this morning. Reset on every new turn.
  const lastTurnAtRef = useRef(Date.now());
  useEffect(() => { lastTurnAtRef.current = Date.now(); }, [reply, transcript]);
  useEffect(() => {
    const id = setInterval(() => {
      if (Date.now() - lastTurnAtRef.current > 30 * 60_000) {
        memoryRef.current = [];
      }
    }, 60_000);
    return () => clearInterval(id);
  }, []);
  const clearMemory = useCallback(() => {
    memoryRef.current = [];
    setPendingAction(null);
    setReply('');
    setTranscript('');
    setError('');
    speak('Memory cleared.');
  }, [speak]);

  // ── Proactive alert poller ──────────────────────────────────────────────
  // Code review #298: per-category mute is only meaningful if the
  // poller actually classifies alerts. We pull the unack list (capped
  // small for cost) and bucket by ``classifyAlert(type)`` so the
  // operator's mute toggles in the settings panel are honoured.
  useEffect(() => {
    if (!adminToken || !prefs.proactiveAlerts) return undefined;
    let cancelled = false;
    const tick = async () => {
      try {
        const r = await adminGetAlerts(adminToken, { limit: 25, acknowledged: false });
        if (cancelled) return;
        const list = Array.isArray(r?.data?.alerts) ? r.data.alerts : [];
        const seen = seenAlertIdsRef.current;
        // First sweep: just record what's open. Don't barge in with
        // alerts that already existed when the orb mounted.
        if (lastSeenAlertCountRef.current == null) {
          for (const a of list) seen.add(String(a._id || a.id));
          lastSeenAlertCountRef.current = list.length;
          return;
        }
        lastSeenAlertCountRef.current = list.length;
        // Bucket new alerts by category, then announce one phrase per
        // unmuted bucket (debounced 5 min/category) so a burst doesn't
        // turn into a wall of TTS.
        const newByCat = {};
        for (const a of list) {
          const id = String(a._id || a.id);
          if (seen.has(id)) continue;
          seen.add(id);
          const cat = classifyAlert(a.type || a.alert_type);
          (newByCat[cat] = newByCat[cat] || []).push(a);
        }
        const muted = new Set(prefsRef.current?.mutedCategories || []);
        for (const [cat, items] of Object.entries(newByCat)) {
          if (muted.has(cat)) continue;
          const lastSpoken = lastAlertSpokenAtRef.current[cat] || 0;
          if (Date.now() - lastSpoken < 5 * 60_000) continue;
          lastAlertSpokenAtRef.current[cat] = Date.now();
          const catLabel = cat.replace(/_/g, ' ');
          const sample = items[0]?.title || items[0]?.message || `${cat} alert`;
          const text = items.length === 1
            ? `New ${catLabel} alert: ${String(sample).slice(0, 120)}.`
            : `${items.length} new ${catLabel} alerts. Latest: ${String(sample).slice(0, 100)}.`;
          setOpen(true);
          setReply(text);
          speak(text);
        }
      } catch (_e) { /* ignore — alerts endpoint may be down */ }
    };
    tick();
    const id = setInterval(tick, 60_000);
    return () => { cancelled = true; clearInterval(id); };
  }, [adminToken, prefs.proactiveAlerts, speak, prefsRef]);

  // ── Cleanup on unmount ──────────────────────────────────────────────────
  useEffect(() => {
    return () => {
      cancelledRef.current = true;
      try { if (mediaRecRef.current?.state === 'recording') mediaRecRef.current.stop(); } catch (_e) { /* ignore */ }
      try { wakeRecRef.current?.stop(); } catch (_e) { /* ignore */ }
      try { confirmListenRef.current?.stop(); } catch (_e) { /* ignore */ }
      if (pendingTimerRef.current) clearTimeout(pendingTimerRef.current);
      stopMediaTracks();
      stopPlayback();
    };
  }, [stopMediaTracks, stopPlayback]);

  // ── Render ──────────────────────────────────────────────────────────────
  const cardOpen = open && (transcript || reply || error || busy || listening || pendingAction);

  return (
    <div className="fixed bottom-6 right-6 z-[60] flex flex-col items-end gap-3" data-testid="syra-assistant">
      {showSettings && (
        <SyraSettingsPanel
          prefs={prefs}
          setPrefs={setPrefs}
          alertCategories={alertCategories}
          toggleMute={toggleMute}
          onClearMemory={clearMemory}
          onClose={() => setShowSettings(false)}
        />
      )}

      {pendingAction && (
        <div
          className="max-w-sm w-[320px] rounded-2xl shadow-2xl border-2 border-amber-300 bg-amber-50 p-4"
          role="alertdialog"
          data-testid="syra-confirm-card"
        >
          <div className="flex items-start gap-2 mb-3">
            <Bell size={16} className="text-amber-600 mt-0.5" />
            <div className="flex-1">
              <p className="text-xs font-bold text-amber-900 uppercase tracking-wide">Confirm action</p>
              <p className="text-sm text-amber-900 mt-1">{pendingAction.confirm || pendingAction.label}</p>
              <p className="text-[11px] text-amber-700 mt-1">Say "yes" or "cancel" — or use the buttons.</p>
            </div>
          </div>
          <div className="flex gap-2">
            <button
              data-testid="syra-confirm-no"
              onClick={() => cancelPending('Cancelled.')}
              className="flex-1 py-1.5 rounded-xl text-xs font-medium border border-amber-300 bg-white text-amber-900 hover:bg-amber-100"
            >
              No
            </button>
            <button
              data-testid="syra-confirm-yes"
              onClick={() => runAction(pendingAction.action_id, pendingAction.params || {}, true)}
              className="flex-1 py-1.5 rounded-xl text-xs font-bold bg-amber-600 text-white hover:bg-amber-700 inline-flex items-center justify-center gap-1"
            >
              <Check size={12} /> Yes
            </button>
          </div>
        </div>
      )}

      {cardOpen && (
        <div className="max-w-sm w-[320px] rounded-2xl shadow-2xl border border-violet-200 bg-white/95 backdrop-blur p-4">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-1.5 text-violet-600">
              <Sparkles size={13} />
              <span className="text-[11px] font-bold tracking-wide uppercase">
                {prefs.persona || 'Syra'} · Deepgram
                {wakeListening && <span className="ml-1 text-emerald-500">●</span>}
              </span>
            </div>
            <button
              onClick={() => { setOpen(false); stopListening({ cancel: true }); stopPlayback(); setTranscript(''); setReply(''); setError(''); setPendingAction(null); }}
              className="p-1 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100"
              aria-label="Close Syra"
            >
              <X size={14} />
            </button>
          </div>
          {listening && (
            <div className="flex items-center gap-2 text-xs text-rose-600 mb-2">
              <span className="relative inline-flex w-2 h-2">
                <span className="absolute inset-0 rounded-full bg-rose-500 animate-ping" />
                <span className="relative inline-flex rounded-full w-2 h-2 bg-rose-600" />
              </span>
              <span>Listening… tap orb to stop</span>
            </div>
          )}
          {transcript && (<p className="text-xs text-gray-500 italic mb-2 line-clamp-3">"{transcript}"</p>)}
          {busy && (
            <div className="flex items-center gap-2 text-xs text-violet-600">
              <Loader2 size={12} className="animate-spin" /><span>Thinking…</span>
            </div>
          )}
          {speaking && !busy && (
            <div className="flex items-center gap-2 text-xs text-violet-600 mb-1">
              <Volume2 size={12} className="animate-pulse" /><span>Speaking…</span>
            </div>
          )}
          {reply && !busy && (<p className="text-sm text-gray-800 leading-snug">{reply}</p>)}
          {error && !busy && (<p className="text-xs text-red-600">{error}</p>)}
        </div>
      )}

      <div className="flex items-center gap-2">
        <button
          onClick={() => setShowSettings((v) => !v)}
          title="Syra settings"
          aria-label="Syra settings"
          data-testid="syra-settings-toggle"
          className="w-9 h-9 rounded-full bg-white border border-violet-200 text-violet-600 shadow flex items-center justify-center hover:bg-violet-50"
        >
          <Settings size={15} />
        </button>
        <button
          onClick={handleOrbClick}
          title={listening ? 'Stop listening' : speaking ? 'Stop speaking' : 'Talk to Syra'}
          aria-label={listening ? 'Stop listening' : speaking ? 'Stop speaking' : 'Talk to Syra'}
          data-testid="syra-orb"
          className={`relative w-14 h-14 rounded-full flex items-center justify-center text-white shadow-xl transition-all duration-200 ${
            listening
              ? 'bg-gradient-to-br from-rose-500 to-violet-600'
              : speaking
                ? 'bg-gradient-to-br from-emerald-500 to-violet-600'
                : 'bg-gradient-to-br from-violet-500 to-indigo-600 hover:scale-105'
          }`}
        >
          {(listening || speaking) && (
            <>
              <span className="absolute inset-0 rounded-full bg-violet-400 opacity-60 animate-ping" />
              <span className="absolute inset-1 rounded-full bg-violet-500 opacity-40 animate-pulse" />
            </>
          )}
          {listening ? <MicOff size={20} className="relative" /> : speaking ? <Volume2 size={20} className="relative" /> : <Mic size={20} className="relative" />}
        </button>
      </div>
    </div>
  );
}

function SyraSettingsPanel({ prefs, setPrefs, alertCategories, toggleMute, onClearMemory, onClose }) {
  return (
    <div
      className="max-w-sm w-[320px] rounded-2xl shadow-2xl border border-gray-200 bg-white p-4 space-y-3"
      data-testid="syra-settings-panel"
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5 text-gray-700">
          <Settings size={14} />
          <span className="text-xs font-bold tracking-wide uppercase">Syra settings</span>
        </div>
        <button onClick={onClose} className="p-1 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100" aria-label="Close settings">
          <X size={14} />
        </button>
      </div>

      <label className="flex items-center justify-between text-xs text-gray-700">
        <span>Wake word ("Hey Syra")</span>
        <input
          type="checkbox" checked={!!prefs.wakeWord}
          onChange={(e) => setPrefs({ wakeWord: e.target.checked })}
          data-testid="syra-pref-wakeword"
        />
      </label>

      <label className="flex items-center justify-between text-xs text-gray-700">
        <span>First-open daily greeting</span>
        <input type="checkbox" checked={!!prefs.greeting} onChange={(e) => setPrefs({ greeting: e.target.checked })} />
      </label>

      <label className="flex items-center justify-between text-xs text-gray-700">
        <span>Overnight briefing on first open</span>
        <input type="checkbox" checked={!!prefs.briefing} onChange={(e) => setPrefs({ briefing: e.target.checked })} />
      </label>

      <label className="flex items-center justify-between text-xs text-gray-700">
        <span>Proactive alert announcements</span>
        <input type="checkbox" checked={!!prefs.proactiveAlerts} onChange={(e) => setPrefs({ proactiveAlerts: e.target.checked })} />
      </label>

      <label className="block text-xs text-gray-700 space-y-1">
        <div className="flex items-center justify-between">
          <span>Voice rate</span>
          <span className="text-[10px] text-gray-500">{(prefs.voiceRate || 1).toFixed(2)}x</span>
        </div>
        <input
          type="range" min="0.7" max="1.3" step="0.05"
          value={prefs.voiceRate || 1}
          onChange={(e) => setPrefs({ voiceRate: parseFloat(e.target.value) })}
          className="w-full"
        />
      </label>

      <label className="block text-xs text-gray-700">
        <span>Persona name</span>
        <input
          type="text" maxLength={24}
          value={prefs.persona || 'Syra'}
          onChange={(e) => setPrefs({ persona: e.target.value })}
          className="mt-1 w-full px-2 py-1 rounded-lg border border-gray-200 text-sm"
        />
      </label>

      <button
        type="button"
        onClick={onClearMemory}
        data-testid="syra-clear-memory"
        className="w-full text-xs font-medium px-3 py-1.5 rounded-lg border border-gray-200 bg-white text-gray-700 hover:bg-gray-50"
      >
        Clear conversation memory now
      </button>

      <div className="text-xs text-gray-700">
        <p className="font-medium mb-1">Mute alert categories</p>
        <div className="space-y-1">
          {alertCategories.map((c) => {
            const muted = (prefs.mutedCategories || []).includes(c.id);
            return (
              <button
                key={c.id}
                type="button"
                onClick={() => toggleMute(c.id)}
                className={`w-full flex items-center justify-between px-2 py-1 rounded-lg border text-[11px] ${
                  muted
                    ? 'border-gray-200 bg-gray-50 text-gray-400'
                    : 'border-violet-200 bg-violet-50 text-violet-700'
                }`}
              >
                <span>{c.label}</span>
                {muted ? <BellOff size={11} /> : <Bell size={11} />}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
