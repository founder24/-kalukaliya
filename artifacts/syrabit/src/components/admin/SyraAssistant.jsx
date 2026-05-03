import { useState, useEffect, useRef, useCallback } from 'react';
import { Mic, MicOff, X, Sparkles, Loader2, Volume2 } from 'lucide-react';
import {
  adminSyraChat,
  adminSyraSTT,
  adminSyraTTS,
  adminGetDashboard,
  adminGetUsers,
  adminGetAnalytics,
  adminGetConversations,
} from '@/utils/api';

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
    'audio/webm;codecs=opus',
    'audio/webm',
    'audio/ogg;codecs=opus',
    'audio/ogg',
    'audio/mp4',
  ];
  for (const m of candidates) {
    try { if (window.MediaRecorder.isTypeSupported(m)) return m; } catch (_e) { /* ignore */ }
  }
  return '';
}

export default function SyraAssistant({ activeSection, onNavigate, adminToken }) {
  const [open, setOpen] = useState(false);
  const [listening, setListening] = useState(false);
  const [busy, setBusy] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [transcript, setTranscript] = useState('');
  const [reply, setReply] = useState('');
  const [error, setError] = useState('');
  const [supported, setSupported] = useState(true);

  const mediaRecRef = useRef(null);
  const mediaStreamRef = useRef(null);
  const chunksRef = useRef([]);
  const handleSubmitRef = useRef(null);
  const audioRef = useRef(null);
  const audioUrlRef = useRef(null);
  // Track the auto-stop timer so a stale timeout from a previous session
  // can never stop a fresh recorder. Bumped sessionIdRef + cancelledRef
  // let `onstop` short-circuit when the user has cancelled/closed.
  const autoStopTimerRef = useRef(null);
  const sessionIdRef = useRef(0);
  const cancelledRef = useRef(false);
  // Synchronous lock that blocks re-entry between user click and the
  // resolution of `getUserMedia` (the permission prompt window) — without
  // it, rapid double-clicks can each pass the `listening`/`mediaRecRef`
  // checks because state hasn't flipped yet.
  const startingRef = useRef(false);

  // ── Capability probe ──────────────────────────────────────────────────────
  // Deepgram does the actual transcription; we only need MediaRecorder +
  // getUserMedia in the browser to capture a few seconds of audio.
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const ok = !!(window.MediaRecorder && navigator?.mediaDevices?.getUserMedia);
    setSupported(ok);
  }, []);

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

  // Speak via Deepgram Aura-2 over the admin/syra/tts endpoint. Falls back
  // to window.speechSynthesis only when the network leg fails so the orb
  // is never silent on a transient backend hiccup.
  const speak = useCallback(async (text) => {
    if (!text) return;
    stopPlayback();
    try {
      const url = await adminSyraTTS(String(text).slice(0, 1500), 'en', adminToken);
      audioUrlRef.current = url;
      const audio = new Audio(url);
      audioRef.current = audio;
      setSpeaking(true);
      audio.onended = () => stopPlayback();
      audio.onerror = () => stopPlayback();
      await audio.play();
    } catch (_err) {
      // Network/Deepgram fallback — use the browser's built-in voice so
      // the operator still hears something rather than silent failure.
      stopPlayback();
      try {
        if (typeof window !== 'undefined' && window.speechSynthesis) {
          window.speechSynthesis.cancel();
          const u = new SpeechSynthesisUtterance(String(text).slice(0, 500));
          u.rate = 1.05;
          window.speechSynthesis.speak(u);
        }
      } catch (_e) { /* ignore */ }
    }
  }, [adminToken, stopPlayback]);

  const handleSubmit = useCallback(async (text) => {
    if (!text) return;
    setBusy(true);
    setError('');
    setReply('');
    try {
      const res = await adminSyraChat(text, activeSection, adminToken);
      const data = res.data || {};
      const action = data.action || 'answer';
      const target = data.target;
      let spoken = data.response || '';

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
          } catch (_e) {
            spoken += " (Couldn't fetch that data.)";
          }
        }
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
  }, [activeSection, adminToken, onNavigate, speak]);

  useEffect(() => { handleSubmitRef.current = handleSubmit; }, [handleSubmit]);

  // ── Recorder lifecycle ────────────────────────────────────────────────────
  // We capture into MediaRecorder then upload the resulting blob to
  // /api/admin/syra/stt where Deepgram Nova-3 transcribes it. Keeping the
  // audio short (<= 30s, capped by stopListening or silence) keeps the
  // latency budget under ~1.5s round-trip on a warm gateway.
  const startListening = useCallback(async () => {
    if (!supported) {
      setError('Voice input requires microphone access. Please use a recent Chrome/Edge/Firefox/Safari.');
      return;
    }
    // Guard against overlapping sessions — block if mid-flight (sync lock
    // for the pre-permission window) or already capturing; the orb should
    // be a strict toggle, not a re-entrant call.
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
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      // The user may have closed/cancelled the orb while the permission
      // prompt was open — discard the stream and bail before we touch any
      // recorder state.
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

        // Skip when the session was cancelled (close/abort) or a newer
        // recording started in the meantime — never spend STT/TTS credits
        // on audio the operator already abandoned.
        if (cancelledRef.current || sessionId !== sessionIdRef.current) {
          return;
        }

        // Empty/very short capture = nothing to transcribe.
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
          // Hand off to the chat pipeline (which sets/clears `busy` itself).
          if (handleSubmitRef.current) {
            await handleSubmitRef.current(text);
          }
        } catch (e) {
          const msg = e?.response?.data?.detail || 'Speech recognition failed.';
          setError(msg);
          setBusy(false);
        }
      };

      // Auto-stop after 20s as a hard guard against runaway captures. The
      // timer captures `sessionId` so a stale firing from an older session
      // can never stop a fresh recorder.
      rec.start();
      setListening(true);
      autoStopTimerRef.current = setTimeout(() => {
        autoStopTimerRef.current = null;
        if (
          sessionId === sessionIdRef.current &&
          mediaRecRef.current &&
          mediaRecRef.current.state === 'recording'
        ) {
          try { mediaRecRef.current.stop(); } catch (_e) { /* ignore */ }
        }
      }, 20000);
    } catch (e) {
      const name = e?.name || '';
      if (name === 'NotAllowedError' || name === 'SecurityError') {
        setError('Microphone permission denied.');
      } else if (name === 'NotFoundError') {
        setError('No microphone detected.');
      } else {
        setError(e?.message || 'Could not start recording.');
      }
      stopMediaTracks();
      setListening(false);
    } finally {
      // Always release the sync start lock — including the bail-on-cancel
      // path above, which returns from inside the try block.
      startingRef.current = false;
    }
  }, [adminToken, supported, busy, listening, stopMediaTracks, stopPlayback, clearAutoStopTimer]);

  // `cancel=true` is set by the close button so `onstop` discards the
  // capture instead of paying for STT/TTS the operator already aborted.
  // Plain stopListening (e.g. orb tap) keeps cancel=false and submits.
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
    if (!open) { setOpen(true); }
    startListening();
  };

  // ── Cleanup on unmount ───────────────────────────────────────────────────
  useEffect(() => {
    return () => {
      cancelledRef.current = true;
      try {
        if (mediaRecRef.current && mediaRecRef.current.state === 'recording') {
          mediaRecRef.current.stop();
        }
      } catch (_e) { /* ignore */ }
      stopMediaTracks();
      stopPlayback();
    };
  }, [stopMediaTracks, stopPlayback]);

  return (
    <div className="fixed bottom-6 right-6 z-[60] flex flex-col items-end gap-3" data-testid="syra-assistant">
      {open && (transcript || reply || error || busy || listening) && (
        <div className="max-w-sm w-[320px] rounded-2xl shadow-2xl border border-violet-200 bg-white/95 backdrop-blur p-4 animate-in fade-in slide-in-from-bottom-2">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-1.5 text-violet-600">
              <Sparkles size={13} />
              <span className="text-[11px] font-bold tracking-wide uppercase">Syra · Deepgram</span>
            </div>
            <button
              onClick={() => { setOpen(false); stopListening({ cancel: true }); stopPlayback(); setTranscript(''); setReply(''); setError(''); }}
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
          {transcript && (
            <p className="text-xs text-gray-500 italic mb-2 line-clamp-3">"{transcript}"</p>
          )}
          {busy && (
            <div className="flex items-center gap-2 text-xs text-violet-600">
              <Loader2 size={12} className="animate-spin" />
              <span>Thinking…</span>
            </div>
          )}
          {speaking && !busy && (
            <div className="flex items-center gap-2 text-xs text-violet-600 mb-1">
              <Volume2 size={12} className="animate-pulse" />
              <span>Speaking…</span>
            </div>
          )}
          {reply && !busy && (
            <p className="text-sm text-gray-800 leading-snug">{reply}</p>
          )}
          {error && !busy && (
            <p className="text-xs text-red-600">{error}</p>
          )}
        </div>
      )}
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
  );
}
