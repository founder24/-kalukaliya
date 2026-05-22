/**
 * SubjectTopicIndex — pillar-page topic index for SubjectLandingPage.
 *
 * Lists every published topic across every chapter under this subject,
 * grouped by chapter, each linking to the topic deep-link route added
 * in Task #914 (`/<board>/<class>/<stream?>/<subject>/<chapter>/topic/
 * <slug>`).
 *
 * Why this is here (not just on the chapter page):
 *   - The subject page is the topical-authority *pillar* — the one URL
 *     a search engine should associate with "everything about
 *     <subject>". Surfacing every citable topic from every chapter as
 *     real `<a>` tags gives the pillar an internal-linking graph that
 *     compounds authority back to each topic page.
 *   - Same DOM for SSR, prerender, and SPA — bots reading the linear
 *     HTML (no JS) get the full topic graph immediately.
 *
 * Renders nothing when the index is empty so brand-new subjects
 * without any citable topic don't show an empty pillar block.
 */
import { Link } from 'react-router-dom';
import { BookOpen } from 'lucide-react';

export default function SubjectTopicIndex({ index }) {
  const chapters = Array.isArray(index?.chapters) ? index.chapters : [];
  const total = Number(index?.total_topics || 0);
  if (chapters.length === 0 || total === 0) return null;

  return (
    <section
      data-testid="subject-topic-index"
      className="mt-10 mb-12 max-w-4xl mx-auto px-4"
      aria-labelledby="subject-topic-index-heading"
    >
      <header className="mb-5">
        <h2
          id="subject-topic-index-heading"
          className="text-lg sm:text-xl font-semibold text-foreground flex items-center gap-2"
        >
          <BookOpen size={18} className="text-purple-600" />
          All topics in this subject
          <span className="text-xs font-normal text-muted-foreground ml-1">
            ({total} citable topic{total === 1 ? '' : 's'})
          </span>
        </h2>
      </header>

      <div className="space-y-5">
        {chapters.map((ch) => (
          <div
            key={ch.chapter_id}
            data-topic-index-chapter
            className="rounded-xl border border-purple-200/40 bg-white/60 p-4 sm:p-5"
          >
            <Link
              to={ch.chapter_url}
              className="text-sm sm:text-base font-semibold text-foreground hover:text-purple-700 transition-colors"
            >
              {ch.chapter_title}
            </Link>
            <ul className="mt-3 flex flex-wrap gap-2">
              {ch.topics.map((t) => (
                <li key={t.topic_id || t.topic_slug}>
                  <Link
                    to={t.deep_link_path}
                    data-topic-index-link
                    className="inline-flex items-center px-3 py-1.5 rounded-full text-xs font-medium bg-purple-50 text-purple-700 hover:bg-purple-100 hover:text-purple-900 border border-purple-200/70 transition-colors no-underline"
                  >
                    {t.title}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}
