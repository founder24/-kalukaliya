/**
 * useSpeechRecognition — voice input for the unified bar.
 *
 * Uses the Web Speech API in supported browsers (Chrome/Edge/Safari)
 * with continuous=false interim results. Falls back to a record-and-
 * upload flow against `/api/edu/stt` (Sarvam Saaras) where the native
 * API is missing. Supports English (en-IN) and Assamese (as-IN) plus
 * Hindi/Bengali for completeness.
 */
import { useState, useRef, useCallback, useEffect } from 'react';
import { studyApi } from '@/utils/studyApi';

const Native = typeof window !== 'undefined'
  ? (window.SpeechRecognition || window.webkitSpeechRecognition)
  : null;

const _BCP = { en: 'en-IN', as: 'as-IN', hi: 'hi-IN', bn: 'bn-IN' };

export function useSpeechRecognition({ language = 'en-IN', onResult } = {}) {
  const [listening, setListening] = useState(false);
  const [error, setError] = useState('');
  const recRef = useRef(null);
  const mediaRef = useRef(null);
  const chunksRef = useRef([]);

  const lang = _BCP[language] || language || 'en-IN';

  const stop = useCallback(() => {
    setListening(false);
    if (recRef.current) {
      try { recRef.current.stop(); } catch {}
      recRef.current = null;
    }
    if (mediaRef.current) {
      try { mediaRef.current.stop(); } catch {}
      mediaRef.current = null;
    }
  }, []);

  const startNative = useCallback(() => {
    setError('');
    const rec = new Native();
    rec.lang = lang;
    rec.interimResults = false;
    rec.continuous = false;
    rec.maxAlternatives = 1;
    rec.onresult = (ev) => {
      const text = Array.from(ev.results).map(r => r[0]?.transcript || '').join(' ').trim();
      if (text && onResult) onResult(text);
    };
    rec.onerror = (ev) => {
      setError(ev.error || 'mic_error');
      setListening(false);
    };
    rec.onend = () => setListening(false);
    try { rec.start(); recRef.current = rec; setListening(true); }
    catch (e) { setError(String(e?.message || e)); }
  }, [lang, onResult]);

  const startFallback = useCallback(async () => {
    setError('');
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const Mr = window.MediaRecorder;
      if (!Mr) { setError('no_recorder'); return; }
      const mr = new Mr(stream);
      chunksRef.current = [];
      mr.ondataavailable = (e) => { if (e.data && e.data.size) chunksRef.current.push(e.data); };
      mr.onstop = async () => {
        stream.getTracks().forEach(t => t.stop());
        setListening(false);
        const blob = new Blob(chunksRef.current, { type: 'audio/webm' });
        if (blob.size > 10 * 1024 * 1024) {
          setError('recording_too_large');
          return;
        }
        try {
          const res = await studyApi.stt(blob, lang);
          if (res?.text && onResult) onResult(res.text);
        } catch (e) {
          setError(e.message || 'stt_failed');
        }
      };
      mediaRef.current = mr;
      mr.start();
      setListening(true);
    } catch (e) {
      setError(e?.message || 'mic_denied');
    }
  }, [lang, onResult]);

  const start = useCallback(() => {
    if (listening) { stop(); return; }
    if (Native) startNative();
    else startFallback();
  }, [listening, stop, startNative, startFallback]);

  useEffect(() => () => stop(), [stop]);

  return { listening, error, start, stop, supported: true };
}
