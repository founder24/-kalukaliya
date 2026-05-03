import { useState, useEffect, useRef, useCallback } from 'react';
import { Mic, MicOff, X, Sparkles, Loader2 } from 'lucide-react';
import {
  adminSyraChat,
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

function speak(text) {
  if (!text || typeof window === 'undefined' || !window.speechSynthesis) return;
  try {
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(String(text).slice(0, 500));
    u.rate = 1.05;
    u.pitch = 1.0;
    u.volume = 1.0;
    window.speechSynthesis.speak(u);
  } catch (_e) { /* ignore */ }
}

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

export default function SyraAssistant({ activeSection, onNavigate, adminToken }) {
  const [open, setOpen] = useState(false);
  const [listening, setListening] = useState(false);
  const [busy, setBusy] = useState(false);
  const [transcript, setTranscript] = useState('');
  const [reply, setReply] = useState('');
  const [error, setError] = useState('');
  const [supported, setSupported] = useState(true);
  const recogRef = useRef(null);
  const finalTranscriptRef = useRef('');
  const handleSubmitRef = useRef(null);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) { setSupported(false); return; }
    const r = new SR();
    r.lang = 'en-US';
    r.interimResults = true;
    r.continuous = false;
    r.maxAlternatives = 1;
    r.onresult = (ev) => {
      let interim = '';
      let final = '';
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const res = ev.results[i];
        if (res.isFinal) final += res[0].transcript;
        else interim += res[0].transcript;
      }
      if (final) finalTranscriptRef.current = (finalTranscriptRef.current + ' ' + final).trim();
      setTranscript((finalTranscriptRef.current + ' ' + interim).trim());
    };
    r.onerror = (ev) => {
      setError(ev.error === 'not-allowed' ? 'Microphone permission denied.' : `Mic error: ${ev.error}`);
      setListening(false);
    };
    r.onend = () => {
      setListening(false);
      const text = finalTranscriptRef.current.trim();
      if (text && handleSubmitRef.current) handleSubmitRef.current(text);
    };
    recogRef.current = r;
    return () => { try { r.abort(); } catch (_e) { /* ignore */ } };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

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
        // Defer so re-renders settle if a navigation also happened.
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
  }, [activeSection, adminToken, onNavigate]);

  // Keep latest handleSubmit accessible to the SpeechRecognition.onend
  // callback (which is bound once on mount). Without this ref the
  // recognizer would call a stale closure and send the activeSection
  // captured at mount time.
  useEffect(() => { handleSubmitRef.current = handleSubmit; }, [handleSubmit]);

  const startListening = () => {
    if (!supported) {
      setError('Voice input is not supported in this browser. Try Chrome.');
      return;
    }
    setError('');
    setReply('');
    setTranscript('');
    finalTranscriptRef.current = '';
    setOpen(true);
    try {
      recogRef.current?.start();
      setListening(true);
    } catch (_e) {
      // already running — stop instead.
      try { recogRef.current?.stop(); } catch (__e) { /* ignore */ }
    }
  };

  const stopListening = () => {
    try { recogRef.current?.stop(); } catch (_e) { /* ignore */ }
    setListening(false);
  };

  const handleOrbClick = () => {
    if (listening) { stopListening(); return; }
    if (!open) { setOpen(true); startListening(); return; }
    startListening();
  };

  return (
    <div className="fixed bottom-6 right-6 z-[60] flex flex-col items-end gap-3" data-testid="syra-assistant">
      {open && (transcript || reply || error || busy) && (
        <div className="max-w-sm w-[320px] rounded-2xl shadow-2xl border border-violet-200 bg-white/95 backdrop-blur p-4 animate-in fade-in slide-in-from-bottom-2">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-1.5 text-violet-600">
              <Sparkles size={13} />
              <span className="text-[11px] font-bold tracking-wide uppercase">Syra</span>
            </div>
            <button
              onClick={() => { setOpen(false); stopListening(); setTranscript(''); setReply(''); setError(''); }}
              className="p-1 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100"
              aria-label="Close Syra"
            >
              <X size={14} />
            </button>
          </div>
          {transcript && (
            <p className="text-xs text-gray-500 italic mb-2 line-clamp-3">"{transcript}"</p>
          )}
          {busy && (
            <div className="flex items-center gap-2 text-xs text-violet-600">
              <Loader2 size={12} className="animate-spin" />
              <span>Thinking…</span>
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
        title={listening ? 'Stop listening' : 'Talk to Syra'}
        aria-label={listening ? 'Stop listening' : 'Talk to Syra'}
        data-testid="syra-orb"
        className={`relative w-14 h-14 rounded-full flex items-center justify-center text-white shadow-xl transition-all duration-200 ${
          listening
            ? 'bg-gradient-to-br from-rose-500 to-violet-600'
            : 'bg-gradient-to-br from-violet-500 to-indigo-600 hover:scale-105'
        }`}
      >
        {listening && (
          <>
            <span className="absolute inset-0 rounded-full bg-violet-400 opacity-60 animate-ping" />
            <span className="absolute inset-1 rounded-full bg-violet-500 opacity-40 animate-pulse" />
          </>
        )}
        {listening ? <MicOff size={20} className="relative" /> : <Mic size={20} className="relative" />}
      </button>
    </div>
  );
}
