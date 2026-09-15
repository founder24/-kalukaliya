export const PUBLIC_ROUTE = '/ahsec/class-12/physics/public-image-fixture';
export const PAGE_ONE_URL = 'https://cdn.fixture.test/public-page-1.png';
export const PAGE_TWO_URL = 'https://cdn.fixture.test/public-page-2.png';
export const ASSAMESE_PAGE_ONE_URL = 'https://cdn.fixture.test/assamese-page-1.png';
export const ASSAMESE_PAGE_TWO_URL = 'https://cdn.fixture.test/assamese-page-2.png';

export const fixtureChapter = {
  chapter_id: 'public-image-fixture',
  chapter_title: 'Image-backed chapter',
  title: 'Image-backed chapter',
  topic_title: 'Image-backed chapter',
  subject_name: 'Physics',
  subject_id: 'physics',
  board_name: 'AHSEC',
  class_name: 'Class 12',
  content_type: 'notes',
  content: [
    '# Image-backed notes',
    '',
    'These pages were saved by the staff chapter editor.',
    '',
    `![Page 1](${PAGE_ONE_URL})`,
    '',
    `![Page 2](${PAGE_TWO_URL})`,
  ].join('\n'),
  content_en: [
    '# Image-backed notes',
    '',
    'These pages were saved by the staff chapter editor.',
    '',
    `![Page 1](${PAGE_ONE_URL})`,
    '',
    `![Page 2](${PAGE_TWO_URL})`,
  ].join('\n'),
  notes_en: [
    '# Image-backed notes',
    '',
    'These pages were saved by the staff chapter editor.',
    '',
    `![Page 1](${PAGE_ONE_URL})`,
    '',
    `![Page 2](${PAGE_TWO_URL})`,
  ].join('\n'),
  published_topics: [],
};

export const assameseChapter = {
  chapter_id: 'as-chapter-goti',
  chapter_title: 'গতি',
  title: 'Motion',
  topic_title: 'Motion',
  subject_name: 'পদাৰ্থবিজ্ঞান',
  subject_id: 'as-subject-physics',
  board_name: 'AHSEC',
  class_name: 'HS 1st Year',
  slug: 'motion',
  slug_as: 'goti',
  content_type: 'notes',
  content: [
    '# গতি',
    '',
    'অসমীয়া পথৰ fixture notes।',
    '',
    `![পৃষ্ঠা ১](${ASSAMESE_PAGE_ONE_URL})`,
    '',
    `![পৃষ্ঠা ২](${ASSAMESE_PAGE_TWO_URL})`,
  ].join('\n'),
  content_en: '# Motion\n\nEnglish route fixture notes.',
  notes_en: '# Motion\n\nEnglish route fixture notes.',
  published_topics: [],
};
