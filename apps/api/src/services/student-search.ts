import type { AiSearchInstanceBinding } from '../types';

export type StudentSearchLanguage = 'en' | 'as';

export type StudentSearchScope = {
  board_slug?: string;
  class_slug?: string;
  stream_slug?: string;
};

export type StudentSearchQuery = StudentSearchScope & {
  query: string;
  language: StudentSearchLanguage;
};

export type StudentSearchResult = {
  chapter_id: string;
  title: string;
  snippet: string;
  url: string;
  score: number;
};

export type StudentSearchIndexSource = {
  id: string;
  title: string;
  titleAs?: string | null;
  status: string | null;
  updatedAt: number | null;
  subjectId: string;
  subjectIsPublished: boolean;
  subjectName: string;
  boardSlug: string;
  classSlug: string;
  streamSlug: string;
  subjectSlug: string;
  ragSectionsEn?: string | null;
  ragSectionsAs?: string | null;
  ragTextEn?: string | null;
  ragTextAs?: string | null;
  notesEn?: string | null;
  notesAs?: string | null;
  contentEn?: string | null;
  contentAs?: string | null;
};

export type StudentSearchIndexDocument = {
  key: string;
  content: string;
  metadata: {
    board_slug: string;
    class_slug: string;
    stream_slug: string;
    language: StudentSearchLanguage;
    updated_at: number;
  };
};

const MAX_RESULTS = 8;
const MAX_SNIPPET_LENGTH = 360;

function nonblank(value: unknown): string {
  return typeof value === 'string' ? value.normalize('NFC').trim() : '';
}

function parseSections(
  raw: string | null | undefined,
  language: StudentSearchLanguage,
): string {
  if (!raw) return '';
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return '';
    return parsed.flatMap((item) => {
      if (!item || typeof item !== 'object' || Array.isArray(item)) return [];
      const section = item as Record<string, unknown>;
      const title = language === 'as'
        ? nonblank(section.title_as) || nonblank(section.title)
        : nonblank(section.title_en) || nonblank(section.title);
      const content = language === 'as'
        ? nonblank(section.content_as) || nonblank(section.content) || nonblank(section.definition_as) || nonblank(section.definition)
        : nonblank(section.content_en) || nonblank(section.content) || nonblank(section.definition_en) || nonblank(section.definition);
      if (!title && !content) return [];
      return [`${title ? `## ${title}\n\n` : ''}${content}`];
    }).join('\n\n');
  } catch {
    return '';
  }
}

function chapterText(source: StudentSearchIndexSource, language: StudentSearchLanguage): string {
  const structured = parseSections(
    language === 'as' ? source.ragSectionsAs : source.ragSectionsEn,
    language,
  );
  if (structured) return structured;

  const candidates = language === 'as'
    ? [source.ragTextAs, source.notesAs, source.contentAs]
    : [source.ragTextEn, source.notesEn, source.contentEn];
  return candidates.map(nonblank).find(Boolean) ?? '';
}

/**
 * Build one deterministic upload document from an already-published D1 row.
 * This helper never generates or translates curriculum content.
 */
export function buildStudentSearchDocument(
  source: StudentSearchIndexSource,
  language: StudentSearchLanguage,
): StudentSearchIndexDocument | null {
  if (source.status !== 'published' || !source.subjectIsPublished) return null;
  if (!source.id || source.id.length > 128 || /[\\/]/u.test(source.id)) return null;
  if (!nonblank(source.boardSlug) || !nonblank(source.classSlug) || !nonblank(source.streamSlug)) return null;
  const body = chapterText(source, language);
  if (!body) return null;

  const title = language === 'as' ? nonblank(source.titleAs) || nonblank(source.title) : nonblank(source.title);
  const updatedAt = Number.isSafeInteger(source.updatedAt) && (source.updatedAt ?? 0) >= 0
    ? source.updatedAt ?? 0
    : 0;
  const key = `student-search/v1/${language}/${encodeURIComponent(source.id)}/${updatedAt}.md`;
  // Cloudflare AI Search item filenames are limited to 128 characters.
  if (key.length > 128) return null;
  const content = [
    `# ${title}`,
    '',
    `Subject: ${nonblank(source.subjectName)}`,
    `Board: ${nonblank(source.boardSlug)}`,
    `Class: ${nonblank(source.classSlug)}`,
    `Stream: ${nonblank(source.streamSlug)}`,
    '',
    body,
  ].join('\n').slice(0, 900_000);

  return {
    key,
    content,
    metadata: {
      board_slug: source.boardSlug,
      class_slug: source.classSlug,
      stream_slug: source.streamSlug,
      language,
      updated_at: updatedAt,
    },
  };
}

