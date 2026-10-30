export type PublicFaqEntry = { question: string; answer: string };

function safeParseArray(json: string | null | undefined): unknown[] {
  if (!json) return [];
  try {
    const parsed: unknown = JSON.parse(json);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function faqEntriesFromJson(
  json: string | null | undefined,
  language: 'en' | 'as',
): PublicFaqEntry[] {
  return safeParseArray(json).flatMap((item) => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return [];
    const row = item as Record<string, unknown>;
    const getText = (...keys: string[]) => keys
      .map(key => row[key])
      .find((value): value is string => typeof value === 'string' && value.trim().length > 0)
      ?.trim() ?? '';
    const question = language === 'as'
      ? getText('question_as', 'question')
      : getText('question_en', 'question');
    const answer = language === 'as'
      ? getText('answer_as', 'solution_as', 'answer', 'solution', 'explanation_as')
      : getText('answer_en', 'answer', 'solution', 'explanation');
    const hasExpectedScript = language === 'as'
      ? /[\u0980-\u09FF]/u.test(question) && /[\u0980-\u09FF]/u.test(answer)
      : /\p{Script=Latin}{2,}/u.test(question) && /\p{Script=Latin}{2,}/u.test(answer);
    return question.length > 5 && answer.length > 10 && hasExpectedScript
      ? [{ question, answer }]
      : [];
  });
}

export function faqJsonLd(entries: PublicFaqEntry[]): Record<string, unknown> | null {
  if (entries.length < 2) return null;
  return {
    '@context': 'https://schema.org',
    '@type': 'FAQPage',
    mainEntity: entries.slice(0, 20).map(({ question, answer }) => ({
      '@type': 'Question',
      name: question,
      acceptedAnswer: { '@type': 'Answer', text: answer },
    })),
  };
}
