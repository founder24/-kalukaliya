import { describe, expect, it } from 'vitest';
import { completeTopicPageLanguages, resolveTopicPageContent } from './topic-page';

const topic = {
  id: 'topic-modern-assam-1',
  title: 'Modern Assamese literature',
  topic_slug: 'modern-assamese-literature',
  notes_en: 'Approved notes about the topic.',
  notes_status_en: 'approved',
  title_as: 'আধুনিক অসমীয়া সাহিত্য',
  topic_slug_as: 'adhunik-asamiya-sahitya',
  notes_as: 'বিষয়টোৰ অনুমোদিত টোকা।',
  notes_status_as: 'approved',
};

describe('topic page source matching', () => {
  it('includes only questions explicitly linked by stable topic ID or slug', () => {
    const result = resolveTopicPageContent(topic, [
      { id: 'q1', topic_id: topic.id, question: 'What changed?', answer: 'A supported answer.' },
      { id: 'q2', topic_slug: topic.topic_slug, question: 'Why did it change?', solution: 'A supported solution.' },
      { id: 'q3', question: 'Modern Assamese literature', answer: 'Title similarity is not a link.' },
    ], 'en');

    expect(result?.questions.map((question) => question.id)).toEqual(['q1', 'q2']);
  });

  it('does not fall back to English notes or questions for Assamese pages', () => {
    expect(resolveTopicPageContent(topic, [], 'as')).toBeNull();
  });

  it('publishes each language only when its own notes and linked question source are complete', () => {
    const englishOnly = [{ topic_id: topic.id, question: 'Question', answer: 'Answer' }];
    const bothLanguages = [
      { topic_id: topic.id, question: 'English question', answer: 'English answer' },
    ];
    const withAssamese = [
      ...bothLanguages,
      { topic_id: topic.id, question: 'অসমীয়া প্ৰশ্ন', answer: 'অসমীয়া উত্তৰ' },
    ];

    expect(completeTopicPageLanguages(topic, englishOnly, [])).toEqual(['en']);
    expect(completeTopicPageLanguages(topic, englishOnly, withAssamese)).toEqual(['en', 'as']);
  });
});
