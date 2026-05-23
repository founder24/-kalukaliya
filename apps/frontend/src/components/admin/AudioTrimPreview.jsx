import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { Play, Pause, Scissors, X, Upload, Loader2, AlertTriangle } from 'lucide-react';

let _lamejsPromise = null;
function loadLamejs() {
  if (!_lamejsPromise) {
    _lamejsPromise = import('lamejs').then(m => m.default || m);
  }
  return _lamejsPromise;
}

const MAX_DURATION = 5;
const WAVEFORM_BARS = 80;
const BAR_WIDTH = 3;
const BAR_GAP = 1;
const MP3_BITRATE = 128;

function formatTime(s) {
  const sec = Math.max(0, s || 0);
  const m = Math.floor(sec / 60);
  const r = (sec % 60).toFixed(1);
  return `${m}:${r.padStart(4, '0')}`;
}

function drawWaveform(audioBuffer) {
  const raw = audioBuffer.getChannelData(0);
  const step = Math.floor(raw.length / WAVEFORM_BARS);
  const bars = [];
  for (let i = 0; i < WAVEFORM_BARS; i++) {
    let sum = 0;
    for (let j = 0; j < step; j++) {
      sum += Math.abs(raw[i * step + j]);
    }
    bars.push(sum / step);
  }
  const max = Math.max(...bars, 0.01);
  return bars.map(b => b / max);
}

function trimAudioBuffer(audioBuffer, startTime, endTime) {
  const sr = audioBuffer.sampleRate;
  const ch = audioBuffer.numberOfChannels;
  const startSample = Math.floor(startTime * sr);
  const endSample = Math.floor(endTime * sr);
  const length = endSample - startSample;
  const offlineCtx = new OfflineAudioContext(ch, length, sr);
  const newBuffer = offlineCtx.createBuffer(ch, length, sr);
  for (let c = 0; c < ch; c++) {
    const src = audioBuffer.getChannelData(c);
    const dst = newBuffer.getChannelData(c);
    for (let i = 0; i < length; i++) {
      dst[i] = src[startSample + i] || 0;
    }
  }
  return newBuffer;
}

