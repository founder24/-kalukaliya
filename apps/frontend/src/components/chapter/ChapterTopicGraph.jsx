/**
 * ChapterTopicGraph — sibling + cross-chapter related-topic blocks.
 *
 * Topical-mapping surface (Task: topical mapping + topical authority).
 * Renders BELOW the AI answer cards on every chapter page so:
 *   1. Bots reading the linear DOM see a structured internal-linking
 *      graph that stays consistent for SSR / prerender / SPA — same
 *      `<a href>`s in every render path (no JS-only nav).
 *   2. Humans get a "More topics in this chapter" rail and a
 *      "Related across the syllabus" rail with real, navigable links.
 *
 * Both rails point at canonical surfaces:
 *   - Sibling links use `#topic-<slug>` (same-page anchor → answer card).
 *   - Cross-chapter links use the topic deep-link path that the SPA
 *     route added in Task #914 already 200s with the right canonical.
 *
 * The component renders nothing when both arrays are empty so chapters
 * with a single topic don't show empty rails.
 */
import { Link } from 'react-router-dom';
import { ArrowRight } from 'lucide-react';

function GraphPill({ to, title, chapterTitle, isAnchor }) {
  // Anchor links (#topic-<slug>) use a plain <a> so the browser handles
  // the in-page jump. Cross-route links use react-router's <Link> so
  // the SPA navigation stays single-page.
  const className =
    'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium ' +
    'bg-purple-50 text-purple-700 hover:bg-purple-100 hover:text-purple-900 ' +
    'border border-purple-200/70 transition-colors no-underline';
  if (isAnchor) {
    return (
      <a href={to} className={className} data-topic-graph-link="sibling">
        {title}
        {chapterTitle && chapterTitle !== title ? (
          <span className="text-purple-500/70 font-normal">· {chapterTitle}</span>
        ) : null}
      </a>
    );
  }
  return (
    <Link to={to} className={className} data-topic-graph-link="cross-chapter">
      {title}
      {chapterTitle && chapterTitle !== title ? (
        <span className="text-purple-500/70 font-normal">· {chapterTitle}</span>
      ) : null}
    </Link>
  );
}

export default function ChapterTopicGraph({ siblings = [], crossChapter = [] }) {
  const hasSiblings = Array.isArray(siblings) && siblings.length > 0;
  const hasCross = Array.isArray(crossChapter) && crossChapter.length > 0;
  if (!hasSiblings && !hasCross) return null;

  return (
    <section
      data-testid="chapter-topic-graph"
      className="mt-10 mb-8 space-y-6"
      aria-label="Related topics"
    >
      {hasSiblings && (
        <aside data-topic-graph="siblings">
          <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide mb-3">
            More topics in this chapter
          </h2>
          <div className="flex flex-wrap gap-2">
            {siblings.map((t) => (
              <GraphPill
                key={`sib-${t.topic_id || t.topic_slug}`}
                to={`#topic-${t.topic_slug}`}
                title={t.title}
                isAnchor
              />
            ))}
          </div>
        </aside>
      )}

      {hasCross && (
        <aside data-topic-graph="cross-chapter">
          <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide mb-3 flex items-center gap-1.5">
            Related across the syllabus
            <ArrowRight size={12} className="opacity-60" />
          </h2>
          <div className="flex flex-wrap gap-2">
            {crossChapter.map((t) => (
              <GraphPill
                key={`cross-${t.topic_id || t.topic_slug}`}
                to={t.deep_link_path}
                title={t.title}
                chapterTitle={t.chapter_title}
              />
            ))}
          </div>
        </aside>
      )}
    </section>
  );
}
