import { describe, expect, it } from 'vitest';
import { assessSubjectPublishReadiness } from './subject-publishing-readiness';
import {
  notesTextForRag,
  publishedTopicTextForRag,
  qaTextFromApprovedPairs,
  splitRagText,
} from './rag-indexing';

function words(count: number, ending = ''): string {
  const vocabulary = ['historical', 'social', 'political', 'cultural', 'institutional'];
  return `${Array.from({ length: count }, (_, index) => vocabulary[index % vocabulary.length]).join(' ')} ${ending}`.trim();
}

function assameseWords(count: number, ending = ''): string {
  const vocabulary = ['ইতিহাস', 'সমাজ', 'সংস্কৃতি', 'ৰাজনীতি', 'প্ৰতিষ্ঠান'];
  return `${Array.from({ length: count }, (_, index) => vocabulary[index % vocabulary.length]).join(' ')} ${ending}`.trim();
}

function qaItems(language: 'en' | 'as') {
  return JSON.stringify(Array.from({ length: 5 }, (_, index) => ({
    question: language === 'en'
      ? `What is one key theme in unit ${index + 1}?`
      : `প্ৰশ্ন ${index + 1}: এই গোটৰ এটা মুখ্য বিষয় কি?`,
    answer: language === 'en'
      ? words(22, `Answer ${index + 1} explains the syllabus content with a specific example.`)
      : assameseWords(22, `উত্তৰ ${index + 1} ত পাঠ্যক্ৰমৰ বিষয়টো এটা নিৰ্দিষ্ট উদাহৰণেৰে বুজোৱা হৈছে।`),
  })));
}

function addCompleteRagEvidence(syllabus: Record<string, unknown>) {
  const notesEn = [
    notesTextForRag(String(syllabus.rag_text ?? syllabus.notes_en ?? ''), String(syllabus.rag_sections_en ?? '[]')),
    publishedTopicTextForRag(String(syllabus.published_topics ?? '[]'), 'english'),
  ].filter(Boolean).join('\n\n');
  const notesAs = [
    notesTextForRag(String(syllabus.rag_text_as ?? syllabus.notes_as ?? ''), String(syllabus.rag_sections_as ?? '[]')),
    publishedTopicTextForRag(String(syllabus.published_topics ?? '[]'), 'assamese'),
  ].filter(Boolean).join('\n\n');
  const qaEn = qaTextFromApprovedPairs(String(syllabus.qa_en ?? '[]')) ?? '';
  const qaAs = qaTextFromApprovedPairs(String(syllabus.qa_as ?? '[]')) ?? '';
  return {
    rag_notes_en_count: splitRagText(notesEn).length,
    rag_notes_en_latest_at: 100,
    rag_notes_as_count: splitRagText(notesAs).length,
    rag_notes_as_latest_at: 100,
    rag_qa_en_count: splitRagText(qaEn).length,
    rag_qa_en_latest_at: 100,
    rag_qa_as_count: splitRagText(qaAs).length,
    rag_qa_as_latest_at: 100,
  };
}

