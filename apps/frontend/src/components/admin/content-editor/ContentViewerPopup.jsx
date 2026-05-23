import { X, Book } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

export default function ContentViewerPopup({ item, onClose }) {
  if (!item) return null;
  const wordCount = (item.content || '').trim().split(/\s+/).filter(Boolean).length;
  const readMin = Math.max(1, Math.ceil(wordCount / 200));
  return (
    <div className="fixed inset-0 z-[9999] flex items-center justify-center" onClick={onClose}>
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" />
      <div
        className="relative flex flex-col rounded-2xl overflow-hidden shadow-2xl"
        style={{ width: '90vw', maxWidth: '860px', height: '92vh', background: '#ffffff', border: '1px solid #e5e7eb' }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-6 py-4 border-b" style={{ borderColor: '#e5e7eb', background: '#f9fafb' }}>
          <div className="flex items-center gap-3 min-w-0">
            <Book size={18} className="text-violet-600 flex-shrink-0" />
            <div className="min-w-0">
              <h3 className="text-base font-bold truncate" style={{ color: '#111827' }}>{item.title || 'Untitled'}</h3>
              <p className="text-xs mt-0.5" style={{ color: '#9ca3af' }}>
                {wordCount > 0 ? `${wordCount.toLocaleString()} words · ${readMin} min read` : 'No content yet'}
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="w-8 h-8 rounded-lg flex items-center justify-center hover:bg-gray-100 transition-colors"
            style={{ color: '#6b7280' }}
            data-testid="close-viewer"
          >
            <X size={16} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto" style={{ background: '#ffffff' }}>
          <div className="blog-view-tab">
            <div className="px-8 py-10 max-w-[740px] mx-auto">
              {item.description && (
                <p className="text-base italic mb-8 pb-6 border-b" style={{ color: '#6b7280', borderColor: '#e5e7eb' }}>
                  {item.description}
                </p>
              )}
              {item.content ? (
                <div className="learn-article max-w-none">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {item.content}
                  </ReactMarkdown>
                </div>
              ) : (
                <p className="text-center py-16 italic" style={{ color: '#9ca3af' }}>No content available — generate notes using the ✨ button.</p>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
