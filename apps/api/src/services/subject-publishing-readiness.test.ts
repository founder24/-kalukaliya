import { describe, expect, it } from 'vitest';
import { assessSubjectPublishReadiness } from './subject-publishing-readiness';

function words(count: number, ending = ''): string {
  return `${Array.from({ length: count }, (_, index) => `study${index}`).join(' ')} ${ending}`.trim();
}

function qaItems(language: 'en' | 'as') {
  return JSON.stringify(Array.from({ length: 5 }, (_, index) => ({
    question: `Question ${index + 1} ${language}`,
    answer: words(10, `Answer ${index + 1} explains the syllabus content in ${language}.`),
  })));
}

function completeFixture() {
  const unitsEn = ['Unit I', 'Unit II', 'Unit III'].map((unit) => `## ${unit}\n\n${words(190, 'This unit explains the course content and learning outcome.')}`).join('\n\n');
  const unitsAs = ['গোট ১', 'গোট ২', 'গোট ৩'].map((unit) => `## ${unit}\n\n${words(190, 'এই গোটত পাঠ্যক্ৰমৰ বিষয়বস্তু আৰু শিক্ষণৰ ফলাফল বৰ্ণনা কৰা হৈছে।')}`).join('\n\n');
  return {
    subject: {
      publication_profile: 'bilingual_syllabus',
      name: 'Globalisation and the Contemporary World MDC-03013',
      name_as: 'বিশ্বায়ন আৰু সমসাময়িক বিশ্ব',
      slug: 'globalisation-contemporary-world-mdc-03013',
      description: `MDC-03013 Semester 3 at Example University. ${words(25)}`,
      stream_name: 'B.A.',
      board_name: 'Degree',
      class_name: '3rd Semester',
    },
    syllabus: [{
      id: 'syllabus',
      title: 'Globalisation and the Contemporary World',
      title_as: 'বিশ্বায়ন আৰু সমসাময়িক বিশ্ব',
      slug: 'course-syllabus-globalisation-mdc-03013',
      slug_as: 'course-syllabus-globalisation-mdc-03013-as',
      status: 'published',
      meta_description: 'MDC-03013 Semester 3 syllabus covers globalization, global institutions, trade, culture, and political change in a bilingual undergraduate guide.',
      meta_description_as: 'MDC-03013 তৃতীয় ষাণ্মাসিকৰ পাঠ্যক্ৰমত বিশ্বায়ন, বিশ্ব প্ৰতিষ্ঠান, বাণিজ্য, সংস্কৃতি আৰু ৰাজনৈতিক পৰিৱৰ্তনৰ বিষয়ে স্নাতক শিক্ষাৰ্থীৰ বাবে গোটভিত্তিক দ্বিভাষিক গাইড আছে।',
      keywords: 'globalisation, MDC-03013, Semester 3',
      keywords_as: 'বিশ্বায়ন, MDC-03013, তৃতীয় ষাণ্মাসিক',
      notes_en: `MDC-03013 is a Semester 3 course at Example University. ${words(45)}\n\n${unitsEn}\n\nSources: https://example.org/official-syllabus and https://example.org/research`,
      notes_as: `MDC-03013 তৃতীয় ষাণ্মাসিকৰ পাঠ্যক্ৰম। ${words(45)}\n\n${unitsAs}\n\nউৎস: https://example.org/official-syllabus and https://example.org/research`,
      qa_en: qaItems('en'),
      qa_as: qaItems('as'),
      published_topics: '[]',
      rag_updated_at: 100,
      rag_indexed_at: 100,
    }],
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
    expect(result.blockers).toContain('One published course-syllabus page is available');
  });

  it('reaches full readiness only when every bilingual check passes', () => {
    const fixture = completeFixture();
    const result = assessSubjectPublishReadiness(fixture.subject, fixture.syllabus);
    expect(result.scores).toEqual({ seo: 100, geo: 100, aeo: 100 });
    expect(result.blockers).toEqual([]);
    expect(result.can_publish).toBe(true);
    expect(result.rag_ready).toBe(true);
  });

  it('allows publishing at 90 in every category and lists remaining improvements separately', () => {
    const fixture = completeFixture();
    fixture.syllabus[0]!.keywords = '';
    fixture.syllabus[0]!.keywords_as = '';
    const result = assessSubjectPublishReadiness(fixture.subject, fixture.syllabus);
    expect(result.scores.seo).toBe(90);
    expect(result.scores.geo).toBeGreaterThanOrEqual(90);
    expect(result.scores.aeo).toBeGreaterThanOrEqual(90);
    expect(result.can_publish).toBe(true);
    expect(result.blockers).toHaveLength(0);
    expect(result.improvements).toContain('English and Assamese topic keywords are present');
  });

  it('blocks publishing when any category falls below 90', () => {
    const fixture = completeFixture();
    fixture.syllabus[0]!.meta_description = '';
    const result = assessSubjectPublishReadiness(fixture.subject, fixture.syllabus);
    expect(result.scores.seo).toBe(85);
    expect(result.can_publish).toBe(false);
    expect(result.blockers).toContain('English meta description is 120–160 characters');
  });

  it('keeps AEO incomplete when bilingual FAQ pairs are missing', () => {
    const fixture = completeFixture();
    fixture.syllabus[0]!.qa_as = '[]';
    const result = assessSubjectPublishReadiness(fixture.subject, fixture.syllabus);
    expect(result.scores.aeo).toBeLessThan(100);
    expect(result.can_publish).toBe(false);
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
