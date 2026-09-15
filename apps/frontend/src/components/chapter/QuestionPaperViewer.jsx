import { useState, useCallback } from 'react';
import { X, ZoomIn, ZoomOut, Maximize2, Download, ChevronLeft, ChevronRight } from 'lucide-react';
import AdSlot from '@/components/ads/AdSlot';

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
export function normalizeQuestionPaperPages(papers = []) {
  const pages = [];
  papers.forEach((paper, paperIndex) => {
    if (Array.isArray(paper?.file_urls)) {
      paper.file_urls.filter(Boolean).forEach((url, pageIndex) => {
        pages.push({
          ...paper,
          id: `${paper.id || paperIndex}-page-${pageIndex + 1}`,
          url,
          page_index: pageIndex,
        });
      });
      return;
    }
    if (paper?.url) {
      pages.push({ ...paper, id: paper.id || `page-${paperIndex + 1}` });
    }
  });
  return pages;
}

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

  const pages = normalizeQuestionPaperPages(papers);

  if (!pages.length) {
    return (
      <div className="py-12 text-center space-y-2" data-testid="pyq-images-empty">
        <p className="text-sm text-muted-foreground">
          {lang === 'as' ? 'কোনো প্ৰশ্নকাকত উপলব্ধ নহয়।' : 'No question paper available for this chapter yet.'}
        </p>
      </div>
    );
  }

  // Raw chapter uploads have one {id, url, uploaded_at} entry per page.
  // Render the normalized list directly: sorting/grouping here would break
  // the upload order that represents the scanned paper's page order.
  const pageUrls = pages.map(page => page.url);

  return (
    <>
      <div className="space-y-8" data-testid="pyq-images-viewer">
        {pages.map((paper, pageIndex) => {
          const year = paper.exam_year || paper.year || null;
          const previousYear = pages[pageIndex - 1]?.exam_year || pages[pageIndex - 1]?.year || null;
          const showYear = year && year !== previousYear;
          return (
            <div key={paper.id} className="mb-4">
              {showYear && (
                <div className="flex items-center gap-2 mb-3">
                  <span className="text-[11px] font-bold px-2.5 py-1 rounded-full bg-violet-100 text-violet-700 tracking-wide">
                    {lang === 'as' ? `${year} চন` : `${year} Exam`}
                  </span>
                  <div className="flex-1 h-px bg-violet-100" />
                </div>
              )}
              <div
                className="relative group cursor-zoom-in rounded-xl overflow-hidden border border-gray-200 shadow-sm bg-gray-50"
                onClick={() => openLightbox(pageUrls, pageIndex)}
              >
                <img
                  src={paper.url}
                  alt={lang === 'as'
                    ? `প্ৰশ্নকাকত ${year || ''} — পৃষ্ঠা ${pageIndex + 1}`
                    : `Question Paper ${year || ''} — Page ${pageIndex + 1}`}
                  className="w-full h-auto block"
                  loading={pageIndex === 0 ? 'eager' : 'lazy'}
                  decoding="async"
                />
                <span className="absolute top-2 right-2 text-[10px] font-mono bg-black/50 text-white px-1.5 py-0.5 rounded-full pointer-events-none">
                  {pageIndex + 1}/{pages.length}
                </span>
                <div className="absolute inset-0 bg-black/0 group-hover:bg-black/10 flex items-center justify-center transition-all pointer-events-none">
                  <ZoomIn size={24} className="text-white opacity-0 group-hover:opacity-80 transition-opacity drop-shadow" />
                </div>
              </div>
              {pageIndex < pages.length - 1 && <AdSlot placement="chapter.pyq.betweenImages" />}
            </div>
          );
        })}
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
