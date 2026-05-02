/**
 * MicButton — speech-to-text trigger for the unified input bar.
 * Uses the browser SpeechRecognition API where available; otherwise
 * records a short clip and posts to the Sarvam Saaras backend.
 */
import { useEffect } from 'react';
import { Mic, MicOff } from 'lucide-react';
import { toast } from 'sonner';
import { useSpeechRecognition } from '@/hooks/useSpeechRecognition';

const MIC_ERROR_MESSAGES = {
  no_recorder:         'Voice recording is not supported in this browser.',
  mic_denied:          'Microphone access was denied — please allow it in your browser settings.',
  recording_too_large: 'Recording too long — please try a shorter message.',
  'not-allowed':       'Microphone access was denied — please allow it in your browser settings.',
};

export function MicButton({ onTranscript, language = 'en-IN', className = '', disabled = false }) {
  const { listening, error, start } = useSpeechRecognition({
    language,
    onResult: (text) => { if (onTranscript) onTranscript(text); },
  });

  useEffect(() => {
    if (!error) return;
    const msg = MIC_ERROR_MESSAGES[error] || `Microphone error: ${error}`;
    toast.error(msg);
  }, [error]);

  return (
    <button
      type="button"
      onClick={start}
      disabled={disabled}
      className={[
        'inline-flex items-center justify-center w-9 h-9 rounded-full transition-colors',
        listening ? 'bg-red-500 text-white animate-pulse' : 'bg-muted hover:bg-muted/80 text-muted-foreground',
        disabled ? 'opacity-50 cursor-not-allowed' : '',
        className,
      ].join(' ')}
      aria-label={listening ? 'Stop recording' : 'Speak'}
      title={error ? `Mic error: ${error}` : (listening ? 'Listening… tap to stop' : 'Voice input')}
    >
      {listening ? <MicOff className="w-4 h-4" /> : <Mic className="w-4 h-4" />}
    </button>
  );
}
