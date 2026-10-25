export const TOPIC_TRAFFIC_INVENTORY_VERSION = 'topic-url-inventory-v1';
export const TOPIC_TRAFFIC_REPORT_VERSION = 'topic-traffic-report-v1';
export const TOPIC_TRAFFIC_TARGET_PER_DAY = 10;
export const TOPIC_TRAFFIC_TARGET_PER_7D = TOPIC_TRAFFIC_TARGET_PER_DAY * 7;
export const MIN_TOPIC_DEFINITION_WORDS = 40;
export const SITE_ORIGIN = 'https://syrabit.ai';

export type TopicInventorySourceRow = {
  chapter_id: string;
  chapter_title: string;
  chapter_slug: string;
  chapter_status: string | null;
  published_topics: string | null;
  subject_id: string | null;
  subject_name: string | null;
  subject_slug: string | null;
  subject_published: number | string | null;
  board_slug: string | null;
  class_slug: string | null;
};

export type TopicUrlInventoryItem = {
  topic_group_id: string;
  topic_id: string;
  topic_title: string | null;
  subject_id: string | null;
  subject_name: string | null;
  chapter_id: string;
  chapter_title: string;
  language: 'en' | 'as' | 'unknown';
  canonical_path: string | null;
  canonical_url: string | null;
  eligible: boolean;
  exclusion_reason: string | null;
};

type TopicRecord = Record<string, unknown>;

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function wordCount(value: string): number {
  return value.split(/\s+/).filter(Boolean).length;
}

function routeBase(row: TopicInventorySourceRow): string | null {
  if (!row.board_slug || !row.class_slug || !row.subject_slug || !row.chapter_slug) {
    return null;
  }
  return `/${row.board_slug}/${row.class_slug}/${row.subject_slug}/${row.chapter_slug}`;
}

function exclusionForTopic(
  row: TopicInventorySourceRow,
  topic: TopicRecord,
  title: string,
  slug: string,
  definition: string,
): string | null {
  if (row.chapter_status !== 'published') return 'chapter_unpublished';
  if (!row.subject_id) return 'subject_missing';
  if (Number(row.subject_published) !== 1) return 'subject_unpublished';
  if (!routeBase(row)) return 'canonical_hierarchy_incomplete';
  if (topic.definition_status !== 'ok') return 'topic_not_reviewed';
  if (!title || !slug) return 'topic_title_or_slug_missing';
  if (wordCount(definition) < MIN_TOPIC_DEFINITION_WORDS) {
    return 'topic_definition_under_40_words';
  }
  return null;
}

function candidate(
  row: TopicInventorySourceRow,
  topic: TopicRecord,
  index: number,
  language: 'en' | 'as',
  title: string,
  slug: string,
  baseReason: string | null,
  topicIsAssameseOnly: boolean,
  seenPaths: Set<string>,
): TopicUrlInventoryItem {
  const topicId = text(topic.topic_id) || text(topic.id) || text(topic.topicId)
    || text(topic.topic_slug) || text(topic.slug) || `topic-${index + 1}`;
  const topicGroupId = `${row.chapter_id}:${topicId}`;
  const base = routeBase(row);
  const initialPathSlug = language === 'as' && !topicIsAssameseOnly
    ? text(topic.topic_slug_as)
    : slug;
  let canonicalPath: string | null = base && initialPathSlug
    ? `${topicIsAssameseOnly || language === 'as' ? '/as' : ''}${base}/topic/${encodeURIComponent(initialPathSlug)}`
    : null;

  let reason = baseReason;
  if (!reason && language === 'en' && topicIsAssameseOnly) {
    reason = 'assamese_only_topic';
  }
  if (!reason && language === 'as' && !topicIsAssameseOnly) {
    const asTitle = text(topic.title_as);
    const asSlug = text(topic.topic_slug_as);
    const asDefinition = text(topic.definition_as) || text(topic.content_as);
    if (
      !asTitle ||
      !asSlug ||
      wordCount(asDefinition) < MIN_TOPIC_DEFINITION_WORDS
    ) {
      reason = 'assamese_translation_incomplete';
    } else {
      canonicalPath = base
        ? `/as${base}/topic/${encodeURIComponent(asSlug)}`
        : null;
    }
  }
  if (!reason && !canonicalPath) reason = 'canonical_url_unavailable';
  if (!reason && canonicalPath && seenPaths.has(canonicalPath)) {
    reason = 'duplicate_canonical_url';
  }
  if (!reason && canonicalPath) seenPaths.add(canonicalPath);

  return {
    topic_group_id: topicGroupId,
    topic_id: topicId,
    topic_title: title || null,
    subject_id: row.subject_id,
    subject_name: row.subject_name,
    chapter_id: row.chapter_id,
    chapter_title: row.chapter_title,
    language,
    canonical_path: canonicalPath,
    canonical_url: canonicalPath ? `${SITE_ORIGIN}${canonicalPath}` : null,
    eligible: reason === null,
    exclusion_reason: reason,
  };
}

