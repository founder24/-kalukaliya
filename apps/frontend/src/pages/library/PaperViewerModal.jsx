import { useEffect, useRef, useState, useCallback } from 'react';
import { X, ZoomIn, ZoomOut, RotateCcw } from 'lucide-react';

/**
 * PaperViewerModal
 * Full-screen in-page viewer for PYQ question papers.
 * - Images: pinch/scroll-zoomable with +/− buttons.
 * - PDFs (is_pdf=true): rendered in an <embed> / <iframe>.
 * Closes on Escape or backdrop click.
 */
export default function PaperViewerModal({ paper, onClose }) {
  const backdropRef = useRef(null);
  const [scale, setScale] = useState(1);

  // Trap focus and handle Escape
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    // Prevent body scroll while modal is open
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = prev;
    };
  }, [onClose]);

  const handleBackdropClick = useCallback(
    (e) => {
      if (e.target === backdropRef.current) onClose();
    },
    [onClose]
  );

  const zoomIn = () => setScale((s) => Math.min(s + 0.25, 4));
  const zoomOut = () => setScale((s) => Math.max(s - 0.25, 0.5));
  const resetZoom = () => setScale(1);

  if (!paper) return null;

  const isPdf = paper.is_pdf;

  return (
    <div
      ref={backdropRef}
      onClick={handleBackdropClick}
      className="fixed inset-0 z-50 flex flex-col"
      style={{ background: 'rgba(0,0,0,0.88)' }}
      role="dialog"
      aria-modal="true"
      aria-label={paper.title}
    >
      {/* Header bar */}
      <div
        className="flex items-center gap-3 px-4 py-3 flex-shrink-0"
        style={{ background: 'rgba(0,0,0,0.6)', borderBottom: '1px solid rgba(255,255,255,0.08)' }}
      >
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-white truncate">{paper.title}</p>
          {(paper.subject || paper.year) && (
            <p className="text-xs text-white/50 mt-0.5">
              {[paper.subject, paper.year].filter(Boolean).join(' · ')}
            </p>
          )}
        </div>

        {/* Zoom controls — only for image viewer */}
        {!isPdf && (
          <div className="flex items-center gap-1">
            <button
              onClick={zoomOut}
              disabled={scale <= 0.5}
              className="p-1.5 rounded-lg transition-colors disabled:opacity-30"
              style={{ background: 'rgba(255,255,255,0.08)' }}
              aria-label="Zoom out"
            >
              <ZoomOut size={14} className="text-white" />
            </button>
            <button
              onClick={resetZoom}
              className="px-2 py-1 rounded-lg text-[11px] font-mono text-white/70 transition-colors min-w-[42px] text-center"
              style={{ background: 'rgba(255,255,255,0.08)' }}
              aria-label="Reset zoom"
            >
              {Math.round(scale * 100)}%
            </button>
            <button
              onClick={zoomIn}
              disabled={scale >= 4}
              className="p-1.5 rounded-lg transition-colors disabled:opacity-30"
              style={{ background: 'rgba(255,255,255,0.08)' }}
              aria-label="Zoom in"
            >
              <ZoomIn size={14} className="text-white" />
            </button>
          </div>
        )}

        <button
          onClick={onClose}
          className="p-1.5 rounded-lg transition-colors ml-1"
          style={{ background: 'rgba(255,255,255,0.08)' }}
          aria-label="Close viewer"
        >
          <X size={16} className="text-white" />
        </button>
      </div>

      {/* Content area */}
      <div className="flex-1 overflow-auto flex items-start justify-center p-4">
        {isPdf ? (
          <embed
            src={paper.image_url}
            type="application/pdf"
            className="w-full rounded-lg"
            style={{ minHeight: 'calc(100vh - 120px)', height: 'calc(100vh - 120px)' }}
            title={paper.title}
          />
        ) : (
          <div
            style={{
              transform: `scale(${scale})`,
              transformOrigin: 'top center',
              transition: 'transform 0.15s ease',
              // Ensure the container grows with scaled content so scrolling works
              width: `${100 / scale}%`,
            }}
          >
            <img
              src={paper.image_url}
              alt={paper.title}
              className="max-w-full rounded-lg shadow-2xl mx-auto block"
              style={{ userSelect: 'none' }}
              draggable={false}
            />
          </div>
        )}
      </div>
    </div>
  );
}
