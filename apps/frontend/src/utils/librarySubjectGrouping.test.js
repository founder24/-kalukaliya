import { describe, expect, it } from 'vitest';
import { groupLibrarySubjects } from './librarySubjectGrouping';

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
});
