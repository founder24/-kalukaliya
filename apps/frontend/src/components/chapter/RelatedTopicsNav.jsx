/**
 * RelatedTopicsNav - Internal linking for SEO topical clusters.
 * Renders prev/next chapter links + parent subject link using plain <a> tags
 * so they are crawlable in prerendered HTML without JavaScript.
 */
import { ChevronLeft, ChevronRight, FolderOpen } from 'lucide-react';

export default function RelatedTopicsNav({ prevChapter, nextChapter, parentSubject }) {
  const hasAny = prevChapter || nextChapter || parentSubject;
  if (!hasAny) return null;

  return (
    <nav aria-label="Related content" className="mt-8 mb-6 border-t border-gray-200 pt-6" data-testid="related-topics-nav">
      <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide mb-4">
        Continue Learning
      </h2>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {prevChapter && (
          <a
            href={prevChapter.path}
            className="flex items-center gap-2 p-3 rounded-lg border border-gray-200 hover:border-purple-300 hover:bg-purple-50 transition-colors no-underline text-gray-700 hover:text-purple-700"
          >
            <ChevronLeft size={16} className="flex-shrink-0 text-gray-400" />
            <div className="min-w-0">
              <span className="text-xs text-gray-500">Previous</span>
              <p className="text-sm font-medium truncate">{prevChapter.title}</p>
            </div>
          </a>
        )}
        {nextChapter && (
          <a
            href={nextChapter.path}
            className="flex items-center gap-2 p-3 rounded-lg border border-gray-200 hover:border-purple-300 hover:bg-purple-50 transition-colors no-underline text-gray-700 hover:text-purple-700 sm:text-right sm:flex-row-reverse"
          >
            <ChevronRight size={16} className="flex-shrink-0 text-gray-400" />
            <div className="min-w-0">
              <span className="text-xs text-gray-500">Next</span>
              <p className="text-sm font-medium truncate">{nextChapter.title}</p>
            </div>
          </a>
        )}
      </div>
      {parentSubject && (
        <a
          href={parentSubject.path}
          className="mt-3 flex items-center gap-2 p-3 rounded-lg border border-gray-200 hover:border-purple-300 hover:bg-purple-50 transition-colors no-underline text-gray-700 hover:text-purple-700"
        >
          <FolderOpen size={16} className="flex-shrink-0 text-gray-400" />
          <div className="min-w-0">
            <span className="text-xs text-gray-500">Subject</span>
            <p className="text-sm font-medium truncate">{parentSubject.name} - All Chapters</p>
          </div>
        </a>
      )}
    </nav>
  );
}
