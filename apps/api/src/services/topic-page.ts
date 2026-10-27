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

export function stableQuestionId(question: JsonRecord): string {
  const existing = text(question.question_id) || text(question.id);
  if (existing) return existing;
  const source = text(question.question);
  if (!source) return '';
  // Stable, deterministic editor identity for legacy structured Q&A entries.
  let hash = 2166136261;
  for (const char of source.normalize('NFC').trim()) {
    hash ^= char.codePointAt(0) ?? 0;
    hash = Math.imul(hash, 16777619);
  }
  return `legacy-${(hash >>> 0).toString(36)}`;
}

function writeList(record: JsonRecord, singular: string, plural: string, entries: string[]) {
  delete record[singular];
  delete record[`linked_${singular}`];
  delete record[plural];
  if (entries.length) record[plural] = entries;
}

/** Replace only explicit topic-link metadata; question and answer text is untouched. */
export function replaceTopicQuestionLinks(
  questions: unknown[],
  topic: JsonRecord,
  selectedQuestionIds: string[],
  language: TopicPageLanguage,
): unknown[] {
  const identity = topicIdentity(topic, language);
  const selected = new Set(selectedQuestionIds);
  const topicId = identity.id || identity.slug;
  const slug = identity.slug;

  return questions.map((entry) => {
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) return entry;
    const question = { ...(entry as JsonRecord) };
    const stableId = stableQuestionId(question);
    const oldIds = [
      ...values(question.topic_id),
      ...values(question.linked_topic_id),
      ...values(question.topic_ids),
    ];
    const oldSlugs = [
      ...values(question.topic_slug),
      ...values(question.linked_topic_slug),
      ...values(question.topic_slugs),
    ];
    const isSelected = selected.has(stableId);
    const wasLinked = oldIds.includes(topicId) || oldSlugs.includes(slug);
    if (!isSelected && !wasLinked) return entry;
    const ids = oldIds.filter((id) => id !== topicId);
    const slugs = oldSlugs.filter((value) => value !== slug);
    if (isSelected) {
      if (topicId) ids.push(topicId);
      if (slug) slugs.push(slug);
      // Persist an ID only when the user explicitly links this legacy entry.
      if (!text(question.id) && !text(question.question_id)) question.question_id = stableId;
    }
    writeList(question, 'topic_id', 'topic_ids', [...new Set(ids)]);
    writeList(question, 'topic_slug', 'topic_slugs', [...new Set(slugs)]);
    return question;
  });
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
  if (!identity.slug || !identity.title || !identity.notes ||
      topic[`notes_status_${language}`] !== 'approved') return null;

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
