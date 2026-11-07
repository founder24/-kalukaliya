import { describe, expect, it } from 'vitest';
import {
  buildStudentSearchDocument,
  buildStudentSearchDocuments,
  buildStudentSearchFilters,
  extractStudentSearchItemRef,
  readStudentSearchChunks,
  type StudentSearchIndexSource,
} from './student-search';

function source(overrides: Partial<StudentSearchIndexSource> = {}): StudentSearchIndexSource {
  return {
    id: 'legacy-chapter-7',
    title: 'Motion',
    titleAs: 'গতি',
    status: 'published',
    updatedAt: 100,
    subjectId: 'physics-id',
    subjectIsPublished: true,
    subjectName: 'Physics',
    boardSlug: 'ahsec',
    classSlug: 'class-12',
    streamSlug: 'science',
    subjectSlug: 'physics',
    ragSectionsEn: JSON.stringify([{ title: 'Acceleration', content: 'Acceleration changes velocity over time.' }]),
    ragSectionsAs: JSON.stringify([{ title: 'ত্বৰণ', content: 'ত্বৰণে সময়ৰ লগে লগে বেগ সলনি কৰে।' }]),
    ...overrides,
  };
}

describe('student Smart Search indexing and query contracts', () => {
  it('builds separate language documents using only source-provided sections', () => {
    const english = buildStudentSearchDocument(source(), 'en');
    const assamese = buildStudentSearchDocument(source(), 'as');

    expect(english).toMatchObject({
      key: 'student-search/v1/en/legacy-chapter-7/100.md',
      metadata: {
        board_slug: 'ahsec',
        class_slug: 'class-12',
        stream_slug: 'science',
        language: 'en',
        updated_at: 100,
      },
    });
    expect(english?.content).toContain('Acceleration changes velocity over time.');
    expect(english?.content).not.toContain('ত্বৰণে');
    expect(assamese?.key).toBe('student-search/v1/as/legacy-chapter-7/100.md');
    expect(assamese?.content).toContain('ত্বৰণে সময়ৰ লগে লগে বেগ সলনি কৰে।');
    expect(assamese?.content).not.toContain('Acceleration changes velocity over time.');
    expect(buildStudentSearchDocuments(source())).toHaveLength(2);
  });

  it('uses the documented retrieval source priority and rejects drafts or empty translations', () => {
    const fallback = buildStudentSearchDocument(source({
      ragSectionsEn: '[]',
      ragTextEn: '',
      notesEn: 'Reviewed English notes.',
    }), 'en');
    expect(fallback?.content).toContain('Reviewed English notes.');

    expect(buildStudentSearchDocument(source({ status: 'draft' }), 'en')).toBeNull();
    expect(buildStudentSearchDocument(source({ subjectIsPublished: false }), 'en')).toBeNull();
    expect(buildStudentSearchDocument(source({
      ragSectionsAs: '[]',
      ragTextAs: '',
      notesAs: '',
      contentAs: '',
    }), 'as')).toBeNull();
  });

  it('rejects documents that cannot be safely filtered or fit the provider key limit', () => {
    expect(buildStudentSearchDocument(source({ boardSlug: '' }), 'en')).toBeNull();
    expect(buildStudentSearchDocument(source({ id: 'x'.repeat(101) }), 'en')).toBeNull();
  });

  it('extracts only the exact stable chapter item-key format', () => {
    expect(extractStudentSearchItemRef('student-search/v1/as/legacy-chapter-7/100.md'))
      .toEqual({ chapterId: 'legacy-chapter-7', language: 'as', updatedAt: 100 });
    expect(extractStudentSearchItemRef('student-search/v1/en/unsafe%2Fid/100.md')).toBeNull();
    expect(extractStudentSearchItemRef('other-index/en/legacy-chapter-7.md')).toBeNull();
  });

  it('builds exact curriculum and language filters', () => {
    expect(buildStudentSearchFilters({
      query: 'force',
      language: 'as',
      class_slug: 'class-12',
      stream_slug: 'science',
    })).toEqual({
      language: 'as',
      class_slug: 'class-12',
      stream_slug: 'science',
    });
  });

  it('deduplicates chunks, bounds excerpts, and drops mismatched language keys', () => {
    const chunks = readStudentSearchChunks({
      chunks: [
        {
          item: { key: 'student-search/v1/en/legacy-chapter-7/100.md' },
          text: 'A'.repeat(500),
          score: 1.4,
        },
        {
          item: { key: 'student-search/v1/en/legacy-chapter-7/100.md' },
          text: 'duplicate',
          score: 0.8,
        },
        {
          item: { key: 'student-search/v1/en/legacy-chapter-7/99.md' },
          text: 'stale version',
          score: 0.7,
        },
        {
          item: { key: 'student-search/v1/as/legacy-chapter-8/101.md' },
          text: 'wrong language',
          score: 0.9,
        },
      ],
    }, 'en');

    expect(chunks).toHaveLength(2);
    expect(chunks?.[0]?.snippet).toHaveLength(360);
    expect(chunks?.[0]?.score).toBe(1);
    expect(chunks?.map(chunk => chunk.updatedAt)).toEqual([100, 99]);
  });

  it('fails closed on partial provider errors or malformed results', () => {
    expect(readStudentSearchChunks({ chunks: [], errors: [{ message: 'partial' }] }, 'en')).toBeNull();
    expect(readStudentSearchChunks({ items: [] }, 'en')).toBeNull();
  });
});
