/**
 * Machine-checkable readiness for syllabus-led bilingual subject cards.
 *
 * These are content-completeness scores, not a promise of search rankings or
 * third-party certification. The strict profile is opt-in so existing subject
 * publishing remains backward compatible.
 */

import { resolveTopicPageContent } from './topic-page';

type CheckGroup = 'seo' | 'geo' | 'aeo';
export const SUBJECT_PUBLISH_SCORE_THRESHOLD = 90;

export type SubjectReadinessCheck = {
  id: string;
  group: CheckGroup;
  label: string;
  points: number;
  passed: boolean;
  blocking: boolean;
};

export type SubjectPublishReadiness = {
  profile: 'standard' | 'bilingual_syllabus';
  required: boolean;
  can_publish: boolean;
  scores: Record<CheckGroup, number>;
  blockers: string[];
  improvements: string[];
  checks: SubjectReadinessCheck[];
  syllabus_chapter_id: string | null;
  rag_ready: boolean;
  topic_pages: {
    total: number;
    ready: number;
    topics: Array<{
      topic_id: string;
      title_en: string;
      title_as: string;
      scores: Record<CheckGroup, number>;
      ready: boolean;
      blockers: string[];
    }>;
  };
};

type LooseRow = Record<string, unknown>;

function field(row: LooseRow | undefined, ...names: string[]): string {
  if (!row) return '';
  for (const name of names) {
    const value = row[name];
    if (typeof value === 'string') return value.trim();
  }
  return '';
}

function words(value: string): number {
  return value.trim().split(/\s+/u).filter(Boolean).length;
}

function countUrls(value: string): number {
  return new Set(value.match(/https?:\/\/[^\s)>\]]+/giu) ?? []).size;
}

