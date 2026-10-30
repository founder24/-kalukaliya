import { describe, expect, it } from 'vitest';
import { deduplicateSharedSubjects, groupLibrarySubjects } from './librarySubjectGrouping';

describe('library subject grouping', () => {
  it.each(['HS 1st Year', 'HS 2nd Year'])(
    'keeps Commerce subjects after the first ten subjects for %s',
    (className) => {
      const streamMap = new Map([['commerce', { class_id: 'class-id' }]]);
      const classMap = new Map([['class-id', { name: className }]]);
      const subjects = [
        ...Array.from({ length: 12 }, (_, index) => ({
          id: `subject-${index + 1}`,
          name: `Other Subject ${index + 1}`,
          stream_id: 'commerce',
        })),
        { id: 'accountancy', name: 'Accountancy', stream_id: 'commerce' },
        { id: 'business-studies', name: 'Business Studies', stream_id: 'commerce' },
      ];

      const grouped = groupLibrarySubjects(subjects, streamMap, classMap);

      expect(grouped).toHaveLength(14);
      expect(grouped.map((subject) => subject.id)).toContain('accountancy');
      expect(grouped.map((subject) => subject.id)).toContain('business-studies');
      expect(grouped.at(-2).name).toBe('Accountancy');
      expect(grouped.at(-1).name).toBe('Business Studies');
    },
  );

  it('shows one shared Makers course card across its B.A., B.Com, and B.Sc copies', () => {
    const classMap = new Map([['semester-1', { name: '1st Semester' }]]);
    const streamMap = new Map([
      ['ba-stream', { class_id: 'semester-1', name: 'B.A.', slug: 'ba' }],
      ['bcom-stream', { class_id: 'semester-1', name: 'B.Com', slug: 'bcom' }],
      ['bsc-stream', { class_id: 'semester-1', name: 'B.Sc', slug: 'bsc' }],
    ]);
    const subjects = [
      { id: 'bcom', slug: 'makers-of-modern-assam-mdc-01013', stream_id: 'bcom-stream' },
      { id: 'bsc', slug: 'makers-of-modern-assam-mdc-01013', stream_id: 'bsc-stream' },
      { id: 'ba', slug: 'makers-of-modern-assam-mdc-01013', stream_id: 'ba-stream' },
    ];

    const grouped = groupLibrarySubjects(subjects, streamMap, classMap);

    expect(grouped).toHaveLength(1);
    expect(grouped[0].id).toBe('ba');
    expect(grouped[0].available_stream_names).toEqual(['B.Com', 'B.Sc', 'B.A.']);
    expect(grouped[0].duplicate_subject_ids).toEqual(['bcom', 'bsc']);
  });

  it('consolidates shared courses by slug and class without merging other semesters', () => {
    const classMap = new Map([
      ['semester-1', { name: '1st Semester' }],
      ['semester-2', { name: '2nd Semester' }],
    ]);
    const streamMap = new Map([
      ['ba-stream', { class_id: 'semester-1', name: 'B.A.', slug: 'ba' }],
      ['bcom-stream', { class_id: 'semester-1', name: 'B.Com', slug: 'bcom' }],
      ['bsc-stream', { class_id: 'semester-1', name: 'B.Sc', slug: 'bsc' }],
      ['ba-stream-2', { class_id: 'semester-2', name: 'B.A.', slug: 'ba' }],
    ]);
    const subjects = [
      { id: 'ba', slug: 'globalisation-and-contemporary-world', name: 'Globalisation', stream_id: 'ba-stream' },
      { id: 'bcom', slug: 'globalisation-and-contemporary-world', name: 'Globalisation', stream_id: 'bcom-stream' },
      { id: 'bsc', slug: 'globalisation-and-contemporary-world', name: 'Globalisation', stream_id: 'bsc-stream' },
      { id: 'other-semester', slug: 'globalisation-and-contemporary-world', name: 'Globalisation', stream_id: 'ba-stream-2' },
    ];

    const grouped = groupLibrarySubjects(subjects, streamMap, classMap);

    expect(grouped).toHaveLength(2);
    expect(grouped[0].id).toBe('ba');
    expect(grouped[0].available_stream_names).toEqual(['B.A.', 'B.Com', 'B.Sc']);
    expect(grouped[0].duplicate_subject_ids).toEqual(['bcom', 'bsc']);
    expect(grouped[1].id).toBe('other-semester');
  });

  it('keeps all stream labels when one stream filter is active', () => {
    const streamMap = new Map([
      ['ba-stream', { class_id: 'semester-1', name: 'B.A.', slug: 'ba' }],
      ['bcom-stream', { class_id: 'semester-1', name: 'B.Com', slug: 'bcom' }],
      ['bsc-stream', { class_id: 'semester-1', name: 'B.Sc', slug: 'bsc' }],
    ]);
    const catalog = [
      { id: 'ba', slug: 'makers-of-modern-assam-mdc-01013', stream_id: 'ba-stream', is_saved: true },
      { id: 'bcom', slug: 'makers-of-modern-assam-mdc-01013', stream_id: 'bcom-stream', is_saved: false },
      { id: 'bsc', slug: 'makers-of-modern-assam-mdc-01013', stream_id: 'bsc-stream' },
    ];

    const filtered = deduplicateSharedSubjects([catalog[1]], streamMap, catalog);

    expect(filtered).toHaveLength(1);
    expect(filtered[0].id).toBe('bcom');
    expect(filtered[0].available_stream_names).toEqual(['B.A.', 'B.Com', 'B.Sc']);
    expect(filtered[0].duplicate_subject_ids).toEqual(['ba', 'bsc']);
    expect(filtered[0].is_saved).toBe(true);
  });

  it('keeps second-semester courses in the library card list', () => {
    const classMap = new Map([['semester-2', { name: '2nd Semester' }]]);
    const streamMap = new Map([['ba-stream', { class_id: 'semester-2', name: 'B.A.', slug: 'ba' }]]);
    const course = {
      id: 'mdc-02013',
      name: 'Essence of Democracy and Indian Constitution (MDC-02013)',
      slug: 'essence-of-democracy-and-indian-constitution',
      stream_id: 'ba-stream',
    };

    expect(groupLibrarySubjects([course], streamMap, classMap)).toContainEqual(course);
  });
});
