import { useState, useEffect, useRef, useCallback } from 'react';
import { API_BASE } from '@/utils/api';
import { toast } from 'sonner';

const TTS_LANG_KEY = 'syrabit_tts_lang';
const CHUNK_LIMIT = 490;

function stripMarkdown(text) {
  return text
    .replace(/```[\s\S]*?```/g, '')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/!\[.*?\]\(.*?\)/g, '')
    .replace(/\[([^\]]+)\]\(.*?\)/g, '$1')
    .replace(/#{1,6}\s+/g, '')
    .replace(/(\*{1,3}|_{1,3})(.*?)\1/g, '$2')
    .replace(/~~(.*?)~~/g, '$1')
    .replace(/>\s+/g, '')
    .replace(/[-*+]\s+/g, '')
    .replace(/\d+\.\s+/g, '')
    .replace(/---+/g, '')
    .replace(/\|.*\|/g, '')
    .replace(/<[^>]+>/g, '')
    .replace(/\n{2,}/g, '\n')
    .replace(/[-—–]\s*(source|ref|via|from|credit)[:\s].*/gi, '')
    .replace(/\(?(source|ref|via|from|credit)[:\s][^)]*\)?/gi, '')
    .replace(/📚.*$/gm, '')
    .replace(/\*?\s*[-—–]?\s*(varena|assam|ahsec|seba|board|chapter|subject|class\s*\d+|hs\s*\d+)[\s,].*$/gim, '')
    .trim();
}

function chunkText(text) {
  if (text.length <= CHUNK_LIMIT) return [text];
  const chunks = [];
  let remaining = text;
  while (remaining.length > 0) {
    if (remaining.length <= CHUNK_LIMIT) {
      chunks.push(remaining);
      break;
    }
    let splitIdx = remaining.lastIndexOf('. ', CHUNK_LIMIT);
    if (splitIdx < CHUNK_LIMIT * 0.3) {
      splitIdx = remaining.lastIndexOf(' ', CHUNK_LIMIT);
    }
    if (splitIdx <= 0) splitIdx = CHUNK_LIMIT;
    else splitIdx += 1;
    chunks.push(remaining.slice(0, splitIdx).trim());
    remaining = remaining.slice(splitIdx).trim();
  }
  return chunks.filter(Boolean);
}

let _sarvamStatus = null;
let _statusPromise = null;
let _statusRetryCount = 0;
const MAX_STATUS_RETRIES = 2;

function fetchSarvamStatus() {
  return fetch(`${API_BASE}/sarvam/status`)
    .then(r => {
      if (!r.ok) throw new Error('status failed');
      return r.json();
    })
    .then(data => {
      _sarvamStatus = {
        enabled: data.enabled,
        languages: (data.supported_languages || []).filter(l => l.includes('-')),
      };
      _statusRetryCount = 0;
      return _sarvamStatus;
    })
    .catch(() => {
      if (_statusRetryCount < MAX_STATUS_RETRIES) {
        _statusRetryCount++;
        _statusPromise = null;
        return null;
      }
      _sarvamStatus = { enabled: false, languages: [] };
      return _sarvamStatus;
    });
}

function useSarvamStatus() {
  const [enabled, setEnabled] = useState(_sarvamStatus?.enabled ?? null);
  const [languages, setLanguages] = useState(_sarvamStatus?.languages ?? []);

  useEffect(() => {
    if (_sarvamStatus) {
      setEnabled(_sarvamStatus.enabled);
      setLanguages(_sarvamStatus.languages);
      return;
    }
    if (!_statusPromise) {
      _statusPromise = fetchSarvamStatus();
    }
    _statusPromise.then(status => {
      if (status) {
        setEnabled(status.enabled);
        setLanguages(status.languages);
      } else {
        const retryTimer = setTimeout(() => {
          _statusPromise = fetchSarvamStatus();
          _statusPromise.then(s => {
            if (s) {
              setEnabled(s.enabled);
              setLanguages(s.languages);
            }
          });
        }, 3000);
        return () => clearTimeout(retryTimer);
      }
    });
  }, []);

  return { enabled, languages };
}

const LANG_LABELS = {
  'en-IN': 'English',
  'hi-IN': 'Hindi',
  'bn-IN': 'Bengali',
  'as-IN': 'Assamese',
  'gu-IN': 'Gujarati',
  'kn-IN': 'Kannada',
  'ml-IN': 'Malayalam',
  'mr-IN': 'Marathi',
  'od-IN': 'Odia',
  'pa-IN': 'Punjabi',
  'ta-IN': 'Tamil',
  'te-IN': 'Telugu',
};