export function buildStudentSearchDocuments(
  source: StudentSearchIndexSource,
): StudentSearchIndexDocument[] {
  return (['en', 'as'] as const).flatMap((language) => {
    const document = buildStudentSearchDocument(source, language);
    return document ? [document] : [];
  });
}

export function extractStudentSearchItemRef(
  key: unknown,
): { chapterId: string; language: StudentSearchLanguage; updatedAt: number } | null {
  if (typeof key !== 'string') return null;
  const match = /^student-search\/v1\/(en|as)\/([^/]+)\/(\d+)\.md$/.exec(key);
  if (!match) return null;
  try {
    const chapterId = decodeURIComponent(match[2] ?? '');
    const updatedAt = Number(match[3]);
    if (
      !chapterId
      || chapterId.length > 128
      || /[\\/\u0000-\u001f\u007f]/u.test(chapterId)
      || !Number.isSafeInteger(updatedAt)
    ) return null;
    return { chapterId, language: match[1] as StudentSearchLanguage, updatedAt };
  } catch {
    return null;
  }
}

export function buildStudentSearchFilters(
  query: StudentSearchQuery,
): Record<string, string> {
  const filters: Record<string, string> = { language: query.language };
  if (query.board_slug) filters.board_slug = query.board_slug;
  if (query.class_slug) filters.class_slug = query.class_slug;
  if (query.stream_slug) filters.stream_slug = query.stream_slug;
  return filters;
}

export function readStudentSearchChunks(
  response: unknown,
  language: StudentSearchLanguage,
): Array<{ chapterId: string; updatedAt: number; snippet: string; score: number }> | null {
  if (!response || typeof response !== 'object' || Array.isArray(response)) return null;
  const payload = response as { chunks?: unknown; errors?: unknown };
  if (!Array.isArray(payload.chunks) || (Array.isArray(payload.errors) && payload.errors.length > 0)) {
    return null;
  }

  const seen = new Set<string>();
  const results: Array<{ chapterId: string; updatedAt: number; snippet: string; score: number }> = [];
  for (const value of payload.chunks) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) continue;
    const chunk = value as {
      text?: unknown;
      score?: unknown;
      item?: { key?: unknown } | null;
    };
    const ref = extractStudentSearchItemRef(chunk.item?.key);
    const text = nonblank(chunk.text).replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, '');
    const versionKey = ref ? `${ref.chapterId}\u0000${ref.updatedAt}` : '';
    if (!ref || ref.language !== language || !text || seen.has(versionKey)) continue;
    seen.add(versionKey);
    const numericScore = typeof chunk.score === 'number' && Number.isFinite(chunk.score)
      ? Math.min(1, Math.max(0, chunk.score))
      : 0;
    const snippet = [...text].slice(0, MAX_SNIPPET_LENGTH).join('');
    results.push({ chapterId: ref.chapterId, updatedAt: ref.updatedAt, snippet, score: numericScore });
    if (results.length >= MAX_RESULTS) break;
  }
  return results;
}

export async function queryStudentSearch(
  binding: AiSearchInstanceBinding,
  input: StudentSearchQuery,
): Promise<unknown> {
  return binding.search({
    query: input.query,
    ai_search_options: {
      retrieval: {
        retrieval_type: 'hybrid',
        max_num_results: MAX_RESULTS,
        filters: buildStudentSearchFilters(input),
        return_on_failure: false,
      },
    },
  });
}
