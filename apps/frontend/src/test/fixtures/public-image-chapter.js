export const PUBLIC_ROUTE = '/ahsec/class-12/physics/public-image-fixture';
export const PAGE_ONE_URL = 'https://cdn.fixture.test/public-page-1.png';
export const PAGE_TWO_URL = 'https://cdn.fixture.test/public-page-2.png';

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