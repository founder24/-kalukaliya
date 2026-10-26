export type TopicPageLanguage = 'en' | 'as';

type JsonRecord = Record<string, unknown>;

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function values(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(text).filter(Boolean);
  const single = text(value);
  return single ? [single] : [];
}

function topicIdentity(topic: JsonRecord, language: TopicPageLanguage) {
  return {
    id: text(topic.topic_id) || text(topic.id),
    slug: language === 'as'
      ? text(topic.topic_slug_as)
      : text(topic.topic_slug) || text(topic.slug),
    title: language === 'as' ? text(topic.title_as) : text(topic.title),
    notes: language === 'as' ? text(topic.notes_as) : text(topic.notes_en),
  };
}

function questionMatchesTopic(question: JsonRecord, topic: JsonRecord, language: TopicPageLanguage): boolean {
  const { id, slug } = topicIdentity(topic, language);
  const topicIds = [
    ...values(question.topic_id),
    ...values(question.linked_topic_id),
    ...values(question.topic_ids),
  ];
  const topicSlugs = [
    ...values(question.topic_slug),
    ...values(question.linked_topic_slug),
    ...values(question.topic_slugs),
  ];
  return Boolean(
    (id && topicIds.includes(id)) ||
    (slug && topicSlugs.includes(slug)),
  );
}

export function resolveTopicPageContent(
  topic: JsonRecord,
  questions: unknown[],
  language: TopicPageLanguage,
) {
  const identity = topicIdentity(topic, language);
  if (!identity.slug || !identity.title || !identity.notes) return null;

  const linkedQuestions = questions
    .filter((value): value is JsonRecord => Boolean(value && typeof value === 'object' && !Array.isArray(value)))
    .filter((question) => questionMatchesTopic(question, topic, language))
    .filter((question) => text(question.question) && (text(question.solution) || text(question.answer)));

  if (linkedQuestions.length === 0) return null;
  return {
    topic_id: identity.id || identity.slug,
    topic_slug: identity.slug,
    title: identity.title,
    notes: identity.notes,
    language,
    questions: linkedQuestions,
  };
}

export function completeTopicPageLanguages(topic: JsonRecord, qaEn: unknown[], qaAs: unknown[]): TopicPageLanguage[] {
  const languages: TopicPageLanguage[] = [];
  if (resolveTopicPageContent(topic, qaEn, 'en')) languages.push('en');
  if (resolveTopicPageContent(topic, qaAs, 'as')) languages.push('as');
  return languages;
}
