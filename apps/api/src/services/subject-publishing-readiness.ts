/**
 * Machine-checkable readiness for syllabus-led bilingual subject cards.
 *
 * These are content-completeness scores, not a promise of search rankings or
 * third-party certification. The strict profile is opt-in so existing subject
 * publishing remains backward compatible.
 */

import { resolveTopicPageContent } from './topic-page';
import {
  notesTextForRag,
  publishedTopicTextForRag,
  qaTextFromApprovedPairs,
  splitRagText,
} from './rag-indexing';

type CheckGroup = 'seo' | 'geo' | 'aeo';
export const SUBJECT_PUBLISH_SCORE_THRESHOLD = 100;
const TOPIC_PAGE_READINESS_SCORE_THRESHOLD = 90;

export type SubjectReadinessCheck = {
  id: string;
  group: CheckGroup;
  label: string;
  fix_hint: string;
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
  rag_score: number;
  rag_checks: Array<{
    id: string;
    label: string;
    points: number;
    passed: boolean;
    expected_chunks: number;
    indexed_chunks: number;
  }>;
  rag_evaluation: {
    status: 'not_scored';
    score: null;
    reason: string;
  };
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

function nullableTextField(row: LooseRow | undefined, ...names: string[]): string | null {
  if (!row) return null;
  for (const name of names) {
    const value = row[name];
    if (typeof value === 'string') return value;
  }
  return null;
}

function numberField(row: LooseRow | undefined, ...names: string[]): number {
  if (!row) return 0;
  for (const name of names) {
    const value = row[name];
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) return Number(value);
  }
  return 0;
}

function words(value: string): number {
  return value.trim().split(/\s+/u).filter(Boolean).length;
}

function containsLatinScript(value: string): boolean {
  return /\p{Script=Latin}{2,}/u.test(value);
}

function containsAssameseScript(value: string): boolean {
  // Assamese uses the Bengali Unicode block, with Assamese-specific letters
  // commonly appearing in authored material. This confirms script presence,
  // not translation accuracy; a human language review is still required.
  return /[\u0980-\u09FF]/u.test(value);
}

function countSecureUrls(value: string): number {
  return new Set(value.match(/https:\/\/[^\s)>\]]+/giu) ?? []).size;
}

function hasDistinctValues(values: string[]): boolean {
  const normalized = values.map(value => value.toLocaleLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim());
  return normalized.length > 0 && new Set(normalized).size === normalized.length;
}

function keywordTerms(value: string): string[] {
  return [...new Set(value.split(/[,;\n|]+/u).map(item => item.trim()).filter(item => item.length >= 2))];
}

function isValidSlug(value: string): boolean {
  return /^[\p{L}\p{N}]+(?:-[\p{L}\p{N}]+)*$/u.test(value);
}

function hasFaqJsonLdEntries(value: string): boolean {
  return parseJsonArray(value).filter((item) => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return false;
    const row = item as Record<string, unknown>;
    const question = typeof row.question === 'string' ? row.question.trim() : '';
    const answer = typeof row.answer === 'string' && row.answer.trim()
      ? row.answer.trim()
      : typeof row.solution === 'string' ? row.solution.trim() : '';
    return question.length > 5 && answer.length > 10;
  }).length >= 5;
}