function completeFixture() {
  const unitsEn = ['Unit I', 'Unit II', 'Unit III'].map((unit) => `## ${unit}\n\n${words(190, 'This unit explains the course content and learning outcome.')}`).join('\n\n');
  const unitsAs = ['গোট ১', 'গোট ২', 'গোট ৩'].map((unit) => `## ${unit}\n\n${assameseWords(190, 'এই গোটত পাঠ্যক্ৰমৰ বিষয়বস্তু আৰু শিক্ষণৰ ফলাফল বৰ্ণনা কৰা হৈছে।')}`).join('\n\n');
  const syllabus = {
    id: 'syllabus',
    title: 'Globalisation and the Contemporary World',
    title_as: 'বিশ্বায়ন আৰু সমসাময়িক বিশ্ব',
    slug: 'course-syllabus-globalisation-mdc-03013',
    slug_as: 'course-syllabus-globalisation-mdc-03013-as',
    status: 'published',
    content_type: 'notes',
    meta_description: 'MDC-03013 Semester 3 syllabus covers globalization, global institutions, trade, culture, and political change in a bilingual undergraduate guide.',
    meta_description_as: 'MDC-03013 তৃতীয় ষাণ্মাসিকৰ পাঠ্যক্ৰমত বিশ্বায়ন, বিশ্ব প্ৰতিষ্ঠান, বাণিজ্য, সংস্কৃতি আৰু ৰাজনৈতিক পৰিৱৰ্তনৰ বিষয়ে স্নাতক শিক্ষাৰ্থীৰ বাবে গোটভিত্তিক দ্বিভাষিক গাইড আছে।',
    keywords: 'globalisation, global institutions, trade, Semester 3',
    keywords_as: 'বিশ্বায়ন, বিশ্ব প্ৰতিষ্ঠান, বাণিজ্য, তৃতীয় ষাণ্মাসিক',
    notes_en: `MDC-03013 is a Semester 3 course at Gauhati University. ${words(45, 'This syllabus explains globalisation through trade, institutions, culture, and political change for undergraduate students.')}\n\n${unitsEn}\n\nSources: https://example.org/official-syllabus and https://example.org/research`,
    notes_as: `MDC-03013 তৃতীয় ষাণ্মাসিকৰ গুৱাহাটী বিশ্ববিদ্যালয়ৰ পাঠ্যক্ৰম। ${assameseWords(45, 'এই পাঠ্যক্ৰমত বিশ্বায়ন, বাণিজ্য, প্ৰতিষ্ঠান, সংস্কৃতি আৰু ৰাজনৈতিক পৰিৱৰ্তন আলোচনা কৰা হৈছে।')}\n\n${unitsAs}\n\nউৎস: https://example.org/official-syllabus-as and https://example.org/research-as`,
    qa_en: qaItems('en'),
    qa_as: qaItems('as'),
    published_topics: '[]',
    rag_updated_at: 100,
    rag_indexed_at: 100,
  };
  Object.assign(syllabus, addCompleteRagEvidence(syllabus));
  return {
    subject: {
      publication_profile: 'bilingual_syllabus',
      name: 'Globalisation and the Contemporary World MDC-03013',
      name_as: 'বিশ্বায়ন আৰু সমসাময়িক বিশ্ব',
      slug: 'globalisation-contemporary-world-mdc-03013',
      description: 'MDC-03013 Semester 3 · Gauhati University · Four-Year Undergraduate Programme (FYUGP, NEP 2020). Globalisation, trade, culture, and political change.',
      stream_name: 'B.A.',
      board_name: 'Degree',
      class_name: '3rd Semester',
    },
    syllabus: [syllabus],
  };
}

