import { useMemo } from 'react';

const INDENT = { default: { 2: 'pl-0', 3: 'pl-4' }, card: { 1: 'pl-4 font-medium', 2: 'pl-6', 3: 'pl-8' } };

export default function StickyToc({
  headings,
  activeId,
  filterFn,
  getId = (h) => h.anchor ?? h.id,
  getLevel = (h) => h.level,
  label = 'On this page',
  labelIcon,
  onItemClick,
  variant = 'default',
  className = '',
  minItems = 2,
}) {
  const isCard = variant === 'card';

  const filtered = useMemo(() => {
    if (filterFn) return filterFn(headings);
    if (isCard) return headings;
    return headings.filter((h) => {
      const lvl = getLevel(h);
      return lvl === 2 || lvl === 3;
    });
  }, [headings, filterFn, getLevel, isCard]);

  if (filtered.length < minItems) return null;

  const indentMap = isCard ? INDENT.card : INDENT.default;

  if (isCard) {
    return (
      <nav
        className={`sticky top-6 w-56 flex-shrink-0 hidden xl:block ${className}`}
        aria-label="Table of contents"
      >
        <div className="rounded-2xl border border-border/20 overflow-hidden" style={{ background: 'hsl(var(--card))' }}>
          <div className="flex items-center gap-2 px-4 py-3 border-b border-border/20">
            {labelIcon}
            <span className="text-xs font-semibold text-muted-foreground/60 uppercase tracking-wider">{label}</span>
          </div>
          <ul className="py-2 max-h-[70vh] overflow-y-auto">
            {filtered.map((h) => {
              const id = getId(h);
              const level = getLevel(h);
              const isActive = activeId === id;
              return (
                <li key={id}>
                  <a
                    href={`#${id}`}
                    className={`block py-1.5 pr-4 text-xs transition-colors leading-snug ${
                      indentMap[level] || 'pl-4'
                    } ${
                      isActive
                        ? 'text-violet-400 border-r-2 border-violet-500'
                        : 'text-muted-foreground/50 hover:text-foreground/70'
                    }`}
                    onClick={(e) => {
                      e.preventDefault();
                      if (onItemClick) onItemClick(h);
                      document
                        .getElementById(id)
                        ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
                    }}
                  >
                    {h.text}
                  </a>
                </li>
              );
            })}
          </ul>
        </div>
      </nav>
    );
  }

  return (
    <nav
      className={`sticky top-20 w-56 shrink-0 hidden xl:block self-start ${className}`}
      aria-label="Table of contents"
    >
      <p className="text-[11px] font-semibold uppercase tracking-wider mb-3 text-muted-foreground/50">
        {label}
      </p>
      <ul className="space-y-0.5">
        {filtered.map((h) => {
          const id = getId(h);
          const level = getLevel(h);
          const isActive = activeId === id;
          return (
            <li key={id}>
              <a
                href={`#${id}`}
                className={`block py-1 text-[12px] leading-snug transition-colors rounded ${
                  indentMap[level] || 'pl-0'
                } ${
                  isActive
                    ? 'text-primary font-medium toc-active'
                    : 'text-muted-foreground/50 hover:text-foreground/70'
                }`}
                style={{
                  borderLeft:
                    level === 2
                      ? isActive
                        ? '2px solid #9575e0'
                        : '2px solid transparent'
                      : 'none',
                }}
                onClick={(e) => {
                  e.preventDefault();
                  if (onItemClick) onItemClick(h);
                  document
                    .getElementById(id)
                    ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
                }}
              >
                {h.text}
              </a>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
