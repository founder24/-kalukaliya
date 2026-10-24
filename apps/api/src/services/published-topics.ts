const MIN_STANDALONE_TOPIC_WORDS = 40;

type PublishedTopic = Record<string, unknown>;

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function wordCount(value: string): number {
  return value.split(/\s+/).filter(Boolean).length;
}

/** Match backend indexability: reviewed, substantial, explicitly slugged topics. */
export function indexablePublishedTopics(input: unknown[]): PublishedTopic[] {
  const seen = new Set<string>();
  return input.flatMap((value) => {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return [];
    const topic = value as PublishedTopic;
    const definition = text(topic.definition) || text(topic.content);
    const title = text(topic.title) || text(topic.name);
    const slug = text(topic.topic_slug) || text(topic.slug);
    if (
      topic.definition_status !== 'ok' ||
      !title ||
      !slug ||
      wordCount(definition) < MIN_STANDALONE_TOPIC_WORDS
    ) return [];
    const identity = definition.toLowerCase().replace(/\s+/g, ' ').trim();
    if (seen.has(identity)) return [];
    seen.add(identity);
    return [{ ...topic, title, definition, topic_slug: slug }];
  });
}

export function completeAssameseTopic(topic: PublishedTopic): boolean {
  const title = text(topic.title_as);
  const slug = text(topic.topic_slug_as);
  const definition = text(topic.definition_as) || text(topic.content_as);
  return Boolean(title && slug && wordCount(definition) >= MIN_STANDALONE_TOPIC_WORDS);
}

export const MIN_TOPIC_WORDS = MIN_STANDALONE_TOPIC_WORDS;
