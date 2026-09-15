import { describe, expect, it } from 'vitest';
import { serializePublicChapterList, type PublicChapterListRow } from './public-chapter-list';

const row: PublicChapterListRow = {
  id: 'chapter-1',
  title: ' Motion ',
  titleAs: 'গতি',
  slug: 'motion',
  slugAs: 'gati',
  metaDescription: 'Motion is the change in position of an object.',
  metaDescriptionAs: 'গতি হৈছে কোনো বস্তুৰ অৱস্থানৰ পৰিৱৰ্তন।',
  chapterNumber: 1,
  status: 'published',
  notesEn: 'Generated chapter notes',
  notesAs: 'উৎপাদিত অধ্যায়ৰ টোকা',
  qaEn: '[{"question":"What is motion?"}]',
  qaAs: '[]',
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
      description: 'Motion is the change in position of an object.',
      description_as: 'গতি হৈছে কোনো বস্তুৰ অৱস্থানৰ পৰিৱৰ্তন।',
      slug: 'motion',
      slug_as: 'gati',
      chapter_number: 1,
      status: 'published',
      notes_generated: true,
      has_assamese: true,
      has_qa: true,
      has_qa_as: false,
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
      qaAs: 'not-json',
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

  it('tracks Assamese-only Q&A independently from English Q&A', () => {
    const [assameseOnly, englishOnly] = serializePublicChapterList([
      { ...row, id: 'assamese-only', qaEn: '[]', qaAs: '[{"question":"প্ৰশ্ন"}]' },
      { ...row, id: 'english-only', qaEn: '[{"question":"Question"}]', qaAs: '[]' },
    ]);

    expect(assameseOnly).toMatchObject({ has_qa: false, has_qa_as: true });
    expect(englishOnly).toMatchObject({ has_qa: true, has_qa_as: false });
  });

  it('only advertises notes when meaningful content exists', () => {
    const [placeholder, content] = serializePublicChapterList([
      { ...row, id: 'placeholder', notesEn: '   1234567890   ', notesAs: '   ' },
      { ...row, id: 'content', notesEn: '12345678901', notesAs: 'অসমীয়া বিষয়বস্তু' },
    ]);

    expect(placeholder).toMatchObject({ notes_generated: false, has_assamese: false });
    expect(content).toMatchObject({ notes_generated: true, has_assamese: true });
  });
});