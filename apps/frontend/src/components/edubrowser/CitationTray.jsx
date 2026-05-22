import { memo } from 'react';
import { SourceFavicon } from './SourceFavicon';

/**
 * CitationTray — Bing-style "Sources" panel.
 *
 * Renders each citation as a numbered card with favicon, domain, title and
 * snippet. Cards link out (new tab) when a URL is present; clicking the
 * number jumps to the source via the optional `onCite` handler.
 */
export const CitationTray = memo(function CitationTray({ citations = [], onCite }) {
  if (!Array.isArray(citations) || citations.length === 0) return null;
  return (
    <section
      className="border-t border-border/60 bg-muted/20 px-3 md:px-4 pt-3 pb-3"
      aria-label="Sources used to build this answer"
    >
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wide">
          Sources
        </h3>
        <span className="text-[11px] text-muted-foreground/80">
          {citations.length} {citations.length === 1 ? 'source' : 'sources'}
        </span>
      </div>
      <ol className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        {citations.map((c) => {
          const href = c.url || '';
          const isExternal = /^https?:\/\//i.test(href);
          const Body = (
            <>
              <div className="flex items-center gap-2 min-w-0">
                <SourceFavicon domain={c.domain} size={16} />
                <span className="text-[11px] text-muted-foreground truncate">
                  {c.domain || (c.type === 'chapter' ? 'Syrabit chapter' : c.type === 'page' ? 'Current page' : 'Source')}
                </span>
                <span className="ml-auto inline-flex items-center justify-center rounded-full bg-background ring-1 ring-border text-[10px] font-semibold text-muted-foreground w-5 h-5 flex-none">
                  {c.index}
                </span>
              </div>
              <div className="mt-1 text-sm font-medium text-foreground line-clamp-2 leading-snug">
                {c.title || c.domain || 'Source'}
              </div>
              {c.snippet && (
                <div className="mt-1 text-xs text-muted-foreground line-clamp-2 leading-snug">
                  {c.snippet}
                </div>
              )}
            </>
          );

          const cardClass =
            'group flex flex-col rounded-lg border border-border bg-background hover:bg-muted/40 hover:border-primary/40 transition-colors p-2.5 text-left focus:outline-none focus:ring-2 focus:ring-ring';

          if (isExternal) {
            return (
              <li key={`${c.index}-${href || c.title}`}>
                <a
                  href={href}
                  target="_blank"
                  rel="noopener noreferrer"
                  className={cardClass}
                  title={c.title || c.domain || 'Source'}
                  onClick={onCite ? (e) => { e.preventDefault(); onCite(c); window.open(href, '_blank', 'noopener,noreferrer'); } : undefined}
                >
                  {Body}
                </a>
              </li>
            );
          }

          return (
            <li key={`${c.index}-${href || c.title}`}>
              <button
                type="button"
                onClick={onCite ? () => onCite(c) : undefined}
                className={cardClass}
                title={c.title || c.domain || 'Source'}
              >
                {Body}
              </button>
            </li>
          );
        })}
      </ol>
    </section>
  );
});