/**
 * Build the report inventory from the same D1 published_topics source and
 * eligibility rules as the public topic sitemap. Every topic produces both a
 * language candidate so incomplete translations and unpublished pages stay
 * visible rather than silently disappearing from the denominator.
 */
export function buildTopicUrlInventory(
  rows: TopicInventorySourceRow[],
): TopicUrlInventoryItem[] {
  const inventory: TopicUrlInventoryItem[] = [];
  const seenPaths = new Set<string>();

  for (const row of rows) {
    const seenDefinitions = new Set<string>();
    let topics: unknown;
    try {
      topics = JSON.parse(row.published_topics ?? '[]');
    } catch {
      topics = null;
    }
    if (!Array.isArray(topics)) {
      inventory.push({
        topic_group_id: `${row.chapter_id}:invalid-topic-json`,
        topic_id: `invalid-topic-json-${row.chapter_id}`,
        topic_title: null,
        subject_id: row.subject_id,
        subject_name: row.subject_name,
        chapter_id: row.chapter_id,
        chapter_title: row.chapter_title,
        language: 'unknown',
        canonical_path: null,
        canonical_url: null,
        eligible: false,
        exclusion_reason: 'topic_json_invalid',
      });
      continue;
    }

    topics.forEach((value, index) => {
      if (!value || typeof value !== 'object' || Array.isArray(value)) {
        inventory.push({
          topic_group_id: `${row.chapter_id}:invalid-topic-${index + 1}`,
          topic_id: `invalid-topic-${index + 1}`,
          topic_title: null,
          subject_id: row.subject_id,
          subject_name: row.subject_name,
          chapter_id: row.chapter_id,
          chapter_title: row.chapter_title,
          language: 'unknown',
          canonical_path: null,
          canonical_url: null,
          eligible: false,
          exclusion_reason: 'topic_record_invalid',
        });
        return;
      }

      const topic = value as TopicRecord;
      const title = text(topic.title) || text(topic.name);
      const slug = text(topic.topic_slug) || text(topic.slug);
      const definition = text(topic.definition) || text(topic.content);
      let baseReason = exclusionForTopic(row, topic, title, slug, definition);
      if (!baseReason) {
        const identity = definition.toLowerCase().replace(/\s+/g, ' ').trim();
        if (seenDefinitions.has(identity)) {
          baseReason = 'duplicate_topic_definition';
        } else {
          seenDefinitions.add(identity);
        }
      }

      const topicIsAssameseOnly = topic.language === 'Assamese';
      const english = candidate(
        row, topic, index, 'en', title, slug, baseReason, topicIsAssameseOnly, seenPaths,
      );
      const asTitle = text(topic.title_as);
      const asSlug = text(topic.topic_slug_as) || (topicIsAssameseOnly ? slug : '');
      const assamese = candidate(
        row,
        topic,
        index,
        'as',
        asTitle || (topicIsAssameseOnly ? title : ''),
        asSlug,
        baseReason,
        topicIsAssameseOnly,
        seenPaths,
      );
      inventory.push(english, assamese);
    });
  }

  return inventory;
}

const SEARCH_REFERRER_DOMAINS = [
  'bing.com', 'duckduckgo.com', 'search.yahoo.com', 'yandex.com',
  'baidu.com', 'brave.com', 'ecosia.org', 'startpage.com', 'qwant.com',
];

const AI_REFERRER_DOMAINS = [
  'chatgpt.com', 'chat.openai.com', 'perplexity.ai', 'claude.ai',
  'gemini.google.com', 'copilot.microsoft.com', 'you.com', 'phind.com', 'poe.com',
];

function matchesDomain(hostname: string, domains: string[]): boolean {
  return domains.some((domain) => hostname === domain || hostname.endsWith(`.${domain}`));
}

export function classifyDiscoveryReferrer(
  value: unknown,
): 'organic_search' | 'ai_referral' | 'other_referral' | 'direct' | 'unknown' {
  if (typeof value !== 'string' || !value.trim()) return 'direct';
  try {
    const hostname = new URL(value).hostname.toLowerCase().replace(/^www\./, '');
    if (!hostname) return 'unknown';
    if (matchesDomain(hostname, AI_REFERRER_DOMAINS)) return 'ai_referral';
    if (/^google\.[a-z]{2,3}(?:\.[a-z]{2})?$/.test(hostname) || matchesDomain(hostname, SEARCH_REFERRER_DOMAINS)) {
      return 'organic_search';
    }
    if (hostname === 'syrabit.ai' || hostname.endsWith('.syrabit.ai')) return 'direct';
    return 'other_referral';
  } catch {
    return 'unknown';
  }
}

export function targetStatus(
  value: number | null,
  target: number,
  options: { eligible: boolean; complete: boolean },
): 'not_eligible' | 'not_available' | 'in_progress' | 'met' | 'below_target' {
  if (!options.eligible) return 'not_eligible';
  if (value === null) return 'not_available';
  if (!options.complete) return 'in_progress';
  return value >= target ? 'met' : 'below_target';
}

export function wordsInTopicDefinition(value: string): number {
  return wordCount(value);
}

