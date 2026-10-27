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
