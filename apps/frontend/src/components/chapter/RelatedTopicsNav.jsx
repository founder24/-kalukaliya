/**
 * RelatedTopicsNav - Navigation component for prev/next chapter and parent subject.
 *
 * Uses plain <a> tags for crawlability by search engines and AI bots.
 */

export default function RelatedTopicsNav({ prevChapter, nextChapter, parentSubject }) {
  if (!prevChapter && !nextChapter && !parentSubject) return null;

  return (
    <nav
      aria-label="Related Topics"
      className="related-topics-nav mt-8 pt-6 border-t border-border/30"
    >
      <div className="flex flex-col sm:flex-row items-start sm:items-center gap-3 sm:gap-4">
        {prevChapter && (
          <a
            href={prevChapter.href}
            className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-primary transition-colors"
          >
            <span aria-hidden="true">&larr;</span>
            <span className="truncate max-w-[200px]">{prevChapter.title}</span>
          </a>
        )}
        {parentSubject && (
          <a
            href={parentSubject.href}
            className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-primary transition-colors"
          >
            <span aria-hidden="true">&uarr;</span>
            <span className="truncate max-w-[200px]">{parentSubject.title}</span>
          </a>
        )}
        {nextChapter && (
          <a
            href={nextChapter.href}
            className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-primary transition-colors ml-auto"
          >
            <span className="truncate max-w-[200px]">{nextChapter.title}</span>
            <span aria-hidden="true">&rarr;</span>
          </a>
        )}
      </div>
    </nav>
  );
}