function getLangLabel(code) {
  return LANG_LABELS[code] || code;
}

export function getTTSLang() {
  const stored = localStorage.getItem(TTS_LANG_KEY) || 'en-IN';
  if (_sarvamStatus?.languages?.length && !_sarvamStatus.languages.includes(stored)) {
    return 'en-IN';
  }
  return stored;
}

function setTTSLang(lang) {
  localStorage.setItem(TTS_LANG_KEY, lang);
}

export function useTTS() {
  const [state, setState] = useState('idle');
  const [activeMsgId, setActiveMsgId] = useState(null);
  const audioRef = useRef(null);
  const abortRef = useRef(false);
  const fetchControllerRef = useRef(null);
  const currentUrlsRef = useRef([]);

  const cleanup = useCallback(() => {
    abortRef.current = true;
    if (fetchControllerRef.current) {
      fetchControllerRef.current.abort();
      fetchControllerRef.current = null;
    }
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.src = '';
      audioRef.current = null;
    }
    currentUrlsRef.current.forEach(url => URL.revokeObjectURL(url));
    currentUrlsRef.current = [];
    setState('idle');
    setActiveMsgId(null);
  }, []);

  useEffect(() => {
    return () => {
      abortRef.current = true;
      if (fetchControllerRef.current) {
        fetchControllerRef.current.abort();
        fetchControllerRef.current = null;
      }
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current.src = '';
        audioRef.current = null;
      }
      currentUrlsRef.current.forEach(url => URL.revokeObjectURL(url));
      currentUrlsRef.current = [];
    };
  }, []);

  const playingRef = useRef(false);

  const speak = useCallback(async (rawText, msgId) => {
    if (playingRef.current) return;
    playingRef.current = true;
    abortRef.current = false;

    const text = stripMarkdown(rawText);
    if (!text) { playingRef.current = false; return; }

    setState('loading');
    setActiveMsgId(msgId || null);
    const chunks = chunkText(text);
    const lang = getTTSLang();

    const fetchChunkAudio = async (chunkText, signal) => {
      const res = await fetch(`${API_BASE}/sarvam/tts`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          text: chunkText,
          target_language_code: lang,
          speaker: 'karun',
        }),
        signal,
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'TTS request failed');
      }
      const data = await res.json();
      if (!data.audio_base64) throw new Error('No audio returned');
      const binary = atob(data.audio_base64);
      const bytes = new Uint8Array(binary.length);
      for (let j = 0; j < binary.length; j++) bytes[j] = binary.charCodeAt(j);
      const blob = new Blob([bytes], { type: `audio/${data.format || 'wav'}` });
      const url = URL.createObjectURL(blob);
      currentUrlsRef.current.push(url);
      return url;
    };

    try {
      let nextAudioPromise = null;

      for (let i = 0; i < chunks.length; i++) {
        if (abortRef.current) return;

        const controller = new AbortController();
        fetchControllerRef.current = controller;

        const audioUrl = nextAudioPromise
          ? await nextAudioPromise
          : await fetchChunkAudio(chunks[i], controller.signal);

        if (abortRef.current) return;

        if (i + 1 < chunks.length) {
          const nextController = new AbortController();
          nextAudioPromise = fetchChunkAudio(chunks[i + 1], nextController.signal);
        } else {
          nextAudioPromise = null;
        }

        await new Promise((resolve, reject) => {
          if (abortRef.current) { resolve(); return; }
          const audio = new Audio(audioUrl);
          audioRef.current = audio;
          setState('playing');
          audio.onended = resolve;
          audio.onerror = () => reject(new Error('Audio playback error'));
          audio.play().catch(reject);
        });
      }
    } catch (err) {
      if (err.name === 'AbortError') return;
      if (!abortRef.current) {
        toast.error(err.message || 'Voice playback failed');
      }
    } finally {
      playingRef.current = false;
      if (!abortRef.current) {
        setState('idle');
        setActiveMsgId(null);
      }
      currentUrlsRef.current.forEach(url => URL.revokeObjectURL(url));
      currentUrlsRef.current = [];
      fetchControllerRef.current = null;
    }
  }, []);

  const stop = useCallback(() => {
    playingRef.current = false;
    cleanup();
  }, [cleanup]);

  return { state, activeMsgId, speak, stop };
}
