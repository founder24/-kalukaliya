/**
 * Tasks #398 / #409 — shared builder for the chat payload's
 * `card_context` field (consumed by the backend's "library"
 * grounding branch in rag.py `build_rag_system_prompt`).
 *
 * Two callers feed into this:
 *
 *   1. ChatPage's `?subject=…&chapter=…` URL params load the subject
 *      + its chapters and produce the syllabus + active-chapter
 *      summary (the SubjectCard / SubjectPage / SubjectLandingPage
 *      / ChapterPage Ask-AI buttons all route through this path).
 *
 *   2. Pages that have richer page-card content link to /chat with
 *      a `seedContext` string passed via react-router Link `state`
 *      (currently PersonalizedCmsPage's "Ask AI about this plan"
 *      button). The seed is treated as the highest-priority block
 *      and prepended above the subject summary so the LLM treats
 *      the originating page's content as authoritative — matches
 *      the parent-page-and-content-card prioritisation contract
 *      the rag.py library branch enforces.
 *
 * Returns null when there is nothing to ground on (no subject and
 * no seed) so the chat call falls back to its non-grounded path.
 */
export function buildCardContext({
  subject,
  scopedChapters = [],
  activeChapter = null,
  user = null,
  seedContext = '',
} = {}) {
  const seed = (seedContext || '').trim();
  if (!subject && !seed) return null;

  const lines = [];
  if (seed) lines.push(seed);

  if (subject) {
    if (seed) lines.push('');
    lines.push(`Subject: ${subject.name}`);
    if (subject.description) lines.push(`Description: ${subject.description}`);
    if (Array.isArray(subject.tags) && subject.tags.length) {
      lines.push(`Topics covered: ${subject.tags.join(', ')}`);
    }

    const rawBoard = (user?.board_name || '').trim();
    const boardLabel = rawBoard || null;
    const parts = [boardLabel, user?.class_name, user?.stream_name].filter(Boolean);
    if (parts.length) lines.push(`Board/Class: ${parts.join(' | ')}`);

    // When a specific chapter is active, surface its full content
    // first so the LLM and the vector retrieval both weight it
    // highest — same wording the original ChatPage useMemo used so
    // the rag.py library branch (which keys off "Active chapter
    // (priority context):") keeps working.
    if (activeChapter) {
      lines.push('');
      lines.push(`Active chapter (priority context): ${activeChapter.title}`);
      if (activeChapter.description) lines.push(`Description: ${activeChapter.description}`);
      if (activeChapter.content) lines.push(activeChapter.content.slice(0, 1200));
      lines.push('');
      lines.push('Other chapters in this subject:');
    } else if (scopedChapters.length) {
      lines.push('');
      lines.push('Syllabus chapters:');
    }

    scopedChapters
      .slice()
      .sort((a, b) => (a.order_index ?? a.order ?? 0) - (b.order_index ?? b.order ?? 0))
      .forEach((ch, i) => {
        if (activeChapter && ch.id === activeChapter.id) return;
        const num = ch.chapter_number ?? ch.order_index ?? i + 1;
        let entry = `Chapter ${num} — ${ch.title}`;
        if (ch.description) entry += `: ${ch.description}`;
        if (ch.content) entry += `\n${ch.content.slice(0, 400)}`;
        lines.push(entry);
      });
  }

  // Same 4_000-char cap as the original useMemo — keeps the payload
  // under the backend's library-branch budget so a long syllabus
  // doesn't push the system prompt past `_PROMPT_CAP`.
  return lines.join('\n').slice(0, 4000);
}

/**
 * Build a `seedContext` string from a personalised study-plan
 * document (PersonalizedCmsPage). Strips inline HTML from
 * `content_html` when raw `content` is unavailable so the seed
 * doesn't leak `<p>` / `<h2>` markup into the LLM prompt, then
 * truncates to a reasonable budget that leaves room for the
 * backend's library-branch boilerplate.
 *
 * The header line "PERSONALIZED STUDY PLAN (priority context):"
 * mirrors the rag.py library branch's wording so on-call can grep
 * for it in chat logs when a plan-scoped turn answers off-topic.
 */
export function buildPlanSeedContext(doc) {
  if (!doc || typeof doc !== 'object') return '';
  const parts = ['PERSONALIZED STUDY PLAN (priority context):'];
  if (doc.title) parts.push(`Title: ${doc.title}`);
  if (doc.subject_name) parts.push(`Subject: ${doc.subject_name}`);
  if (doc.days) parts.push(`Plan length: ${doc.days}-day sprint`);
  if (Array.isArray(doc.weak_topics) && doc.weak_topics.length) {
    parts.push(`Weak topics flagged for review: ${doc.weak_topics.join(', ')}`);
  }
  const rawContent =
    (typeof doc.content === 'string' && doc.content) ||
    (typeof doc.content_html === 'string'
      ? doc.content_html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()
      : '');
  if (rawContent) {
    parts.push('');
    parts.push('Plan content:');
    parts.push(rawContent.slice(0, 2000));
  }
  return parts.join('\n');
}
