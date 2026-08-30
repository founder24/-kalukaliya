// Public SEO copy for the /library page and its /browser alias.
//
// Keep this vocabulary separate from curriculum/source metadata. The public
// board name for this catalogue is Degree; source records may still contain
// their own internal board labels.

export const LIBRARY_SEO_TITLE =
  "Degree Subject Library — Notes, MCQs, Definitions & Exam Prep";

export const LIBRARY_SEO_DESCRIPTION =
  "Explore AHSEC, SEBA, and Degree subjects. AI-powered notes, MCQs, definitions, and exam preparation for Assam students.";

export const LIBRARY_SEO_KEYWORDS =
  "Degree study material, AHSEC notes, SEBA notes, Class 11 notes Assam, Class 12 notes Assam, MCQs, definitions, important questions, exam preparation Assam, Syrabit";

export const LIBRARY_SEO_URL = "https://syrabit.ai/library";

export function getLibrarySeoDescription(subjectCount, topicCount) {
  const subjects = Number(subjectCount) || 0;
  const topics = Number(topicCount) || 0;
  if (!subjects || !topics) return LIBRARY_SEO_DESCRIPTION;
  return `Explore ${subjects} subjects across AHSEC, SEBA, and Degree with ${topics} study topics. AI-powered notes, MCQs, definitions, important questions, and exam prep for Assam students.`;
}