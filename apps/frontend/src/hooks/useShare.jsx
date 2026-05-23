import { useState, useCallback } from 'react';
import { toast } from 'sonner';

const SITE_ORIGIN = 'https://syrabit.ai';

function openWhatsApp(text, url) {
  const msg = `${text}\n${url}`;
  const waUrl = `https://wa.me/?text=${encodeURIComponent(msg)}`;
  window.open(waUrl, '_blank', 'noopener');
}

export function useShare() {
  const [sharing, setSharing] = useState(false);
  const [serpPreview, setSerpPreview] = useState(null);

  const share = useCallback(async (title, url, options = {}) => {
    if (sharing) return;
    const shareUrl = url.startsWith('http') ? url : `${SITE_ORIGIN}${url}`;
    const text = options.text || title || 'Check this out on Syrabit.ai';

    if (options.showSerpPreview) {
      setSerpPreview({
        title: title || 'Syrabit.ai',
        url: shareUrl,
        description: options.description || '',
      });
      return;
    }

    setSharing(true);
    openWhatsApp(text, shareUrl);
    setSharing(false);
  }, [sharing]);

  const confirmShare = useCallback(async () => {
    if (!serpPreview) return;
    const { title, url } = serpPreview;
    setSerpPreview(null);
    setSharing(true);
    openWhatsApp(title, url);
    setSharing(false);
  }, [serpPreview]);

  const dismissPreview = useCallback(() => {
    setSerpPreview(null);
  }, []);

  return { sharing, share, serpPreview, confirmShare, dismissPreview };
}

export function SerpPreviewModal({ preview, onConfirm, onDismiss }) {
  if (!preview) return null;
  const displayUrl = preview.url.replace(/^https?:\/\//, '').replace(/\/$/, '');
  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4" onClick={onDismiss}>
      <div
        className="w-full max-w-lg rounded-2xl overflow-hidden"
        style={{ background: '#1a1a2e', border: '1px solid rgba(139,92,246,0.2)' }}
        onClick={e => e.stopPropagation()}
      >
        <div className="p-4 border-b border-white/5">
          <p className="text-xs font-semibold text-white/40 uppercase tracking-wider">Share Preview</p>
        </div>
        <div className="p-5">
          <div className="rounded-xl p-4" style={{ background: '#fff' }}>
            <p className="text-xs text-[#202124] mb-0.5" style={{ fontFamily: 'Arial, sans-serif' }}>{displayUrl}</p>
            <p className="text-lg leading-tight mb-1" style={{ color: '#1a0dab', fontFamily: 'Arial, sans-serif' }}>
              {preview.title}
            </p>
            {preview.description && (
              <p className="text-sm leading-relaxed line-clamp-2" style={{ color: '#4d5156', fontFamily: 'Arial, sans-serif' }}>
                {preview.description}
              </p>
            )}
          </div>
        </div>
        <div className="flex items-center gap-3 p-4 border-t border-white/5">
          <button
            onClick={onDismiss}
            className="flex-1 h-10 rounded-xl text-sm font-medium text-white/60 hover:text-white transition-colors"
            style={{ border: '1px solid rgba(255,255,255,0.1)' }}
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className="flex-1 h-10 rounded-xl text-sm font-semibold text-white transition-all hover:opacity-90 flex items-center justify-center gap-2"
            style={{ background: '#25D366' }}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="white"><path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z"/></svg>
            Share on WhatsApp
          </button>
        </div>
      </div>
    </div>
  );
}
