/**
 * The production AdSense contract shared by the Vite config and the
 * Cloudflare Pages release loader.
 *
 * Every manual placement has its own Google ad unit. Keeping the environment
 * variable names here prevents a new placement from accidentally inheriting a
 * different route's unit.
 */

export const ADSENSE_PLACEMENT_ENV_KEYS = Object.freeze({
  'chat.afterAssistant': 'VITE_ADS_ADSENSE_CHAT_AFTER_ASSISTANT_SLOT',
  'pyq.topOfContent': 'VITE_ADS_ADSENSE_PYQ_TOP_SLOT',
  'pyq.inContent': 'VITE_ADS_ADSENSE_PYQ_INCONTENT_SLOT',
  'pyq.betweenImages': 'VITE_ADS_ADSENSE_PYQ_BETWEEN_IMAGES_SLOT',
  'pyq.endOfContent': 'VITE_ADS_ADSENSE_PYQ_END_SLOT',
  'learn.topOfContent': 'VITE_ADS_ADSENSE_LEARN_TOP_SLOT',
  'learn.inContent': 'VITE_ADS_ADSENSE_LEARN_INCONTENT_SLOT',
  'learn.afterPyqs': 'VITE_ADS_ADSENSE_LEARN_AFTER_PYQS_SLOT',
  'learn.afterFlashcards': 'VITE_ADS_ADSENSE_LEARN_AFTER_FLASHCARDS_SLOT',
  'learn.endOfContent': 'VITE_ADS_ADSENSE_LEARN_END_SLOT',
  'learn.sidebar': 'VITE_ADS_ADSENSE_LEARN_SIDEBAR_SLOT',
  'learn.afterQuestion': 'VITE_ADS_ADSENSE_LEARN_AFTER_QUESTION_SLOT',
  'chapter.notes.top': 'VITE_ADS_ADSENSE_CHAPTER_NOTES_TOP_SLOT',
  'chapter.notes.inContent': 'VITE_ADS_ADSENSE_CHAPTER_NOTES_INCONTENT_SLOT',
  'chapter.notes.end': 'VITE_ADS_ADSENSE_CHAPTER_NOTES_END_SLOT',
  'chapter.qa.inContent': 'VITE_ADS_ADSENSE_CHAPTER_QA_INCONTENT_SLOT',
  'chapter.qa.end': 'VITE_ADS_ADSENSE_CHAPTER_QA_END_SLOT',
  'chapter.sidebar': 'VITE_ADS_ADSENSE_CHAPTER_SIDEBAR_SLOT',
  'chapter.pyq.top': 'VITE_ADS_ADSENSE_CHAPTER_PYQ_TOP_SLOT',
  'chapter.pyq.betweenImages': 'VITE_ADS_ADSENSE_CHAPTER_PYQ_BETWEEN_IMAGES_SLOT',
  'chapter.pyq.inContent': 'VITE_ADS_ADSENSE_CHAPTER_PYQ_INCONTENT_SLOT',
});

export const ADSENSE_SLOT_ENV_KEYS = Object.freeze(
  Object.values(ADSENSE_PLACEMENT_ENV_KEYS),
);

export const ADSENSE_SLOT_ID_PATTERN = /^\d{5,20}$/;

function normalizedValue(environment, key) {
  const value = environment?.[key];
  return typeof value === 'string' ? value.trim() : '';
}

/**
 * Validate the complete production slot map without exposing slot values in
 * errors or logs. Duplicate IDs are invalid because they collapse provider
 * reporting for otherwise distinct placements.
 */
export function validateAdsenseSlotEnv(environment, { requireUnique = true } = {}) {
  const missing = [];
  const invalid = [];
  const duplicates = [];
  const seen = new Map();

  for (const key of ADSENSE_SLOT_ENV_KEYS) {
    const value = normalizedValue(environment, key);
    if (!value) {
      missing.push(key);
      continue;
    }
    if (!ADSENSE_SLOT_ID_PATTERN.test(value)) {
      invalid.push(key);
      continue;
    }
    if (requireUnique && seen.has(value)) {
      duplicates.push(`${key} duplicates ${seen.get(value)}`);
    } else {
      seen.set(value, key);
    }
  }

  return {
    valid: missing.length === 0 && invalid.length === 0 && duplicates.length === 0,
    missing,
    invalid,
    duplicates,
  };
}
