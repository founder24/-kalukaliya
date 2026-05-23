/**
 * ReadAloudButton — small reusable speak/stop button.
 * Hands the supplied text to the existing useTTS hook (Sarvam Bulbul).
 */
import { Volume2, Square, Loader2 } from 'lucide-react';
import { useTTS } from '@/hooks/useTTS';

export function ReadAloudButton({ text = '', id = 'read', className = '', label = 'Read aloud' }) {
  const { state, activeMsgId, speak, stop } = useTTS();
  const isMine = activeMsgId === id;
  const playing = isMine && (state === 'playing');
  const loading = isMine && (state === 'loading');
  const onClick = () => {
    if (playing || loading) stop();
    else if (text && text.trim()) speak(text, id);
  };
  return (
    <button
      onClick={onClick}
      disabled={!text || !text.trim()}
      className={`inline-flex items-center gap-1.5 text-xs px-2 py-1 rounded-md hover:bg-muted text-muted-foreground hover:text-foreground transition-colors ${className}`}
      aria-label={playing ? 'Stop reading' : label}
      title={playing ? 'Stop' : label}
    >
      {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> :
       playing ? <Square className="w-3.5 h-3.5" /> :
                 <Volume2 className="w-3.5 h-3.5" />}
      <span className="hidden sm:inline">{playing ? 'Stop' : label}</span>
    </button>
  );
}
