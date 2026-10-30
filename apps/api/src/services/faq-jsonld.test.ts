import { describe, expect, it } from 'vitest';
import { faqEntriesFromJson, faqJsonLd } from './faq-jsonld';

describe('public FAQ extraction', () => {
  const pairs = [
    {
      question_en: 'What does this course examine?',
      answer_en: 'It examines social and political change in modern Assam.',
      question_as: 'এই পাঠ্যক্ৰমে কি আলোচনা কৰা হৈছে?',
      answer_as: 'ই আধুনিক অসমৰ সামাজিক আৰু ৰাজনৈতিক পৰিৱৰ্তন আলোচনা কৰে।',
    },
    {
      question_en: 'Which semester includes this course?',
      answer_en: 'The course belongs to the first semester of the undergraduate programme.',
      question_as: 'এই পাঠ্যক্ৰমটো কোনটো ষাণ্মাসিকত আছে?',
      answer_as: 'এই পাঠ্যক্ৰমটো স্নাতক কাৰ্যসূচীৰ প্ৰথম ষাণ্মাসিকত অন্তৰ্ভুক্ত।',
    },
  ];

  it('extracts matching English and Assamese answers from bilingual records', () => {
    const raw = JSON.stringify(pairs);
    const english = faqEntriesFromJson(raw, 'en');
    const assamese = faqEntriesFromJson(raw, 'as');

    expect(english).toHaveLength(2);
    expect(assamese).toHaveLength(2);
    expect(english[0]?.question).toBe(pairs[0]?.question_en);
    expect(assamese[0]?.question).toBe(pairs[0]?.question_as);
    expect(faqJsonLd(assamese)).toMatchObject({
      '@type': 'FAQPage',
      mainEntity: expect.arrayContaining([
        expect.objectContaining({
          name: pairs[0]?.question_as,
          acceptedAnswer: { '@type': 'Answer', text: pairs[0]?.answer_as },
        }),
      ]),
    });
  });

  it('does not treat missing translations or malformed Q&A as complete FAQ entries', () => {
    const raw = JSON.stringify([
      pairs[0],
      { question_as: pairs[1]?.question_as, answer_as: pairs[1]?.answer_as },
      { question_en: 'Short?', answer_en: 'Too short.' },
    ]);

    expect(faqEntriesFromJson(raw, 'en')).toHaveLength(1);
    expect(faqEntriesFromJson(raw, 'as')).toHaveLength(2);
    expect(faqEntriesFromJson('{malformed', 'as')).toEqual([]);
    expect(faqJsonLd(faqEntriesFromJson(JSON.stringify([pairs[0]]), 'en'))).toBeNull();
  });
});
