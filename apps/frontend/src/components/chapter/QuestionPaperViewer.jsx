import { useState, useCallback } from 'react';
import { X, ZoomIn, ZoomOut, Maximize2, Download, ChevronLeft, ChevronRight } from 'lucide-react';

/**
 * QuestionPaperViewer
 *
 * Renders a list of question-paper records (from /content/chapters/{id}/pyq-images)
 * as a vertical sequence of images — one page below the next, grouped by exam year.
 *
 * Props:
 *   papers   — array of { id, exam_year, filename, page_count, file_urls: string[] }
 *   lang     — 'en' | 'as'
 */
export default function QuestionPaperViewer({ papers = [], lang = 'en' }) {
  const [lightbox, setLightbox] = useState(null); // { urls: [], idx: 0 }
  const [zoom, setZoom] = useState(100);

  const openLightbox = useCallback((urls, idx) => {
    setZoom(100);
    setLightbox({ urls, idx });
  }, []);
  const closeLightbox = useCallback(() => setLightbox(null), []);
  const prevPage = useCallback(() => setLightbox(l => l ? { ...l, idx: Math.max(0, l.idx - 1) } : l), []);
  const nextPage = useCallback(() => setLightbox(l => l ? { ...l, idx: Math.min(l.urls.length - 1, l.idx + 1) } : l), []);

  if (!papers.length) {
    return (
      <div className="py-12 text-center space-y-2" data-testid="pyq-images-empty">
        <p className="text-sm text-muted-foreground">
          {lang === 'as' ? 'কোনো প্ৰশ্নকাকত উপলব্ধ নহয়।' : 'No question paper available for this chapter yet.'}
        </p>
      </div>
    );
  }

  // Group by year, newest first
  const byYear = {};
  for (const p of papers) {
    const yr = p.exam_year || 'Unknown';
    (byYear[yr] = byYear[yr] || []).push(p);
  }
  const years = Object.keys(byYear).sort((a, b) => b - a);

  return (
    <>
      <div className="space-y-8" data-testid="pyq-images-viewer">
        {years.map(yr => (
          <div key={yr}>
            <div className="flex items-center gap-2 mb-3">
              <span className="text-[11px] font-bold px-2.5 py-1 rounded-full bg-violet-100 text-violet-700 tracking-wide">
                {yr === 'Unknown'
                  ? (lang === 'as' ? 'বছৰ অজ্ঞাত' : 'Year unknown')
                  : (lang === 'as' ? `${yr} চন` : `${yr} Exam`)}
              </span>
              <div className="flex-1 h-px bg-violet-100" />
            </div>

            {byYear[yr].map(paper => {
              const urls = paper.file_urls || [];
              return (
                <div key={paper.id} className="space-y-2 mb-4">
                  {urls.map((url, pageIdx) => (
                    <div
                      key={pageIdx}
                      className="relative group cursor-zoom-in rounded-xl overflow-hidden border border-gray-200 shadow-sm bg-gray-50"
                      onClick={() => openLightbox(urls, pageIdx)}
                    >
                      <img
                        src={url}
                        alt={lang === 'as'
                          ? `প্ৰশ্নকাকত ${yr} — পৃষ্ঠা ${pageIdx + 1}`
                          : `Question Paper ${yr} — Page ${pageIdx + 1}`}
                        className="w-full h-auto block"
                        loading={pageIdx === 0 ? 'eager' : 'lazy'}
                        decoding="async"
                      />
                      {/* page badge */}
                      {urls.length > 1 && (
                        <span className="absolute top-2 right-2 text-[10px] font-mono bg-black/50 text-white px-1.5 py-0.5 rounded-full pointer-events-none">
                          {pageIdx + 1}/{urls.length}
                        </span>
                      )}
                      {/* hover overlay */}
                      <div className="absolute inset-0 bg-black/0 group-hover:bg-black/10 flex items-center justify-center transition-all pointer-events-none">
                        <ZoomIn size={24} className="text-white opacity-0 group-hover:opacity-80 transition-opacity drop-shadow" />
                      </div>
                    </div>
                  ))}
                </div>
              );
            })}
          </div>
        ))}
      </div>

      {/* ── Lightbox ── */}
      {lightbox && (
        <div
          className="fixed inset-0 z-[9999] bg-black/90 flex flex-col"
          onClick={closeLightbox}
        >
          {/* toolbar */}
          <div
            className="flex items-center justify-between px-4 py-2 bg-black/60 flex-shrink-0"
            onClick={e => e.stopPropagation()}
          >
            <span className="text-xs text-white/70 font-mono">
              {lightbox.idx + 1} / {lightbox.urls.length}
            </span>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setZoom(z => Math.max(50, z - 25))}
                className="p-1.5 rounded-lg text-white/70 hover:text-white hover:bg-white/10 transition-colors"
                title="Zoom out"
              >
                <ZoomOut size={16} />
              </button>
              <span className="text-xs text-white/60 w-10 text-center font-mono">{zoom}%</span>
              <button
                onClick={() => setZoom(z => Math.min(200, z + 25))}
                className="p-1.5 rounded-lg text-white/70 hover:text-white hover:bg-white/10 transition-colors"
                title="Zoom in"
              >
                <ZoomIn size={16} />
              </button>
              <a
                href={lightbox.urls[lightbox.idx]}
                target="_blank"
                rel="noopener noreferrer"
                className="p-1.5 rounded-lg text-white/70 hover:text-white hover:bg-white/10 transition-colors"
                title="Download"
                onClick={e => e.stopPropagation()}
              >
                <Download size={16} />
              </a>
              <button
                onClick={closeLightbox}
                className="p-1.5 rounded-lg text-white/70 hover:text-white hover:bg-white/10 transition-colors"
                title="Close"
              >
                <X size={16} />
              </button>
            </div>
          </div>

          {/* image */}
          <div
            className="flex-1 overflow-auto flex items-start justify-center p-4"
            onClick={e => e.stopPropagation()}
          >
            <img
              src={lightbox.urls[lightbox.idx]}
              alt={`Page ${lightbox.idx + 1}`}
              style={{ width: `${zoom}%`, maxWidth: '100%' }}
              className="rounded-lg shadow-2xl block mx-auto"
              draggable={false}
            />
          </div>

          {/* prev / next */}
          {lightbox.urls.length > 1 && (
            <>
              {lightbox.idx > 0 && (
                <button
                  onClick={e => { e.stopPropagation(); prevPage(); }}
                  className="absolute left-3 top-1/2 -translate-y-1/2 p-2 rounded-full bg-black/50 text-white hover:bg-black/70 transition-colors"
                >
                  <ChevronLeft size={22} />
                </button>
              )}
              {lightbox.idx < lightbox.urls.length - 1 && (
                <button
                  onClick={e => { e.stopPropagation(); nextPage(); }}
                  className="absolute right-3 top-1/2 -translate-y-1/2 p-2 rounded-full bg-black/50 text-white hover:bg-black/70 transition-colors"
                >
                  <ChevronRight size={22} />
                </button>
              )}
            </>
          )}

          {/* dot strip */}
          {lightbox.urls.length > 1 && (
            <div
              className="flex items-center justify-center gap-1.5 py-3 flex-shrink-0"
              onClick={e => e.stopPropagation()}
            >
              {lightbox.urls.map((_, i) => (
                <button
                  key={i}
                  onClick={() => setLightbox(l => ({ ...l, idx: i }))}
                  className={`rounded-full transition-all ${
                    i === lightbox.idx
                      ? 'w-4 h-2 bg-violet-400'
                      : 'w-2 h-2 bg-white/30 hover:bg-white/60'
                  }`}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </>
  );
}
