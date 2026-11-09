import { describe, expect, it } from 'vitest';
import {
  topicPageApiPath,
  topicPageMatchesRoute,
  topicPageUrl,
} from './topic-page-links';

describe('topic page routes', () => {
  it('uses the page resolver and preserves the requested language', () => {
    expect(topicPageApiPath('chapter/1', 'প্ৰথম নিয়ম', 'as')).toBe(
      '/content/chapters/chapter%2F1/topics/%E0%A6%AA%E0%A7%8D%E0%A7%B0%E0%A6%A5%E0%A6%AE%20%E0%A6%A8%E0%A6%BF%E0%A6%AF%E0%A6%BC%E0%A6%AE/page?lang=as',
    );
    expect(topicPageApiPath('chapter-1', 'first-law')).toBe(
      '/content/chapters/chapter-1/topics/first-law/page',
    );
  });

  it('only creates standalone links when the API says the page is complete', () => {
    const chapterPath = '/ahsec/hs-1st-year/physics/laws-of-motion';
    const topic = {
      topic_slug: 'newtons-first-law',
      topic_slug_as: 'নিউটনৰ-প্ৰথম-নিয়ম',
      topic_page_available_en: true,
      topic_page_available_as: true,
    };

    expect(topicPageUrl(topic, chapterPath, 'en')).toBe(
      `${chapterPath}/topic/newtons-first-law`,
    );
    expect(topicPageUrl(topic, chapterPath, 'as')).toBe(
      `${chapterPath}/topic/%E0%A6%A8%E0%A6%BF%E0%A6%89%E0%A6%9F%E0%A6%A8%E0%A7%B0-%E0%A6%AA%E0%A7%8D%E0%A7%B0%E0%A6%A5%E0%A6%AE-%E0%A6%A8%E0%A6%BF%E0%A6%AF%E0%A6%BC%E0%A6%AE`,
    );
    expect(topicPageUrl({ ...topic, topic_page_available_en: false }, chapterPath, 'en')).toBeNull();
    expect(topicPageUrl({ ...topic, topic_page_available_as: false }, chapterPath, 'as')).toBeNull();
  });

  it('matches a resolved page only to its topic slug and language', () => {
    const response = {
      chapter_id: 'chapter-1',
      topic: {
        topic_slug: 'newtons-first-law',
        topic_slug_as: 'নিউটনৰ-প্ৰথম-নিয়ম',
        language: 'as',
      },
    };

    expect(topicPageMatchesRoute(response, 'নিউটনৰ-প্ৰথম-নিয়ম', 'as', 'chapter-1')).toBe(true);
    expect(topicPageMatchesRoute(response, 'নিউটনৰ-প্ৰথম-নিয়ম', 'as', 'another-chapter')).toBe(false);
    expect(topicPageMatchesRoute(response, 'newtons-first-law', 'en', 'chapter-1')).toBe(false);
    expect(topicPageMatchesRoute(response, 'other-topic', 'as', 'chapter-1')).toBe(false);
  });
});