function unitHeadings(value: string): number {
  return (value.match(/^#{1,3}\s+(?:unit\s+(?:[ivx]+|\d+)|গোট\s*[০-৯\d]+)/gimu) ?? []).length;
}

type QA = { question: string; answer: string };

function parseQa(value: string): QA[] {
  try {
    const parsed: unknown = JSON.parse(value);
    if (!Array.isArray(parsed)) return [];
    return parsed.flatMap((item) => {
      if (!item || typeof item !== 'object' || Array.isArray(item)) return [];
      const row = item as Record<string, unknown>;
      const question = [row.question, row.question_as, row.prompt].find((entry) => typeof entry === 'string') as string | undefined;
      const answer = [row.answer, row.solution, row.answer_as, row.explanation].find((entry) => typeof entry === 'string') as string | undefined;
      return question?.trim() && answer?.trim()
        ? [{ question: question.trim(), answer: answer.trim() }]
        : [];
    });
  } catch {
    return [];
  }
}

function parseJsonArray(value: string): unknown[] {
  try {
    const parsed: unknown = JSON.parse(value);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function scoreTopicPages(syllabus: LooseRow | undefined): SubjectPublishReadiness['topic_pages'] {
  const topics = parseJsonArray(field(syllabus, 'published_topics', 'publishedTopics'))
    .filter((topic): topic is LooseRow => Boolean(topic && typeof topic === 'object' && !Array.isArray(topic)));
  const questionsEn = parseJsonArray(field(syllabus, 'qa_en', 'qaEn'));
  const questionsAs = parseJsonArray(field(syllabus, 'qa_as', 'qaAs'));
  const seenIdentities = new Set<string>();
  const scored = topics.map((topic, index) => {
    const id = field(topic, 'topic_id', 'id');
    const slugEn = field(topic, 'topic_slug', 'slug');
    const slugAs = field(topic, 'topic_slug_as');
    const titleEn = field(topic, 'title');
    const titleAs = field(topic, 'title_as');
    const notesEn = field(topic, 'notes_en');
    const notesAs = field(topic, 'notes_as');
    const identity = id || slugEn;
    const duplicate = !identity || seenIdentities.has(identity);
    if (identity) seenIdentities.add(identity);

    const pageEn = resolveTopicPageContent(topic, questionsEn, 'en');
    const pageAs = resolveTopicPageContent(topic, questionsAs, 'as');
    const linkedEn = pageEn?.questions ?? [];
    const linkedAs = pageAs?.questions ?? [];
    const answerEn = linkedEn.some((question) => words(field(question, 'solution', 'answer')) >= 8);
    const answerAs = linkedAs.some((question) => words(field(question, 'solution', 'answer')) >= 8);
    const directAnswer = words(notesEn.split(/\n\s*\n/u)[0] ?? '') >= 15
      && words(notesAs.split(/\n\s*\n/u)[0] ?? '') >= 15;
    const sources = countUrls(`${notesEn}\n${notesAs}`);
    const hasHeadings = (notesEn.match(/^#{1,3}\s+\S.+$/gmu) ?? []).length >= 1
      && (notesAs.match(/^#{1,3}\s+\S.+$/gmu) ?? []).length >= 1;
    const approvedEn = topic.notes_status_en === 'approved' && words(notesEn) >= 80;
    const approvedAs = topic.notes_status_as === 'approved' && words(notesAs) >= 80;
    const stablePair = Boolean(!duplicate && id && slugEn && slugAs);
    const scores = {
      seo: (id && slugEn && titleEn ? 20 : 0)
        + (slugAs && titleAs ? 20 : 0)
        + (approvedEn ? 30 : 0)
        + (approvedAs ? 30 : 0),
      geo: (directAnswer ? 30 : 0)
        + (words(notesEn) >= 80 && words(notesAs) >= 80 ? 30 : 0)
        + (sources >= 1 ? 20 : 0)
        + (hasHeadings ? 20 : 0),
      aeo: (pageEn && pageAs ? 50 : 0)
        + (answerEn && answerAs ? 30 : 0)
        + (stablePair ? 20 : 0),
    };
    const blockers = [
      ...(duplicate ? ['Add a unique stable topic ID'] : []),
      ...(!slugEn || !titleEn ? ['Add the English topic title and slug'] : []),
      ...(!slugAs || !titleAs ? ['Add the Assamese topic title and slug'] : []),
      ...(!approvedEn ? ['Approve and complete English topic notes'] : []),
      ...(!approvedAs ? ['Approve and complete Assamese topic notes'] : []),
      ...(!pageEn || !pageAs ? ['Link complete questions and solutions in both languages by topic ID or slug'] : []),
      ...(!answerEn || !answerAs ? ['Add a self-contained linked answer in both languages'] : []),
      ...(sources < 1 ? ['Add a source link to the topic notes'] : []),
      ...(!hasHeadings ? ['Structure both topic pages with headings'] : []),
    ];
    return {
      topic_id: identity || `unidentified-topic-${index + 1}`,
      title_en: titleEn,
      title_as: titleAs,
      scores,
    ready: scores.seo >= SUBJECT_PUBLISH_SCORE_THRESHOLD
      && scores.geo >= SUBJECT_PUBLISH_SCORE_THRESHOLD
      && scores.aeo >= SUBJECT_PUBLISH_SCORE_THRESHOLD,
      blockers,
    };
  });

  return {
    total: scored.length,
    ready: scored.filter((topic) => topic.ready).length,
    topics: scored,
  };
}

function inLengthRange(value: string, min: number, max: number): boolean {
  const length = [...value].length;
  return length >= min && length <= max;
}

function addCheck(
  checks: SubjectReadinessCheck[],
  id: string,
  group: CheckGroup,
  label: string,
  points: number,
  passed: boolean,
): void {
  checks.push({ id, group, label, points, passed, blocking: true });
}

export function assessSubjectPublishReadiness(
  subject: LooseRow,
  syllabusRows: LooseRow[],
): SubjectPublishReadiness {
  const profileValue = field(subject, 'publication_profile', 'publicationProfile');
  const profile = profileValue === 'bilingual_syllabus' ? 'bilingual_syllabus' : 'standard';
  const required = profile === 'bilingual_syllabus';
  const syllabus = syllabusRows.length === 1 ? syllabusRows[0] : undefined;
  const name = field(subject, 'name');
  const nameAs = field(subject, 'name_as', 'nameAs');
  const slug = field(subject, 'slug');
  const description = field(subject, 'description');
  const streamName = field(subject, 'stream_name', 'streamName');
  const boardName = field(subject, 'board_name', 'boardName');
  const className = field(subject, 'class_name', 'className');
  const title = field(syllabus, 'title');
  const titleAs = field(syllabus, 'title_as', 'titleAs');
  const syllabusSlug = field(syllabus, 'slug');
  const syllabusSlugAs = field(syllabus, 'slug_as', 'slugAs');
  const notesEn = field(syllabus, 'notes_en', 'notesEn');
  const notesAs = field(syllabus, 'notes_as', 'notesAs');
  const metaEn = field(syllabus, 'meta_description', 'metaDescription');
  const metaAs = field(syllabus, 'meta_description_as', 'metaDescriptionAs');
  const keywordsEn = field(syllabus, 'keywords');
  const keywordsAs = field(syllabus, 'keywords_as', 'keywordsAs');
  const status = field(syllabus, 'status');
  const qaEn = parseQa(field(syllabus, 'qa_en', 'qaEn'));
  const qaAs = parseQa(field(syllabus, 'qa_as', 'qaAs'));
  const combined = [name, description, notesEn, notesAs, metaEn, metaAs].join('\n');
  const courseCode = /\b[A-Z]{2,8}[- ]\d{3,6}\b/iu.test(combined);
  const semester = /\bsemester\b|ষাণ্মাসিক/iu.test(combined);
  const affiliation = /\buniversity\b|\bcollege\b|বিশ্ববিদ্যালয়|মহাবিদ্যালয়/iu.test(combined);
  const entityContext = courseCode && semester && affiliation && Boolean(streamName && boardName && className);
  const firstParagraphEn = notesEn.split(/\n\s*\n/u)[0]?.trim() ?? '';
  const firstParagraphAs = notesAs.split(/\n\s*\n/u)[0]?.trim() ?? '';
  const directAnswer = words(firstParagraphEn) >= 25
    && words(firstParagraphEn) <= 120
    && words(firstParagraphAs) >= 25
    && words(firstParagraphAs) <= 120;
  const unitHeadingsEn = unitHeadings(notesEn);
  const unitHeadingsAs = unitHeadings(notesAs);
  const sourceCount = countUrls(`${notesEn}\n${notesAs}`);
  const wordCountEn = words(notesEn);
  const wordCountAs = words(notesAs);
  const completePairCount = Math.min(qaEn.length, qaAs.length);
  const substantiveAnswers = qaEn.length >= 5
    && qaAs.length >= 5
    && qaEn.slice(0, 5).every((item) => words(item.answer) >= 8)
    && qaAs.slice(0, 5).every((item) => words(item.answer) >= 8);

  const checks: SubjectReadinessCheck[] = [];

  // SEO: page identity, paired metadata, substantive localized syllabus, and
  // a published chapter that the existing route/sitemap pipeline can expose.
  addCheck(checks, 'seo-subject-identity', 'seo', 'English subject name and URL slug are present', 10, Boolean(name && slug));
  addCheck(checks, 'seo-localized-identity', 'seo', 'Assamese subject and syllabus slugs are present', 10, Boolean(nameAs && titleAs && syllabusSlugAs));
  addCheck(checks, 'seo-card-description', 'seo', 'Card description includes course code and semester', 15, description.length >= 120 && /\b[A-Z]{2,8}[- ]\d{3,6}\b/iu.test(description) && /\bsemester\b/iu.test(description));
  addCheck(checks, 'seo-meta-en', 'seo', 'English meta description is 120–160 characters', 15, inLengthRange(metaEn, 120, 160));
  addCheck(checks, 'seo-meta-as', 'seo', 'Assamese meta description is 80–200 characters', 15, inLengthRange(metaAs, 80, 200));
  addCheck(checks, 'seo-keywords', 'seo', 'English and Assamese topic keywords are present', 10, Boolean(keywordsEn && keywordsAs));
  addCheck(checks, 'seo-bilingual-syllabus', 'seo', 'Both syllabus versions contain at least 300 words', 15, wordCountEn >= 300 && wordCountAs >= 300);
  addCheck(checks, 'seo-indexable-syllabus', 'seo', 'One published course-syllabus page is available', 10, syllabusRows.length === 1 && syllabusSlug.startsWith('course-syllabus-') && status === 'published' && Boolean(title));

  // GEO: machine-checkable evidence for clear curriculum context, answer-first
  // structure, topical organization, citations, and sufficient bilingual depth.
  addCheck(checks, 'geo-entity-context', 'geo', 'Course code, term, affiliation, and stream scope are explicit', 20, entityContext);
  addCheck(checks, 'geo-answer-first', 'geo', 'Both languages open with a concise direct answer', 20, directAnswer);
  addCheck(checks, 'geo-unit-structure', 'geo', 'At least three unit headings appear in both languages', 20, unitHeadingsEn >= 3 && unitHeadingsAs >= 3);
  addCheck(checks, 'geo-source-links', 'geo', 'At least two source links are included', 20, sourceCount >= 2);
  addCheck(checks, 'geo-topical-depth', 'geo', 'Both language versions contain at least 500 words', 20, wordCountEn >= 500 && wordCountAs >= 500);

  // AEO: paired, self-contained question-answer evidence that can feed the
  // chapter's existing FAQPage structured-data path.
  addCheck(checks, 'aeo-paired-faqs', 'aeo', 'Five complete English and Assamese FAQ pairs are present', 30, completePairCount >= 5 && qaEn.length === qaAs.length);
  addCheck(checks, 'aeo-substantive-answers', 'aeo', 'The first five answers are self-contained in both languages', 20, substantiveAnswers);
  addCheck(checks, 'aeo-direct-answer', 'aeo', 'The bilingual syllabus starts with a direct answer', 15, directAnswer);
  addCheck(checks, 'aeo-course-entity', 'aeo', 'Course code and semester are stated in both language versions', 10, courseCode && semester && Boolean(notesEn && notesAs));
  addCheck(checks, 'aeo-faq-schema', 'aeo', 'FAQ data is complete for the existing FAQ structured-data output', 15, completePairCount >= 5 && qaEn.length === qaAs.length);
  addCheck(checks, 'aeo-topic-outline', 'aeo', 'The page exposes at least three unit headings in both languages', 10, unitHeadingsEn >= 3 && unitHeadingsAs >= 3);

  const scores: Record<CheckGroup, number> = { seo: 0, geo: 0, aeo: 0 };
  for (const check of checks) {
    if (check.passed) scores[check.group] += check.points;
  }
  for (const check of checks) {
    check.blocking = !check.passed && scores[check.group] < SUBJECT_PUBLISH_SCORE_THRESHOLD;
  }
  const blockers = checks.filter((check) => !check.passed && check.blocking).map((check) => check.label);
  const improvements = checks.filter((check) => !check.passed && !check.blocking).map((check) => check.label);
  const canPublish = !required || (syllabusRows.length === 1
    && scores.seo >= SUBJECT_PUBLISH_SCORE_THRESHOLD
    && scores.geo >= SUBJECT_PUBLISH_SCORE_THRESHOLD
    && scores.aeo >= SUBJECT_PUBLISH_SCORE_THRESHOLD);
  const ragUpdatedAt = Number(syllabus?.rag_updated_at ?? syllabus?.ragUpdatedAt ?? 0);
  const ragIndexedAt = Number(syllabus?.rag_indexed_at ?? syllabus?.ragIndexedAt ?? 0);
  const topicPages = scoreTopicPages(syllabus);

  return {
    profile,
    required,
    can_publish: canPublish,
    scores,
    blockers,
    improvements,
    checks,
    syllabus_chapter_id: field(syllabus, 'id') || null,
    rag_ready: Boolean(syllabus && ragIndexedAt > 0 && ragUpdatedAt > 0 && ragIndexedAt >= ragUpdatedAt),
    topic_pages: topicPages,
  };
}
