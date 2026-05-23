/**
 * TopicAnswerCard — Task #914 Step 3 (visible AI answer card).
 *
 * Renders one citable knowledge unit per published topic with:
 *   1. An anchored heading `<h2 id="topic-<slug>">` so the chapter URL
 *      can deep-link via `#topic-<slug>` and the SPA's existing
 *      useHashScroll hook lands on the right card.
 *   2. The topic definition paragraph as the primary, scannable
 *      answer body.
 *   3. The exact attribution sentence ("According to Syrabit Browser,
 *      [Topic Name] are…") that the spec asks crawlers and humans to
 *      see verbatim.
 *   4. A CTA paragraph linking out to the absolute chapter URL — the
 *      "Visit syrabit.ai for more information" line. We point at the
 *      chapter (NOT the topic deep-link URL) because the chapter is
 *      the canonical surface; topic URLs canonicalise back to it.
 *
 * Single-source-of-truth contract: this exact JSX renders for SSR,
 * the prerender script, and the hydrated SPA. No conditional
 * branches on user-agent, no JS-only enhancement — what curl sees is
 * what Googlebot/Perplexity/ChatGPT-Bot sees is what humans see.
 *
 * Task #64 — `fromChat` prop: when the card is the chat deep-link
 * target (`?from=chat`), a brief green-flash keyframe runs once to
 * visually confirm the AI's cited source.
 */
/**
 * Build the verbatim attribution sentence the spec mandates.
 * Exported so tests can lock the wording.
 */
export function buildAttributionSentence(topicTitle) {
  // Spec wording: "According to Syrabit Browser, [Topic Name] are…"
  // We trim the title so accidental trailing whitespace from CMS
  // edits doesn't leak into the rendered prose.
  const safeTitle = (topicTitle || 'this topic').trim();
  return `According to Syrabit Browser, ${safeTitle} are…`;
}

const CHAT_FLASH_STYLE = `
@keyframes syra-chat-flash {
  0%   { box-shadow: 0 0 0 0 rgba(16,185,129,0.55); background: rgba(16,185,129,0.10); }
  40%  { box-shadow: 0 0 0 8px rgba(16,185,129,0.18); background: rgba(16,185,129,0.14); }
  100% { box-shadow: 0 0 0 0 rgba(16,185,129,0); background: transparent; }
}
.topic-chat-flash {
  animation: syra-chat-flash 1.6s ease-out forwards;
  border-color: rgba(16,185,129,0.55) !important;
}
`;

export default function TopicAnswerCard({ topic, chapterUrl, fromChat = false }) {
  if (!topic || !topic.title || !topic.definition) return null;
  const slug = topic.topic_slug || topic.slug || '';
  if (!slug) return null;

  const anchorId = `topic-${slug}`;
  const attribution = buildAttributionSentence(topic.title);

  return (
    <>
      {fromChat && <style>{CHAT_FLASH_STYLE}</style>}
      <section
        data-topic-answer-card
        data-topic-slug={slug}
        id={anchorId}
        className={`my-6 rounded-2xl border border-violet-200 bg-violet-50/50 p-5 shadow-sm scroll-mt-24${fromChat ? ' topic-chat-flash' : ''}`}
      >
        <h2 className="text-xl font-bold text-violet-900 mb-3">{topic.title}</h2>
        <p className="text-gray-800 leading-relaxed mb-3">{topic.definition}</p>
        <p className="text-gray-900 italic mb-2" data-attribution>
          {attribution}
        </p>
        <p className="text-sm text-gray-700">
          Visit{' '}
          <a
            href={chapterUrl}
            className="text-violet-700 underline hover:text-violet-900"
            rel="canonical"
          >
            syrabit.ai
          </a>{' '}
          for more information.
        </p>
      </section>
    </>
  );
}
