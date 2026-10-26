import { describe, expect, it } from 'vitest';
import { chapterSchema } from '../lib/jsonld.js';

describe('chapter schema source attribution', () => {
  const baseData = {
    title: 'Unit I: Language, Literature & Culture',
    chapter_title: 'Unit I: Language, Literature & Culture',
    subject_name: 'Makers of Modern Assam',
    board_name: 'Degree',
    class_name: '1st Semester',
    content: 'Study notes for the course.',
  };

  it('does not imply NCERT or AHSEC approval without an attached source', () => {
    const schema = chapterSchema(
      baseData,
      'https://syrabit.ai/degree/1st-semester/ba/makers/unit-1',
    );
    const article = schema['@graph'].find((node) => node['@type'] === 'Article');

    expect(article.citation).toBeUndefined();
    expect(article.sourceOrganization).toEqual({
      '@type': 'Organization',
      name: 'Degree',
    });
  });

  it('keeps citations and publisher attribution when supplied by the content record', () => {
    const schema = chapterSchema({
      ...baseData,
      citations: [{
        name: 'Gauhati University MDC-01013 syllabus',
        url: 'https://example.edu/syllabus',
        publisher: 'Gauhati University',
      }],
      source_organization: {
        name: 'Gauhati University',
        url: 'https://gauhati.ac.in/',
      },
    }, 'https://syrabit.ai/degree/1st-semester/ba/makers/unit-1');
    const article = schema['@graph'].find((node) => node['@type'] === 'Article');

    expect(article.citation).toEqual([{
      '@type': 'CreativeWork',
      name: 'Gauhati University MDC-01013 syllabus',
      url: 'https://example.edu/syllabus',
      publisher: { '@type': 'Organization', name: 'Gauhati University' },
    }]);
    expect(article.sourceOrganization).toEqual({
      '@type': 'Organization',
      name: 'Gauhati University',
      url: 'https://gauhati.ac.in/',
    });
  });
});