describe('bilingual subject publishing readiness', () => {
  it('does not apply the strict gate to standard subjects', () => {
    const result = assessSubjectPublishReadiness({ publication_profile: 'standard' }, []);
    expect(result.required).toBe(false);
    expect(result.can_publish).toBe(true);
  });

  it('blocks a syllabus-led card with no bilingual syllabus', () => {
    const result = assessSubjectPublishReadiness({ publication_profile: 'bilingual_syllabus' }, []);
    expect(result.can_publish).toBe(false);
    expect(result.scores.seo).toBeLessThan(100);
    expect(result.blockers).toContain('Exactly one published bilingual syllabus route is available');
  });

  it('reaches full readiness only when every bilingual check passes', () => {
    const fixture = completeFixture();
    const result = assessSubjectPublishReadiness(fixture.subject, fixture.syllabus);
    expect(result.scores).toEqual({ seo: 100, geo: 100, aeo: 100 });
    expect(result.rag_score).toBe(100);
    expect(result.rag_evaluation).toMatchObject({ status: 'not_scored', score: null });
    expect(result.blockers).toEqual([]);
    expect(result.can_publish).toBe(true);
    expect(result.rag_ready).toBe(true);
  });

  it('blocks the strict publish gate when one language scope is missing indexed vectors', () => {
    const fixture = completeFixture();
    Object.assign(fixture.syllabus[0]!, { rag_qa_as_count: 0 });
    const result = assessSubjectPublishReadiness(fixture.subject, fixture.syllabus);
    expect(result.rag_score).toBe(80);
    expect(result.rag_ready).toBe(false);
    expect(result.can_publish).toBe(false);
    expect(result.blockers).toContain('English and Assamese notes and approved Q&A must be fully indexed and current');
  });

  it('keeps the answer-first check correct when notes begin with a Markdown title', () => {
    const fixture = completeFixture();
    fixture.syllabus[0]!.notes_en = `# MDC-03013 Syllabus\n\n${fixture.syllabus[0]!.notes_en}`;
    fixture.syllabus[0]!.notes_as = `# MDC-03013 পাঠ্যক্ৰম\n\n${fixture.syllabus[0]!.notes_as}`;

    const result = assessSubjectPublishReadiness(fixture.subject, fixture.syllabus);
    expect(result.checks.find(check => check.id === 'geo-answer-first')?.passed).toBe(true);
    expect(result.checks.find(check => check.id === 'aeo-direct-answer')?.passed).toBe(true);
  });

  it('blocks subject publishing unless all three scores reach 100', () => {
    const fixture = completeFixture();
    fixture.syllabus[0]!.keywords = '';
    fixture.syllabus[0]!.keywords_as = '';
    const result = assessSubjectPublishReadiness(fixture.subject, fixture.syllabus);
    expect(result.scores.seo).toBe(75);
    expect(result.scores.geo).toBeGreaterThanOrEqual(90);
    expect(result.scores.aeo).toBeGreaterThanOrEqual(90);
    expect(result.can_publish).toBe(false);
    expect(result.blockers).toContain('Both languages have at least three localized syllabus keywords');
    expect(result.improvements).toHaveLength(0);
  });

  it('blocks publishing when any category is below 100', () => {
    const fixture = completeFixture();
    fixture.syllabus[0]!.meta_description = '';
    const result = assessSubjectPublishReadiness(fixture.subject, fixture.syllabus);
    expect(result.scores.seo).toBe(85);
    expect(result.can_publish).toBe(false);
    expect(result.blockers).toContain('English meta description is 120–160 characters and names the course and term');
  });

  it('keeps AEO incomplete when bilingual FAQ pairs are missing', () => {
    const fixture = completeFixture();
    fixture.syllabus[0]!.qa_as = '[]';
    const result = assessSubjectPublishReadiness(fixture.subject, fixture.syllabus);
    expect(result.scores.aeo).toBeLessThan(100);
    expect(result.can_publish).toBe(false);
  });

  it('scores complete bilingual FAQs separately from answer depth', () => {
    const fixture = completeFixture();
    const english = JSON.parse(fixture.syllabus[0]!.qa_en as string) as Array<{ answer: string }>;
    const assamese = JSON.parse(fixture.syllabus[0]!.qa_as as string) as Array<{ answer: string }>;
    english.forEach((item, index) => {
      item.answer = `Unit ${index + 1} covers people, culture, education, institutions, and political change in modern Assam.`;
    });
    assamese.forEach((item, index) => {
      item.answer = `গোট ${index + 1} ত ভাষা, সংস্কৃতি, শিক্ষা, প্ৰতিষ্ঠান আৰু অসমৰ ৰাজনৈতিক পৰিৱৰ্তনৰ কথা আলোচনা কৰা হৈছে।`;
    });
    fixture.syllabus[0]!.qa_en = JSON.stringify(english);
    fixture.syllabus[0]!.qa_as = JSON.stringify(assamese);

    const result = assessSubjectPublishReadiness(fixture.subject, fixture.syllabus);
    expect(result.checks.find(check => check.id === 'aeo-paired-faqs')?.passed).toBe(true);
    expect(result.checks.find(check => check.id === 'aeo-faq-schema')?.passed).toBe(true);
    expect(result.checks.find(check => check.id === 'aeo-substantive-answers')?.passed).toBe(false);
    expect(result.scores.aeo).toBe(80);
  });

  it('awards full AEO readiness when each bilingual syllabus FAQ has a substantive answer', () => {
    const fixture = completeFixture();
    const answersEn = [
      'MDC-01013 examines Assam’s transition into modernity under colonial rule, tracing modern Assamese language, literature, music, culture, political institutions, tribal rights and women’s education.',
      'The syllabus has three units. Unit I covers language, literature and culture. Unit II covers political foundations and tribal rights. Unit III examines women’s education, politics and literature.',
      'Unit I highlights Anandaram Dhekiyal Phukan, Lakshminath Bezbaruah and Banikanta Kakati in language and literature, alongside Jyoti Prasad Agarwala, Bishnu Prasad Rabha and Bhupen Hazarika in music and culture.',
      'Unit II traces political foundations through Asom Chatra Sanmilan and Assam Association, followed by the roles of Tarun Ram Phukan and Gopinath Bordoloi. It also covers Kalicharan Brahma and Bhimbar Deori on tribal consciousness and rights.',
      'Unit III covers Baptist missionaries and women’s education, reformers Chandraprabha Saikiani, Amal Prabha Das and Rajabala Das, and the literary organization Asom Lekhika Samaroh.',
    ];
    const answersAs = [
      'MDC-01013-ত ঔপনিৱেশিক শাসনৰ সময়ত অসমৰ আধুনিক যুগলৈ ৰূপান্তৰ, আধুনিক অসমীয়া ভাষা-সাহিত্য, সংগীত-সংস্কৃতি, ৰাজনৈতিক প্ৰতিষ্ঠান, জনজাতীয় অধিকাৰ আৰু নাৰী শিক্ষাৰ বিষয়ে আলোচনা কৰা হয়।',
      'এই পাঠ্যক্ৰম তিনিটা গোটত বিভক্ত। গোট ১-ত ভাষা, সাহিত্য আৰু সংস্কৃতি আলোচনা কৰা হৈছে। গোট ২-ত ৰাজনৈতিক ভিত্তি আৰু জনজাতীয় অধিকাৰ আছে। গোট ৩-ত নাৰী শিক্ষা, ৰাজনীতি আৰু সাহিত্যৰ বিষয়সমূহ অন্তৰ্ভুক্ত।',
      'গোট ১-ত ভাষা-সাহিত্যৰ ক্ষেত্ৰত আনন্দৰাম ঢেকিয়াল ফুকন, লক্ষ্মীনাথ বেজবৰুৱা আৰু বাণীকান্ত কাকতি অন্তৰ্ভুক্ত। সংগীত-সংস্কৃতিত জ্যোতিপ্ৰসাদ আগৰৱালা, বিষ্ণুপ্ৰসাদ ৰাভা আৰু ভূপেন হাজৰিকাক আলোচনা কৰা হৈছে।',
      'গোট ২-ত অসম ছাত্ৰ সন্মিলন আৰু অসম এছ’চিয়েশ্যনৰ লগতে তৰুণ ৰাম ফুকন আৰু গোপীনাথ বৰদলৈৰ ৰাজনৈতিক ভূমিকা আলোচনা কৰা হৈছে। জনজাতীয় অধিকাৰৰ ক্ষেত্ৰত কালীচৰণ ব্ৰহ্ম আৰু ভিমবৰ দেউৰী অন্তৰ্ভুক্ত।',
      'গোট ৩-ত বেপ্তিষ্ট মিছনেৰীসকলৰ নাৰী শিক্ষাত ভূমিকা, চন্দ্ৰপ্ৰভা সাইকীয়ানী, অমল প্ৰভা দাস আৰু ৰাজবালা দাসৰ সংস্কাৰমূলক অৱদান, আৰু অসম লেখিকা সমাৰোহৰ সাহিত্যিক ভূমিকা আলোচনা কৰা হৈছে।',
    ];
    const replaceAnswers = (value: string, answers: string[]) => {
      const items = JSON.parse(value) as Array<Record<string, unknown>>;
      return JSON.stringify(items.map((item, index) => ({ ...item, answer: answers[index] })));
    };
    fixture.syllabus[0]!.qa_en = replaceAnswers(fixture.syllabus[0]!.qa_en as string, answersEn);
    fixture.syllabus[0]!.qa_as = replaceAnswers(fixture.syllabus[0]!.qa_as as string, answersAs);
    Object.assign(fixture.syllabus[0]!, addCompleteRagEvidence(fixture.syllabus[0]!));

    const result = assessSubjectPublishReadiness(fixture.subject, fixture.syllabus);
    expect(result.scores.aeo).toBe(100);
    expect(result.rag_score).toBe(100);
    expect(result.can_publish).toBe(true);
  });

  it('does not award bilingual readiness when English is copied into Assamese fields', () => {
    const fixture = completeFixture();
    fixture.syllabus[0]!.meta_description_as = fixture.syllabus[0]!.meta_description;
    fixture.syllabus[0]!.keywords_as = fixture.syllabus[0]!.keywords;
    fixture.syllabus[0]!.notes_as = fixture.syllabus[0]!.notes_en;
    fixture.syllabus[0]!.qa_as = fixture.syllabus[0]!.qa_en;

    const result = assessSubjectPublishReadiness(fixture.subject, fixture.syllabus);
    expect(result.scores.seo).toBeLessThan(100);
    expect(result.scores.geo).toBeLessThan(100);
    expect(result.scores.aeo).toBeLessThan(100);
    expect(result.checks.find(check => check.id === 'seo-meta-as')?.passed).toBe(false);
    expect(result.checks.find(check => check.id === 'aeo-paired-faqs')?.passed).toBe(false);
  });

  it('requires HTTPS source references in both language notes', () => {
    const fixture = completeFixture();
    fixture.syllabus[0]!.notes_en = fixture.syllabus[0]!.notes_en.replaceAll('https://', 'http://');
    fixture.syllabus[0]!.notes_as = fixture.syllabus[0]!.notes_as.replaceAll('https://', 'http://');

    const result = assessSubjectPublishReadiness(fixture.subject, fixture.syllabus);
    expect(result.scores.geo).toBe(80);
    expect(result.checks.find(check => check.id === 'geo-source-links')?.passed).toBe(false);
  });

  it('requires distinct substantive answers rather than five repeated FAQ answers', () => {
    const fixture = completeFixture();
    const english = JSON.parse(fixture.syllabus[0]!.qa_en as string) as Array<{ question: string; answer: string }>;
    const assamese = JSON.parse(fixture.syllabus[0]!.qa_as as string) as Array<{ question: string; answer: string }>;
    english.forEach(item => { item.answer = english[0]!.answer; });
    assamese.forEach(item => { item.answer = assamese[0]!.answer; });
    fixture.syllabus[0]!.qa_en = JSON.stringify(english);
    fixture.syllabus[0]!.qa_as = JSON.stringify(assamese);

    const result = assessSubjectPublishReadiness(fixture.subject, fixture.syllabus);
    expect(result.scores.aeo).toBeLessThan(100);
    expect(result.checks.find(check => check.id === 'aeo-substantive-answers')?.passed).toBe(false);
  });

  it('only awards FAQ schema readiness for fields the public FAQ route actually reads', () => {
    const fixture = completeFixture();
    const english = JSON.parse(fixture.syllabus[0]!.qa_en as string) as Array<{ question: string; answer: string }>;
    fixture.syllabus[0]!.qa_en = JSON.stringify(english.map(item => ({
      question_en: item.question,
      answer_en: item.answer,
    })));

    const result = assessSubjectPublishReadiness(fixture.subject, fixture.syllabus);
    expect(result.checks.find(check => check.id === 'aeo-faq-schema')?.passed).toBe(false);
    expect(result.scores.aeo).toBe(85);
  });

  it('scores topic pages only when bilingual notes and explicitly linked solutions are complete', () => {
    const fixture = completeFixture();
    const topic = {
      topic_id: 'global-trade',
      topic_slug: 'global-trade',
      topic_slug_as: 'global-trade-as',
      title: 'Global Trade',
      title_as: 'বিশ্ব বাণিজ্য',
      notes_status_en: 'approved',
      notes_status_as: 'approved',
      notes_en: `Global trade connects economies through exchange, institutions, and rules. It shapes markets and influences development across national borders.\n\n## Key details\n\n${words(90)}\n\nSource: https://example.org/global-trade`,
      notes_as: `বিশ্ব বাণিজ্য বিনিময়, প্ৰতিষ্ঠান আৰু নিয়মৰ জৰিয়তে অৰ্থনীতিসমূহক সংযোগ কৰে। ই বজাৰ গঢ়ে আৰু ৰাষ্ট্ৰীয় সীমাৰ ওপাৰে উন্নয়নত প্ৰভাৱ পেলায়।\n\n## মূল কথা\n\n${words(90)}\n\nউৎস: https://example.org/global-trade-as`,
    };
    fixture.syllabus[0]!.published_topics = JSON.stringify([topic]);
    fixture.syllabus[0]!.qa_en = JSON.stringify([{
      question_id: 'trade-q-en',
      question: 'What is global trade?',
      answer: words(10, 'It exchanges goods and services across economies.'),
      topic_id: 'global-trade',
    }]);
    fixture.syllabus[0]!.qa_as = JSON.stringify([{
      question_id: 'trade-q-as',
      question: 'বিশ্ব বাণিজ্য কি?',
      answer: words(10, 'ইয়াৰ জৰিয়তে অৰ্থনীতিসমূহৰ মাজত সামগ্ৰী আৰু সেৱা বিনিময় হয়।'),
      topic_id: 'global-trade',
    }]);

    const result = assessSubjectPublishReadiness(fixture.subject, fixture.syllabus);
    expect(result.topic_pages).toMatchObject({
      total: 1,
      ready: 1,
      topics: [{
        topic_id: 'global-trade',
        scores: { seo: 100, geo: 100, aeo: 100 },
        ready: true,
      }],
    });
  });
});
