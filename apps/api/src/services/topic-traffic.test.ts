import { describe, expect, it } from 'vitest';
import {
  buildTopicUrlInventory,
  classifyDiscoveryReferrer,
  targetStatus,
} from './topic-traffic';
import type { TopicInventorySourceRow } from './topic-traffic';

const definition = Array.from({ length: 42 }, (_, index) => `concept${index}`).join(' ');

function sourceRow(overrides: Partial<TopicInventorySourceRow> = {}): TopicInventorySourceRow {
  return {
    chapter_id: 'chapter-1',
    chapter_title: 'Chapter one',
    chapter_slug: 'chapter-one',
    chapter_status: 'published',
    published_topics: JSON.stringify([{
      id: 'topic-1',
      title: 'A studied topic',
      topic_slug: 'studied-topic',
      definition_status: 'ok',
      definition,
    }]),
    subject_id: 'subject-1',
    subject_name: 'Economics',
    subject_slug: 'economics',
    subject_published: 1,
    board_slug: 'ahsec',
    class_slug: 'hs-2nd-year',
    ...overrides,
  };
}

describe('topic traffic inventory and metric definitions', () => {
  it('keeps English and Assamese URL candidates separate and marks incomplete translation excluded', () => {
    const rows = buildTopicUrlInventory([sourceRow({
      published_topics: JSON.stringify([{
        id: 'topic-1',
        title: 'A studied topic',
        topic_slug: 'studied-topic',
        definition_status: 'ok',
        definition,
        title_as: 'অসমীয়া শিৰোনাম',
      }]),
    })]);

    expect(rows).toHaveLength(2);
    expect(rows[0]).toMatchObject({
      language: 'en',
      canonical_url: 'https://syrabit.ai/ahsec/hs-2nd-year/economics/chapter-one/topic/studied-topic',
      eligible: true,
      exclusion_reason: null,
    });
    expect(rows[1]).toMatchObject({
      language: 'as',
      canonical_path: null,
      eligible: false,
      exclusion_reason: 'assamese_translation_incomplete',
    });
  });

  it('includes only reviewed substantial topics in the eligible inventory and exposes duplicate candidates', () => {
    const rows = buildTopicUrlInventory([sourceRow({
      published_topics: JSON.stringify([
        {
          id: 'topic-1',
          title: 'First topic',
          topic_slug: 'first-topic',
          definition_status: 'ok',
          definition,
        },
        {
          id: 'topic-2',
          title: 'Duplicate topic',
          topic_slug: 'duplicate-topic',
          definition_status: 'ok',
          definition: ` ${definition.toUpperCase()} `,
        },
        {
          id: 'topic-3',
          title: 'Draft topic',
          topic_slug: 'draft-topic',
          definition_status: 'pending',
          definition,
        },
      ]),
    })]);

    expect(rows.filter(row => row.eligible).map(row => row.canonical_path)).toEqual([
      '/ahsec/hs-2nd-year/economics/chapter-one/topic/first-topic',
    ]);
    expect(rows.filter(row => row.exclusion_reason === 'duplicate_topic_definition')).toHaveLength(2);
    expect(rows.filter(row => row.exclusion_reason === 'topic_not_reviewed')).toHaveLength(2);
  });

  it('exposes short content and duplicate canonical URLs as exclusions', () => {
    const rows = buildTopicUrlInventory([sourceRow({
      published_topics: JSON.stringify([
        {
          id: 'long-topic',
          title: 'Long topic',
          topic_slug: 'shared-topic-path',
          definition_status: 'ok',
          definition,
        },
        {
          id: 'same-path',
          title: 'Different topic with same URL',
          topic_slug: 'shared-topic-path',
          definition_status: 'ok',
          definition: definition.replace('concept0', 'distinct0'),
        },
        {
          id: 'short-topic',
          title: 'Short topic',
          topic_slug: 'short-topic',
          definition_status: 'ok',
          definition: 'Only a few words.',
        },
      ]),
    })]);

    expect(rows.filter(row => row.exclusion_reason === 'duplicate_canonical_url')).toHaveLength(1);
    expect(rows.filter(row => row.exclusion_reason === 'topic_definition_under_40_words')).toHaveLength(2);
    expect(rows.filter(row => row.eligible)).toHaveLength(1);
  });

  it('matches the public sitemap duplicate scope per chapter and displays unpublished pages', () => {
    const sameTopic = JSON.stringify([{
      id: 'topic-1',
      title: 'A studied topic',
      topic_slug: 'studied-topic',
      definition_status: 'ok',
      definition,
    }]);
    const rows = buildTopicUrlInventory([
      sourceRow({ published_topics: sameTopic }),
      sourceRow({
        chapter_id: 'chapter-2',
        chapter_title: 'Chapter two',
        chapter_slug: 'chapter-two',
        published_topics: sameTopic,
      }),
      sourceRow({
        chapter_id: 'chapter-draft',
        chapter_title: 'Draft chapter',
        chapter_slug: 'draft-chapter',
        chapter_status: 'draft',
        published_topics: sameTopic,
      }),
    ]);

    expect(rows.filter(row => row.eligible)).toHaveLength(2);
    expect(rows.filter(row => row.exclusion_reason === 'chapter_unpublished')).toHaveLength(2);
    expect(rows.some(row => row.canonical_path?.includes('/draft-chapter/'))).toBe(true);
  });

  it('keeps malformed topic data visible instead of treating it as an empty inventory', () => {
    expect(buildTopicUrlInventory([sourceRow({ published_topics: '{bad json' })])).toMatchObject([
      { language: 'unknown', eligible: false, exclusion_reason: 'topic_json_invalid' },
    ]);
  });

  it('classifies only known search and AI provider hosts', () => {
    expect(classifyDiscoveryReferrer('https://www.google.com/search?q=topic')).toBe('organic_search');
    expect(classifyDiscoveryReferrer('https://www.google.co.in/search?q=topic')).toBe('organic_search');
    expect(classifyDiscoveryReferrer('https://www.perplexity.ai/')).toBe('ai_referral');
    expect(classifyDiscoveryReferrer('https://gemini.google.com/')).toBe('ai_referral');
    expect(classifyDiscoveryReferrer('https://notgoogle.com/')).toBe('other_referral');
    expect(classifyDiscoveryReferrer('')).toBe('direct');
    expect(classifyDiscoveryReferrer('not a URL')).toBe('unknown');
  });

  it('does not mark partial, unavailable, or excluded traffic as meeting the target', () => {
    expect(targetStatus(10, 10, { eligible: true, complete: true })).toBe('met');
    expect(targetStatus(9, 10, { eligible: true, complete: true })).toBe('below_target');
    expect(targetStatus(12, 10, { eligible: true, complete: false })).toBe('in_progress');
    expect(targetStatus(null, 10, { eligible: true, complete: true })).toBe('not_available');
    expect(targetStatus(50, 10, { eligible: false, complete: true })).toBe('not_eligible');
  });
});

