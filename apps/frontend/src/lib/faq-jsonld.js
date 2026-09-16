export function normalizeFaqEntries(faqEntries) {
  if (!Array.isArray(faqEntries)) return [];

  return faqEntries
    .map((entry) => ({
      question: String(entry?.question || entry?.name || "").trim(),
      answer: String(entry?.answer || entry?.text || "").trim(),
    }))
    .filter((entry) => entry.question.length > 5 && entry.answer.length > 10)
    .slice(0, 10);
}