import { and, eq, ne } from 'drizzle-orm';
import { chapters } from '../db/schema';

export type PublicChapterListRow = {
  id: string;
  title: string;
  titleAs: string | null;
  slug: string;
  slugAs: string | null;
  chapterNumber: number | null;
  status: string | null;
  notesEn: string | null;
  notesAs: string | null;
  qaEn: string | null;
  publishedTopics: string | null;
  pyqPdfUrl: string | null;
  pyqPapers: string | null;
};

export type PublicChapterListItem = {
  id: string;
  chapter_id: string;
  title: string;
  title_as: string | null;
  slug: string;
  slug_as: string | null;
  chapter_number: number | null;
  status: string;
  notes_generated: boolean;
  has_assamese: boolean;
  has_qa: boolean;
  has_pyq: boolean;
  syllabus_topics: string[];
  syllabus_topics_as: string[];
  topic_count: number;
  content_type: 'chapter';
};

/** Shared row-membership rule for every public chapter-list read and prewarm. */
export function publicChapterListWhere(subjectId: string) {
  return and(eq(chapters.subjectId, subjectId), ne(chapters.status, 'archived'));
}

function parseArray<T>(raw: string | null): T[] {
  try {
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed as T[] : [];
  } catch {
    return [];
  }
}

export function serializePublicChapterList(
  rows: PublicChapterListRow[],
): PublicChapterListItem[] {
  return rows.map(chapter => {
    const topics = parseArray<{ title?: unknown; title_as?: unknown }>(chapter.publishedTopics);
    const syllabusTopics = topics
      .map(topic => typeof topic?.title === 'string' ? topic.title.trim() : '')
      .filter(Boolean);
    const syllabusTopicsAs = topics
      .map(topic => typeof topic?.title_as === 'string' && topic.title_as.trim()
        ? topic.title_as.trim()
        : (typeof topic?.title === 'string' ? topic.title.trim() : ''))
      .filter(Boolean);

    return {
      id: chapter.id,
      chapter_id: chapter.id,
      title: chapter.title,
      title_as: chapter.titleAs ?? null,
      slug: chapter.slug,
      slug_as: chapter.slugAs ?? null,
      chapter_number: chapter.chapterNumber ?? null,
      status: chapter.status ?? 'draft',
      notes_generated: Boolean(chapter.notesEn),
      has_assamese: Boolean(chapter.notesAs),
      has_qa: parseArray<unknown>(chapter.qaEn).length > 0,
      has_pyq: Boolean(chapter.pyqPdfUrl) || parseArray<unknown>(chapter.pyqPapers).length > 0,
      syllabus_topics: syllabusTopics,
      syllabus_topics_as: syllabusTopicsAs,
      topic_count: syllabusTopics.length,
      content_type: 'chapter',
    };
  });
}