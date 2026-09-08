import { describe, expect, it } from 'vitest';
import { serializePublicChapterList, type PublicChapterListRow } from './public-chapter-list';

const row: PublicChapterListRow = {
  id: 'chapter-1',
  title: ' Motion ',
  titleAs: 'গতি',
  slug: 'motion',
  slugAs: 'gati',
  chapterNumber: 1,
  status: 'published',
  notesEn: 'Notes',
  notesAs: 'টোকা',
  qaEn: '[{"question":"What is motion?"}]',
  publishedTopics: '[{"title":" Speed ","title_as":" দ্ৰুতি "},{"title":"Velocity"}]',
  pyqPdfUrl: null,
  pyqPapers: '[{"year":2025}]',
};

describe('serializePublicChapterList', () => {
  it('builds the complete public chapter shape', () => {
    expect(serializePublicChapterList([row])).toEqual([{
      id: 'chapter-1',
      chapter_id: 'chapter-1',
      title: ' Motion ',
      title_as: 'গতি',
      slug: 'motion',
      slug_as: 'gati',
      chapter_number: 1,
      status: 'published',
      notes_generated: true,
      has_assamese: true,
      has_qa: true,
      has_pyq: true,
      syllabus_topics: ['Speed', 'Velocity'],
      syllabus_topics_as: ['দ্ৰুতি', 'Velocity'],
      topic_count: 2,
      content_type: 'chapter',
    }]);
  });

  it('handles malformed stored JSON consistently', () => {
    expect(serializePublicChapterList([{
      ...row,
      status: null,
      qaEn: 'not-json',
      publishedTopics: '{}',
      pyqPapers: 'null',
      pyqPdfUrl: null,
    }])[0]).toMatchObject({
      status: 'draft',
      has_qa: false,
      has_pyq: false,
      syllabus_topics: [],
      syllabus_topics_as: [],
      topic_count: 0,
    });
  });
});