function audioBufferToWav(buffer) {
  const numChannels = buffer.numberOfChannels;
  const sampleRate = buffer.sampleRate;
  const format = 1;
  const bitsPerSample = 16;
  const bytesPerSample = bitsPerSample / 8;
  const blockAlign = numChannels * bytesPerSample;
  const interleaved = numChannels === 1
    ? buffer.getChannelData(0)
    : interleaveChannels(buffer);
  const dataLength = interleaved.length * bytesPerSample;
  const headerLength = 44;
  const arrayBuffer = new ArrayBuffer(headerLength + dataLength);
  const view = new DataView(arrayBuffer);

  writeString(view, 0, 'RIFF');
  view.setUint32(4, 36 + dataLength, true);
  writeString(view, 8, 'WAVE');
  writeString(view, 12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, format, true);
  view.setUint16(22, numChannels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * blockAlign, true);
  view.setUint16(32, blockAlign, true);
  view.setUint16(34, bitsPerSample, true);
  writeString(view, 36, 'data');
  view.setUint32(40, dataLength, true);

  let offset = 44;
  for (let i = 0; i < interleaved.length; i++, offset += 2) {
    const s = Math.max(-1, Math.min(1, interleaved[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
  }
  return new Blob([arrayBuffer], { type: 'audio/wav' });
}

function interleaveChannels(buffer) {
  const ch0 = buffer.getChannelData(0);
  const ch1 = buffer.numberOfChannels > 1 ? buffer.getChannelData(1) : ch0;
  const length = ch0.length + ch1.length;
  const result = new Float32Array(length);
  let idx = 0;
  for (let i = 0; i < ch0.length; i++) {
    result[idx++] = ch0[i];
    result[idx++] = ch1[i];
  }
  return result;
}

function writeString(view, offset, str) {
  for (let i = 0; i < str.length; i++) {
    view.setUint8(offset + i, str.charCodeAt(i));
  }
}

function floatTo16BitPCM(float32Array) {
  const int16 = new Int16Array(float32Array.length);
  for (let i = 0; i < float32Array.length; i++) {
    const s = Math.max(-1, Math.min(1, float32Array[i]));
    int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
  }
  return int16;
}

async function audioBufferToMp3(buffer) {
  const lamejs = await loadLamejs();
  const numChannels = buffer.numberOfChannels;
  const sampleRate = buffer.sampleRate;
  const encoder = new lamejs.Mp3Encoder(numChannels, sampleRate, MP3_BITRATE);
  const blockSize = 1152;
  const left = floatTo16BitPCM(buffer.getChannelData(0));
  const right = numChannels > 1 ? floatTo16BitPCM(buffer.getChannelData(1)) : null;
  const chunks = [];
  for (let i = 0; i < left.length; i += blockSize) {
    const leftChunk = left.subarray(i, i + blockSize);
    const rightChunk = right ? right.subarray(i, i + blockSize) : null;
    const mp3buf = numChannels === 1
      ? encoder.encodeBuffer(leftChunk)
      : encoder.encodeBuffer(leftChunk, rightChunk);
    if (mp3buf.length > 0) chunks.push(mp3buf);
  }
  const end = encoder.flush();
  if (end.length > 0) chunks.push(end);
  return new Blob(chunks, { type: 'audio/mpeg' });
}

export default function AudioTrimPreview({ file, onConfirm, onCancel, uploading }) {
  const [audioBuffer, setAudioBuffer] = useState(null);
  const [waveformData, setWaveformData] = useState(null);
  const [duration, setDuration] = useState(0);
  const [trimStart, setTrimStart] = useState(0);
  const [trimEnd, setTrimEnd] = useState(0);
  const [announcement, setAnnouncement] = useState('');
  const [playing, setPlaying] = useState(false);
  const [playbackPos, setPlaybackPos] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [sizeError, setSizeError] = useState(null);
  const sourceRef = useRef(null);
  const audioCtxRef = useRef(null);
  const animFrameRef = useRef(null);
  const playStartTimeRef = useRef(0);
  const playOffsetRef = useRef(0);
  const waveContainerRef = useRef(null);
  const draggingRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        setLoading(true);
        setError(null);
        const arrayBuffer = await file.arrayBuffer();
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        audioCtxRef.current = ctx;
        const decoded = await ctx.decodeAudioData(arrayBuffer);
        if (cancelled) return;
        setAudioBuffer(decoded);
        setDuration(decoded.duration);
        setTrimEnd(Math.min(decoded.duration, MAX_DURATION));
        setWaveformData(drawWaveform(decoded));
      } catch {
        if (!cancelled) setError('Could not decode audio file');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    return () => {
      cancelled = true;
      stopPlayback();
    };
  }, [file]);

  const stopPlayback = useCallback(() => {
    if (sourceRef.current) {
      // AudioBufferSourceNode.stop() throws InvalidStateError if the
      // node was never started or has already been stopped — both are
      // safe no-ops here, so log at debug level rather than warn.
      try { sourceRef.current.stop(); } catch (err) { console.debug('AudioTrimPreview: source.stop() (already stopped):', err?.message); }
      sourceRef.current = null;
    }
    if (animFrameRef.current) {
      cancelAnimationFrame(animFrameRef.current);
      animFrameRef.current = null;
    }
    setPlaying(false);
  }, []);

  const playPreview = useCallback(() => {
    if (!audioBuffer || !audioCtxRef.current) return;
    stopPlayback();
    const ctx = audioCtxRef.current;
    if (ctx.state === 'suspended') ctx.resume();
    const src = ctx.createBufferSource();
    src.buffer = audioBuffer;
    src.connect(ctx.destination);
    const clipDuration = trimEnd - trimStart;
    src.start(0, trimStart, clipDuration);
    sourceRef.current = src;
    playStartTimeRef.current = ctx.currentTime;
    playOffsetRef.current = trimStart;
    setPlaying(true);

    const tick = () => {
      const elapsed = ctx.currentTime - playStartTimeRef.current;
      const pos = trimStart + elapsed;
      if (pos >= trimEnd) {
        stopPlayback();
        setPlaybackPos(trimStart);
        return;
      }
      setPlaybackPos(pos);
      animFrameRef.current = requestAnimationFrame(tick);
    };
    animFrameRef.current = requestAnimationFrame(tick);
    src.onended = () => {
      stopPlayback();
      setPlaybackPos(trimStart);
    };
  }, [audioBuffer, trimStart, trimEnd, stopPlayback]);

  const togglePlay = useCallback(() => {
    if (playing) stopPlayback();
    else playPreview();
  }, [playing, stopPlayback, playPreview]);

  const clipDuration = useMemo(() => trimEnd - trimStart, [trimStart, trimEnd]);

  const needsTrim = useMemo(() => {
    if (!audioBuffer) return false;
    return trimStart > 0.01 || Math.abs(trimEnd - audioBuffer.duration) > 0.01;
  }, [audioBuffer, trimStart, trimEnd]);

  const estimatedSizeKB = useMemo(() => {
    if (!audioBuffer) return 0;
    if (!needsTrim) return file.size / 1024;
    return (MP3_BITRATE * 1000 / 8 * clipDuration) / 1024;
  }, [audioBuffer, clipDuration, needsTrim, file.size]);

  const outputFormat = needsTrim ? 'mp3' : (file.name.match(/\.(\w+)$/)?.[1]?.toLowerCase() || 'wav');

  const sizeExceedsLimit = estimatedSizeKB > 500;

  const initialTrimRef = useRef(true);
  useEffect(() => {
    if (initialTrimRef.current) {
      initialTrimRef.current = false;
      return;
    }
    const id = setTimeout(() => {
      const dur = (trimEnd - trimStart).toFixed(1);
      setAnnouncement(
        `Clip: ${dur} seconds, from ${formatTime(trimStart)} to ${formatTime(trimEnd)}`
      );
    }, 300);
    return () => clearTimeout(id);
  }, [trimStart, trimEnd]);

  const handleConfirm = useCallback(async () => {
    if (!audioBuffer) return;
    setSizeError(null);
    if (needsTrim) {
      const trimmed = trimAudioBuffer(audioBuffer, trimStart, trimEnd);
      const blob = await audioBufferToMp3(trimmed);
      if (blob.size > 500 * 1024) {
        setSizeError(`Trimmed file is ${Math.round(blob.size / 1024)} KB — exceeds the 500 KB limit. Try a shorter selection.`);
        return;
      }
      const trimmedFile = new File([blob], file.name.replace(/\.\w+$/, '.mp3'), { type: 'audio/mpeg' });
      onConfirm(trimmedFile);
    } else {
      onConfirm(file);
    }
  }, [audioBuffer, trimStart, trimEnd, file, onConfirm, needsTrim]);

  const pctForTime = useCallback((t) => duration > 0 ? (t / duration) * 100 : 0, [duration]);

  const timeForClientX = useCallback((clientX) => {
    if (!waveContainerRef.current || !duration) return 0;
    const rect = waveContainerRef.current.getBoundingClientRect();
    const pct = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
    return pct * duration;
  }, [duration]);

  const handlePointerDown = useCallback((e, handle) => {
    e.preventDefault();
    draggingRef.current = handle;
    const onMove = (ev) => {
      if (!draggingRef.current) return;
      setSizeError(null);
      const t = timeForClientX(ev.clientX);
      if (draggingRef.current === 'start') {
        const clamped = Math.max(0, Math.min(t, trimEnd - 0.1));
        const maxStart = Math.max(0, trimEnd - MAX_DURATION);
        setTrimStart(Math.max(maxStart, clamped));
      } else {
        const clamped = Math.min(duration, Math.max(t, trimStart + 0.1));
        const maxEnd = trimStart + MAX_DURATION;
        setTrimEnd(Math.min(maxEnd, clamped));
      }
    };
    const onUp = () => {
      draggingRef.current = null;
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  }, [timeForClientX, trimStart, trimEnd, duration]);

  const handleKeyDown = useCallback((e, handle) => {
    const step = e.shiftKey ? 0.5 : 0.1;
    let delta = 0;
    if (e.key === 'ArrowRight') delta = step;
    else if (e.key === 'ArrowLeft') delta = -step;
    else return;
    e.preventDefault();
    setSizeError(null);
    if (handle === 'start') {
      setTrimStart((prev) => {
        const next = prev + delta;
        const maxStart = Math.max(0, trimEnd - MAX_DURATION);
        return Math.max(maxStart, Math.max(0, Math.min(next, trimEnd - 0.1)));
      });
    } else {
      setTrimEnd((prev) => {
        const next = prev + delta;
        const maxEnd = trimStart + MAX_DURATION;
        return Math.min(maxEnd, Math.min(duration, Math.max(next, trimStart + 0.1)));
      });
    }
  }, [trimStart, trimEnd, duration]);

  if (loading) {
    return (
      <div className="mt-2 p-3 rounded-lg border border-violet-200 bg-violet-50/50">
        <div className="flex items-center gap-2 text-[10px] text-violet-600">
          <Loader2 size={12} className="animate-spin" />
          Loading audio...
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="mt-2 p-3 rounded-lg border border-red-200 bg-red-50/50">
        <div className="flex items-center justify-between">
          <span className="text-[10px] text-red-600">{error}</span>
          <button onClick={onCancel} className="text-gray-400 hover:text-gray-600">
            <X size={12} />
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="mt-2 p-3 rounded-lg border border-violet-200 bg-violet-50/50 space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-[10px] font-medium text-gray-700 truncate max-w-[160px]">
          {file.name}
        </span>
        <button onClick={onCancel} className="text-gray-400 hover:text-gray-600" title="Cancel">
          <X size={12} />
        </button>
      </div>

      <div
        ref={waveContainerRef}
        className="relative h-12 bg-white rounded border border-gray-200 overflow-hidden select-none"
        style={{ touchAction: 'none' }}
      >
        {waveformData && (
          <div className="absolute inset-0 flex items-center justify-center gap-px px-1">
            {waveformData.map((v, i) => {
              const barTime = (i / WAVEFORM_BARS) * duration;
              const inRange = barTime >= trimStart && barTime <= trimEnd;
              return (
                <div
                  key={i}
                  className={`rounded-sm transition-colors ${inRange ? 'bg-violet-400' : 'bg-gray-200'}`}
                  style={{
                    width: BAR_WIDTH,
                    height: `${Math.max(8, v * 85)}%`,
                    flexShrink: 0,
                  }}
                />
              );
            })}
          </div>
        )}

        <div
          className="absolute top-0 bottom-0 bg-violet-500/10 border-l-2 border-r-2 border-violet-500/40 pointer-events-none"
          style={{
            left: `${pctForTime(trimStart)}%`,
            width: `${pctForTime(trimEnd) - pctForTime(trimStart)}%`,
          }}
        />

        <div
          role="slider"
          tabIndex={0}
          aria-label="Trim start"
          aria-valuemin={0}
          aria-valuemax={duration}
          aria-valuenow={Math.round(trimStart * 10) / 10}
          aria-valuetext={`Trim start at ${formatTime(trimStart)}`}
          className="absolute top-0 bottom-0 w-3 cursor-col-resize z-10 group outline-none focus-visible:ring-2 focus-visible:ring-violet-400 focus-visible:ring-offset-1 rounded-sm"
          style={{ left: `calc(${pctForTime(trimStart)}% - 6px)` }}
          onPointerDown={(e) => handlePointerDown(e, 'start')}
          onKeyDown={(e) => handleKeyDown(e, 'start')}
        >
          <div className="absolute left-1.5 top-0 bottom-0 w-0.5 bg-violet-600 group-hover:bg-violet-700" />
          <div className="absolute left-0.5 top-1/2 -translate-y-1/2 w-2 h-4 rounded-sm bg-violet-600 group-hover:bg-violet-700" />
        </div>

        <div
          role="slider"
          tabIndex={0}
          aria-label="Trim end"
          aria-valuemin={0}
          aria-valuemax={duration}
          aria-valuenow={Math.round(trimEnd * 10) / 10}
          aria-valuetext={`Trim end at ${formatTime(trimEnd)}`}
          className="absolute top-0 bottom-0 w-3 cursor-col-resize z-10 group outline-none focus-visible:ring-2 focus-visible:ring-violet-400 focus-visible:ring-offset-1 rounded-sm"
          style={{ left: `calc(${pctForTime(trimEnd)}% - 6px)` }}
          onPointerDown={(e) => handlePointerDown(e, 'end')}
          onKeyDown={(e) => handleKeyDown(e, 'end')}
        >
          <div className="absolute left-1.5 top-0 bottom-0 w-0.5 bg-violet-600 group-hover:bg-violet-700" />
          <div className="absolute left-0.5 top-1/2 -translate-y-1/2 w-2 h-4 rounded-sm bg-violet-600 group-hover:bg-violet-700" />
        </div>

        {playing && (
          <div
            className="absolute top-0 bottom-0 w-0.5 bg-red-500 z-20 pointer-events-none transition-none"
            style={{ left: `${pctForTime(playbackPos)}%` }}
          />
        )}
      </div>

      <div className="flex items-center justify-between text-[9px] text-gray-500">
        <span>{formatTime(trimStart)}</span>
        <span className="flex items-center gap-1">
          <Scissors size={9} />
          {clipDuration.toFixed(1)}s{clipDuration > MAX_DURATION && (
            <span className="text-red-500 font-medium"> (max {MAX_DURATION}s)</span>
          )}
          <span className="mx-0.5">·</span>
          <span className={sizeExceedsLimit ? 'text-red-500 font-medium' : ''}>
            ~{Math.round(estimatedSizeKB)} KB {outputFormat.toUpperCase()}
          </span>
        </span>
        <span>{formatTime(trimEnd)}</span>
      </div>

      {sizeExceedsLimit && (
        <div className="flex items-start gap-1.5 text-[9px] text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1">
          <AlertTriangle size={10} className="flex-shrink-0 mt-px" />
          <span>
            Estimated size (~{Math.round(estimatedSizeKB)} KB) exceeds the 500 KB limit. Shorten the selection.
          </span>
        </div>
      )}

      <div className="flex items-center gap-2">
        <button
          onClick={togglePlay}
          className="flex items-center gap-1 text-[10px] px-2 py-1 rounded-md bg-white border border-gray-200 hover:bg-violet-50 text-gray-700 font-medium transition-colors"
        >
          {playing ? <Pause size={10} /> : <Play size={10} />}
          {playing ? 'Stop' : 'Preview'}
        </button>
        <div className="flex-1" />
        <button
          onClick={onCancel}
          className="text-[10px] px-2 py-1 rounded-md border border-gray-200 text-gray-500 hover:bg-gray-50 font-medium transition-colors"
          disabled={uploading}
        >
          Cancel
        </button>
        <button
          onClick={handleConfirm}
          disabled={uploading || clipDuration < 0.1}
          className="flex items-center gap-1 text-[10px] px-2.5 py-1 rounded-md bg-violet-600 text-white hover:bg-violet-700 font-medium transition-colors disabled:opacity-50"
        >
          {uploading ? <Loader2 size={10} className="animate-spin" /> : <Upload size={10} />}
          {uploading ? 'Uploading...' : 'Upload'}
        </button>
      </div>

      {sizeError && (
        <div className="flex items-center gap-1.5 text-[9px] text-red-600 bg-red-50 border border-red-200 rounded px-2 py-1">
          <AlertTriangle size={10} className="flex-shrink-0" />
          {sizeError}
        </div>
      )}

      <p className="text-[9px] text-gray-400">
        Drag handles or use arrow keys to trim (Shift for larger steps). Max {MAX_DURATION}s. MP3/WAV, max 500 KB after trim.
      </p>

      <div
        aria-live="polite"
        aria-atomic="true"
        className="sr-only"
        style={{
          position: 'absolute',
          width: '1px',
          height: '1px',
          padding: 0,
          margin: '-1px',
          overflow: 'hidden',
          clip: 'rect(0,0,0,0)',
          whiteSpace: 'nowrap',
          border: 0,
        }}
      >
        {announcement}
      </div>
    </div>
  );
}
