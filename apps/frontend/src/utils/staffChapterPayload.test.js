import { describe, expect, it } from 'vitest';
import {
  buildStaffChapterCreateTopics,
  buildStaffChapterPatchPayload,
  normalizeStaffChapterCreateStatus,
} from './staffChapterPayload';

describe('Staff chapter write payloads', () => {
  it('removes PYQ metadata and topic snapshots from chapter PATCH writes', () => {
    const payload = buildStaffChapterPatchPayload({
      title: 'Chapter',
      notes_en: 'Notes',
      pyq_pdf_url: 'https://example.invalid/old.pdf',
      published_topics: [{ id: 'stale-topic' }],
    });

    expect(payload).toEqual({ title: 'Chapter', notes_en: 'Notes' });
    expect(payload).not.toHaveProperty('pyq_pdf_url');
    expect(payload).not.toHaveProperty('published_topics');
  });

  it('always creates a draft unless the caller has publish capability', () => {
    expect(normalizeStaffChapterCreateStatus('published', false)).toBe('draft');
    expect(normalizeStaffChapterCreateStatus('draft', false)).toBe('draft');
    expect(normalizeStaffChapterCreateStatus('published', true)).toBe('published');
  });

  it('serializes initial topic titles for the chapter-create API', () => {
    expect(buildStaffChapterCreateTopics(['  Cell structure ', '', ' ', 'Mitosis']))
      .toEqual([{ title: 'Cell structure' }, { title: 'Mitosis' }]);
    expect(buildStaffChapterCreateTopics(null)).toEqual([]);
  });
});