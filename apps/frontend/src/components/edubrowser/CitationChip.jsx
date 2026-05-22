import { memo } from 'react';
import { BookOpen, Globe, FileText } from 'lucide-react';

const TYPE_STYLE = {
  page:    { Icon: FileText, ring: 'ring-violet-200 bg-violet-50 text-violet-700' },
  chapter: { Icon: BookOpen, ring: 'ring-emerald-200 bg-emerald-50 text-emerald-700' },
  web:     { Icon: Globe,    ring: 'ring-sky-200 bg-sky-50 text-sky-700' },
};

export const CitationChip = memo(function CitationChip({ citation, compact = false, onClick }) {
  if (!citation) return null;
  const { index, type = 'web' } = citation;
  const style = TYPE_STYLE[type] || TYPE_STYLE.web;
  const Icon = style.Icon;
  const label = compact ? `[${index}]` : `${index}`;
  const title = citation.title || citation.domain || 'Source';
  const className = `inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold ring-1 ${style.ring} align-baseline`;

  // When no onClick handler is provided, make the chip a real anchor to the
  // source URL (if we have one) so the student can click [1] and jump straight
  // to the citation. Falls back to a plain <span> if the citation has no URL,
  // avoiding a focusable no-op control.
  if (!onClick) {
    if (citation.url) {
      return (
        <a
          href={citation.url}
          target="_blank"
          rel="noopener noreferrer"
          className={`${className} hover:opacity-80 transition-opacity`}
          title={title}
          aria-label={`Source ${index}${citation.title ? `: ${citation.title}` : ''} (opens in new tab)`}
        >
          {!compact && <Icon className="w-3 h-3" aria-hidden="true" />}
          <span>{label}</span>
        </a>
      );
    }
    return (
      <span
        className={className}
        title={title}
        aria-label={`Source ${index}${citation.title ? `: ${citation.title}` : ''}`}
      >
        {!compact && <Icon className="w-3 h-3" aria-hidden="true" />}
        <span>{label}</span>
      </span>
    );
  }

  return (
    <button
      type="button"
      onClick={() => onClick(citation)}
      className={`${className} hover:opacity-80 transition-opacity`}
      title={title}
      aria-label={`Source ${index}${citation.title ? `: ${citation.title}` : ''}`}
    >
      {!compact && <Icon className="w-3 h-3" aria-hidden="true" />}
      <span>{label}</span>
    </button>
  );
});