function unitHeadings(value: string): number {
  return (value.match(/^#{1,3}\s+(?:unit\s+(?:[ivx]+|\d+)|গোট\s*[০-৯\d]+)/gimu) ?? []).length;
}

function firstContentParagraph(value: string): string {
  return value.split(/\n\s*\n/u)
    .map(block => block.split(/\r?\n/u)
      .map(line => line.trim())
      .filter(line => line && !/^#{1,6}\s/u.test(line) && !/^[-*+]\s/u.test(line))
      .join(' '))
    .find(block => block.trim())
    ?.replace(/[*_`>#]/gu, '')
    .trim() ?? '';
}

type QA = { question: string; answer: string };

function parseQa(value: string, language?: 'en' | 'as'): QA[] {
  try {
    const parsed: unknown = JSON.parse(value);
    if (!Array.isArray(parsed)) return [];
    return parsed.flatMap((item) => {
      if (!item || typeof item !== 'object' || Array.isArray(item)) return [];
      const row = item as Record<string, unknown>;
      const questionKeys = language === 'as'
        ? ['question_as', 'question', 'prompt']
        : ['question_en', 'question', 'prompt'];
      const answerKeys = language === 'as'
        ? ['answer_as', 'answer', 'solution', 'explanation']
        : ['answer_en', 'answer', 'solution', 'explanation'];
      const question = questionKeys.map(key => row[key]).find((entry) => typeof entry === 'string') as string | undefined;
      const answer = answerKeys.map(key => row[key]).find((entry) => typeof entry === 'string') as string | undefined;
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
    const sources = countSecureUrls(`${notesEn}\n${notesAs}`);
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
      ready: scores.seo >= TOPIC_PAGE_READINESS_SCORE_THRESHOLD
        && scores.geo >= TOPIC_PAGE_READINESS_SCORE_THRESHOLD
        && scores.aeo >= TOPIC_PAGE_READINESS_SCORE_THRESHOLD,
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
  fixHint: string,
  points: number,
  passed: boolean,
): void {
  checks.push({ id, group, label, fix_hint: fixHint, points, passed, blocking: true });
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
  const contentType = field(syllabus, 'content_type', 'contentType');
  const qaEn = parseQa(field(syllabus, 'qa_en', 'qaEn'), 'en');
  const qaAs = parseQa(field(syllabus, 'qa_as', 'qaAs'), 'as');
  const combined = [name, description, notesEn, notesAs, metaEn, metaAs].join('\n');
  const courseCode = /\b[A-Z]{2,8}[- ]\d{3,6}\b/iu.test(combined);
  const semesterEn = /\b(?:\d+(?:st|nd|rd|th)\s+)?semester\s*\d*\b|\bsemester\s*\d+\b/iu.test(`${description}\n${notesEn}\n${metaEn}`);
  const semesterAs = /ষাণ্মাসিক|ছমাহীয়া/iu.test(`${notesAs}\n${metaAs}`);
  const affiliationEn = /\buniversity\b|\bcollege\b/iu.test(`${description}\n${notesEn}\n${metaEn}`);
  const affiliationAs = /বিশ্ববিদ্যালয়|মহাবিদ্যালয়/iu.test(`${notesAs}\n${metaAs}`);
  const programmeContext = /\b(?:programme|program|FYUGP|NEP\s*2020)\b/iu.test(`${description}\n${notesEn}`);
  const entityContext = courseCode && semesterEn && semesterAs && affiliationEn
    && Boolean(streamName && boardName && className);
  const firstParagraphEn = firstContentParagraph(notesEn);
  const firstParagraphAs = firstContentParagraph(notesAs);
  const directAnswer = words(firstParagraphEn) >= 25
    && words(firstParagraphEn) <= 120
    && words(firstParagraphAs) >= 25
    && words(firstParagraphAs) <= 120
    && containsLatinScript(firstParagraphEn)
    && containsAssameseScript(firstParagraphAs);
  const unitHeadingsEn = unitHeadings(notesEn);
  const unitHeadingsAs = unitHeadings(notesAs);
  const sourceCountEn = countSecureUrls(notesEn);
  const sourceCountAs = countSecureUrls(notesAs);
  const wordCountEn = words(notesEn);
  const wordCountAs = words(notesAs);
  const completePairCount = Math.min(qaEn.length, qaAs.length);
  const faqQuestionsEn = qaEn.map(item => item.question);
  const faqQuestionsAs = qaAs.map(item => item.question);
  const faqPairsComplete = completePairCount >= 5
    && qaEn.length === qaAs.length
    && qaEn.every(item => item.question.length >= 12 && item.answer.length > 10 && containsLatinScript(item.question) && containsLatinScript(item.answer))
    && qaAs.every(item => item.question.length >= 8 && item.answer.length > 10 && containsAssameseScript(item.question) && containsAssameseScript(item.answer))
    && hasDistinctValues(faqQuestionsEn)
    && hasDistinctValues(faqQuestionsAs);
  const substantiveAnswers = faqPairsComplete
    && qaEn.slice(0, 5).every((item) => words(item.answer) >= 20)
    && qaAs.slice(0, 5).every((item) => words(item.answer) >= 20)
    && hasDistinctValues(qaEn.slice(0, 5).map(item => item.answer))
    && hasDistinctValues(qaAs.slice(0, 5).map(item => item.answer));
  const keywordListEn = keywordTerms(keywordsEn);
  const keywordListAs = keywordTerms(keywordsAs);
  const cardDescriptionTerms = keywordListEn
    .map(term => term.toLocaleLowerCase())
    .filter(term => term.length >= 4
      && !/\b(?:syllabus|semester|course|university|programme|program|fyugp|nep|mdc)[\s-]*\d*\b/iu.test(term));
  const cardCoversSyllabus = cardDescriptionTerms
    .filter(term => description.toLocaleLowerCase().includes(term))
    .length >= 2;
  const titleIdentity = Boolean(name && slug && isValidSlug(slug) && courseCode);
  const subjectTitleHasCode = /\b[A-Z]{2,8}[- ]\d{3,6}\b/iu.test(name);
  const localizedIdentity = Boolean(
    nameAs && containsAssameseScript(nameAs)
    && titleAs && containsAssameseScript(titleAs)
    && syllabusSlugAs && isValidSlug(syllabusSlugAs),
  );
  const metaEnReady = inLengthRange(metaEn, 120, 160)
    && containsLatinScript(metaEn)
    && /\b[A-Z]{2,8}[- ]\d{3,6}\b/iu.test(metaEn)
    && /\bsemester\b/iu.test(metaEn);
  const metaAsReady = inLengthRange(metaAs, 80, 200)
    && containsAssameseScript(metaAs)
    && /\b[A-Z]{2,8}[- ]\d{3,6}\b/iu.test(metaAs)
    && /ষাণ্মাসিক|ছমাহীয়া/iu.test(metaAs);
  const keywordsReady = keywordListEn.length >= 3
    && keywordListAs.length >= 3
    && containsLatinScript(keywordsEn)
    && containsAssameseScript(keywordsAs);
  const cardDescriptionReady = description.length >= 120
    && /\b[A-Z]{2,8}[- ]\d{3,6}\b/iu.test(description)
    && semesterEn
    && affiliationEn
    && programmeContext
    && cardCoversSyllabus;
  const publishedSyllabusReady = syllabusRows.length === 1
    && syllabusSlug.startsWith('course-syllabus-')
    && isValidSlug(syllabusSlug)
    && status === 'published'
    && Boolean(title)
    && containsLatinScript(title)
    && containsAssameseScript(titleAs)
    && Boolean(notesEn && notesAs);
  const syllabusLanguageReady = wordCountEn >= 500
    && wordCountAs >= 500
    && containsLatinScript(notesEn)
    && containsAssameseScript(notesAs);
  const faqSchemaReady = !['question_paper', 'pyq'].includes(contentType)
    && hasFaqJsonLdEntries(field(syllabus, 'qa_en', 'qaEn'))
    && hasFaqJsonLdEntries(field(syllabus, 'qa_as', 'qaAs'));

  const checks: SubjectReadinessCheck[] = [];

  // This rubric is a publication-completeness gate. It checks the actual
  // bilingual fields that the card, syllabus route, and FAQ endpoint consume;
  // it does not predict rankings or verify source authority.
  addCheck(checks, 'seo-subject-identity', 'seo', 'Subject name contains the course code and has a valid URL slug', 'Put the official course code in the subject-card title and use a stable hyphenated subject slug.', 10, titleIdentity && subjectTitleHasCode);
  addCheck(checks, 'seo-localized-identity', 'seo', 'Assamese subject name and syllabus URL are present in Assamese script', 'Enter the reviewed Assamese title and a valid localized syllabus slug.', 10, localizedIdentity);
  addCheck(checks, 'seo-card-description', 'seo', 'Card summary includes course code, term, university, programme, and syllabus coverage', 'Write a 120+ character summary with the official code, semester, university, programme, and at least two syllabus keywords.', 15, cardDescriptionReady);
  addCheck(checks, 'seo-meta-en', 'seo', 'English meta description is 120–160 characters and names the course and term', 'Add a 120–160 character English description containing the course code and semester.', 15, metaEnReady);
  addCheck(checks, 'seo-meta-as', 'seo', 'Assamese meta description is localized and names the course and term', 'Add an 80–200 character Assamese-script description containing the course code and Assamese semester term.', 15, metaAsReady);
  addCheck(checks, 'seo-keywords', 'seo', 'Both languages have at least three localized syllabus keywords', 'Add at least three comma-separated English keywords and three Assamese-script keywords.', 10, keywordsReady);
  addCheck(checks, 'seo-bilingual-syllabus', 'seo', 'English and Assamese syllabus notes are substantial and use their intended scripts', 'Provide at least 500 words in each language; use English in notes_en and Assamese script in notes_as.', 15, syllabusLanguageReady);
  addCheck(checks, 'seo-indexable-syllabus', 'seo', 'Exactly one published bilingual syllabus route is available', 'Keep one published course-syllabus chapter with both titles, localized slugs, and notes.', 10, publishedSyllabusReady);

  addCheck(checks, 'geo-entity-context', 'geo', 'Course code, term, affiliation, and stream scope are explicit in both languages', 'State the course code, semester, university, board, class/term, and stream; include the university and semester in Assamese text too.', 20, entityContext && programmeContext && affiliationAs);
  addCheck(checks, 'geo-answer-first', 'geo', 'Both language versions open with a concise answer in the matching script', 'Start each notes field with a 25–120 word summary in the correct language/script.', 20, directAnswer);
  addCheck(checks, 'geo-unit-structure', 'geo', 'At least three unit headings appear in both languages', 'Use explicit Unit I/II/III headings in English and গোট ১/২/৩ headings in Assamese.', 20, unitHeadingsEn >= 3 && unitHeadingsAs >= 3);
  addCheck(checks, 'geo-source-links', 'geo', 'At least one HTTPS source reference appears in each language version', 'Add at least one distinct HTTPS source link to each notes version; staff must still review its authority.', 20, sourceCountEn >= 1 && sourceCountAs >= 1);
  addCheck(checks, 'geo-topical-depth', 'geo', 'Both language versions contain at least 500 words', 'Expand the syllabus notes to at least 500 words in each language without padding or repeated filler.', 20, wordCountEn >= 500 && wordCountAs >= 500 && containsLatinScript(notesEn) && containsAssameseScript(notesAs));

  addCheck(checks, 'aeo-paired-faqs', 'aeo', 'At least five complete, distinct bilingual question and answer pairs exist', 'Add five aligned English/Assamese pairs with distinct questions, non-empty answers, and the correct script in each language.', 30, faqPairsComplete);
  addCheck(checks, 'aeo-substantive-answers', 'aeo', 'The first five answers are distinct and at least 20 words in both languages', 'Write a direct 20+ word answer for each of the first five questions in both languages; avoid repeated boilerplate.', 20, substantiveAnswers);
  addCheck(checks, 'aeo-direct-answer', 'aeo', 'The bilingual syllabus starts with a direct answer', 'Make both opening paragraphs direct 25–120 word answers in their respective languages.', 15, directAnswer);
  addCheck(checks, 'aeo-course-entity', 'aeo', 'Course code and semester are identified in both language versions', 'Include the official course code and semester in English and Assamese notes.', 10, courseCode && semesterEn && semesterAs && Boolean(notesEn && notesAs) && containsAssameseScript(notesAs));
  addCheck(checks, 'aeo-faq-schema', 'aeo', 'Public English and Assamese FAQ routes can build FAQPage JSON-LD', 'Store complete question and answer fields in qa_en and qa_as using the public FAQ route’s question/answer or solution shape.', 15, faqSchemaReady);
  addCheck(checks, 'aeo-topic-outline', 'aeo', 'At least three unit headings are exposed in both languages', 'Add three explicit unit headings to each language version.', 10, unitHeadingsEn >= 3 && unitHeadingsAs >= 3);

  const scores: Record<CheckGroup, number> = { seo: 0, geo: 0, aeo: 0 };
  for (const check of checks) {
    if (check.passed) scores[check.group] += check.points;
  }
  for (const check of checks) {
    check.blocking = !check.passed && scores[check.group] < SUBJECT_PUBLISH_SCORE_THRESHOLD;
  }
  const blockers = checks.filter((check) => !check.passed && check.blocking).map((check) => check.label);
  const improvements = checks.filter((check) => !check.passed && !check.blocking).map((check) => check.label);
  const categoryScoresReady = syllabusRows.length === 1
    && scores.seo >= SUBJECT_PUBLISH_SCORE_THRESHOLD
    && scores.geo >= SUBJECT_PUBLISH_SCORE_THRESHOLD
    && scores.aeo >= SUBJECT_PUBLISH_SCORE_THRESHOLD;
  const ragUpdatedAt = Number(syllabus?.rag_updated_at ?? syllabus?.ragUpdatedAt ?? 0);
  const ragIndexedAt = Number(syllabus?.rag_indexed_at ?? syllabus?.ragIndexedAt ?? 0);
  const ragSourceBaseEn = nullableTextField(syllabus, 'rag_text', 'ragText') ?? notesEn;
  const ragSourceBaseAs = nullableTextField(syllabus, 'rag_text_as', 'ragTextAs') ?? notesAs;
  const topicTextEn = publishedTopicTextForRag(field(syllabus, 'published_topics', 'publishedTopics'), 'english');
  const topicTextAs = publishedTopicTextForRag(field(syllabus, 'published_topics', 'publishedTopics'), 'assamese');
  const ragNotesEn = [notesTextForRag(ragSourceBaseEn, field(syllabus, 'rag_sections_en', 'ragSectionsEn')), topicTextEn]
    .filter(Boolean).join('\n\n');
  const ragNotesAs = [notesTextForRag(ragSourceBaseAs, field(syllabus, 'rag_sections_as', 'ragSectionsAs')), topicTextAs]
    .filter(Boolean).join('\n\n');
  const ragQaEn = qaTextFromApprovedPairs(field(syllabus, 'qa_en', 'qaEn'));
  const ragQaAs = qaTextFromApprovedPairs(field(syllabus, 'qa_as', 'qaAs'));
  const expectedRagChunks = {
    notes_en: splitRagText(ragNotesEn).length,
    notes_as: splitRagText(ragNotesAs).length,
    qa_en: ragQaEn ? splitRagText(ragQaEn).length : 0,
    qa_as: ragQaAs ? splitRagText(ragQaAs).length : 0,
  };
  const ragCheckSpecs = [
    {
      id: 'rag-freshness',
      label: 'RAG index timestamps are current',
      points: 20,
      expected: 1,
      indexed: ragIndexedAt > 0 && ragUpdatedAt > 0 && ragIndexedAt >= ragUpdatedAt ? 1 : 0,
      latest: ragIndexedAt,
    },
    {
      id: 'rag-notes-en',
      label: 'Every English notes chunk has a vector mapping',
      points: 20,
      expected: expectedRagChunks.notes_en,
      indexed: numberField(syllabus, 'rag_notes_en_count', 'ragNotesEnCount'),
      latest: numberField(syllabus, 'rag_notes_en_latest_at', 'ragNotesEnLatestAt'),
    },
    {
      id: 'rag-notes-as',
      label: 'Every Assamese notes chunk has a vector mapping',
      points: 20,
      expected: expectedRagChunks.notes_as,
      indexed: numberField(syllabus, 'rag_notes_as_count', 'ragNotesAsCount'),
      latest: numberField(syllabus, 'rag_notes_as_latest_at', 'ragNotesAsLatestAt'),
    },
    {
      id: 'rag-qa-en',
      label: 'Every approved English Q&A chunk has a vector mapping',
      points: 20,
      expected: expectedRagChunks.qa_en,
      indexed: numberField(syllabus, 'rag_qa_en_count', 'ragQaEnCount'),
      latest: numberField(syllabus, 'rag_qa_en_latest_at', 'ragQaEnLatestAt'),
    },
    {
      id: 'rag-qa-as',
      label: 'Every approved Assamese Q&A chunk has a vector mapping',
      points: 20,
      expected: expectedRagChunks.qa_as,
      indexed: numberField(syllabus, 'rag_qa_as_count', 'ragQaAsCount'),
      latest: numberField(syllabus, 'rag_qa_as_latest_at', 'ragQaAsLatestAt'),
    },
  ];
  const ragChecks = ragCheckSpecs.map(check => ({
    id: check.id,
    label: check.label,
    points: check.points,
    expected_chunks: check.expected,
    indexed_chunks: check.indexed,
    passed: check.id === 'rag-freshness'
      ? check.indexed === 1
      : check.expected > 0 && check.indexed === check.expected && check.latest >= ragUpdatedAt,
  }));
  const ragScore = ragChecks.reduce((total, check) => total + (check.passed ? check.points : 0), 0);
  const canPublish = !required || (categoryScoresReady && ragScore === 100);
  const allBlockers = [
    ...blockers,
    ...(required && ragScore < 100 ? ['English and Assamese notes and approved Q&A must be fully indexed and current'] : []),
  ];
  const topicPages = scoreTopicPages(syllabus);

  return {
    profile,
    required,
    can_publish: canPublish,
    scores,
    blockers: allBlockers,
    improvements,
    checks,
    syllabus_chapter_id: field(syllabus, 'id') || null,
    rag_ready: Boolean(syllabus && ragScore === 100),
    rag_score: ragScore,
    rag_checks: ragChecks,
    rag_evaluation: {
      status: 'not_scored',
      score: null,
      reason: 'No approved, reviewer-verified RAG gold set exists for this subject; index completeness is not retrieval-quality certification.',
    },
    topic_pages: topicPages,
  };
}
