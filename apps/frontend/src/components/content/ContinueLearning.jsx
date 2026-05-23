import { Link } from 'react-router-dom';
import { ArrowLeft, ArrowRight, Layers, Sparkles, ChevronRight, BookOpen } from 'lucide-react';

/**
 * Reusable "Continue learning" block used on Chapter, Learn and PYQ pages
 * to deepen sessions through visible internal links.
 *
 * Props:
 *   prev:        { title, path } | null
 *   next:        { title, path } | null
 *   related:     [{ id, title, seo_path }]   // up to ~6
 *   subjectName: string  (for headings & CTA copy)
 *   subjectPath: string  (e.g. "/ahsec/class-12/physics") — link back to subject
 *   chatHref:    string  (Ask AI deep link)
 *   contentLang: 'en' | 'as'
 */
export default function ContinueLearning({
  prev,
  next,
  related = [],
  subjectName = '',
  subjectPath = '',
  chatHref = '/chat',
  contentLang = 'en',
}) {
  const isAS = contentLang === 'as';
  const hasPrevNext = !!(prev || next);
  const safeRelated = (Array.isArray(related) ? related : []).filter(
    (r) => r && r.seo_path && r.seo_path !== '#'
  );
  const hasRelated = safeRelated.length > 0;
  if (!hasPrevNext && !hasRelated && !subjectPath) return null;

  return (
    <section
      aria-label={isAS ? 'অধ্যয়ন চলাই থাকক' : 'Continue learning'}
      className="mt-8 rounded-2xl border border-violet-500/15 overflow-hidden"
      data-testid="continue-learning"
    >
      <div
        className="px-5 py-3.5 border-b border-violet-500/10 flex items-center gap-2"
        style={{ background: 'rgba(139,92,246,0.05)' }}
      >
        <Layers size={15} className="text-violet-500" />
        <span className="text-sm font-bold text-foreground">
          {isAS ? 'অধ্যয়ন চলাই থাকক' : 'Continue learning'}
        </span>
        {subjectName && (
          <>
            <span className="text-muted-foreground/40 text-xs">·</span>
            <span className="text-xs text-muted-foreground">{subjectName}</span>
          </>
        )}
      </div>

      <div className="p-4 sm:p-5 space-y-4" style={{ background: 'hsl(var(--card))' }}>
        {hasPrevNext && (
          <nav
            aria-label={isAS ? 'অধ্যায়ৰ পথপ্ৰদৰ্শন' : 'Chapter navigation'}
            className="grid grid-cols-1 sm:grid-cols-2 gap-3"
            data-testid="prev-next-nav"
          >
            {prev ? (
              <Link
                to={prev.path}
                rel="prev"
                className="group flex items-start gap-3 px-4 py-3 rounded-xl border border-border/40 hover:border-violet-400/40 hover:bg-violet-500/5 transition-colors"
                data-testid="prev-chapter-link"
              >
                <ArrowLeft size={16} className="mt-1 text-violet-500 shrink-0" />
                <div className="min-w-0">
                  <div className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                    {isAS ? 'পূৰ্বৰ অধ্যায়' : 'Previous chapter'}
                  </div>
                  <div className="text-sm font-medium text-foreground truncate group-hover:text-violet-600 transition-colors">
                    {prev.title}
                  </div>
                </div>
              </Link>
            ) : <div className="hidden sm:block" />}

            {next ? (
              <Link
                to={next.path}
                rel="next"
                className="group flex items-start gap-3 px-4 py-3 rounded-xl border border-border/40 hover:border-violet-400/40 hover:bg-violet-500/5 transition-colors sm:justify-end sm:text-right"
                data-testid="next-chapter-link"
              >
                <div className="min-w-0 sm:order-1">
                  <div className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                    {isAS ? 'পৰৱৰ্তী অধ্যায়' : 'Next chapter'}
                  </div>
                  <div className="text-sm font-medium text-foreground truncate group-hover:text-violet-600 transition-colors">
                    {next.title}
                  </div>
                </div>
                <ArrowRight size={16} className="mt-1 text-violet-500 shrink-0 sm:order-2" />
              </Link>
            ) : <div className="hidden sm:block" />}
          </nav>
        )}

        {hasRelated && (
          <div data-testid="related-list">
            <div className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground mb-2">
              {isAS ? 'সম্পৰ্কীয় বিষয়' : 'Related topics'}
            </div>
            <ul className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {safeRelated.slice(0, 6).map((rt) => (
                <li key={rt.id || rt.seo_path}>
                  <Link
                    to={rt.seo_path}
                    className="flex items-center justify-between gap-2 px-3 py-2.5 rounded-xl border border-border/30 hover:border-violet-400/40 hover:bg-violet-500/5 transition-colors text-sm text-foreground"
                  >
                    <span className="truncate">{rt.title}</span>
                    <ChevronRight size={14} className="text-muted-foreground/50 flex-shrink-0" />
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2 pt-1">
          <Link
            to={chatHref}
            className="inline-flex items-center gap-2 h-10 px-4 rounded-xl text-sm font-semibold text-white transition-all hover:opacity-90 active:scale-95"
            style={{ background: 'linear-gradient(135deg, #7c3aed, #8b5cf6)' }}
            data-testid="continue-chat-cta"
          >
            <Sparkles size={14} />
            {isAS
              ? `${subjectName ? subjectName + ' ' : ''}বিষয়ে Syra সোধক`
              : `Ask Syra${subjectName ? ' about ' + subjectName : ''}`}
          </Link>
          {subjectPath && (
            <Link
              to={subjectPath}
              className="inline-flex items-center gap-2 h-10 px-4 rounded-xl text-sm font-medium text-muted-foreground border border-border/40 hover:text-foreground hover:bg-accent/30 transition-colors"
              data-testid="continue-subject-link"
            >
              <BookOpen size={14} />
              {isAS ? 'সকলো অধ্যায় চাওক' : 'All chapters'}
            </Link>
          )}
          <Link
            to="/library"
            className="inline-flex items-center gap-2 h-10 px-4 rounded-xl text-sm font-medium text-muted-foreground border border-border/40 hover:text-foreground hover:bg-accent/30 transition-colors"
          >
            {isAS ? 'লাইব্ৰেৰীলৈ' : 'Browse library'}
            <ChevronRight size={14} />
          </Link>
        </div>
      </div>
    </section>
  );
}
